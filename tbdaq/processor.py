"""Durable server-side conversion queue for completed TestbenchDAQ sessions."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import time
from typing import Any

from tbdaq.isa_export import export_endaq_ide_signals, export_gator_signals, write_signal_map

_LOG = logging.getLogger(__name__)
_REQUEST = "processing-request.json"
_CLAIM = "processing-claim.json"
_RETRY_DELAYS_MINUTES = (1, 5, 15)


def write_processing_request(session_dir: Path) -> None:
    """Atomically queue a session after all raw artifacts are safe on NFS."""
    _write_json(session_dir / _REQUEST, {"schema_version": 1, "requested_at_utc": _utc_now()})


class ProcessingWorker:
    """Single-worker, filesystem-backed processor safe to restart at any time."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def process_once(self) -> bool:
        for request in sorted(self.root.glob(f"*/*/{_REQUEST}")):
            if not self._ready(request.parent):
                continue
            if self._claim(request):
                self._process_claim(request.parent)
                return True
        return False

    @staticmethod
    def _ready(session_dir: Path) -> bool:
        try:
            retry_after = _read_json(session_dir / "session_manifest.json").get("processing", {}).get("retry_after_utc")
            if not retry_after:
                return True
            return datetime.fromisoformat(str(retry_after).replace("Z", "+00:00")) <= datetime.now(timezone.utc)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return True

    def _claim(self, request: Path) -> bool:
        claim = request.with_name(_CLAIM)
        try:
            request.replace(claim)
            return True
        except FileNotFoundError:
            return False

    def _process_claim(self, session_dir: Path) -> None:
        claim = session_dir / _CLAIM
        try:
            manifest = _read_json(session_dir / "session_manifest.json")
            processing = manifest.setdefault("processing", {})
            attempts = int(processing.get("attempts", 0)) + 1
            processing.update({"status": "processing", "attempts": attempts, "last_error": None})
            _set_phase(manifest, "processing")
            _write_json(session_dir / "session_manifest.json", manifest)

            rows: list[dict[str, Any]] = []
            for run in manifest.get("runs", []):
                run_dir = session_dir / str(run["run_id"])
                signals = []
                gator_raw = run_dir / "raw" / "gator" / "gator_channel.csv"
                if gator_raw.is_file():
                    signals.extend(export_gator_signals(gator_raw, run_dir / "signals" / "gator"))
                for ide in sorted((run_dir / "raw" / "endaq").glob("*.IDE")) + sorted((run_dir / "raw" / "endaq").glob("*.ide")):
                    signals.extend(export_endaq_ide_signals(ide, run_dir / "signals" / "endaq"))
                if not signals:
                    raise RuntimeError(f"No supported raw acquisition files found for {run_dir.name}.")
                failed = [signal for signal in signals if signal.status != "success"]
                run["signals"] = [_signal_dict(signal) for signal in signals]
                run["processing_status"] = "success" if not failed else "incomplete"
                for family in ("gator", "endaq"):
                    family_signals = [signal for signal in signals if _family(signal.alias) == family]
                    if family_signals:
                        run.setdefault("adapters", {}).setdefault(family, {})["processing"] = {
                            "status": "success" if all(s.status == "success" for s in family_signals) else "failed",
                            "successful_signals": sum(s.status == "success" for s in family_signals),
                            "failed_signals": sum(s.status != "success" for s in family_signals),
                        }
                rows.extend(_signal_rows(run["run_id"], signals))
                if failed:
                    raise RuntimeError(f"Signal conversion failed for {run_dir.name}.")
            write_signal_map(session_dir / "signal_export_map.csv", rows)
            processing.update({"status": "completed", "completed_at_utc": _utc_now(), "last_error": None})
            manifest["status"] = "success"
            manifest["ended_at_utc"] = _utc_now()
            _set_phase(manifest, "completed")
            _write_json(session_dir / "session_manifest.json", manifest)
            claim.unlink(missing_ok=True)
        except Exception as exc:
            _LOG.exception("Processing failed for %s", session_dir.name)
            manifest = _read_json(session_dir / "session_manifest.json")
            processing = manifest.setdefault("processing", {})
            attempts = int(processing.get("attempts", 1))
            processing["last_error"] = str(exc)
            if attempts >= len(_RETRY_DELAYS_MINUTES):
                processing["status"] = "failed"
                manifest["status"] = "processing_failed"
                _set_phase(manifest, "processing_failed")
                claim.unlink(missing_ok=True)
            else:
                processing["status"] = "queued"
                processing["retry_after_utc"] = _utc_after(_RETRY_DELAYS_MINUTES[attempts - 1])
                _set_phase(manifest, "queued")
                claim.replace(session_dir / _REQUEST)
            _write_json(session_dir / "session_manifest.json", manifest)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"Expected JSON object: {path}")
    return value


def _write_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _set_phase(manifest: dict[str, Any], phase: str) -> None:
    live = manifest.setdefault("live_status", {})
    live["phase"] = phase
    live["last_updated_utc"] = _utc_now()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _utc_after(minutes: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def _family(alias: str) -> str:
    return "gator" if alias.startswith("gator") else "endaq"


def _signal_dict(signal: Any) -> dict[str, Any]:
    return {key: getattr(signal, key) for key in (
        "alias", "source_file", "source_column", "status", "error", "sample_count",
        "first_time_s", "last_time_s", "alignment_method",
    )} | {"path": str(signal.path)}


def _signal_rows(run_id: str, signals: list[Any]) -> list[dict[str, Any]]:
    return [{
        "run_id": run_id, "device_family": _family(signal.alias), "signal_alias": signal.alias,
        "source_file": signal.source_file, "source_column": signal.source_column,
        "signal_path": str(signal.path), "time_unit": "s", "sample_count": signal.sample_count,
        "first_time_s": signal.first_time_s, "last_time_s": signal.last_time_s,
        "alignment_method": signal.alignment_method or "", "status": signal.status,
        "error": signal.error or "",
    } for signal in signals]


def main() -> int:
    parser = argparse.ArgumentParser(description="Process queued TestbenchDAQ signal exports.")
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--poll-seconds", type=float, default=10.0)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    worker = ProcessingWorker(args.data_root)
    while True:
        if not worker.process_once():
            time.sleep(args.poll_seconds)
    

if __name__ == "__main__":
    raise SystemExit(main())
