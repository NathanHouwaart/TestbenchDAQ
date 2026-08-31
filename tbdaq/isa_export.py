"""Post-processing: splits raw gator_channel.csv and endaq ide2csv output into
   individual per-signal ISA-format CSVs (columns: time_s, value)."""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Shared result type
# ---------------------------------------------------------------------------

@dataclass
class SignalFile:
    alias: str            # filename stem, used as the ISA signal name
    path: Path            # absolute path to the written CSV
    source_file: str      # relative path of the source file
    source_column: str    # column name inside the source file
    status: str           # "success" | "failed"
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Gator splitter
# ---------------------------------------------------------------------------

_GATOR_SENSOR_COLUMNS = [
    "sensor_1_fm", "sensor_2_fm", "sensor_3_fm", "sensor_4_fm",
    "sensor_5_fm", "sensor_6_fm", "sensor_7_fm", "sensor_8_fm",
]
_GATOR_TIMESTAMP_COLUMN = "utc_timestamp_us"


def export_gator_isa(
    gator_csv: Path,
    isa_dir: Path,
    gator_label: str = "gator",
) -> list[SignalFile]:
    """Split a gator_channel.csv into one time,value CSV per non-zero sensor column.

    Time axis is run-relative seconds (t=0 at first sample).
    Sensor columns that are all-zero (inactive) are skipped.
    """
    isa_dir.mkdir(parents=True, exist_ok=True)
    results: list[SignalFile] = []
    source_rel = str(gator_csv)

    try:
        with gator_csv.open("r", newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            rows = list(reader)
    except Exception as exc:
        return [SignalFile(alias=gator_label, path=isa_dir, source_file=source_rel,
                           source_column="", status="failed", error=str(exc))]

    if not rows:
        return [SignalFile(alias=gator_label, path=isa_dir, source_file=source_rel,
                           source_column="", status="failed", error="Empty gator CSV.")]

    header = list(rows[0].keys())
    if _GATOR_TIMESTAMP_COLUMN not in header:
        return [SignalFile(alias=gator_label, path=isa_dir, source_file=source_rel,
                           source_column="", status="failed",
                           error=f"Column '{_GATOR_TIMESTAMP_COLUMN}' not found.")]

    t0_us = int(rows[0][_GATOR_TIMESTAMP_COLUMN])

    for col in _GATOR_SENSOR_COLUMNS:
        if col not in header:
            continue

        # Skip columns that are all zero (sensor not connected)
        if all(row[col] == "0" for row in rows):
            continue

        alias = f"{gator_label}_{col}"
        out_path = isa_dir / f"{alias}.csv"

        try:
            with out_path.open("w", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                writer.writerow(["time_s", "value_fm"])
                for row in rows:
                    t_s = (int(row[_GATOR_TIMESTAMP_COLUMN]) - t0_us) / 1_000_000
                    writer.writerow([f"{t_s:.6f}", row[col]])

            results.append(SignalFile(
                alias=alias,
                path=out_path,
                source_file=source_rel,
                source_column=col,
                status="success",
            ))
        except Exception as exc:
            results.append(SignalFile(
                alias=alias,
                path=out_path,
                source_file=source_rel,
                source_column=col,
                status="failed",
                error=str(exc),
            ))

    if not results:
        results.append(SignalFile(
            alias=gator_label,
            path=isa_dir,
            source_file=source_rel,
            source_column="",
            status="failed",
            error="No active (non-zero) sensor columns found.",
        ))

    return results


# ---------------------------------------------------------------------------
# Endaq splitter
# ---------------------------------------------------------------------------

_ENDAQ_SOURCE_PATTERN = re.compile(r"_Ch(?P<channel>\d+)_(?P<label>.+)\.csv$", re.IGNORECASE)


def export_endaq_isa(
    endaq_csv_dir: Path,
    isa_dir: Path,
    allowlist: set[str] | None = None,
) -> list[SignalFile]:
    """Split all DAQ*_ChNN_<label>.csv files produced by ide2csv into per-axis ISA CSVs.

    Time axis is run-relative seconds (t=0 at first row).
    Each non-time column becomes its own file: endaq_<label>_<axis>.csv
    """
    isa_dir.mkdir(parents=True, exist_ok=True)
    results: list[SignalFile] = []
    used_aliases: set[str] = set()

    source_files = sorted(endaq_csv_dir.glob("DAQ*.csv"))
    if not source_files:
        return [SignalFile(alias="endaq", path=isa_dir, source_file=str(endaq_csv_dir),
                           source_column="", status="failed",
                           error="No DAQ*.csv files found after ide2csv conversion.")]

    for source_path in source_files:
        match = _ENDAQ_SOURCE_PATTERN.search(source_path.name)
        if not match:
            continue

        channel = int(match.group("channel"))
        label = match.group("label")

        if allowlist and label.strip().lower() not in allowlist:
            continue

        source_rel = str(source_path)

        try:
            with source_path.open("r", newline="", encoding="utf-8") as fh:
                reader = csv.reader(fh, skipinitialspace=True)
                raw_rows = list(reader)
        except Exception as exc:
            results.append(SignalFile(alias=f"endaq_ch{channel:02d}_{_sanitize(label)}",
                                      path=isa_dir, source_file=source_rel,
                                      source_column="", status="failed", error=str(exc)))
            continue

        if not raw_rows:
            continue

        header = [c.strip().strip('"') for c in raw_rows[0]]
        time_index = next((i for i, h in enumerate(header) if h.lower() == "time"), None)
        if time_index is None:
            results.append(SignalFile(alias=f"endaq_ch{channel:02d}_{_sanitize(label)}",
                                      path=isa_dir, source_file=source_rel,
                                      source_column="", status="failed",
                                      error="No 'time' column found."))
            continue

        data_rows = raw_rows[1:]
        if not data_rows:
            continue

        try:
            t0 = float(data_rows[0][time_index])
        except (ValueError, IndexError):
            t0 = 0.0

        for col_index, col_name in enumerate(header):
            if col_index == time_index or not col_name:
                continue

            alias = _unique_alias(used_aliases, f"endaq_{_sanitize(label)}_{_sanitize(col_name)}")
            out_path = isa_dir / f"{alias}.csv"

            try:
                with out_path.open("w", newline="", encoding="utf-8") as fh:
                    writer = csv.writer(fh)
                    writer.writerow(["time_s", col_name])
                    for row in data_rows:
                        try:
                            t_s = float(row[time_index]) - t0
                            val = row[col_index] if col_index < len(row) else ""
                            writer.writerow([f"{t_s:.6f}", val])
                        except (ValueError, IndexError):
                            continue

                results.append(SignalFile(
                    alias=alias,
                    path=out_path,
                    source_file=source_rel,
                    source_column=col_name,
                    status="success",
                ))
            except Exception as exc:
                results.append(SignalFile(
                    alias=alias,
                    path=out_path,
                    source_file=source_rel,
                    source_column=col_name,
                    status="failed",
                    error=str(exc),
                ))

    return results


# ---------------------------------------------------------------------------
# ISA export map writer
# ---------------------------------------------------------------------------

_EXPORT_MAP_COLUMNS = [
    "run_id", "device_family", "signal_alias",
    "source_file", "source_column", "isa_path",
    "time_unit", "status", "error",
]


def write_export_map(export_map_path: Path, rows: list[dict]) -> None:
    export_map_path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not export_map_path.exists()
    with export_map_path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_EXPORT_MAP_COLUMNS, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sanitize(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip())
    return re.sub(r"_+", "_", cleaned).strip("_").lower()


def _unique_alias(used: set[str], base: str) -> str:
    alias = base
    suffix = 2
    while alias in used:
        alias = f"{base}_{suffix}"
        suffix += 1
    used.add(alias)
    return alias
