"""Split raw device output into interim one-signal CSV files."""
from __future__ import annotations

import csv
from contextlib import ExitStack
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Shared result type
# ---------------------------------------------------------------------------

@dataclass
class SignalFile:
    alias: str            # filename stem, used as the signal name
    path: Path            # absolute path to the written CSV
    source_file: str      # relative path of the source file
    source_column: str    # column name inside the source file
    status: str           # "success" | "failed"
    error: Optional[str] = None
    sample_count: Optional[int] = None
    first_time_s: Optional[float] = None
    last_time_s: Optional[float] = None
    alignment_method: Optional[str] = None


@dataclass(frozen=True)
class ExportWindow:
    """Host-defined interval retained in processed signal exports."""

    start_utc_s: float
    stop_utc_s: float

    @property
    def duration_s(self) -> float:
        return max(0.0, self.stop_utc_s - self.start_utc_s)


# ---------------------------------------------------------------------------
# Gator splitter
# ---------------------------------------------------------------------------

_GATOR_SENSOR_COLUMNS = [
    "sensor_1_fm", "sensor_2_fm", "sensor_3_fm", "sensor_4_fm",
    "sensor_5_fm", "sensor_6_fm", "sensor_7_fm", "sensor_8_fm",
]
_GATOR_TIMESTAMP_COLUMN = "utc_timestamp_us"


def export_gator_signals(
    gator_csv: Path,
    isa_dir: Path,
    gator_label: str = "gator",
    *,
    window: ExportWindow | None = None,
) -> list[SignalFile]:
    """Split a gator_channel.csv into one time,value CSV per sensor column.

    Time axis is run-relative seconds (t=0 at first sample).
    All eight columns are exported, including all-zero channels, so every run
    has a deterministic signal schema. Zero values remain useful evidence that
    a sensor was absent or not detected during that run.
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

    try:
        t0_us = int(rows[0][_GATOR_TIMESTAMP_COLUMN])
        timed_rows = [
            ((int(row[_GATOR_TIMESTAMP_COLUMN]) - t0_us) / 1_000_000, row)
            for row in rows
        ]
    except (KeyError, TypeError, ValueError) as exc:
        return [SignalFile(alias=gator_label, path=isa_dir, source_file=source_rel,
                           source_column="", status="failed",
                           error=f"Invalid Gator timestamp: {exc}")]

    if window is not None:
        timed_rows = [item for item in timed_rows if 0.0 <= item[0] <= window.duration_s]
    if not timed_rows:
        return [SignalFile(alias=gator_label, path=isa_dir, source_file=source_rel,
                           source_column="", status="failed",
                           error="No Gator samples overlap the measurement window.")]

    first_time_s = timed_rows[0][0]
    last_time_s = timed_rows[-1][0]

    for col in _GATOR_SENSOR_COLUMNS:
        if col not in header:
            continue

        alias = f"{gator_label}_{col}"
        out_path = isa_dir / f"{alias}.csv"

        try:
            with out_path.open("w", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                writer.writerow(["time_s", "value_fm"])
                for t_s, row in timed_rows:
                    writer.writerow([f"{t_s:.6f}", row[col]])

            results.append(SignalFile(
                alias=alias,
                path=out_path,
                source_file=source_rel,
                source_column=col,
                status="success",
                sample_count=len(timed_rows),
                first_time_s=first_time_s,
                last_time_s=last_time_s,
                alignment_method="first_gator_sample_relative_window",
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
            error="No Gator sensor columns found.",
        ))

    return results


# ---------------------------------------------------------------------------
# Endaq splitter
# ---------------------------------------------------------------------------

_ENDAQ_SOURCE_PATTERN = re.compile(r"_Ch(?P<channel>\d+)_(?P<label>.+)\.csv$", re.IGNORECASE)


def export_endaq_signals(
    endaq_csv_dir: Path,
    isa_dir: Path,
    allowlist: set[str] | None = None,
) -> list[SignalFile]:
    """Split DAQ channel CSV files into individual per-axis signal CSVs.

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


