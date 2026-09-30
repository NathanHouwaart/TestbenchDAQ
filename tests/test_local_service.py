from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from tbdaq.local_service import (
    LocalServiceController,
    RunRequest,
    ServiceError,
    merge_run_request,
)


class FakeSession:
    def __init__(self, config, *, session_id, allow_local_output):
        del allow_local_output
        self.session_dir = Path(config.output_root) / session_id
        self.session_dir.mkdir(parents=True)
        self.stop_requested = threading.Event()
        self.complete_requested = threading.Event()
        self.recording = threading.Event()

    def run(self):
        self._write("measuring")
        self.recording.set()
        self.stop_requested.wait(timeout=2)
        status = "success" if self.complete_requested.is_set() else "interrupted" if self.stop_requested.is_set() else "success"
        self._write(status)
        return {
            "status": status,
            "abort_reason": "Stop requested by controller." if status == "interrupted" else None,
            "live_status": {"phase": status, "current_run": None},
        }

    def request_stop(self):
        self.stop_requested.set()

    def request_complete(self):
        self.complete_requested.set()
        self.stop_requested.set()

    def _write(self, phase: str):
        (self.session_dir / "session_manifest.json").write_text(
            json.dumps({"live_status": {"phase": phase, "current_run": 1}}),
            encoding="utf-8",
        )


class LocalServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.config_path = self.root / "machine.json"
        self.config_path.write_text(json.dumps({
            "schema": "testbenchdaq/machine/v1",
            "output_root": str(self.root / "data"),
            "gator": {
                "binary_path": "/protected/gator_recorder",
                "device_index": 3,
                "start_timeout_s": 30,
                "stop_timeout_s": 10,
            },
            "endaq": {
                "serial": "protected-serial",
                "delete_after_verified_offload": False,
            },
        }), encoding="utf-8")

    def tearDown(self):
        self.temp_dir.cleanup()

    def _request(self):
        return RunRequest.from_mapping({
            "session_id": "bearing-baseline__2026-09-28T10-15-22Z-a3f8",
            "acquisition": {
                "schema": "testbenchdaq/acquisition/v1",
                "name": "bearing-baseline",
                "schedule": {"window_duration_s": 10, "run_count": 1},
                "gator": {
                    "enabled": True, "channel": 8,
                    "sample_rate_hz": 1000, "full_scale": 8,
                },
                "endaq": {"enabled": False, "channels": []},
            },
        })

    def test_request_merges_only_operator_safe_settings(self):
        values = json.loads(self.config_path.read_text(encoding="utf-8"))
        config = merge_run_request(values, self._request(), base_dir=self.root)

        self.assertEqual(config.name, "bearing-baseline")
        self.assertEqual(config.gator.channel, 8)
        self.assertEqual(config.gator.samplerate, 1000)
        self.assertEqual(config.gator.fullscale, 8)
        self.assertTrue(
            config.gator.binary_path.replace("\\", "/").endswith(
                "/protected/gator_recorder"
            )
        )
        self.assertEqual(config.gator.device_index, 3)
        self.assertEqual(config.endaq.serial, "protected-serial")

    def test_unknown_protected_setting_is_rejected(self):
        request = self._request()
        request.acquisition["gator"]["binary_path"] = "/not/allowed"

        with self.assertRaisesRegex(ServiceError, "Unknown acquisition.gator"):
            merge_run_request(
                json.loads(self.config_path.read_text(encoding="utf-8")),
                request,
                base_dir=self.root,
            )

    def test_controller_reports_recording_then_safely_finalises(self):
        sessions: list[FakeSession] = []

        def factory(*args, **kwargs):
            session = FakeSession(*args, **kwargs)
            sessions.append(session)
            return session

        controller = LocalServiceController(
            self.config_path, allow_local_output=True, session_factory=factory,
        )
        start = controller.start(self._request())
        self.assertEqual(start["state"], "starting")
        self.assertTrue(sessions[0].recording.wait(timeout=1))
        self.assertEqual(controller.status()["run"]["state"], "recording")

        controller.finalise()
        deadline = time.time() + 1
        while controller.status()["run"]["state"] == "recording" and time.time() < deadline:
            time.sleep(0.01)

        self.assertTrue(sessions[0].stop_requested.is_set())
        self.assertEqual(controller.status()["run"]["state"], "aborted")

    def test_controller_rejects_a_second_active_run(self):
        controller = LocalServiceController(
            self.config_path, allow_local_output=True, session_factory=FakeSession,
        )
        controller.start(self._request())
        with self.assertRaisesRegex(ServiceError, "busy"):
            controller.start(self._request())
        controller.finalise()

    def test_controller_complete_is_not_reported_as_an_abort(self):
        controller = LocalServiceController(
            self.config_path, allow_local_output=True, session_factory=FakeSession,
        )
        controller.start(self._request())
        deadline = time.time() + 1
        while controller.status()["run"]["state"] != "recording" and time.time() < deadline:
            time.sleep(0.01)
        controller.complete()
        deadline = time.time() + 1
        while controller.status()["run"]["state"] == "recording" and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual(controller.status()["run"]["state"], "completed")
