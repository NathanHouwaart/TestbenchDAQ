"""Derive per-acquisition-window PLC CSVs from continuous profile logging."""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Any


def slice_plc_by_acquisition_window(session_dir: Path, manifest: dict[str, Any]) -> list[dict[str, str]]:
    """Copy profile-time rows into each window using approximate host-time alignment."""
    metadata_path = session_dir / "gui" / "run-metadata.json"
    plc_dir = session_dir / "plc"
    if not metadata_path.is_file() or not plc_dir.is_dir():
        return []
    metadata = _read_json(metadata_path)
    profile_start = metadata.get("profile_started_at_utc")
    if not isinstance(profile_start, str):
        return []
    profile_start_s = _timestamp(profile_start)
    derived: list[dict[str, str]] = []
    for run in manifest.get("runs", []):
        window = run.get("measurement_window", {})
        start = window.get("start_utc")
        stop = window.get("stop_requested_utc")
        if not isinstance(start, str) or not isinstance(stop, str):
            continue
        destination = session_dir / str(run["run_id"]) / "plc"
        destination.mkdir(parents=True, exist_ok=True)
        for source in sorted(plc_dir.glob("*.csv")):
            target = destination / source.name
            _slice_csv(source, target, _timestamp(start) - profile_start_s, _timestamp(stop) - profile_start_s)
            derived.append({
                "window_id": str(run["run_id"]),
                "path": target.relative_to(session_dir).as_posix(),
                "derived_from": source.relative_to(session_dir).as_posix(),
            })
    return derived


def _slice_csv(source: Path, target: Path, start_s: float, stop_s: float) -> None:
    with source.open(newline="", encoding="utf-8") as source_file, target.open("w", newline="", encoding="utf-8") as target_file:
        reader = csv.DictReader(source_file)
        if not reader.fieldnames or "Time" not in reader.fieldnames:
            raise ValueError(f"PLC CSV requires a Time column: {source}")
        writer = csv.DictWriter(target_file, fieldnames=reader.fieldnames)
        writer.writeheader()
        for row in reader:
            try:
                time_s = float(row["Time"])
            except (TypeError, ValueError):
                continue
            if start_s <= time_s <= stop_s:
                writer.writerow(row)


def _timestamp(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def _read_json(path: Path) -> dict[str, Any]:
    import json
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected object: {path}")
    return value
