from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tbdaq.config import ConfigError, session_config_from_mapping


class ConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_at_least_one_sensor_is_required(self) -> None:
        with self.assertRaisesRegex(ConfigError, "Enable at least one"):
            session_config_from_mapping({}, base_dir=self.base)

    def test_diagnostic_supports_manual_or_timed_single_run(self) -> None:
        config = session_config_from_mapping(
            {"mode": "diagnostic", "gator": {"enabled": True}},
            base_dir=self.base,
        )
        self.assertEqual(config.run_count, 1)
        self.assertIsNone(config.run_duration_s)

        timed = session_config_from_mapping(
            {
                "mode": "diagnostic",
                "run_duration_s": 12.5,
                "endaq": {"enabled": True},
            },
            base_dir=self.base,
        )
        self.assertEqual(timed.run_duration_s, 12.5)

    def test_diagnostic_rejects_multiple_runs(self) -> None:
        with self.assertRaisesRegex(ConfigError, "exactly one run"):
            session_config_from_mapping(
                {
                    "mode": "diagnostic",
                    "run_count": 2,
                    "gator": {"enabled": True},
                },
                base_dir=self.base,
            )

    def test_prognostic_requires_valid_period(self) -> None:
        with self.assertRaisesRegex(ConfigError, "requires run_period_s"):
            session_config_from_mapping(
                {
                    "mode": "prognostic",
                    "run_count": 2,
                    "run_duration_s": 10,
                    "gator": {"enabled": True},
                },
                base_dir=self.base,
            )

        with self.assertRaisesRegex(ConfigError, "must be >= run_duration_s"):
            session_config_from_mapping(
                {
                    "mode": "prognostic",
                    "run_count": 2,
                    "run_duration_s": 10,
                    "run_period_s": 5,
                    "gator": {"enabled": True},
                },
                base_dir=self.base,
            )

    def test_relative_paths_are_config_relative(self) -> None:
        config = session_config_from_mapping(
            {
                "output_root": "results",
                "gator": {
                    "enabled": True,
                    "binary_path": "bin/recorder",
                },
            },
            base_dir=self.base,
        )
        self.assertEqual(config.output_root, str((self.base / "results").resolve()))
        self.assertEqual(
            config.gator.binary_path,
            str((self.base / "bin/recorder").resolve()),
        )

    def test_unknown_keys_fail_fast(self) -> None:
        with self.assertRaisesRegex(ConfigError, "Unknown top-level"):
            session_config_from_mapping(
                {
                    "gator": {"enabled": True},
                    "run_duraton_s": 10,
                },
                base_dir=self.base,
            )


if __name__ == "__main__":
    unittest.main()
