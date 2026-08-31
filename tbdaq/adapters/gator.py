"""Spawns the C++ recordchannel8csv binary, controls it via SIGTERM, parses
   the START_UTC_US token from its stdout for cross-device alignment."""
from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from tbdaq.config import GatorConfig

_START_UTC_RE = re.compile(r"START_UTC_US=(\d+)")


@dataclass
class GatorRunResult:
    start_utc_us: Optional[int] = None
    stop_utc_us: Optional[int] = None
    samples_written: Optional[int] = None
    output_path: str = ""
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None


class GatorAdapter:
    def __init__(self, config: GatorConfig) -> None:
        self._config = config
        self._process: Optional[subprocess.Popen] = None
        self._result = GatorRunResult()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self, output_path: str) -> GatorRunResult:
        """Start the C++ binary and wait for it to print START_UTC_US."""
        cfg = self._config

        if not Path(cfg.binary_path).is_file():
            self._result.error = f"Gator binary not found: {cfg.binary_path}"
            return self._result

        args = [
            cfg.binary_path,
            "--output", output_path,
            "--duration-s", "0",          # run until stop() or natural end
            "--channel", str(cfg.channel),
        ]
        if cfg.samplerate is not None:
            args += ["--samplerate", str(cfg.samplerate)]
        if cfg.fullscale is not None:
            args += ["--fullscale", str(cfg.fullscale)]
        if cfg.threshold is not None:
            args += ["--threshold", str(cfg.threshold)]

        env = dict(os.environ)
        if cfg.library_path:
            existing = env.get("LD_LIBRARY_PATH", "")
            env["LD_LIBRARY_PATH"] = f"{cfg.library_path}:{existing}" if existing else cfg.library_path

        self._result = GatorRunResult(output_path=output_path)

        try:
            self._process = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )
        except OSError as exc:
            self._result.error = f"Failed to launch Gator binary: {exc}"
            return self._result

        # Read lines until we see START_UTC_US or the process exits early
        assert self._process.stdout is not None
        for line in self._process.stdout:
            print(f"[gator] {line}", end="")
            match = _START_UTC_RE.search(line)
            if match:
                self._result.start_utc_us = int(match.group(1))
                break
        else:
            # stdout closed without seeing the token — binary failed at startup
            self._process.wait()
            stderr = self._process.stderr.read() if self._process.stderr else ""
            self._result.error = (
                f"Gator binary exited before recording started "
                f"(exit code {self._process.returncode}). stderr: {stderr.strip()}"
            )

        return self._result

    def stop(self) -> GatorRunResult:
        """Send SIGTERM, wait for the process to finish, capture the stop time."""
        if self._process is None:
            return self._result

        self._result.stop_utc_us = _now_utc_us()

        if self._process.poll() is None:
            self._process.terminate()

        self._drain_stdout()
        self._process.wait()

        if self._process.returncode not in (0, -15):  # -15 = SIGTERM
            stderr = self._process.stderr.read() if self._process.stderr else ""
            self._result.error = (
                f"Gator binary exited with code {self._process.returncode}. "
                f"stderr: {stderr.strip()}"
            )

        self._process = None
        return self._result

    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def wait_for_natural_end(self) -> GatorRunResult:
        """Block until the binary exits on its own (fixed-duration mode)."""
        if self._process is None:
            return self._result

        self._drain_stdout()
        self._process.wait()
        self._result.stop_utc_us = _now_utc_us()

        if self._process.returncode != 0:
            stderr = self._process.stderr.read() if self._process.stderr else ""
            self._result.error = (
                f"Gator binary exited with code {self._process.returncode}. "
                f"stderr: {stderr.strip()}"
            )

        self._process = None
        return self._result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _drain_stdout(self) -> None:
        if self._process is None or self._process.stdout is None:
            return
        samples_re = re.compile(r"Wrote (\d+) samples")
        for line in self._process.stdout:
            print(f"[gator] {line}", end="")
            m = samples_re.search(line)
            if m:
                self._result.samples_written = int(m.group(1))


def _now_utc_us() -> int:
    return int(time.time() * 1_000_000)
