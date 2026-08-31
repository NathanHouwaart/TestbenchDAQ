"""Reliable enDAQ lifecycle: configure, record, remount, offload, convert."""
from __future__ import annotations

import glob
import hashlib
import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from tbdaq.config import EndaqConfig
from tbdaq.isa_export import SignalFile, export_endaq_ide_signals

_LOG = logging.getLogger(__name__)

try:
    import endaq.device as _endaq_device
    from endaq.device.command_interfaces import SerialCommandInterface
    from endaq.device.exceptions import CommandError
    from endaq.device.response_codes import DeviceStatusCode
    _IMPORT_ERROR: Optional[Exception] = None
except Exception as exc:
    _endaq_device = None  # type: ignore[assignment]
    SerialCommandInterface = None  # type: ignore[assignment,misc]
    CommandError = Exception  # type: ignore[assignment,misc]
    DeviceStatusCode = None  # type: ignore[assignment,misc]
    _IMPORT_ERROR = exc


@dataclass
class EndaqRunResult:
    start_utc_us: Optional[int] = None
    stop_utc_us: Optional[int] = None
    start_library_acknowledged: Optional[bool] = None
    ide_path: Optional[str] = None
    ide_sha256: Optional[str] = None
    ide_paths: list[str] = field(default_factory=list)
    ide_sha256_by_path: dict[str, str] = field(default_factory=dict)
    recorder_files_deleted: list[str] = field(default_factory=list)
    multiple_new_ide_files: bool = False
    csv_paths: list[str] = field(default_factory=list)
    conversion_status: str = "not_started"
    conversion_error: Optional[str] = None
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None


