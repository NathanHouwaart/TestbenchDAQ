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
from tbdaq.isa_export import SignalFile, export_endaq_signals

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
    ide_sha256: Optional[str] = None
    csv_paths: list[str] = None  # type: ignore[assignment]
    conversion_status: str = "not_started"
    error: Optional[str] = None

    def __post_init__(self):
        if self.csv_paths is None:
            self.csv_paths = []

    @property
    def ok(self) -> bool:
        return self.error is None


class EndaqAdapter:
    family = "endaq"

    def __init__(self, config: EndaqConfig) -> None:
        self._config = config
        self._device: Any = None
        self._mount_path: Optional[str] = None
        self._data_dir: Optional[str] = None
        self._files_before_start: set[str] = set()
        self._result = EndaqRunResult()

    @property
    def result(self) -> EndaqRunResult:
        return self._result

    def start_run(self, run_dir: Path) -> EndaqRunResult:
        del run_dir
        return self.start()

    def stop_run(self, run_dir: Path) -> EndaqRunResult:
        return self.stop(str(run_dir / "raw" / self.family))

    def export_run(self, run_dir: Path) -> list[SignalFile]:
        converted_dir = run_dir / "converted" / self.family
        converted_dir.mkdir(parents=True, exist_ok=True)
        self.convert(str(converted_dir))
        if self._result.conversion_status != "success":
            return []
        return export_endaq_signals(
            converted_dir,
            run_dir / "signals" / self.family,
        )

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def discover(self) -> Optional[str]:
        """Find and attach to the first endaq device. Returns error string or None."""
        if _IMPORT_ERROR is not None or _endaq_device is None:
            return f"endaq-device not importable: {_IMPORT_ERROR}"

        if self._config.mount_path:
            devices = _endaq_device.getDevices(
                paths=[self._config.mount_path],
                unmounted=False,
                strict=False,
            )
        else:
            devices = _endaq_device.getDevices()

        if self._config.serial:
            devices = [
                device for device in devices
                if str(getattr(device, "serial", "")).casefold()
                == self._config.serial.casefold()
            ]
        if self._config.model:
            devices = [
                device for device in devices
                if str(getattr(device, "productName", "")).casefold()
                == self._config.model.casefold()
            ]
        if not devices:
            expected: list[str] = []
            if self._config.serial:
                expected.append(f"serial={self._config.serial}")
            if self._config.model:
                expected.append(f"model={self._config.model}")
            suffix = f" matching {', '.join(expected)}" if expected else ""
            return f"No enDAQ devices found{suffix}."
        if len(devices) > 1:
            return (
                f"Found {len(devices)} enDAQ devices. Configure endaq.serial "
                "or endaq.mount_path to select exactly one."
            )

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
            self._device.setTime(timeout=self._config.command_timeout_s)
            _LOG.info("Endaq clock synchronised to host UTC.")

            remounted = self._device.command.awaitRemount(
                update=True,
                paths=[self._mount_path],
                strict=False,
                timeout=self._config.remount_timeout_s,
            )
            if remounted is False:
                raise RuntimeError("enDAQ did not report a mounted state before start.")
            self._files_before_start = set(self._list_ide_files())

            self._result.start_utc_us = _now_utc_us()
            started = self._device.command.startRecording(
                timeout=self._config.command_timeout_s
            )
            if started is False:
                raise RuntimeError("enDAQ did not acknowledge recording start.")
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
            self._device.command.stopRecording(timeout=self._config.command_timeout_s)
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

        converter = self._config.ide_converter_path
        if not converter:
            self._result.conversion_status = "skipped"
            _LOG.info("IDE converter not configured — preserving raw IDE without conversion.")
            return self._result

        executable = converter if os.path.isfile(converter) else shutil.which(converter)
        if not executable:
            self._result.conversion_status = "failed"
            self._result.error = f"IDE converter not found: {converter}"
            return self._result

        # Export absolute Unix-epoch seconds. The later synchronization phase
        # will normalize both sensor families to one shared run reference.
        cmd = [
            executable,
            self._result.ide_path,
            "-o", output_dir + "/",
            "-t", "csv",
            "-n",
            "-r",
            "-u",
        ]
        _LOG.info("Running: " + " ".join(cmd))

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            self._result.conversion_status = "success"
            self._result.csv_paths = sorted(
                glob.glob(os.path.join(output_dir, "*.csv"))
            )
            _LOG.info(f"ide2csv produced {len(self._result.csv_paths)} CSV file(s).")
        else:
            self._result.conversion_status = "failed"
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
        remounted = self._device.command.awaitRemount(
            update=True,
            paths=[self._mount_path],
            strict=False,
            timeout=self._config.remount_timeout_s,
        )
        if remounted is False:
            raise RuntimeError("enDAQ did not remount after recording stop.")

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
        source_hash = _sha256(source)
        destination_hash = _sha256(dest)
        if source_hash != destination_hash:
            raise RuntimeError(f"SHA-256 mismatch after copying IDE file to '{dest}'.")

        _LOG.info(f"Offloaded {os.path.basename(source)} → {dest}")
        self._result.ide_path = dest
        self._result.ide_sha256 = destination_hash
        _LOG.info("Recorder-side IDE retained by policy.")


def _now_utc_us() -> int:
    return int(time.time() * 1_000_000)


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
