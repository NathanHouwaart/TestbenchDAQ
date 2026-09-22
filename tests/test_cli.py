from __future__ import annotations

import logging
import unittest

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

    def test_run_until_stopped_sets_an_unlimited_count(self) -> None:
        args = _make_parser().parse_args([
            "--mode", "prognostic", "--run-until-stopped",
            "--run-duration-s", "1", "--run-period-s", "2", "--gator",
        ])
        self.assertIsNone(_build_config(args).run_count)

    def test_local_output_override_is_explicit(self) -> None:
        args = _make_parser().parse_args(["--allow-local-output", "--gator"])
        self.assertTrue(args.allow_local_output)


if __name__ == "__main__":
    unittest.main()
