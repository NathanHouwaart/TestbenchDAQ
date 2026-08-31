from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from tbdaq.adapters.gator import GatorAdapter
from tbdaq.config import GatorConfig


class GatorAdapterTests(unittest.TestCase):
    def test_rapid_buffered_output_does_not_hide_readiness_token(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            recorder = root / "fake_gator.py"
            recorder.write_text(
                f"#!{sys.executable}\n"
                "import signal, time\n"
                "stop = False\n"
                "def handle(_signum, _frame):\n"
                "    global stop\n"
                "    stop = True\n"
                "signal.signal(signal.SIGTERM, handle)\n"
                "print('vendor line one', flush=True)\n"
                "print('vendor line two', flush=True)\n"
                "print('START_UTC_US=1800000000123456', flush=True)\n"
                "while not stop:\n"
                "    time.sleep(0.01)\n"
                "print('Wrote 42 samples', flush=True)\n",
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
            result = adapter.stop()
            self.assertTrue(result.ok)
            self.assertEqual(result.samples_written, 42)


if __name__ == "__main__":
    unittest.main()
