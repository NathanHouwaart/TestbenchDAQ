from __future__ import annotations

import json

from tbdaq.plc_slicing import slice_plc_by_acquisition_window


def test_slices_continuous_plc_csv_using_profile_start_time(tmp_path):
    (tmp_path / "gui").mkdir()
    (tmp_path / "plc").mkdir()
    (tmp_path / "window_001").mkdir()
    (tmp_path / "gui" / "run-metadata.json").write_text(json.dumps({
        "profile_started_at_utc": "2026-10-22T11:00:00Z",
    }), encoding="utf-8")
    (tmp_path / "plc" / "Axial Load.csv").write_text(
        "Time,Axial Load\n0.0,1\n4.9,2\n5.0,3\n7.5,4\n10.0,5\n",
        encoding="utf-8",
    )
    manifest = {"runs": [{
        "run_id": "window_001",
        "measurement_window": {
            "start_utc": "2026-10-22T11:00:05Z",
            "stop_requested_utc": "2026-10-22T11:00:08Z",
        },
    }]}

    derived = slice_plc_by_acquisition_window(tmp_path, manifest)

    assert derived == [{
        "window_id": "window_001",
        "path": "window_001/plc/Axial Load.csv",
        "derived_from": "plc/Axial Load.csv",
    }]
    assert (tmp_path / "window_001" / "plc" / "Axial Load.csv").read_text() == (
        "Time,Axial Load\n5.0,3\n7.5,4\n"
    )
