from __future__ import annotations

import logging
import json
import tempfile
import unittest
from pathlib import Path

from tbdaq.__main__ import _ConsoleFormatter, _build_config, _make_parser, _manifest_messages


class CliTests(unittest.TestCase):
    def test_manifest_messages_expose_nested_run_failure(self) -> None:
        errors, warnings = _manifest_messages({
            "abort_reason": "run_01 ended with status failed.",
            "runs": [{
                "errors": ["gator stop failed: timed out"],
                "warnings": ["raw acquisition retained"],
            }],
        })
        self.assertEqual(errors, ["gator stop failed: timed out"])
        self.assertEqual(warnings, ["raw acquisition retained"])

    def test_manifest_messages_preserve_abort_reason_without_run_error(self) -> None:
        errors, _warnings = _manifest_messages({
            "abort_reason": "Required sensor preflight failed: gator.",
            "runs": [],
        })
        self.assertEqual(errors, ["Required sensor preflight failed: gator."])

    def test_console_formatter_colors_warning_and_error(self) -> None:
        formatter = _ConsoleFormatter(use_color=True)
        warning = logging.LogRecord("test", logging.WARNING, "", 0, "careful", (), None)
        error = logging.LogRecord("test", logging.ERROR, "", 0, "broken", (), None)
        self.assertTrue(formatter.format(warning).startswith("\033[33m"))
        self.assertTrue(formatter.format(error).startswith("\033[31m"))

    def test_machine_and_acquisition_are_required_together(self) -> None:
        args = _make_parser().parse_args(["--machine", "machine.json"])
        with self.assertRaisesRegex(Exception, "provided together"):
            _build_config(args)

    def test_machine_and_acquisition_build_a_session_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "machine.json").write_text(json.dumps({
                "schema": "testbenchdaq/machine/v1",
                "machine_name": "bench",
                "output_root": "/tmp/results",
            }), encoding="utf-8")
            (root / "acquisition.json").write_text(json.dumps({
                "schema": "testbenchdaq/acquisition/v1",
                "name": "test",
                "schedule": {"window_duration_s": 1, "run_count": 1},
                "gator": {"enabled": True, "channel": 1, "sample_rate_hz": 1000},
                "endaq": {"enabled": False, "channels": []},
            }), encoding="utf-8")
            args = _make_parser().parse_args([
                "--machine", str(root / "machine.json"),
                "--acquisition", str(root / "acquisition.json"),
            ])
            config = _build_config(args)
        self.assertEqual(config.machine_name, "bench")
        self.assertEqual(config.run_duration_s, 1)
        self.assertTrue(config.gator.enabled)

    def test_local_output_override_is_explicit(self) -> None:
        args = _make_parser().parse_args(["--allow-local-output"])
        self.assertTrue(args.allow_local_output)


if __name__ == "__main__":
    unittest.main()