class EndaqAdapter:
    family = "endaq"

    def __init__(
        self,
        config: EndaqConfig,
        *,
        run_duration_s: Optional[float] = None,
    ) -> None:
        self._config = config
        self._run_duration_s = run_duration_s
        self._device: Any = None
        self._mount_path: Optional[str] = None
        self._data_dir: Optional[str] = None
        self._files_before_start: set[str] = set()
        self._needs_stop = False
        self._result = EndaqRunResult()

    @property
    def result(self) -> EndaqRunResult:
        return self._result

    @property
    def needs_stop(self) -> bool:
        """Whether a start command was sent and cleanup must be attempted."""
        return self._needs_stop

    def start_run(self, run_dir: Path) -> EndaqRunResult:
        del run_dir
        return self.start()

    def stop_run(self, run_dir: Path) -> EndaqRunResult:
        return self.stop(str(run_dir / "raw" / self.family))

    def export_run(self, run_dir: Path) -> list[SignalFile]:
        """Parse every verified IDE directly into final per-signal CSVs."""
        raw_dir = run_dir / "raw" / self.family
        ide_paths = sorted(raw_dir.glob("*.IDE")) + sorted(raw_dir.glob("*.ide"))
        if not ide_paths:
            self._result.conversion_status = "failed"
            self._result.error = "No offloaded IDE file to convert."
            return []

        all_signals: list[SignalFile] = []
        self._result.csv_paths = []
        multi = len(ide_paths) > 1
        try:
            for ide_path in ide_paths:
                signal_dir = run_dir / "signals" / self.family
                if multi:
                    signal_dir /= ide_path.stem
                _LOG.info("Exporting enDAQ signals directly from %s.", ide_path.name)
                signals = export_endaq_ide_signals(ide_path, signal_dir)
                all_signals.extend(signals)
                self._result.csv_paths.extend(
                    str(signal.path) for signal in signals if signal.status == "success"
                )
                failures = [signal for signal in signals if signal.status != "success"]
                if failures:
                    raise RuntimeError(
                        "; ".join(signal.error or signal.alias for signal in failures)
                    )
        except Exception as exc:
            self._result.conversion_status = "failed"
            self._result.conversion_error = str(exc)
            # Conversion failure must not invalidate the verified raw acquisition.
            _LOG.error("enDAQ conversion failed; verified IDE retained: %s", exc)
            return all_signals

        self._result.conversion_status = "success"
        _LOG.info("Direct IDE export produced %d signal CSV file(s).", len(self._result.csv_paths))
        return all_signals

    # ------------------------------------------------------------------
    # Discovery and one-time session preparation
    # ------------------------------------------------------------------

    def discover(self) -> Optional[str]:
        if _IMPORT_ERROR is not None or _endaq_device is None:
            return f"endaq-device not importable: {_IMPORT_ERROR}"
        error = self._discover_mounted()
        if error:
            return error
        try:
            self._apply_requested_configuration()
            self._check_storage_capacity()
            self._device.setTime(timeout=self._config.command_timeout_s)
            _LOG.info("enDAQ clock synchronised to host UTC for this session.")
        except Exception as exc:
            return f"enDAQ preparation failed: {exc}"
        return None

    def describe(self) -> dict[str, Any]:
        """Return mounted-device configuration for the operator CLI."""
        if _IMPORT_ERROR is not None or _endaq_device is None:
            raise RuntimeError(f"endaq-device not importable: {_IMPORT_ERROR}")
        error = self._discover_mounted()
        if error:
            raise RuntimeError(error)
        config = self._device.config
        channels = []
        for channel_id, channel in sorted(self._device.channels.items()):
            try:
                enabled = self._channel_enabled(config, channel)
            except Exception:
                enabled = None
            try:
                sample_rate = config.getSampleRate(channel)
            except Exception:
                sample_rate = None
            channels.append(
                {
                    "id": channel_id,
                    "name": getattr(channel, "name", ""),
                    "enabled": enabled,
                    "sample_rate_hz": sample_rate,
                    "subchannels": len(getattr(channel, "children", ())),
                }
            )
        usage = shutil.disk_usage(self._mount_path)
        return {
            "serial": str(getattr(self._device, "serial", "")),
            "model": str(getattr(self._device, "productName", "")),
            "mount_path": self._mount_path,
            "free_space_bytes": usage.free,
            "recording_time_limit_s": config.recordingTimeLimit,
            "recording_size_limit_bytes": config.recordingSizeLimit,
            "channels": channels,
        }

    # ------------------------------------------------------------------
    # Run lifecycle
    # ------------------------------------------------------------------

    def start(self) -> EndaqRunResult:
        self._result = EndaqRunResult()
        self._needs_stop = False
        if self._device is None:
            self._result.error = "No enDAQ device attached (call discover() first)."
            return self._result

        try:
            self._refresh_mounted_device()
            self._check_storage_capacity()
            self._files_before_start = set(self._list_ide_files())
            self._result.start_utc_us = _now_utc_us()
            self._needs_stop = True
            acknowledged = self._device.command.startRecording(
                wait=False,
                timeout=self._config.command_timeout_s,
            )
            self._result.start_library_acknowledged = bool(acknowledged)

            # The serial implementation can compare a stale status response
            # after it has sent RecStart. Dismount is authoritative proof.
            dismounted = self._device.command.awaitReboot(
                timeout=self._config.command_timeout_s,
                timeoutMsg="Timed out waiting for enDAQ recording to start",
            )
            if not dismounted:
                raise RuntimeError("enDAQ storage did not dismount after RecStart.")
            _LOG.info(
                "enDAQ recording started (library acknowledgement=%s).",
                acknowledged,
            )
        except Exception as exc:
            self._result.error = f"enDAQ start failed: {exc}"
        return self._result

    def stop(self, raw_output_dir: str) -> EndaqRunResult:
        self._result.stop_utc_us = _now_utc_us()
        if self._device is None:
            self._result.error = "No enDAQ device attached."
            return self._result

        try:
            self._send_stop_with_retry()
            self._await_mounted_device()
            self._needs_stop = False
            _LOG.info("enDAQ recording stopped and storage remounted.")
            self._offload(raw_output_dir)
        except Exception as exc:
            self._result.error = f"enDAQ stop/offload failed: {exc}"
        return self._result

    def stop_recording_and_remount(self) -> str:
        """Explicit operator recovery for an already-recording recorder."""
        if _IMPORT_ERROR is not None or SerialCommandInterface is None:
            raise RuntimeError(f"endaq-device not importable: {_IMPORT_ERROR}")
        if not self._config.serial:
            raise RuntimeError("endaq.serial is required for endaq-stop.")

        mounted = self._filter_devices(self._mounted_devices())
        if mounted:
            self._attach(mounted[0])
            return f"enDAQ {self._config.serial} is already mounted at {self._mount_path}."

        # Create only enough identity for SerialCommandInterface to locate
        # the port by serial number. No session files are touched.
        from endaq.device.base import NonRecorder

        fake = NonRecorder()
        fake._snInt = _serial_as_int(self._config.serial)
        fake._sn = self._config.serial
        fake.command = SerialCommandInterface(fake)
        self._device = fake
        self._mount_path = self._config.mount_path or "/mnt/endaq"
        self._send_stop_with_retry()
        self._await_mounted_device()
        self._needs_stop = False
        return f"Stopped enDAQ {self._config.serial}; remounted at {self._mount_path}."

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _mounted_devices(self) -> list[Any]:
        paths = [self._config.mount_path] if self._config.mount_path else None
        return list(
            _endaq_device.getDevices(
                paths=paths,
                unmounted=False,
                strict=False,
            )
        )

    def _discover_mounted(self) -> Optional[str]:
        try:
            devices = self._filter_devices(self._mounted_devices())
        except Exception as exc:
            return f"enDAQ discovery failed: {exc}"
        if not devices:
            serial_hint = f" ({self._config.serial})" if self._config.serial else ""
            port_hint = self._configured_serial_port()
            detail = (
                f" Serial interface {port_hint} is present, so it may already be recording."
                if port_hint else ""
            )
            return (
                f"No mounted enDAQ{serial_hint} found.{detail} "
                "If it is recording, run: tbdaq --config config.json endaq-stop"
            )
        if len(devices) > 1:
            return (
                f"Found {len(devices)} enDAQ devices. Configure endaq.serial "
                "or endaq.mount_path to select exactly one."
            )
        self._attach(devices[0])
        return None

    def _filter_devices(self, devices: list[Any]) -> list[Any]:
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
        return devices

    def _attach(self, device: Any) -> None:
        self._device = device
        self._mount_path = str(device.path)
        self._data_dir = os.path.join(self._mount_path, "DATA", "RECORD")
        _LOG.info("Found enDAQ at %s", self._mount_path)

    def _configured_serial_port(self) -> Optional[str]:
        if not self._config.serial or SerialCommandInterface is None:
            return None
        try:
            return SerialCommandInterface.findSerialPort(self._config.serial)
        except Exception:
            return None

    def _refresh_mounted_device(self) -> None:
        devices = self._filter_devices(self._mounted_devices())
        if len(devices) != 1:
            raise RuntimeError(
                f"Expected one mounted enDAQ before start, found {len(devices)}."
            )
        self._attach(devices[0])

    def _await_mounted_device(self) -> None:
        deadline = time.monotonic() + self._config.remount_timeout_s
        last_error: Optional[Exception] = None
        while time.monotonic() < deadline:
            try:
                devices = self._filter_devices(self._mounted_devices())
                if len(devices) == 1:
                    self._attach(devices[0])
                    return
            except Exception as exc:
                last_error = exc
            time.sleep(0.25)
        detail = f" Last error: {last_error}" if last_error else ""
        raise TimeoutError(
            f"enDAQ did not remount within {self._config.remount_timeout_s:g}s.{detail}"
        )

    def _send_stop_with_retry(self) -> None:
        deadline = time.monotonic() + self._config.remount_timeout_s
        last_error: Optional[Exception] = None
        while time.monotonic() < deadline:
            try:
                self._device.command.stopRecording(
                    wait=False,
                    timeout=min(self._config.command_timeout_s, 5.0),
                )
                return
            except CommandError as exc:
                last_error = exc
                if self._new_recording_is_mounted():
                    return
                if (
                    DeviceStatusCode is not None
                    and getattr(exc, "errno", None) == DeviceStatusCode.ERR_INVALID_COMMAND
                ):
                    time.sleep(0.5)
                    continue
            except Exception as exc:
                last_error = exc
                if self._new_recording_is_mounted():
                    return
            time.sleep(0.5)
        raise TimeoutError(
            f"Could not send enDAQ stop within {self._config.remount_timeout_s:g}s; "
            f"last error: {last_error}"
        )

    def _new_recording_is_mounted(self) -> bool:
        if not self._files_before_start:
            return False
        try:
            devices = self._filter_devices(self._mounted_devices())
            if len(devices) != 1:
                return False
            self._attach(devices[0])
            return bool(set(self._list_ide_files()) - self._files_before_start)
        except Exception:
            return False

    def _apply_requested_configuration(self) -> None:
        config = self._device.config
        for channel_id, requested in self._config.channels.items():
            channel = self._device.channels.get(channel_id)
            if channel is None:
                raise ValueError(f"enDAQ does not expose channel {channel_id}.")
            if requested.enabled is not None:
                self._set_channel_enabled(config, channel, requested.enabled)
            if requested.sample_rate_hz is not None:
                config.setSampleRate(channel, requested.sample_rate_hz)

        if self._config.recording_time_limit_s is not None:
            value = self._config.recording_time_limit_s
            config.recordingTimeLimit = None if value == 0 else value
        if self._config.recording_size_limit_bytes is not None:
            value = self._config.recording_size_limit_bytes
            config.recordingSizeLimit = None if value == 0 else value

        changes = config.getChanges()
        if changes:
            config.applyConfig()
            _LOG.info("Applied %d requested enDAQ configuration change(s).", len(changes))

    @staticmethod
    def _set_channel_enabled(config: Any, channel: Any, enabled: bool) -> None:
        try:
            config.enableChannel(channel, enabled)
        except Exception as parent_error:
            children = list(getattr(channel, "children", ()))
            if not children:
                raise parent_error
            for child in children:
                config.enableChannel(child, enabled)

    @staticmethod
    def _channel_enabled(config: Any, channel: Any) -> bool:
        try:
            return bool(config.isEnabled(channel))
        except Exception as parent_error:
            children = list(getattr(channel, "children", ()))
            if not children:
                raise parent_error
            return any(bool(config.isEnabled(child)) for child in children)

    def _estimate_bytes_per_second(self) -> int:
        if self._config.estimated_bytes_per_second is not None:
            return self._config.estimated_bytes_per_second
        total = 0.0
        config = self._device.config
        for channel in self._device.channels.values():
            try:
                if not self._channel_enabled(config, channel):
                    continue
                rate = float(config.getSampleRate(channel))
            except Exception:
                continue
            subchannels = max(1, len(getattr(channel, "children", ())))
            total += rate * subchannels * 8
        return max(1_048_576, int(total * 1.25))

    def _check_storage_capacity(self) -> None:
        if not self._mount_path:
            raise RuntimeError("enDAQ mount path is unknown.")
        usage = shutil.disk_usage(self._mount_path)
        required = self._config.minimum_free_space_bytes
        if self._run_duration_s is not None:
            # The recorder continues writing while its serial stop interface
            # and storage remount after the requested measurement window.
            worst_case_recording_s = (
                self._run_duration_s + self._config.remount_timeout_s
            )
            required += int(
                self._estimate_bytes_per_second() * worst_case_recording_s
            )
        if usage.free < required:
            raise RuntimeError(
                f"enDAQ free space is {usage.free} bytes; at least {required} bytes "
                "is required for this run."
            )
        _LOG.info(
            "enDAQ storage preflight: %d bytes free, %d bytes required.",
            usage.free,
            required,
        )

    def _list_ide_files(self) -> list[str]:
        if not self._data_dir:
            return []
        return sorted(
            set(glob.glob(os.path.join(self._data_dir, "*.IDE")))
            | set(glob.glob(os.path.join(self._data_dir, "*.ide")))
        )

    def _offload(self, output_dir: str) -> None:
        current = set(self._list_ide_files())
        new_files = sorted(current - self._files_before_start)
        if not new_files:
            raise RuntimeError("No new IDE recording appeared after remount.")
        if len(new_files) > 1:
            self._result.multiple_new_ide_files = True
            _LOG.warning("Found %d new IDE files; preserving all of them.", len(new_files))

        os.makedirs(output_dir, exist_ok=True)
        for source in new_files:
            destination = os.path.join(output_dir, os.path.basename(source))
            shutil.copy2(source, destination)
            if os.path.getsize(source) != os.path.getsize(destination):
                raise RuntimeError(f"Size mismatch after copying IDE to '{destination}'.")
            source_hash = _sha256(source)
            destination_hash = _sha256(destination)
            if source_hash != destination_hash:
                raise RuntimeError(f"SHA-256 mismatch after copying IDE to '{destination}'.")
            self._result.ide_paths.append(destination)
            self._result.ide_sha256_by_path[destination] = destination_hash
            _LOG.info("Verified enDAQ offload %s (sha256=%s).", destination, destination_hash)

            if self._config.delete_after_verified_offload:
                os.remove(source)
                self._result.recorder_files_deleted.append(source)
                _LOG.warning("Deleted verified recorder-side IDE: %s", source)

        self._result.ide_path = self._result.ide_paths[0]
        self._result.ide_sha256 = self._result.ide_sha256_by_path[self._result.ide_path]

def _serial_as_int(serial: str) -> int:
    digits = serial.lstrip("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0")
    if not digits:
        return 0
    return int(digits)


def _now_utc_us() -> int:
    return int(time.time() * 1_000_000)


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
