"""Session orchestrator: starts both adapters concurrently, records sync data,
   writes a manifest, and handles fixed-duration + manual-stop + multi-run modes."""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from tbdaq.config import SessionConfig
from tbdaq.adapters.gator import GatorAdapter
from tbdaq.adapters.endaq import EndaqAdapter
from tbdaq.isa_export import export_gator_isa, export_endaq_isa, write_export_map, SignalFile

_LOG = logging.getLogger(__name__)

SESSION_ID_FORMAT = "%Y-%m-%dT%H.%M.%S"


class Session:
    def __init__(self, config: SessionConfig) -> None:
        self._config = config
        self._session_id = datetime.now().strftime(SESSION_ID_FORMAT)
        self._session_dir = Path(config.output_root) / self._session_id
        self._session_dir.mkdir(parents=True, exist_ok=True)
        self._manifest: dict = {
            "session_id": self._session_id,
            "session_dir": str(self._session_dir),
            "runs": [],
        }

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self) -> dict:
        cfg = self._config

        # --- Discover endaq once per session (USB device must be connected) ---
        endaq_adapter: Optional[EndaqAdapter] = None
        if cfg.endaq.enabled:
            endaq_adapter = EndaqAdapter(cfg.endaq)
            err = endaq_adapter.discover()
            if err:
                print(f"  Endaq : SKIPPED — {err}")
                endaq_adapter = None
            else:
                print("  Endaq : found")
        else:
            print("  Endaq : disabled in config")

        gator_adapter: Optional[GatorAdapter] = None
        if cfg.gator.enabled:
            gator_adapter = GatorAdapter(cfg.gator)
            print("  Gator : enabled")
        else:
            print("  Gator : disabled in config")

        for run_number in range(1, cfg.run_count + 1):
            run_id = f"run_{run_number:02d}"
            run_dir = self._session_dir / run_id
            run_dir.mkdir(parents=True, exist_ok=True)

            print(f"\n{'='*60}")
            print(f"Session {self._session_id}  |  {run_id} of {cfg.run_count}")
            print(f"{'='*60}")

            run_manifest = self._execute_run(
                run_number=run_number,
                run_id=run_id,
                run_dir=run_dir,
                gator_adapter=gator_adapter,
                endaq_adapter=endaq_adapter,
            )
            self._manifest["runs"].append(run_manifest)
            self._write_manifest()

            # Wait for the next run's start if a period is configured
            if run_number < cfg.run_count and cfg.run_period_s is not None:
                elapsed = run_manifest.get("actual_duration_s", 0) or 0
                wait = max(0.0, cfg.run_period_s - elapsed)
                if wait > 0:
                    print(f"Waiting {wait:.1f}s until next run...")
                    time.sleep(wait)

        print(f"\nSession complete. Output: {self._session_dir}")
        return self._manifest

    # ------------------------------------------------------------------
    # Single run
    # ------------------------------------------------------------------

    def _execute_run(
        self,
        *,
        run_number: int,
        run_id: str,
        run_dir: Path,
        gator_adapter: Optional[GatorAdapter],
        endaq_adapter: Optional[EndaqAdapter],
    ) -> dict:
        cfg = self._config
        run_manifest: dict = {
            "run_id": run_id,
            "run_number": run_number,
            "gator": None,
            "endaq": None,
            "sync": None,
            "status": "failed",
        }

        gator_output = str(run_dir / "gator_channel.csv")
        endaq_raw_dir = str(run_dir / "raw")

        # --- Start both devices as close together as possible ---
        errors: list[str] = []
        gator_start_wall: Optional[float] = None
        endaq_start_wall: Optional[float] = None

        gator_result = None
        endaq_result = None

        def _start_gator():
            nonlocal gator_result, gator_start_wall
            if gator_adapter is None:
                return
            gator_start_wall = time.time()
            gator_result = gator_adapter.start(gator_output)

        def _start_endaq():
            nonlocal endaq_result, endaq_start_wall
            if endaq_adapter is None:
                return
            endaq_start_wall = time.time()
            endaq_result = endaq_adapter.start()

        t_gator = threading.Thread(target=_start_gator, daemon=True)
        t_endaq = threading.Thread(target=_start_endaq, daemon=True)
        t_gator.start()
        t_endaq.start()
        t_gator.join()
        t_endaq.join()

        if gator_result and not gator_result.ok:
            errors.append(f"Gator start: {gator_result.error}")
        if endaq_result and not endaq_result.ok:
            errors.append(f"Endaq start: {endaq_result.error}")

        # Record sync data: wall-clock times at start, and UTC from each device
        run_manifest["sync"] = _build_sync_record(
            gator_start_wall=gator_start_wall,
            gator_start_utc_us=gator_result.start_utc_us if gator_result else None,
            endaq_start_wall=endaq_start_wall,
            endaq_start_utc_us=endaq_result.start_utc_us if endaq_result else None,
        )

        # --- Wait for duration or manual stop ---
        run_start_wall = time.time()

        if cfg.run_duration_s is not None:
            print(f"Recording for {cfg.run_duration_s:.0f} seconds...")
            time.sleep(cfg.run_duration_s)
        else:
            print("Press ENTER to stop recording.")
            try:
                input()
            except (EOFError, KeyboardInterrupt):
                pass

        run_actual_duration = time.time() - run_start_wall

        # --- Stop both ---
        def _stop_gator():
            if gator_adapter is None:
                return
            gator_adapter.stop()

        def _stop_endaq():
            if endaq_adapter is None:
                return
            endaq_adapter.stop(endaq_raw_dir)

        t_sg = threading.Thread(target=_stop_gator, daemon=True)
        t_se = threading.Thread(target=_stop_endaq, daemon=True)
        t_sg.start()
        t_se.start()
        t_sg.join()
        t_se.join()

        # --- Convert endaq IDE to CSV if ide2csv is configured ---
        if endaq_adapter is not None:
            endaq_adapter.convert(str(run_dir))

        # --- ISA export: split each device's output into per-signal time,value CSVs ---
        isa_dir = run_dir / "isa"
        export_map_path = self._session_dir / "isa_export_map.csv"
        isa_signals: list[SignalFile] = []

        if gator_result and gator_result.ok and Path(gator_output).exists():
            gator_signals = export_gator_isa(Path(gator_output), isa_dir / "gator")
            isa_signals.extend(gator_signals)

        endaq_csv_dir = run_dir
        endaq_csvs = list(endaq_csv_dir.glob("DAQ*.csv"))
        if endaq_result and endaq_result.ok and endaq_csvs:
            endaq_signals = export_endaq_isa(endaq_csv_dir, isa_dir / "endaq")
            isa_signals.extend(endaq_signals)

        if isa_signals:
            write_export_map(export_map_path, [
                {
                    "run_id": run_id,
                    "device_family": "gator" if s.alias.startswith("gator") else "endaq",
                    "signal_alias": s.alias,
                    "source_file": s.source_file,
                    "source_column": s.source_column,
                    "isa_path": str(s.path),
                    "time_unit": "s",
                    "status": s.status,
                    "error": s.error or "",
                }
                for s in isa_signals
            ])

        run_manifest["isa"] = {
            "signals": [{"alias": s.alias, "path": str(s.path), "status": s.status}
                        for s in isa_signals],
            "export_map": str(export_map_path) if isa_signals else None,
        }

        run_manifest["actual_duration_s"] = round(run_actual_duration, 3)

        if gator_result:
            run_manifest["gator"] = {
                "output": gator_output,
                "samples_written": gator_result.samples_written,
                "start_utc_us": gator_result.start_utc_us,
                "stop_utc_us": gator_result.stop_utc_us,
                "error": gator_result.error,
            }
            if gator_result.error:
                errors.append(f"Gator: {gator_result.error}")

        if endaq_result:
            run_manifest["endaq"] = {
                "ide_path": endaq_result.ide_path,
                "csv_paths": endaq_result.csv_paths,
                "start_utc_us": endaq_result.start_utc_us,
                "stop_utc_us": endaq_result.stop_utc_us,
                "error": endaq_result.error,
            }
            if endaq_result.error:
                errors.append(f"Endaq: {endaq_result.error}")

        run_manifest["status"] = "failed" if errors else "success"
        run_manifest["errors"] = errors

        _print_run_summary(run_manifest)
        return run_manifest

    # ------------------------------------------------------------------
    # Manifest helpers
    # ------------------------------------------------------------------

    def _write_manifest(self) -> None:
        path = self._session_dir / "session_manifest.json"
        path.write_text(json.dumps(self._manifest, indent=2))


