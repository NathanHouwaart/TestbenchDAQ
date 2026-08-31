"""Spawns the C++ recordchannel8csv binary, controls it via SIGTERM, parses
   the START_UTC_US token from its stdout for cross-device alignment."""
from __future__ import annotations

import os
import re
import selectors
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from tbdaq.config import GatorConfig
from tbdaq.isa_export import SignalFile, export_gator_signals

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
    family = "gator"

    def __init__(self, config: GatorConfig) -> None:
        self._config = config
        self._process: Optional[subprocess.Popen] = None
        self._result = GatorRunResult()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def discover(self) -> Optional[str]:
        binary = Path(self._config.binary_path)
        if not binary.is_file():
            return f"Gator binary not found: {binary}"
        if not os.access(binary, os.X_OK):
            return f"Gator binary is not executable: {binary}"
        if self._config.library_path and not Path(self._config.library_path).is_dir():
            return f"Gator library directory not found: {self._config.library_path}"
        return None

    @property
    def result(self) -> GatorRunResult:
        return self._result

    def start_run(self, run_dir: Path) -> GatorRunResult:
        raw_dir = run_dir / "raw" / self.family
        raw_dir.mkdir(parents=True, exist_ok=True)
        return self.start(str(raw_dir / "gator_channel.csv"))

    def stop_run(self, run_dir: Path) -> GatorRunResult:
        del run_dir
        return self.stop()

    def export_run(self, run_dir: Path) -> list[SignalFile]:
        source = Path(self._result.output_path)
        if not self._result.ok or not source.is_file():
            return []
        return export_gator_signals(source, run_dir / "signals" / self.family)

    def start(self, output_path: str) -> GatorRunResult:
        """Start the C++ binary and wait for it to print START_UTC_US."""
        cfg = self._config
        self._result = GatorRunResult(output_path=output_path)

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

        # Read lines until the recorder reports readiness, exits, or times out.
        assert self._process.stdout is not None
        selector = selectors.DefaultSelector()
        selector.register(self._process.stdout, selectors.EVENT_READ)
        deadline = time.monotonic() + cfg.start_timeout_s
        try:
            while self._result.start_utc_us is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._terminate_after_start_failure(
                        f"Gator did not become ready within {cfg.start_timeout_s:g}s."
                    )
                    break
                if not selector.select(timeout=min(remaining, 0.25)):
                    if self._process.poll() is not None:
                        self._record_early_exit()
                        break
                    continue
                line = self._process.stdout.readline()
                if not line:
                    self._record_early_exit()
                    break
                print(f"[gator] {line}", end="")
                match = _START_UTC_RE.search(line)
                if match:
                    self._result.start_utc_us = int(match.group(1))
        finally:
            selector.close()

        return self._result

    def stop(self) -> GatorRunResult:
        """Send SIGTERM, wait for the process to finish, capture the stop time."""
        if self._process is None:
            return self._result

        self._result.stop_utc_us = _now_utc_us()

        if self._process.poll() is None:
            self._process.terminate()

        try:
            stdout, stderr = self._process.communicate(timeout=self._config.stop_timeout_s)
        except subprocess.TimeoutExpired:
            self._process.kill()
            stdout, stderr = self._process.communicate()
            self._result.error = (
                f"Gator did not stop within {self._config.stop_timeout_s:g}s and was killed."
            )
        self._consume_stdout(stdout)

        if self._process.returncode not in (0, -15) and self._result.error is None:
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

        try:
            stdout, stderr = self._process.communicate(timeout=self._config.stop_timeout_s)
        except subprocess.TimeoutExpired:
            self._process.kill()
            stdout, stderr = self._process.communicate()
            self._result.error = "Gator timed out while waiting for natural completion."
        self._consume_stdout(stdout)
        self._result.stop_utc_us = _now_utc_us()

        if self._process.returncode != 0 and self._result.error is None:
            self._result.error = (
                f"Gator binary exited with code {self._process.returncode}. "
                f"stderr: {stderr.strip()}"
            )

        self._process = None
        return self._result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _consume_stdout(self, output: str) -> None:
        samples_re = re.compile(r"Wrote (\d+) samples")
        for line in output.splitlines(keepends=True):
            print(f"[gator] {line}", end="")
            m = samples_re.search(line)
            if m:
                self._result.samples_written = int(m.group(1))

    def _record_early_exit(self) -> None:
        assert self._process is not None
        stdout, stderr = self._process.communicate()
        self._consume_stdout(stdout)
        self._result.error = (
            "Gator binary exited before recording started "
            f"(exit code {self._process.returncode}). stderr: {stderr.strip()}"
        )

    def _terminate_after_start_failure(self, message: str) -> None:
        assert self._process is not None
        if self._process.poll() is None:
            self._process.terminate()
        try:
            stdout, stderr = self._process.communicate(timeout=self._config.stop_timeout_s)
        except subprocess.TimeoutExpired:
            self._process.kill()
            stdout, stderr = self._process.communicate()
        self._consume_stdout(stdout)
        detail = f" stderr: {stderr.strip()}" if stderr.strip() else ""
        self._result.error = message + detail


def _now_utc_us() -> int:
    return int(time.time() * 1_000_000)
