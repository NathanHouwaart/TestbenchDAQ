from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from tbdaq.isa_export import ExportWindow, export_endaq_ide_signals, export_gator_signals


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

    def test_gator_export_is_cropped_to_window_duration(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "gator.csv"
            with source.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow([
                    "utc_timestamp_us", "channel",
                    *[f"sensor_{index}_fm" for index in range(1, 9)],
                ])
                for timestamp in (1_000_000, 2_000_000, 3_000_000):
                    writer.writerow([timestamp, 8, *range(1, 9)])

            results = export_gator_signals(
                source,
                root / "signals",
                window=ExportWindow(start_utc_s=10, stop_utc_s=11.5),
            )

            self.assertTrue(all(result.sample_count == 2 for result in results))
            self.assertEqual(results[0].last_time_s, 1.0)
            self.assertEqual(
                results[0].path.read_text(encoding="utf-8").splitlines(),
                ["time_s,value_fm", "0.000000,1", "1.000000,1"],
            )

    def test_endaq_export_keeps_complete_recording_with_common_origin(self) -> None:
        class Events:
            def __init__(self, times_us: list[float], values: list[float]) -> None:
                self.array = np.asarray([times_us, values], dtype=float)

            def __len__(self) -> int:
                return self.array.shape[1]

            def __getitem__(self, index: int):
                return self.array[:, index]

            def arraySlice(self, start: int, stop: int):
                return self.array[:, start:stop]

        class Channel:
            def __init__(self, channel_id: int, times: list[float]) -> None:
                self.id = channel_id
                self.name = self.displayName = f"Channel {channel_id}"
                self.subchannels = [SimpleNamespace(visibility=0, name="Value")]
                self.events = Events(times, list(range(len(times))))

            def getSession(self):
                return self.events

        channels = {
            1: Channel(1, [500_000, 1_000_000, 2_000_000, 3_000_000, 3_500_000]),
            2: Channel(2, [1_500_000, 2_500_000]),
        }
        document = SimpleNamespace(
            channels=channels,
            sessions=[SimpleNamespace(utcStartTime=1_000.0)],
            close=lambda: None,
        )
        importer = SimpleNamespace(
            openFile=lambda _path: document,
            readData=lambda _document, channels: None,
        )
        fake_idelib = SimpleNamespace(importer=importer)

        with tempfile.TemporaryDirectory() as temp, patch.dict(
            sys.modules, {"idelib": fake_idelib}
        ):
            results = export_endaq_ide_signals(
                Path(temp) / "raw.IDE",
                Path(temp) / "signals",
                window=ExportWindow(start_utc_s=1_001.0, stop_utc_s=1_003.0),
            )

            self.assertEqual(len(results), 2)
            self.assertEqual(results[0].sample_count, 5)
            self.assertEqual(results[0].first_time_s, 0.0)
            self.assertEqual(results[0].last_time_s, 3.0)
            self.assertEqual(results[1].first_time_s, 1.0)
            self.assertEqual(results[1].last_time_s, 2.0)
            self.assertEqual(
                results[0].alignment_method,
                "ide_recording_first_sample_relative",
            )
            self.assertEqual(
                results[1].path.read_text(encoding="utf-8").splitlines()[1:],
                ["1.000000,0", "2.000000,1"],
            )


if __name__ == "__main__":
    unittest.main()