def export_endaq_ide_signals(
    ide_path: Path,
    signal_dir: Path,
    *,
    chunk_size: int = 65_536,
    window: ExportWindow | None = None,
) -> list[SignalFile]:
    """Parse one IDE once and write final per-signal CSVs directly.

    This avoids the former IDE -> combined channel CSV -> per-signal CSV
    pipeline, which formatted and read the same high-rate data twice. Final
    CSV formatting is chunked to avoid another full-size array allocation.
    All channels in an IDE share the recorder's first captured sample as
    ``t=0``. ``window`` is accepted for API compatibility with the other
    adapters, but deliberately does not filter IDE events: the IDE session
    UTC origin has proved not stable across repeated recordings, so using it
    to translate a host window can silently discard valid samples.

    This makes an enDAQ export run-relative and complete. The manifest still
    records the host orchestration window; it must not be interpreted as
    sample-level clock alignment with enDAQ.
    """
    try:
        import numpy as np
        from idelib import importer
    except Exception as exc:
        return [SignalFile(
            alias="endaq",
            path=signal_dir,
            source_file=str(ide_path),
            source_column="",
            status="failed",
            error=f"Could not import idelib/numpy: {exc}",
        )]

    signal_dir.mkdir(parents=True, exist_ok=True)
    results: list[SignalFile] = []
    used_aliases: set[str] = set()
    document = None
    try:
        document = importer.openFile(str(ide_path))
        channel_ids = [
            channel.id
            for channel in document.channels.values()
            if any(subchannel.visibility < 10 for subchannel in channel.subchannels)
        ]
        loaded_ids = set(channel_ids)
        if 8 in loaded_ids:
            # Channel 8 calibration may depend on temperature channels.
            loaded_ids.update((20, 36))
        importer.readData(document, channels=sorted(loaded_ids))

        del window

        # IDE event timestamps share one recorder clock. Establish a single
        # run-relative origin across all channels rather than relying on the
        # document's session UTC timestamp, which can drift between recording
        # cycles relative to the host clock.
        channel_events = []
        for channel_id in channel_ids:
            channel = document.channels.get(channel_id)
            if channel is None:
                continue
            events = channel.getSession()
            if len(events) > 0:
                channel_events.append((channel_id, events))
        if not channel_events:
            return [SignalFile(
                alias="endaq",
                path=signal_dir,
                source_file=str(ide_path),
                source_column="",
                status="failed",
                error="IDE contains no visible channel samples.",
            )]
        recording_start_us = min(float(events[0][0]) for _, events in channel_events)

        for channel_id, events in channel_events:
            channel = document.channels.get(channel_id)
            if channel is None:
                continue
            channel_label = _sanitize(channel.displayName or channel.name)
            channel_results: list[SignalFile] = []
            try:
                with ExitStack() as stack:
                    handles = []
                    for subchannel in channel.subchannels:
                        alias = _unique_alias(
                            used_aliases,
                            f"endaq_{channel_label}_{_sanitize(subchannel.name)}",
                        )
                        output_path = signal_dir / f"{alias}.csv"
                        handle = stack.enter_context(output_path.open("w", encoding="utf-8"))
                        value_label = subchannel.name.replace("\n", " ")
                        handle.write(f'time_s,"{value_label}"\n')
                        handles.append(handle)
                        channel_results.append(SignalFile(
                            alias=alias,
                            path=output_path,
                            source_file=str(ide_path),
                            source_column=subchannel.name,
                            status="success",
                            sample_count=0,
                            alignment_method="ide_recording_first_sample_relative",
                        ))

                    for start in range(0, len(events), chunk_size):
                        values = events.arraySlice(start, min(start + chunk_size, len(events)))
                        relative_s = (values[0] - recording_start_us) / 1_000_000.0
                        for index, handle in enumerate(handles, start=1):
                            np.savetxt(
                                handle,
                                np.column_stack((relative_s, values[index])),
                                delimiter=",",
                                fmt=("%.6f", "%.9g"),
                            )
                        for result in channel_results:
                            result.sample_count = (result.sample_count or 0) + len(relative_s)
                            if result.first_time_s is None:
                                result.first_time_s = float(relative_s[0])
                            result.last_time_s = float(relative_s[-1])
                results.extend(channel_results)
            except Exception as exc:
                results.extend(
                    SignalFile(
                        alias=result.alias,
                        path=result.path,
                        source_file=result.source_file,
                        source_column=result.source_column,
                        status="failed",
                        error=str(exc),
                    )
                    for result in channel_results
                )
    except Exception as exc:
        return [SignalFile(
            alias="endaq",
            path=signal_dir,
            source_file=str(ide_path),
            source_column="",
            status="failed",
            error=str(exc),
        )]
    finally:
        if document is not None:
            document.close()
    return results


# ---------------------------------------------------------------------------
# Signal export map writer
# ---------------------------------------------------------------------------

_EXPORT_MAP_COLUMNS = [
    "run_id", "device_family", "signal_alias",
    "source_file", "source_column", "signal_path",
    "time_unit", "sample_count", "first_time_s", "last_time_s",
    "alignment_method", "status", "error",
]


def write_signal_map(export_map_path: Path, rows: list[dict]) -> None:
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
