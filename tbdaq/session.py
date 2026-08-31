"""Safe session orchestration for coordinated test-bench acquisition."""
from __future__ import annotations

import json
import logging
import re
import secrets
import threading
import time
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from tbdaq.config import SessionConfig
from tbdaq.isa_export import ExportWindow, SignalFile, write_signal_map

_LOG = logging.getLogger(__name__)


class Session:
    """Run one validated diagnostic or prognostic acquisition session.

    Adapters may be injected for tests. Production sessions create adapters
    from the enabled sensor sections in the configuration.
    """

    def __init__(
        self,
        config: SessionConfig,
        *,
        adapters: Optional[Mapping[str, Any]] = None,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        input_fn: Callable[[str], str] = input,
        session_id: Optional[str] = None,
    ) -> None:
        config.validate()
        self._config = config
        self._clock = clock
        self._monotonic = monotonic
        self._sleep = sleep
        self._input = input_fn
        self._interrupt_requested = False
        self._session_id = session_id or _new_session_id(config.name)
        self._session_dir = Path(config.output_root) / self._session_id
        self._session_dir.mkdir(parents=True, exist_ok=False)
        self._manifest_path = self._session_dir / "session_manifest.json"
        self._export_map_path = self._session_dir / "signal_export_map.csv"
        self._log_handler = self._install_file_logging()
        self._adapters = dict(adapters) if adapters is not None else self._build_adapters()
        self._deferred_processing: list[
            tuple[dict[str, Any], str, Any, Path, ExportWindow]
        ] = []
        self._manifest: dict[str, Any] = {
            "schema_version": 1,
            "application": "TestbenchDAQ",
            "name": config.name,
            "session_id": self._session_id,
            "session_dir": str(self._session_dir),
            "mode": config.mode,
            "status": "initializing",
            "abort_reason": None,
            "started_at_utc": _iso_utc(self._clock()),
            "ended_at_utc": None,
            "config": config.to_dict(),
            "preflight": {},
            "runs": [],
            "artifacts": {
                "manifest": str(self._manifest_path),
                "log": str(self._session_dir / "session.log"),
                "signal_export_map": str(self._export_map_path),
            },
            "limitations": [
                "Signal CSV output is an interim format, not a complete ISA-PHM serialization.",
                "Exports share a measurement window but are not sample-level synchronized.",
            ],
        }
        self._write_manifest()

    def _build_adapters(self) -> dict[str, Any]:
        adapters: dict[str, Any] = {}
        if self._config.gator.enabled:
            from tbdaq.adapters.gator import GatorAdapter

            adapters["gator"] = GatorAdapter(self._config.gator)
        if self._config.endaq.enabled:
            from tbdaq.adapters.endaq import EndaqAdapter

            adapters["endaq"] = EndaqAdapter(
                self._config.endaq,
                run_duration_s=self._config.run_duration_s,
            )
        return adapters

    def run(self) -> dict[str, Any]:
        """Execute the session and always persist a terminal manifest."""
        try:
            active = self._preflight()
            if not active:
                return self._finish("aborted", "No enabled sensors passed preflight.")

            unavailable = [
                family
                for family in self._config.enabled_families()
                if family not in active
            ]
            if unavailable and not self._config.allow_partial:
                return self._finish(
                    "aborted",
                    f"Required sensor preflight failed: {', '.join(unavailable)}.",
                )

            self._manifest["status"] = "running"
            self._write_manifest()
            base_wall = self._clock()
            base_monotonic = self._monotonic()

            for run_number in range(1, self._config.run_count + 1):
                period = self._config.run_period_s or 0.0
                planned_monotonic = base_monotonic + ((run_number - 1) * period)
                planned_wall = base_wall + ((run_number - 1) * period)
                self._wait_until(planned_monotonic)
                lateness = max(0.0, self._monotonic() - planned_monotonic)
                schedule_warning = None
                if lateness > self._config.missed_start_tolerance_s:
                    reason = (
                        f"run_{run_number:02d} missed its planned start by "
                        f"{lateness:.3f}s (tolerance "
                        f"{self._config.missed_start_tolerance_s:.3f}s)."
                    )
                    if self._config.missed_start_policy == "abort":
                        abort_message = (
                            "PROGNOSTIC SCHEDULE VIOLATION — ABORTING SESSION: "
                            f"{reason} Increase run_period_s to leave enough time for "
                            "enDAQ stop/remount/offload, or explicitly set "
                            "missed_start_policy to 'start_late'."
                        )
                        _LOG.error(abort_message)
                        self._manifest["runs"].append(
                            {
                                "run_id": f"run_{run_number:02d}",
                                "run_number": run_number,
                                "status": "aborted",
                                "planned_start_utc": _iso_utc(planned_wall),
                                "lateness_s": round(lateness, 6),
                                "errors": [abort_message],
                            }
                        )
                        return self._finish("aborted", abort_message)
                    schedule_warning = reason + " Starting late by policy."
                    _LOG.warning(schedule_warning)

                run_record = self._execute_run(
                    run_number=run_number,
                    planned_start_wall=planned_wall,
                    lateness=lateness,
                    schedule_warning=schedule_warning,
                    active_adapters=active,
                )
                if unavailable and run_record["status"] == "success":
                    run_record["status"] = "partial"
                    run_record["errors"].append(
                        "Enabled sensors unavailable at preflight: "
                        + ", ".join(unavailable)
                        + "."
                    )
                self._manifest["runs"].append(run_record)
                self._write_manifest()

                if run_record["status"] in {"failed", "interrupted"}:
                    terminal = "interrupted" if run_record["status"] == "interrupted" else "failed"
                    return self._finish(
                        terminal,
                        f"{run_record['run_id']} ended with status {run_record['status']}.",
                    )
                if run_record["status"] == "partial" and not self._config.allow_partial:
                    return self._finish(
                        "failed",
                        f"{run_record['run_id']} was partial while allow_partial is false.",
                    )

            self._process_deferred_runs()
            statuses = [run["status"] for run in self._manifest["runs"]]
            status = "success" if statuses and all(item == "success" for item in statuses) else "partial"
            return self._finish(status)
        except KeyboardInterrupt:
            return self._finish("interrupted", "Interrupted by user.")
        except Exception as exc:
            _LOG.exception("Unhandled session error")
            return self._finish("failed", f"Unhandled session error: {exc}")
        finally:
            self._remove_file_logging()

    def _preflight(self) -> dict[str, Any]:
        active: dict[str, Any] = {}
        for family in self._config.enabled_families():
            adapter = self._adapters.get(family)
            if adapter is None:
                error = f"No adapter implementation was provided for {family}."
            else:
                try:
                    error = adapter.discover()
                except Exception as exc:
                    error = f"Discovery raised {type(exc).__name__}: {exc}"
            available = adapter is not None and error is None
            self._manifest["preflight"][family] = {
                "enabled": True,
                "available": available,
                "error": error,
            }
            if available:
                active[family] = adapter
        self._write_manifest()
        return active

    def _execute_run(
        self,
        *,
        run_number: int,
        planned_start_wall: float,
        lateness: float,
        schedule_warning: Optional[str],
        active_adapters: Mapping[str, Any],
    ) -> dict[str, Any]:
        run_id = f"run_{run_number:02d}"
        run_dir = self._session_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        record: dict[str, Any] = {
            "run_id": run_id,
            "run_number": run_number,
            "status": "starting",
            "planned_start_utc": _iso_utc(planned_start_wall),
            "orchestrator_start_utc": _iso_utc(self._clock()),
            "lateness_s": round(lateness, 6),
            "measurement_window": {
                "reference": "all_started_adapters_ready",
                "start_utc": None,
                "stop_requested_utc": None,
                "target_duration_s": self._config.run_duration_s,
                "actual_duration_s": None,
            },
            "adapters": {},
            "signals": [],
            "processing_status": "pending",
            "warnings": [],
            "errors": [],
        }
        if schedule_warning:
            record["warnings"].append(schedule_warning)

        start_calls = self._call_adapters_concurrently(
            active_adapters,
            method="start_run",
            run_dir=run_dir,
        )
        started: dict[str, Any] = {}
        for family, call in start_calls.items():
            result = call["result"]
            ok = result is not None and bool(getattr(result, "ok", False))
            record["adapters"][family] = {
                "start_dispatch_utc": call["dispatch_utc"],
                "start_return_utc": call["return_utc"],
                "start": _serialize_result(result),
                "stop": None,
                "processing": None,
            }
            if call["error"]:
                record["errors"].append(f"{family} start call failed: {call['error']}")
            elif not ok:
                error = getattr(result, "error", None) or "adapter did not report a successful start"
                record["errors"].append(f"{family} start failed: {error}")
            else:
                started[family] = active_adapters[family]

        missing = [family for family in active_adapters if family not in started]
        if self._interrupt_requested:
            stop_calls = self._stop_started(
                self._adapters_requiring_cleanup(started, active_adapters),
                run_dir,
            )
            self._record_stops(record, stop_calls)
            record["status"] = "interrupted"
            record["errors"].append("Interrupted while sensors were starting.")
            record["ended_at_utc"] = _iso_utc(self._clock())
            return record
        if missing and not self._config.allow_partial:
            stop_calls = self._stop_started(
                self._adapters_requiring_cleanup(started, active_adapters),
                run_dir,
            )
            self._record_stops(record, stop_calls)
            record["status"] = "failed"
            record["errors"].append(
                f"Required sensors failed to start: {', '.join(missing)}."
            )
            record["ended_at_utc"] = _iso_utc(self._clock())
            return record
        if not started:
            record["status"] = "failed"
            record["errors"].append("No sensors started successfully.")
            record["ended_at_utc"] = _iso_utc(self._clock())
            return record

        measurement_start_wall = self._clock()
        measurement_start_monotonic = self._monotonic()
        record["measurement_window"]["start_utc"] = _iso_utc(measurement_start_wall)
        record["status"] = "measuring"
        interrupted = False

        try:
            if self._config.run_duration_s is None:
                self._input("Press Enter to stop the diagnostic run...")
            else:
                self._wait_duration(self._config.run_duration_s)
        except (KeyboardInterrupt, EOFError):
            interrupted = True
            record["errors"].append("Measurement interrupted by user.")
        finally:
            stop_request_wall = self._clock()
            record["measurement_window"]["stop_requested_utc"] = _iso_utc(stop_request_wall)
            record["measurement_window"]["actual_duration_s"] = round(
                self._monotonic() - measurement_start_monotonic,
                6,
            )
            stop_calls = self._stop_started(started, run_dir)
            self._record_stops(record, stop_calls)

        export_stop_wall = stop_request_wall
        if self._config.run_duration_s is not None:
            export_stop_wall = min(
                export_stop_wall,
                measurement_start_wall + self._config.run_duration_s,
            )
        export_window = ExportWindow(measurement_start_wall, export_stop_wall)
        record["synchronization"] = {
            "method": "common_window_only",
            "drift_correction": False,
            "resampling": False,
            "sample_level_synchronized": False,
            "window_start_utc": record["measurement_window"]["start_utc"],
            "window_stop_utc": _iso_utc(export_window.stop_utc_s),
            "device_time_origins": {
                "gator": "first retained Gator sample (device UTC offset not trusted)",
                "endaq": "IDE session UTC mapped to host measurement-window start",
            } if set(started) == {"gator", "endaq"} else {
                family: (
                    "first retained Gator sample (device UTC offset not trusted)"
                    if family == "gator"
                    else "IDE session UTC mapped to host measurement-window start"
                )
                for family in started
            },
            "limitations": [
                "Native sample rates are preserved; timestamps are not resampled.",
                "No clock-drift or phase correction is applied.",
                "Gator device UTC is not used for absolute alignment because its observed offset from host UTC is not trusted.",
            ],
        }

        processing_incomplete = False
        for family, adapter in started.items():
            adapter_record = record["adapters"][family]
            if self._config.mode == "prognostic" and family == "endaq":
                adapter_record["processing"] = {"status": "deferred"}
                self._deferred_processing.append(
                    (record, family, adapter, run_dir, export_window)
                )
                continue
            try:
                signals, complete = self._process_adapter(
                    record, family, adapter, run_dir, export_window
                )
                processing_incomplete |= not complete
            except Exception as exc:
                processing_incomplete = True
                adapter_record["processing"] = {
                    "status": "failed",
                    "successful_signals": 0,
                    "failed_signals": 1,
                    "error": str(exc),
                }
                record["warnings"].append(f"{family} processing failed: {exc}")

        stop_failed = any(
            item.get("stop_call_error")
            or item.get("stop") is None
            or item.get("stop", {}).get("error")
            for item in record["adapters"].values()
            if "stop" in item
        )
        record["processing_status"] = (
            "deferred" if self._config.mode == "prognostic" and "endaq" in started
            else "incomplete" if processing_incomplete
            else "success"
        )
        if interrupted or self._interrupt_requested:
            record["status"] = "interrupted"
        elif stop_failed:
            record["status"] = "failed"
        elif missing:
            record["status"] = "partial"
        else:
            record["status"] = "success"
        record["ended_at_utc"] = _iso_utc(self._clock())
        return record

    @staticmethod
    def _adapters_requiring_cleanup(
        started: Mapping[str, Any],
        active_adapters: Mapping[str, Any],
    ) -> dict[str, Any]:
        cleanup = dict(started)
        for family, adapter in active_adapters.items():
            if bool(getattr(adapter, "needs_stop", False)):
                cleanup[family] = adapter
        return cleanup

    def _process_adapter(
        self,
        record: dict[str, Any],
        family: str,
        adapter: Any,
        run_dir: Path,
        window: ExportWindow,
    ) -> tuple[list[SignalFile], bool]:
        signals = adapter.export_run(run_dir, window=window)
        failed = [signal for signal in signals if signal.status != "success"]
        status = "success" if signals and not failed else "failed"
        if family == "endaq":
            status = getattr(adapter.result, "conversion_status", "unknown")
        record["adapters"][family]["processing"] = {
            "status": status,
            "successful_signals": sum(s.status == "success" for s in signals),
            "failed_signals": len(failed),
        }
        conversion_error = getattr(adapter.result, "conversion_error", None)
        if conversion_error:
            record["adapters"][family]["processing"]["error"] = conversion_error
        complete = status == "success"
        if not complete:
            record["warnings"].append(
                f"{family} processing status is {status}; raw acquisition is retained."
            )
        record["signals"].extend(_serialize_signal(signal) for signal in signals)
        if signals:
            write_signal_map(
                self._export_map_path,
                [
                    {
                        "run_id": record["run_id"],
                        "device_family": _signal_family(signal),
                        "signal_alias": signal.alias,
                        "source_file": signal.source_file,
                        "source_column": signal.source_column,
                        "signal_path": str(signal.path),
                        "time_unit": "s",
                        "sample_count": signal.sample_count,
                        "first_time_s": signal.first_time_s,
                        "last_time_s": signal.last_time_s,
                        "alignment_method": signal.alignment_method or "",
                        "status": signal.status,
                        "error": signal.error or "",
                    }
                    for signal in signals
                ],
            )
        return signals, complete

    def _process_deferred_runs(self) -> None:
        for record, family, adapter, run_dir, window in self._deferred_processing:
            try:
                _signals, complete = self._process_adapter(
                    record, family, adapter, run_dir, window
                )
                record["processing_status"] = "success" if complete else "incomplete"
            except Exception as exc:
                record["processing_status"] = "incomplete"
                record["adapters"][family]["processing"] = {
                    "status": "failed",
                    "successful_signals": 0,
                    "failed_signals": 1,
                    "error": str(exc),
                }
                record["warnings"].append(
                    f"{family} deferred processing failed: {exc}"
                )
            self._write_manifest()
        self._deferred_processing.clear()

    def _call_adapters_concurrently(
        self,
        adapters: Mapping[str, Any],
        *,
        method: str,
        run_dir: Path,
    ) -> dict[str, dict[str, Any]]:
        barrier = threading.Barrier(len(adapters) + 1)
        calls: dict[str, dict[str, Any]] = {
            family: {"result": None, "error": None, "dispatch_utc": None, "return_utc": None}
            for family in adapters
        }

        def invoke(family: str, adapter: Any) -> None:
            try:
                barrier.wait()
                calls[family]["dispatch_utc"] = _iso_utc(self._clock())
                calls[family]["result"] = getattr(adapter, method)(run_dir)
            except Exception as exc:
                calls[family]["error"] = f"{type(exc).__name__}: {exc}"
            finally:
                calls[family]["return_utc"] = _iso_utc(self._clock())

        threads = [
            threading.Thread(target=invoke, args=(family, adapter), daemon=True)
            for family, adapter in adapters.items()
        ]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            while thread.is_alive():
                try:
                    thread.join(timeout=0.2)
                except KeyboardInterrupt:
                    # Adapter calls must reach a known state before cleanup can
                    # be performed. Remember the interrupt and finish joining.
                    self._interrupt_requested = True
        return calls

    def _stop_started(
        self,
        started: Mapping[str, Any],
        run_dir: Path,
    ) -> dict[str, dict[str, Any]]:
        if not started:
            return {}
        return self._call_adapters_concurrently(
            started,
            method="stop_run",
            run_dir=run_dir,
        )

    @staticmethod
    def _record_stops(record: dict[str, Any], calls: Mapping[str, dict[str, Any]]) -> None:
        for family, call in calls.items():
            adapter_record = record["adapters"].setdefault(family, {})
            result = call["result"]
            adapter_record["stop_dispatch_utc"] = call["dispatch_utc"]
            adapter_record["stop_return_utc"] = call["return_utc"]
            adapter_record["stop"] = _serialize_result(result)
            adapter_record["stop_call_error"] = call["error"]
            if call["error"]:
                message = f"{family} stop call failed: {call['error']}"
                record["errors"].append(message)
                _LOG.error(message)
            elif result is None:
                message = f"{family} stop call returned no result."
                record["errors"].append(message)
                _LOG.error(message)
            elif not bool(getattr(result, "ok", False)):
                message = (
                    f"{family} stop failed: "
                    f"{getattr(result, 'error', 'unknown error')}"
                )
                record["errors"].append(message)
                _LOG.error(message)

    def _wait_until(self, target_monotonic: float) -> None:
        while True:
            remaining = target_monotonic - self._monotonic()
            if remaining <= 0:
                return
            self._sleep(min(remaining, 0.2))

    def _wait_duration(self, duration_s: float) -> None:
        self._wait_until(self._monotonic() + duration_s)

    def _finish(self, status: str, reason: Optional[str] = None) -> dict[str, Any]:
        self._manifest["status"] = status
        self._manifest["abort_reason"] = reason
        self._manifest["ended_at_utc"] = _iso_utc(self._clock())
        self._write_manifest()
        return self._manifest

    def _write_manifest(self) -> None:
        temporary = self._manifest_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(self._manifest, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(self._manifest_path)

    def _install_file_logging(self) -> logging.Handler:
        handler = logging.FileHandler(self._session_dir / "session.log", encoding="utf-8")
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S",
            )
        )
        logging.getLogger("tbdaq").addHandler(handler)
        return handler

    def _remove_file_logging(self) -> None:
        if self._log_handler is None:
            return
        logging.getLogger("tbdaq").removeHandler(self._log_handler)
        self._log_handler.close()
        self._log_handler = None