def _build_sync_record(
    gator_start_wall: Optional[float],
    gator_start_utc_us: Optional[int],
    endaq_start_wall: Optional[float],
    endaq_start_utc_us: Optional[int],
) -> dict:
    delta_start_ms: Optional[float] = None
    if gator_start_wall is not None and endaq_start_wall is not None:
        delta_start_ms = round((gator_start_wall - endaq_start_wall) * 1000, 3)

    utc_delta_ms: Optional[float] = None
    if gator_start_utc_us is not None and endaq_start_utc_us is not None:
        utc_delta_ms = round((gator_start_utc_us - endaq_start_utc_us) / 1000, 3)

    return {
        "note": (
            "delta_start_wall_ms: positive = gator started later. "
            "utc_delta_ms: difference in device-reported UTC at start. "
            "Use utc_timestamp_us in gator CSV and endaq timestamps to align in post-processing."
        ),
        "gator_start_wall_s": gator_start_wall,
        "endaq_start_wall_s": endaq_start_wall,
        "delta_start_wall_ms": delta_start_ms,
        "gator_start_utc_us": gator_start_utc_us,
        "endaq_start_utc_us": endaq_start_utc_us,
        "utc_delta_ms": utc_delta_ms,
    }


def _print_run_summary(run: dict) -> None:
    print(f"\nRun {run['run_id']} — {run['status'].upper()}")
    print(f"  Duration: {run.get('actual_duration_s', '?')} s")
    if run.get("gator"):
        g = run["gator"]
        print(f"  Gator:  {g.get('samples_written', '?')} samples  →  {g.get('output', '')}")
    if run.get("endaq"):
        e = run["endaq"]
        print(f"  Endaq:  {e.get('ide_path', 'no IDE')}  |  csv: {e.get('csv_paths', [])}")
    sync = run.get("sync", {})
    if sync.get("delta_start_wall_ms") is not None:
        print(f"  Sync:   start Δ = {sync['delta_start_wall_ms']:+.1f} ms (wall clock)")
    if sync.get("utc_delta_ms") is not None:
        print(f"          UTC Δ  = {sync['utc_delta_ms']:+.1f} ms (device-reported)")
    isa = run.get("isa", {})
    signals = isa.get("signals", [])
    if signals:
        ok = sum(1 for s in signals if s["status"] == "success")
        print(f"  ISA:    {ok}/{len(signals)} signals written → {isa.get('export_map', '')}")
    for err in run.get("errors", []):
        print(f"  ERROR:  {err}")
