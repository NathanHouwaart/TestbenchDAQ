from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

import numpy as np

from tbdaq.plotting import _downsample_envelope, _group_name, _read_signal


class PlottingTests(unittest.TestCase):
    def test_envelope_downsampling_preserves_short_peak(self) -> None:
        time_s = np.arange(10_000, dtype=float) / 1_000
        values = np.zeros(10_000)
        values[4_321] = 99.0

        reduced_time, reduced_values = _downsample_envelope(
            time_s, values, max_points=500
        )

        self.assertLessEqual(len(reduced_time), 500)
        self.assertIn(99.0, reduced_values)
        self.assertIn(time_s[4_321], reduced_time)

    def test_related_axes_are_grouped(self) -> None:
        self.assertEqual(
            _group_name("endaq_100g_pe_acceleration_x_100g", "endaq"),
            "enDAQ 100g PE acceleration",
        )
        self.assertEqual(
            _group_name("gator_sensor_8_fm", "gator"),
            "Gator FBG sensors",
        )

    def test_missing_final_value_is_loaded_as_plot_gap(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "signal.csv"
            path.write_text("time_s,value_fm\n0.0,123\n0.1,\n", encoding="utf-8")

            signal = _read_signal(
                path,
                alias="gator_sensor_1_fm",
                family="gator",
                max_points=100,
                gator_zero_as_gap=False,
            )

            self.assertEqual(signal.time_s.tolist(), [0.0, 0.1])
            self.assertTrue(np.isnan(signal.values[-1]))


if __name__ == "__main__":
    unittest.main()
