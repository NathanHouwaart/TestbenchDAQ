from __future__ import annotations

import json
from pathlib import Path

from tbdaq.isa_export import SignalFile
from tbdaq.processor import ProcessingWorker, write_processing_request


def test_server_processing_publishes_relative_acquisition_contract(tmp_path, monkeypatch):
    session_dir = tmp_path / "machine-a" / "session-1"
    raw = session_dir / "window_001" / "raw" / "gator" / "gator_channel.csv"
    raw.parent.mkdir(parents=True)
    raw.write_text("raw", encoding="utf-8")
    signal_path = session_dir / "window_001" / "signals" / "gator" / "gator_sensor_1_fm.csv"
    signal_path.parent.mkdir(parents=True)
    signal_path.write_text("time_s,value\n0,1\n", encoding="utf-8")
    (session_dir / "session_manifest.json").write_text(json.dumps({
        "session_id": "session-1",
        "runs": [{
            "run_id": "window_001",
            "orchestrator_start_utc": "2026-10-22T11:00:00Z",
            "measurement_window": {
                "start_utc": "2026-10-22T11:00:00Z",
                "stop_requested_utc": "2026-10-22T11:00:30Z",
            },
        }],
    }), encoding="utf-8")
    write_processing_request(session_dir)

    monkeypatch.setattr("tbdaq.processor.export_gator_signals", lambda *_args: [SignalFile(
        alias="gator_sensor_1_fm", path=signal_path, source_file=str(raw),
        source_column="sensor_1_fm", status="success",
    )])

    assert ProcessingWorker(tmp_path).process_once() is True

    public_manifest = json.loads((session_dir / "acquisition-session.json").read_text())
    window = public_manifest["acquisition_windows"][0]
    assert window["acquisition_window_id"] == "window_001"
    assert window["files"][0]["path"] == "window_001/signals/gator/gator_sensor_1_fm.csv"
    assert not Path(window["files"][0]["path"]).is_absolute()
    assert (session_dir / "acquisition_window_map.csv").read_text().startswith(
        "acquisition_window_id,"
    )