def _new_session_id(name: Optional[str] = None) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    generated = f"{timestamp}-{secrets.token_hex(2)}"
    if not name:
        return generated
    prefix = re.sub(r"[^A-Za-z0-9._-]+", "-", name.strip()).strip("-._")
    return f"{prefix}__{generated}"


def _iso_utc(epoch_s: float) -> str:
    return datetime.fromtimestamp(epoch_s, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _serialize_result(result: Any) -> Optional[dict[str, Any]]:
    if result is None:
        return None
    if is_dataclass(result):
        return asdict(result)
    if isinstance(result, dict):
        return dict(result)
    return {
        "ok": bool(getattr(result, "ok", False)),
        "error": getattr(result, "error", None),
    }


def _serialize_signal(signal: SignalFile) -> dict[str, Any]:
    return {
        "alias": signal.alias,
        "path": str(signal.path),
        "source_file": signal.source_file,
        "source_column": signal.source_column,
        "status": signal.status,
        "error": signal.error,
        "sample_count": signal.sample_count,
        "first_time_s": signal.first_time_s,
        "last_time_s": signal.last_time_s,
        "alignment_method": signal.alignment_method,
    }


def _signal_family(signal: SignalFile) -> str:
    return "gator" if signal.alias.startswith("gator") else "endaq"
