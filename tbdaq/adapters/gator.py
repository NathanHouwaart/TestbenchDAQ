"""Spawns the C++ recordchannel8csv binary, controls it via SIGTERM, parses
   the START_UTC_US token from its stdout for cross-device alignment."""
from __future__ import annotations

import json
import logging
import os
import queue
import re
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from tbdaq.config import GatorConfig
from tbdaq.isa_export import ExportWindow, SignalFile, export_gator_signals

_START_UTC_RE = re.compile(r"START_UTC_US=(\d+)")


@dataclass
class GatorRunResult:
    start_utc_us: Optional[int] = None
    stop_utc_us: Optional[int] = None
    samples_written: Optional[int] = None
    last_sample_utc_us: Optional[int] = None
    requested_samplerate_hz: Optional[int] = None
    actual_samplerate_hz: Optional[float] = None
    device_index: Optional[int] = None
    timestamp_source: Optional[str] = None
    vendor_api: Optional[str] = None
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
        self._output_queue: queue.Queue[str] = queue.Queue()
        self._output_thread: Optional[threading.Thread] = None
        self._output_lines: list[str] = []

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

    def export_run(
        self, run_dir: Path, *, window: ExportWindow | None = None
    ) -> list[SignalFile]:
        source = Path(self._result.output_path)
        if not self._result.ok or not source.is_file():
            return []
        return export_gator_signals(
            source, run_dir / "signals" / self.family, window=window
        )

    def start(self, output_path: str) -> GatorRunResult:
        """Start the C++ binary and wait for it to print START_UTC_US."""
        cfg = self._config
        self._result = GatorRunResult(output_path=output_path)
        self._output_queue = queue.Queue()
        self._output_thread = None
        self._output_lines = []

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
        if cfg.device_index is not None:
            args += ["--device-index", str(cfg.device_index)]

        env = dict(os.environ)
        if cfg.library_path:
            existing = env.get("LD_LIBRARY_PATH", "")
            env["LD_LIBRARY_PATH"] = f"{cfg.library_path}:{existing}" if existing else cfg.library_path

        try:
            process_options: dict[str, object] = {}
            if os.name == "nt":
                # Windows terminate() is TerminateProcess, which prevents a
                # recorder from stopping its vendor stream and flushing CSV.
                process_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            self._process = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env,
                **process_options,
            )
        except OSError as exc:
            self._result.error = f"Failed to launch Gator binary: {exc}"
            return self._result

        # A dedicated reader is required here. Using select() with a buffered
        # TextIOWrapper can strand complete lines in Python's user-space
        # buffer, causing a false startup timeout even after START_UTC_US was
        # emitted by the C++ process.
        self._start_output_reader()
        deadline = time.monotonic() + cfg.start_timeout_s
        while self._result.start_utc_us is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._terminate_after_start_failure(
                    f"Gator did not become ready within {cfg.start_timeout_s:g}s."
                )
                break
            try:
                line = self._output_queue.get(timeout=min(remaining, 0.25))
            except queue.Empty:
                if self._process.poll() is not None:
                    self._record_early_exit()
                    break
                continue
            self._consume_line(line)

        return self._result

    def stop(self) -> GatorRunResult:
        """Send SIGTERM, wait for the process to finish, capture the stop time."""
        if self._process is None:
            return self._result

        self._result.stop_utc_us = _now_utc_us()

        if self._process.poll() is None:
            self._request_graceful_stop()

        try:
            self._process.wait(timeout=self._config.stop_timeout_s)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait()
            self._result.error = (
                f"Gator did not stop within {self._config.stop_timeout_s:g}s and was killed."
            )
        self._finish_output_reader()

        if self._process.returncode not in (0, -15) and self._result.error is None:
            self._result.error = (
                f"Gator binary exited with code {self._process.returncode}. "
                f"output: {self._recent_output()}"
            )
        self._mark_missing_samples_as_error()

        self._process = None
        return self._result

    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def wait_for_natural_end(self) -> GatorRunResult:
        """Block until the binary exits on its own (fixed-duration mode)."""
        if self._process is None:
            return self._result

        try:
            self._process.wait(timeout=self._config.stop_timeout_s)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait()
            self._result.error = "Gator timed out while waiting for natural completion."
        self._finish_output_reader()
        self._result.stop_utc_us = _now_utc_us()

        if self._process.returncode != 0 and self._result.error is None:
            self._result.error = (
                f"Gator binary exited with code {self._process.returncode}. "
                f"output: {self._recent_output()}"
            )
        self._mark_missing_samples_as_error()

        self._process = None
        return self._result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _start_output_reader(self) -> None:
        assert self._process is not None and self._process.stdout is not None

        def read_lines() -> None:
            assert self._process is not None and self._process.stdout is not None
            for line in self._process.stdout:
                self._output_queue.put(line)

        self._output_thread = threading.Thread(target=read_lines, daemon=True)
        self._output_thread.start()

    def _consume_line(self, line: str) -> None:
        self._output_lines.append(line.rstrip())
        logging_line = line.rstrip()
        if logging_line:
            logging.getLogger(__name__).debug("native: %s", logging_line)
        start = _START_UTC_RE.search(line)
        if start:
            self._result.start_utc_us = int(start.group(1))
            logging.getLogger(__name__).info("Gator recording started.")
        samples = re.search(r"Wrote (\d+) samples", line)
        if samples:
            self._result.samples_written = int(samples.group(1))
            logging.getLogger(__name__).info(
                "Gator recording stopped after %d samples.",
                self._result.samples_written,
            )
        last_sample = re.search(r"LAST_SAMPLE_UTC_US=(\d+)", line)
        if last_sample:
            self._result.last_sample_utc_us = int(last_sample.group(1))
        metadata = re.search(r"GATOR_METADATA=(\{.*\})", line)
        if metadata:
            try:
                values = json.loads(metadata.group(1))
                self._result.requested_samplerate_hz = values.get("requested_samplerate_hz")
                self._result.actual_samplerate_hz = values.get("actual_samplerate_hz")
                self._result.device_index = values.get("device_index")
                self._result.timestamp_source = values.get("timestamp_source")
                self._result.vendor_api = values.get("vendor_api")
            except (TypeError, ValueError):
                logging.getLogger(__name__).warning("Could not parse Gator metadata: %s", line.rstrip())

    def _finish_output_reader(self) -> None:
        if self._output_thread is not None:
            self._output_thread.join(timeout=2)
        while True:
            try:
                self._consume_line(self._output_queue.get_nowait())
            except queue.Empty:
                break
        if self._process is not None and self._process.stdout is not None:
            self._process.stdout.close()
        self._output_thread = None

    def _recent_output(self) -> str:
        return " | ".join(self._output_lines[-5:])

    def _mark_missing_samples_as_error(self) -> None:
        if self._result.error is None and self._result.samples_written == 0:
            self._result.error = "Gator recorder completed without receiving any samples."

    def _request_graceful_stop(self) -> None:
        assert self._process is not None
        if os.name == "nt":
            self._process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            self._process.terminate()

    def _record_early_exit(self) -> None:
        assert self._process is not None
        self._process.wait()
        self._finish_output_reader()
        self._result.error = (
            "Gator binary exited before recording started "
            f"(exit code {self._process.returncode}). output: {self._recent_output()}"
        )

    def _terminate_after_start_failure(self, message: str) -> None:
        assert self._process is not None
        if self._process.poll() is None:
            self._request_graceful_stop()
        try:
            self._process.wait(timeout=self._config.stop_timeout_s)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait()
        self._finish_output_reader()
        output = self._recent_output()
        detail = f" output: {output}" if output else ""
        self._result.error = message + detail


def _now_utc_us() -> int:
    return int(time.time() * 1_000_000)
