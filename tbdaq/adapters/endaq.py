"""Thin adapter around endaq-device: clock sync, start/stop, offload, convert."""
from __future__ import annotations

import glob
import hashlib
import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from tbdaq.config import EndaqConfig

_LOG = logging.getLogger(__name__)

try:
    import endaq.device as _endaq_device
    _IMPORT_ERROR: Optional[Exception] = None
except Exception as exc:
    _endaq_device = None  # type: ignore[assignment]
    _IMPORT_ERROR = exc


@dataclass
class EndaqRunResult:
    start_utc_us: Optional[int] = None
    stop_utc_us: Optional[int] = None
    ide_path: Optional[str] = None
    csv_paths: list[str] = None  # type: ignore[assignment]
    error: Optional[str] = None

    def __post_init__(self):
        if self.csv_paths is None:
            self.csv_paths = []

    @property
    def ok(self) -> bool:
        return self.error is None


class EndaqAdapter:
    def __init__(self, config: EndaqConfig) -> None:
        self._config = config
        self._device: Any = None
        self._mount_path: Optional[str] = None
        self._data_dir: Optional[str] = None
        self._files_before_start: set[str] = set()
        self._result = EndaqRunResult()

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def discover(self) -> Optional[str]:
        """Find and attach to the first endaq device. Returns error string or None."""
        if _IMPORT_ERROR is not None or _endaq_device is None:
            return f"endaq-device not importable: {_IMPORT_ERROR}"

        devices = _endaq_device.getDevices()
        if not devices:
            return "No endaq devices found."

        self._device = devices[0]
        self._mount_path = str(self._device.path)
        self._data_dir = os.path.join(self._mount_path, "DATA", "RECORD")
        _LOG.info(f"Found endaq at {self._mount_path}")
        return None

    # ------------------------------------------------------------------
    # Run lifecycle
    # ------------------------------------------------------------------

    def start(self) -> EndaqRunResult:
        self._result = EndaqRunResult()

        if self._device is None:
            self._result.error = "No endaq device attached (call discover() first)."
            return self._result

        try:
            # Sync clock to host so timestamps are aligned
            self._device.setTime(time.time() + 1)
            _LOG.info("Endaq clock synchronised to host UTC.")

            self._device.command.awaitRemount(paths=self._mount_path, timeout=-1)
            self._files_before_start = set(self._list_ide_files())

            self._result.start_utc_us = _now_utc_us()
            self._device.command.startRecording(timeout=-1)
            _LOG.info("Endaq recording started.")
        except Exception as exc:
            self._result.error = f"Endaq start failed: {exc}"

        return self._result

    def stop(self, raw_output_dir: str) -> EndaqRunResult:
        self._result.stop_utc_us = _now_utc_us()

        if self._device is None:
            self._result.error = "No endaq device attached."
            return self._result

        try:
            self._device.command.stopRecording()
            _LOG.info("Endaq recording stopped.")
            self._offload(raw_output_dir)
        except Exception as exc:
            self._result.error = f"Endaq stop/offload failed: {exc}"

        return self._result

    def convert(self, output_dir: str) -> EndaqRunResult:
        """Convert the offloaded .IDE to CSV using ide2csv if configured."""
        if not self._result.ide_path:
            self._result.error = "No IDE file to convert."
            return self._result

        ide2csv = self._config.ide2csv_path
        if not ide2csv:
            _LOG.info("ide2csv not configured — skipping conversion.")
            return self._result

        if not os.path.isfile(ide2csv):
            _LOG.warning(f"ide2csv not found at '{ide2csv}' — skipping conversion.")
            return self._result

        cmd = [ide2csv, self._result.ide_path, "-o", output_dir + "/", "-t", "csv", "-n", "-f"]
        _LOG.info("Running: " + " ".join(cmd))

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            self._result.csv_paths = sorted(
                glob.glob(os.path.join(output_dir, "*.csv"))
            )
            _LOG.info(f"ide2csv produced {len(self._result.csv_paths)} CSV file(s).")
        else:
            self._result.error = (
                f"ide2csv failed (exit {result.returncode}): {result.stderr.strip()}"
            )
        return self._result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _list_ide_files(self) -> list[str]:
        if not self._data_dir:
            return []
        return sorted(glob.glob(os.path.join(self._data_dir, "*.IDE")))

    def _offload(self, output_dir: str) -> None:
        assert self._device is not None
        self._device.command.awaitRemount(paths=self._mount_path, timeout=-1)

        current = set(self._list_ide_files())
        new_files = current - self._files_before_start
        if not new_files:
            # Fallback: pick the most recently modified file
            all_files = self._list_ide_files()
            if all_files:
                new_files = {max(all_files, key=os.path.getmtime)}
            else:
                _LOG.warning("No IDE recording file found after stop.")
                return

        source = sorted(new_files)[-1]
        os.makedirs(output_dir, exist_ok=True)
        dest = os.path.join(output_dir, os.path.basename(source))
        shutil.copy2(source, dest)

        if os.path.getsize(source) != os.path.getsize(dest):
            raise RuntimeError(f"Size mismatch after copying IDE file to '{dest}'.")

        _LOG.info(f"Offloaded {os.path.basename(source)} → {dest}")
        self._result.ide_path = dest

        try:
            os.remove(source)
            _LOG.info(f"Deleted recorder-side copy: {source}")
        except OSError as exc:
            _LOG.warning(f"Could not delete recorder-side IDE file: {exc}")


def _now_utc_us() -> int:
    return int(time.time() * 1_000_000)
