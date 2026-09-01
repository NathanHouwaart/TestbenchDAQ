"""Interactive visualization of TestbenchDAQ signal CSV output."""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import numpy as np


@dataclass(frozen=True)
class SignalSeries:
    alias: str
    family: str
    value_label: str
    path: Path
    time_s: np.ndarray
    values: np.ndarray
    original_count: int


def _downsample_envelope(
    time_s: np.ndarray,
    values: np.ndarray,
    max_points: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Retain ordered minima/maxima so display decimation preserves peaks."""
    count = len(time_s)
    if count <= max_points:
        return time_s, values

    bucket_count = max(1, max_points // 2)
    edges = np.linspace(0, count, bucket_count + 1, dtype=int)
    selected: list[int] = []
    for start, stop in zip(edges[:-1], edges[1:]):
        if stop <= start:
            continue
        bucket = values[start:stop]
        finite = np.flatnonzero(np.isfinite(bucket))
        if finite.size == 0:
            selected.append(start)
            continue
        finite_values = bucket[finite]
        low = start + int(finite[int(np.argmin(finite_values))])
        high = start + int(finite[int(np.argmax(finite_values))])
        selected.extend(sorted({low, high}))

    indices = np.asarray(selected, dtype=int)
    return time_s[indices], values[indices]


def _group_name(alias: str, family: str) -> str:
    if family == "gator":
        return "Gator FBG sensors"
    grouped_prefixes = (
        ("endaq_100g_pe_acceleration_", "enDAQ 100g PE acceleration"),
        ("endaq_40g_dc_acceleration_", "enDAQ 40g DC acceleration"),
        ("endaq_rotation_", "enDAQ rotation"),
        ("endaq_relative_orientation_", "enDAQ relative orientation"),
    )
    for prefix, title in grouped_prefixes:
        if alias.startswith(prefix):
            return title
    return alias.replace("_", " ")


def _read_signal(
    path: Path,
    *,
    alias: str,
    family: str,
    max_points: int,
    gator_zero_as_gap: bool,
) -> SignalSeries:
    with path.open(encoding="utf-8") as handle:
        header = next(handle).strip()
    columns = [part.strip().strip('"') for part in header.split(",")]
    value_label = columns[1] if len(columns) > 1 else "value"

    # A recorder interrupted between timestamp and value fields can leave an
    # empty final cell. Keep its timestamp and represent the value as a gap.
    data = np.genfromtxt(
        path,
        delimiter=",",
        skip_header=1,
        usecols=(0, 1),
        filling_values=np.nan,
        ndmin=2,
    )
    time_s = data[:, 0]
    values = data[:, 1]
    original_count = len(time_s)
    if gator_zero_as_gap and family == "gator":
        values = values.copy()
        values[values == 0] = np.nan
    time_s, values = _downsample_envelope(time_s, values, max_points)
    return SignalSeries(
        alias=alias,
        family=family,
        value_label=value_label,
        path=path,
        time_s=time_s,
        values=values,
        original_count=original_count,
    )


def _manifest_signals(
    session_dir: Path,
    run_id: str,
) -> list[dict[str, Any]]:
    manifest_path = session_dir / "session_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not read {manifest_path}: {exc}") from exc

    run = next(
        (item for item in manifest.get("runs", []) if item.get("run_id") == run_id),
        None,
    )
    if run is None:
        raise RuntimeError(f"Run {run_id!r} is not present in {manifest_path}.")

    signals: list[dict[str, Any]] = []
    for item in run.get("signals", []):
        if item.get("status") != "success":
            continue
        alias = str(item.get("alias", ""))
        path = Path(str(item.get("path", "")))
        if not path.is_file():
            family = "gator" if alias.startswith("gator_") else "endaq"
            candidates = list((session_dir / run_id / "signals" / family).rglob(f"{alias}.csv"))
            if not candidates:
                continue
            path = candidates[0]
        signals.append({
            "alias": alias,
            "family": "gator" if alias.startswith("gator_") else "endaq",
            "path": path,
        })
    return signals


def _run_ids(session_dir: Path) -> list[str]:
    manifest = json.loads(
        (session_dir / "session_manifest.json").read_text(encoding="utf-8")
    )
    return [
        str(run["run_id"])
        for run in manifest.get("runs", [])
        if run.get("signals")
    ]


def plot_run(
    session_dir: Path,
    run_id: str,
    output_path: Path,
    *,
    max_points: int = 5_000,
    gator_zero_as_gap: bool = False,
) -> tuple[int, int]:
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
    except ImportError as exc:
        raise RuntimeError(
            "Plotly is required. Install it with: .venv/bin/pip install plotly"
        ) from exc

    descriptions = _manifest_signals(session_dir, run_id)
    if not descriptions:
        raise RuntimeError(f"No successful signal CSVs found for {run_id}.")

    series = [
        _read_signal(
            item["path"],
            alias=item["alias"],
            family=item["family"],
            max_points=max_points,
            gator_zero_as_gap=gator_zero_as_gap,
        )
        for item in descriptions
    ]

    groups: dict[str, list[SignalSeries]] = {}
    for signal in series:
        groups.setdefault(_group_name(signal.alias, signal.family), []).append(signal)

    titles = list(groups)
    figure = make_subplots(
        rows=len(titles),
        cols=1,
        shared_xaxes=True,
        subplot_titles=titles,
        vertical_spacing=min(0.025, 0.25 / len(titles)),
    )
    for row, title in enumerate(titles, start=1):
        for signal in groups[title]:
            trace_name = signal.alias.removeprefix("gator_").removeprefix("endaq_")
            figure.add_trace(
                go.Scattergl(
                    x=signal.time_s,
                    y=signal.values,
                    mode="lines",
                    name=trace_name,
                    legendgroup=title,
                    line={"width": 1},
                    connectgaps=False,
                    customdata=np.full(len(signal.time_s), signal.original_count),
                    hovertemplate=(
                        "t=%{x:.6f} s<br>value=%{y:.9g}"
                        "<br>source samples=%{customdata}<extra>%{fullData.name}</extra>"
                    ),
                ),
                row=row,
                col=1,
            )
        labels = {signal.value_label for signal in groups[title]}
        if len(labels) == 1:
            figure.update_yaxes(title_text=next(iter(labels)), row=row, col=1)

    figure.update_xaxes(title_text="Run-relative time (s)", row=len(titles), col=1)
    figure.update_layout(
        title=f"TestbenchDAQ — {session_dir.name} — {run_id}",
        height=max(650, 250 * len(titles)),
        template="plotly_white",
        hovermode="x unified",
        margin={"l": 90, "r": 30, "t": 90, "b": 60},
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.write_html(
        output_path,
        include_plotlyjs=True,
        full_html=True,
        auto_open=False,
    )
    return len(series), sum(signal.original_count for signal in series)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create offline interactive plots from a TestbenchDAQ session."
    )
    parser.add_argument("session_dir", type=Path, help="Session output directory")
    parser.add_argument(
        "--run",
        dest="run_ids",
        action="append",
        help="Run ID to plot; repeat for multiple runs (default: every completed run)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Output directory (default: SESSION_DIR/plots)",
    )
    parser.add_argument(
        "--max-points",
        type=int,
        default=5_000,
        help="Maximum displayed points per signal; extrema are preserved",
    )
    parser.add_argument(
        "--gator-zero-as-gap",
        action="store_true",
        help="Display Gator zero/missing detections as gaps instead of zero",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    session_dir = args.session_dir.expanduser().resolve()
    if args.max_points < 100:
        raise SystemExit("--max-points must be at least 100")
    if not (session_dir / "session_manifest.json").is_file():
        raise SystemExit(f"Not a TestbenchDAQ session: {session_dir}")

    run_ids = args.run_ids or _run_ids(session_dir)
    if not run_ids:
        raise SystemExit("No runs with signal output were found.")
    output_dir = (
        args.output_dir.expanduser().resolve()
        if args.output_dir
        else session_dir / "plots"
    )

    for run_id in run_ids:
        output_path = output_dir / f"{run_id}_overview.html"
        try:
            signal_count, sample_count = plot_run(
                session_dir,
                run_id,
                output_path,
                max_points=args.max_points,
                gator_zero_as_gap=args.gator_zero_as_gap,
            )
        except Exception as exc:
            raise SystemExit(f"Could not plot {run_id}: {exc}") from exc
        print(
            f"{run_id}: plotted {signal_count} signals from "
            f"{sample_count:,} source samples -> {output_path}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
