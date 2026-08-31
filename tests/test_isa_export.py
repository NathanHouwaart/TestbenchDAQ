from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from tbdaq.isa_export import export_gator_signals


class SignalExportTests(unittest.TestCase):
    def test_gator_exports_all_eight_columns_including_all_zero_sensor(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "gator.csv"
            with source.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow([
                    "utc_timestamp_us", "channel",
                    *[f"sensor_{index}_fm" for index in range(1, 9)],
                ])
                writer.writerow([1_000_000, 8, 0, 2, 3, 4, 5, 6, 7, 8])
                writer.writerow([2_000_000, 8, 0, 12, 13, 14, 15, 16, 17, 18])

            results = export_gator_signals(source, root / "signals")
            self.assertEqual(len(results), 8)
            sensor_one = root / "signals" / "gator_sensor_1_fm.csv"
            self.assertTrue(sensor_one.is_file())
            self.assertEqual(
                sensor_one.read_text(encoding="utf-8").splitlines(),
                ["time_s,value_fm", "0.000000,0", "1.000000,0"],
            )


if __name__ == "__main__":
    unittest.main()
