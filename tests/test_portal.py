from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from portal.api import app as portal_app
from tbdaq.portal import PortalError, SessionIndex


class PortalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.session = self.root / "wentelteef" / "session-1"
        self.session.mkdir(parents=True)
        (self.session / "session_manifest.json").write_text(json.dumps({
            "session_id": "session-1", "status": "running",
            "started_at_utc": "2026-09-22T10:00:00Z",
            "live_status": {"phase": "measuring", "current_run": 2}, "runs": [],
        }), encoding="utf-8")
        (self.session / "session.log").write_text("hello", encoding="utf-8")
        signals = self.session / "run_01" / "signals" / "gator"
        signals.mkdir(parents=True)
        (signals / "signal.csv").write_text("time_s,value_fm\n0,1\n1,3\n2,5\n", encoding="utf-8")
        self.index = SessionIndex(self.root)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_machine_summary_exposes_live_state(self) -> None:
        summary = self.index.machine_summary("wentelteef")
        self.assertEqual(summary["active_session"]["live_status"]["phase"], "measuring")

    def test_overview_and_chart_envelope(self) -> None:
        overview = self.index.overview()
        self.assertEqual(overview["machines"][0]["machine"], "wentelteef")
        chart = self.index.chart("wentelteef", "session-1", "run_01/signals/gator/signal.csv", ["value_fm"], None, None, 100)
        self.assertEqual(chart["available_columns"], ["value_fm"])
        self.assertEqual(len(chart["series"][0]["points"]), 3)

    def test_artifact_cannot_escape_session(self) -> None:
        with self.assertRaises(PortalError):
            self.index.artifact("wentelteef", "session-1", "../../outside")

    def test_artifacts_are_relative_to_the_session(self) -> None:
        self.assertEqual(self.index.artifacts("wentelteef", "session-1"), [
            {"path": "run_01/signals/gator/signal.csv", "size_bytes": 32},
            {"path": "session.log", "size_bytes": 5},
            {"path": "session_manifest.json", "size_bytes": (self.session / "session_manifest.json").stat().st_size},
        ])

    def test_http_api_is_read_only_and_rejects_traversal(self) -> None:
        previous_index = portal_app.index
        portal_app.index = self.index
        try:
            client = TestClient(portal_app.app)
            self.assertEqual(client.get("/api/machines/wentelteef/sessions").status_code, 200)
            self.assertEqual(client.post("/api/machines/wentelteef").status_code, 405)
            response = client.get(
                "/api/machines/wentelteef/sessions/session-1/artifacts/%2E%2E%2Foutside"
            )
            self.assertEqual(response.status_code, 404)
        finally:
            portal_app.index = previous_index

    def test_plot_samples_csv_and_session_download_is_zip(self) -> None:
        previous_index = portal_app.index
        portal_app.index = self.index
        try:
            client = TestClient(portal_app.app)
            plot = client.get("/api/machines/wentelteef/sessions/session-1/plot/run_01/signals/gator/signal.csv")
            self.assertEqual(plot.status_code, 200)
            self.assertEqual(plot.json()["points"], [[0.0, 1.0], [1.0, 3.0], [2.0, 5.0]])
            archive = client.get("/api/machines/wentelteef/sessions/session-1/download")
            self.assertEqual(archive.status_code, 200)
            self.assertTrue(archive.content.startswith(b"PK"))
        finally:
            portal_app.index = previous_index
