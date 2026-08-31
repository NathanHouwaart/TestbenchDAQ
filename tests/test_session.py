from __future__ import annotations

import tempfile
import threading
import unittest
from dataclasses import dataclass
from pathlib import Path

from tbdaq.config import EndaqConfig, GatorConfig, SessionConfig
from tbdaq.isa_export import ExportWindow, SignalFile
from tbdaq.session import Session


@dataclass
class FakeResult:
    error: str | None = None
    conversion_status: str = "success"

    @property
    def ok(self) -> bool:
        return self.error is None


class FakeClock:
    def __init__(self) -> None:
        self.wall = 1_800_000_000.0
        self.mono = 100.0
        self.lock = threading.Lock()

    def time(self) -> float:
        with self.lock:
            return self.wall

    def monotonic(self) -> float:
        with self.lock:
            return self.mono

    def sleep(self, duration: float) -> None:
        with self.lock:
            self.wall += duration
            self.mono += duration


class FakeAdapter:
    def __init__(
        self,
        family: str,
        *,
        discovery_error: str | None = None,
        start_error: str | None = None,
        stop_error: str | None = None,
        needs_stop_after_failed_start: bool = False,
        export_delay_s: float = 0,
        clock: FakeClock | None = None,
    ) -> None:
        self.family = family
        self.discovery_error = discovery_error
        self.start_error = start_error
        self.stop_error = stop_error
        self.needs_stop_after_failed_start = needs_stop_after_failed_start
        self.export_delay_s = export_delay_s
        self.clock = clock
        self.start_calls = 0
        self.stop_calls = 0
        self.result = FakeResult()

    @property
    def needs_stop(self) -> bool:
        return self.needs_stop_after_failed_start and self.result.error is not None

    def discover(self) -> str | None:
        return self.discovery_error

    def start_run(self, run_dir: Path) -> FakeResult:
        del run_dir
        self.start_calls += 1
        self.result = FakeResult(error=self.start_error)
        return self.result

    def stop_run(self, run_dir: Path) -> FakeResult:
        del run_dir
        self.stop_calls += 1
        if self.stop_error:
            self.result.error = self.stop_error
        return self.result

    def export_run(
        self, run_dir: Path, *, window: ExportWindow | None = None
    ) -> list[SignalFile]:
        del window
        if self.export_delay_s and self.clock:
            self.clock.sleep(self.export_delay_s)
        if self.result.error:
            return []
        output = run_dir / "signals" / self.family / f"{self.family}_signal.csv"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("time,value\n0,1\n", encoding="utf-8")
        return [
            SignalFile(
                alias=f"{self.family}_signal",
                path=output,
                source_file="fake",
                source_column="value",
                status="success",
            )
        ]


class SessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.clock = FakeClock()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _session(
        self,
        config: SessionConfig,
        adapters: dict[str, FakeAdapter],
        *,
        session_id: str,
        input_fn=input,
    ) -> Session:
        return Session(
            config,
            adapters=adapters,
            clock=self.clock.time,
            monotonic=self.clock.monotonic,
            sleep=self.clock.sleep,
            input_fn=input_fn,
            session_id=session_id,
        )

    def test_timed_run_uses_common_ready_window_and_succeeds(self) -> None:
        gator = FakeAdapter("gator")
        config = SessionConfig(
            output_root=str(self.root),
            run_duration_s=3,
            gator=GatorConfig(enabled=True),
        )
        manifest = self._session(
            config, {"gator": gator}, session_id="success"
        ).run()
        self.assertEqual(manifest["status"], "success")
        self.assertEqual(manifest["runs"][0]["status"], "success")
        synchronization = manifest["runs"][0]["synchronization"]
        self.assertEqual(synchronization["method"], "common_window_only")
        self.assertFalse(synchronization["sample_level_synchronized"])
        self.assertFalse(synchronization["resampling"])
        self.assertEqual(
            manifest["runs"][0]["measurement_window"]["actual_duration_s"],
            3,
        )
        self.assertEqual(gator.start_calls, 1)
        self.assertEqual(gator.stop_calls, 1)
        self.assertTrue((self.root / "success" / "session_manifest.json").is_file())

    def test_strict_preflight_aborts_before_start(self) -> None:
        gator = FakeAdapter("gator", discovery_error="not connected")
        config = SessionConfig(
            output_root=str(self.root),
            run_duration_s=1,
            gator=GatorConfig(enabled=True),
        )
        manifest = self._session(
            config, {"gator": gator}, session_id="preflight"
        ).run()
        self.assertEqual(manifest["status"], "aborted")
        self.assertEqual(gator.start_calls, 0)

    def test_strict_start_failure_stops_sensor_that_started(self) -> None:
        gator = FakeAdapter("gator")
        endaq = FakeAdapter("endaq", start_error="start failed")
        config = SessionConfig(
            output_root=str(self.root),
            run_duration_s=1,
            gator=GatorConfig(enabled=True),
            endaq=EndaqConfig(enabled=True),
        )
        manifest = self._session(
            config,
            {"gator": gator, "endaq": endaq},
            session_id="start-failure",
        ).run()
        self.assertEqual(manifest["status"], "failed")
        self.assertEqual(gator.stop_calls, 1)
        self.assertEqual(endaq.stop_calls, 0)

    def test_uncertain_failed_start_is_also_cleaned_up(self) -> None:
        endaq = FakeAdapter(
            "endaq",
            start_error="start state uncertain",
            needs_stop_after_failed_start=True,
        )
        config = SessionConfig(
            output_root=str(self.root),
            run_duration_s=1,
            endaq=EndaqConfig(enabled=True),
        )
        manifest = self._session(
            config, {"endaq": endaq}, session_id="uncertain-start"
        ).run()
        self.assertEqual(manifest["status"], "failed")
        self.assertEqual(endaq.stop_calls, 1)

    def test_allow_partial_continues_with_available_sensor(self) -> None:
        gator = FakeAdapter("gator")
        endaq = FakeAdapter("endaq", discovery_error="missing")
        config = SessionConfig(
            output_root=str(self.root),
            run_duration_s=1,
            allow_partial=True,
            gator=GatorConfig(enabled=True),
            endaq=EndaqConfig(enabled=True),
        )
        manifest = self._session(
            config,
            {"gator": gator, "endaq": endaq},
            session_id="partial",
        ).run()
        self.assertEqual(manifest["status"], "partial")
        self.assertEqual(gator.start_calls, 1)
        self.assertEqual(endaq.start_calls, 0)

    def test_manual_interrupt_still_stops_sensor(self) -> None:
        def interrupt(_prompt: str) -> str:
            raise KeyboardInterrupt

        gator = FakeAdapter("gator")
        config = SessionConfig(
            output_root=str(self.root),
            run_duration_s=None,
            gator=GatorConfig(enabled=True),
        )
        manifest = self._session(
            config,
            {"gator": gator},
            session_id="interrupt",
            input_fn=interrupt,
        ).run()
        self.assertEqual(manifest["status"], "interrupted")
        self.assertEqual(gator.stop_calls, 1)

    def test_stop_failure_fails_run(self) -> None:
        gator = FakeAdapter("gator", stop_error="could not stop")
        config = SessionConfig(
            output_root=str(self.root),
            run_duration_s=1,
            gator=GatorConfig(enabled=True),
        )
        manifest = self._session(
            config,
            {"gator": gator},
            session_id="stop-failure",
        ).run()
        self.assertEqual(manifest["status"], "failed")
        self.assertIn(
            "could not stop",
            " ".join(manifest["runs"][0]["errors"]),
        )

    def test_prognostic_schedule_aborts_after_missed_start(self) -> None:
        gator = FakeAdapter("gator")
        normal_export = gator.export_run

        def delayed_export(
            run_dir: Path, *, window: ExportWindow | None = None
        ) -> list[SignalFile]:
            self.clock.sleep(3)
            return normal_export(run_dir, window=window)

        gator.export_run = delayed_export  # type: ignore[method-assign]
        config = SessionConfig(
            mode="prognostic",
            output_root=str(self.root),
            run_count=2,
            run_duration_s=1,
            run_period_s=2,
            missed_start_tolerance_s=0.5,
            missed_start_policy="abort",
            gator=GatorConfig(enabled=True),
        )
        manifest = self._session(
            config,
            {"gator": gator},
            session_id="missed-start",
        ).run()
        self.assertEqual(manifest["status"], "aborted")
        self.assertEqual(manifest["runs"][1]["status"], "aborted")
        self.assertIn("missed its planned start", manifest["abort_reason"])
        self.assertIn("ABORTING SESSION", manifest["abort_reason"])

    def test_start_late_policy_continues_after_cleanup_overrun(self) -> None:
        gator = FakeAdapter("gator")
        normal_export = gator.export_run

        def delayed_export(
            run_dir: Path, *, window: ExportWindow | None = None
        ) -> list[SignalFile]:
            self.clock.sleep(3)
            return normal_export(run_dir, window=window)

        gator.export_run = delayed_export  # type: ignore[method-assign]
        config = SessionConfig(
            mode="prognostic",
            output_root=str(self.root),
            run_count=2,
            run_duration_s=1,
            run_period_s=2,
            missed_start_tolerance_s=0.5,
            missed_start_policy="start_late",
            gator=GatorConfig(enabled=True),
        )
        manifest = self._session(
            config, {"gator": gator}, session_id="start-late"
        ).run()
        self.assertEqual(manifest["status"], "success")
        self.assertEqual(manifest["runs"][1]["status"], "success")
        self.assertIn("Starting late by policy", manifest["runs"][1]["warnings"][0])

    def test_prognostic_endaq_processing_is_deferred_until_after_runs(self) -> None:
        endaq = FakeAdapter(
            "endaq",
            export_delay_s=3,
            clock=self.clock,
        )
        config = SessionConfig(
            mode="prognostic",
            output_root=str(self.root),
            run_count=2,
            run_duration_s=1,
            run_period_s=2,
            missed_start_tolerance_s=0.5,
            endaq=EndaqConfig(enabled=True),
        )
        manifest = self._session(
            config, {"endaq": endaq}, session_id="deferred"
        ).run()
        self.assertEqual(manifest["status"], "success")
        self.assertEqual(len(manifest["runs"]), 2)
        self.assertTrue(
            all(run["processing_status"] == "success" for run in manifest["runs"])
        )

    def test_endaq_conversion_failure_does_not_fail_raw_acquisition(self) -> None:
        endaq = FakeAdapter("endaq")

        def failed_conversion(
            _run_dir: Path, *, window: ExportWindow | None = None
        ) -> list[SignalFile]:
            del window
            endaq.result.conversion_status = "failed"
            return []

        endaq.export_run = failed_conversion  # type: ignore[method-assign]
        config = SessionConfig(
            output_root=str(self.root),
            run_duration_s=1,
            endaq=EndaqConfig(enabled=True),
        )
        manifest = self._session(
            config, {"endaq": endaq}, session_id="conversion-failure"
        ).run()
        self.assertEqual(manifest["status"], "success")
        self.assertEqual(manifest["runs"][0]["status"], "success")
        self.assertEqual(manifest["runs"][0]["processing_status"], "incomplete")


if __name__ == "__main__":
    unittest.main()
