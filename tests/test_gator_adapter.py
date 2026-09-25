from __future__ import annotations

import sys
import signal
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tbdaq.adapters.gator import GatorAdapter, GatorRunResult
from tbdaq.config import GatorConfig


class GatorAdapterTests(unittest.TestCase):
    def test_rapid_buffered_output_does_not_hide_readiness_token(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            implementation = root / "fake_gator.py"
            implementation.write_text(
                "import signal, time\n"
                "stop = False\n"
                "def handle(_signum, _frame):\n"
                "    global stop\n"
                "    stop = True\n"
                "signal.signal(signal.SIGTERM, handle)\n"
                "print('vendor line one', flush=True)\n"
                "print('vendor line two', flush=True)\n"
                "print('START_UTC_US=1800000000123456', flush=True)\n"
                "deadline = time.monotonic() + 0.2\n"
                "while not stop and time.monotonic() < deadline:\n"
                "    time.sleep(0.01)\n"
                "print('Wrote 42 samples', flush=True)\n",
                encoding="utf-8",
            )
            if sys.platform == "win32":
                recorder = root / "fake_gator.cmd"
                recorder.write_text(
                    f'@echo off\r\n"{sys.executable}" "{implementation}"\r\n',
                    encoding="utf-8",
                )
            else:
                recorder = root / "fake_gator"
                recorder.write_text(
                    f"#!{sys.executable}\n{implementation.read_text(encoding='utf-8')}",
                    encoding="utf-8",
                )
                recorder.chmod(0o755)
            adapter = GatorAdapter(
                GatorConfig(
                    enabled=True,
                    binary_path=str(recorder),
                    start_timeout_s=1,
                    stop_timeout_s=1,
                )
            )
            result = adapter.start(str(root / "output.csv"))
            self.assertTrue(result.ok)
            self.assertEqual(result.start_utc_us, 1_800_000_000_123_456)
            result = adapter.wait_for_natural_end()
            self.assertTrue(result.ok)
            self.assertEqual(result.samples_written, 42)

    def test_native_metadata_tokens_are_captured(self) -> None:
        adapter = GatorAdapter(GatorConfig(enabled=True, binary_path="gator_recorder"))
        adapter._result = GatorRunResult()

        adapter._consume_line(
            'GATOR_METADATA={"device_index":1,"requested_samplerate_hz":5000,'
            '"actual_samplerate_hz":4999.7,"timestamp_source":"device_utc",'
            '"vendor_api":"gtrlib-v0.1.0"}\n'
        )
        adapter._consume_line("LAST_SAMPLE_UTC_US=1800000005123456\n")

        self.assertEqual(adapter._result.device_index, 1)
        self.assertEqual(adapter._result.requested_samplerate_hz, 5000)
        self.assertEqual(adapter._result.actual_samplerate_hz, 4999.7)
        self.assertEqual(adapter._result.last_sample_utc_us, 1_800_000_005_123_456)
        self.assertEqual(adapter._result.timestamp_source, "device_utc")
        self.assertEqual(adapter._result.vendor_api, "gtrlib-v0.1.0")

    def test_zero_samples_is_a_failed_result(self) -> None:
        adapter = GatorAdapter(GatorConfig(enabled=True, binary_path="gator_recorder"))
        adapter._result = GatorRunResult(samples_written=0)

        adapter._mark_missing_samples_as_error()

        self.assertFalse(adapter._result.ok)
        self.assertIn("without receiving any samples", adapter._result.error or "")

    def test_windows_requests_ctrl_break_for_graceful_stop(self) -> None:
        adapter = GatorAdapter(GatorConfig(enabled=True, binary_path="gator_recorder.exe"))
        adapter._process = Mock()

        with patch("tbdaq.adapters.gator.os.name", "nt"):
            adapter._request_graceful_stop()

        adapter._process.send_signal.assert_called_once_with(signal.CTRL_BREAK_EVENT)


if __name__ == "__main__":
    unittest.main()
