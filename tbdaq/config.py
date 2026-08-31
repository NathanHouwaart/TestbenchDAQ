"""Typed configuration and validation for TestbenchDAQ."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
import re
from typing import Any, Mapping, Optional


class ConfigError(ValueError):
    """Raised when a configuration cannot describe a safe session."""


@dataclass
class GatorConfig:
    enabled: bool = False
    binary_path: str = "./gator_recorder/build/gator_recorder"
    library_path: str = ""
    channel: int = 8
    samplerate: Optional[int] = None
    fullscale: Optional[int] = None
    threshold: Optional[float] = None
    start_timeout_s: float = 30.0
    stop_timeout_s: float = 10.0


@dataclass
class EndaqChannelConfig:
    enabled: Optional[bool] = None
    sample_rate_hz: Optional[float] = None


@dataclass
class EndaqConfig:
    enabled: bool = False
    serial: Optional[str] = None
    model: Optional[str] = None
    mount_path: Optional[str] = None
    ide_converter_path: Optional[str] = None
    command_timeout_s: float = 30.0
    remount_timeout_s: float = 120.0
    minimum_free_space_bytes: int = 1_073_741_824
    estimated_bytes_per_second: Optional[int] = None
    recording_time_limit_s: Optional[int] = None
    recording_size_limit_bytes: Optional[int] = None
    delete_after_verified_offload: bool = False
    channels: dict[int, EndaqChannelConfig] = field(default_factory=dict)


@dataclass
class SessionConfig:
    name: Optional[str] = None
    mode: str = "diagnostic"
    output_root: str = "./csv-output"
    run_count: int = 1
    run_duration_s: Optional[float] = None
    run_period_s: Optional[float] = None
    missed_start_tolerance_s: float = 2.0
    missed_start_policy: str = "start_late"
    allow_partial: bool = False
    gator: GatorConfig = field(default_factory=GatorConfig)
    endaq: EndaqConfig = field(default_factory=EndaqConfig)

    def enabled_families(self) -> tuple[str, ...]:
        families: list[str] = []
        if self.gator.enabled:
            families.append("gator")
        if self.endaq.enabled:
            families.append("endaq")
        return tuple(families)

    def validate(self) -> None:
        self.mode = self.mode.strip().lower()
        self.missed_start_policy = self.missed_start_policy.strip().lower()
        if self.name is not None:
            self.name = self.name.strip()
            if not self.name:
                self.name = None
            elif len(self.name) > 80 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _.-]*", self.name):
                raise ConfigError(
                    "name must be 1-80 characters using letters, numbers, spaces, '.', '_' or '-'."
                )
        if self.mode not in {"diagnostic", "prognostic"}:
            raise ConfigError("mode must be 'diagnostic' or 'prognostic'.")
        if not self.output_root.strip():
            raise ConfigError("output_root must not be empty.")
        if self.run_count < 1:
            raise ConfigError("run_count must be >= 1.")
        if self.run_duration_s is not None and self.run_duration_s <= 0:
            raise ConfigError("run_duration_s must be > 0 when provided.")
        if self.run_period_s is not None and self.run_period_s <= 0:
            raise ConfigError("run_period_s must be > 0 when provided.")
        if self.missed_start_tolerance_s < 0:
            raise ConfigError("missed_start_tolerance_s must be >= 0.")
        if self.missed_start_policy not in {"abort", "start_late"}:
            raise ConfigError("missed_start_policy must be 'abort' or 'start_late'.")
        if not self.enabled_families():
            raise ConfigError("Enable at least one sensor family.")

        if self.mode == "diagnostic":
            if self.run_count != 1:
                raise ConfigError("diagnostic mode supports exactly one run.")
        else:
            if self.run_duration_s is None:
                raise ConfigError("prognostic mode requires run_duration_s.")
            if self.run_period_s is None:
                raise ConfigError("prognostic mode requires run_period_s.")
            if self.run_period_s < self.run_duration_s:
                raise ConfigError("run_period_s must be >= run_duration_s.")

        if self.gator.enabled:
            if not self.gator.binary_path.strip():
                raise ConfigError("gator.binary_path is required when Gator is enabled.")
            if not 1 <= self.gator.channel <= 8:
                raise ConfigError("gator.channel must be between 1 and 8.")
            if self.gator.samplerate not in {None, 1000, 5000, 10000, 19000}:
                raise ConfigError("gator.samplerate must be 1000, 5000, 10000, or 19000 Hz.")
            if self.gator.fullscale is not None and not 8 <= self.gator.fullscale <= 127:
                raise ConfigError("gator.fullscale must be between 8 and 127.")
            if self.gator.threshold is not None and not 0 <= self.gator.threshold <= 1:
                raise ConfigError("gator.threshold must be between 0 and 1.")
            if self.gator.start_timeout_s <= 0:
                raise ConfigError("gator.start_timeout_s must be > 0.")
            if self.gator.stop_timeout_s <= 0:
                raise ConfigError("gator.stop_timeout_s must be > 0.")

        if self.endaq.enabled:
            if self.endaq.command_timeout_s <= 0:
                raise ConfigError("endaq.command_timeout_s must be > 0.")
            if self.endaq.remount_timeout_s <= 0:
                raise ConfigError("endaq.remount_timeout_s must be > 0.")
            if self.endaq.minimum_free_space_bytes < 0:
                raise ConfigError("endaq.minimum_free_space_bytes must be >= 0.")
            if (
                self.endaq.estimated_bytes_per_second is not None
                and self.endaq.estimated_bytes_per_second <= 0
            ):
                raise ConfigError("endaq.estimated_bytes_per_second must be > 0.")
            if (
                self.endaq.recording_time_limit_s is not None
                and self.endaq.recording_time_limit_s < 0
            ):
                raise ConfigError("endaq.recording_time_limit_s must be >= 0.")
            if (
                self.endaq.recording_size_limit_bytes is not None
                and self.endaq.recording_size_limit_bytes < 0
            ):
                raise ConfigError("endaq.recording_size_limit_bytes must be >= 0.")
            for channel_id, channel in self.endaq.channels.items():
                if channel_id < 0:
                    raise ConfigError("endaq channel IDs must be >= 0.")
                if channel.sample_rate_hz is not None and channel.sample_rate_hz <= 0:
                    raise ConfigError(
                        f"endaq.channels.{channel_id}.sample_rate_hz must be > 0."
                    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_SESSION_KEYS = {
    "name", "mode", "output_root", "run_count", "run_duration_s", "run_period_s",
    "missed_start_tolerance_s", "missed_start_policy", "allow_partial", "gator", "endaq",
}
_GATOR_KEYS = {
    "enabled", "binary_path", "library_path", "channel",
    "samplerate", "fullscale", "threshold", "start_timeout_s", "stop_timeout_s",
}
_ENDAQ_KEYS = {
    "enabled", "serial", "model", "mount_path", "ide_converter_path",
    "command_timeout_s", "remount_timeout_s", "minimum_free_space_bytes",
    "estimated_bytes_per_second", "recording_time_limit_s",
    "recording_size_limit_bytes", "delete_after_verified_offload", "channels",
}
_ENDAQ_CHANNEL_KEYS = {"enabled", "sample_rate_hz"}


def _without_comments(values: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if not key.startswith("_")}


def _reject_unknown(values: Mapping[str, Any], allowed: set[str], section: str) -> None:
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise ConfigError(f"Unknown {section} configuration key(s): {', '.join(unknown)}")


def _section(values: Mapping[str, Any], key: str) -> dict[str, Any]:
    raw = values.get(key, {})
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ConfigError(f"{key} must be a JSON object.")
    return _without_comments(raw)


def session_config_from_mapping(
    values: Mapping[str, Any],
    *,
    base_dir: Path,
) -> SessionConfig:
    """Build and validate a config, resolving paths relative to base_dir."""
    clean = _without_comments(values)
    _reject_unknown(clean, _SESSION_KEYS, "top-level")
    gator_values = _section(clean, "gator")
    endaq_values = _section(clean, "endaq")
    _reject_unknown(gator_values, _GATOR_KEYS, "gator")
    _reject_unknown(endaq_values, _ENDAQ_KEYS, "endaq")

    try:
        gator = GatorConfig(**gator_values)
        raw_channels = endaq_values.pop("channels", {})
        if not isinstance(raw_channels, Mapping):
            raise ConfigError("endaq.channels must be a JSON object keyed by channel ID.")
        channels: dict[int, EndaqChannelConfig] = {}
        for raw_id, raw_settings in raw_channels.items():
            try:
                channel_id = int(raw_id)
            except (TypeError, ValueError) as exc:
                raise ConfigError(f"Invalid enDAQ channel ID: {raw_id!r}.") from exc
            if not isinstance(raw_settings, Mapping):
                raise ConfigError(f"endaq.channels.{raw_id} must be a JSON object.")
            settings = _without_comments(raw_settings)
            _reject_unknown(settings, _ENDAQ_CHANNEL_KEYS, f"endaq.channels.{raw_id}")
            channels[channel_id] = EndaqChannelConfig(**settings)
        endaq = EndaqConfig(**endaq_values, channels=channels)
        session_values = {key: value for key, value in clean.items() if key not in {"gator", "endaq"}}
        config = SessionConfig(**session_values, gator=gator, endaq=endaq)
    except TypeError as exc:
        raise ConfigError(str(exc)) from exc

    config.output_root = _resolve_path(config.output_root, base_dir)
    config.gator.binary_path = _resolve_path(config.gator.binary_path, base_dir)
    if config.gator.library_path:
        config.gator.library_path = _resolve_path(config.gator.library_path, base_dir)
    if config.endaq.mount_path:
        config.endaq.mount_path = _resolve_path(config.endaq.mount_path, base_dir)
    if config.endaq.ide_converter_path:
        config.endaq.ide_converter_path = _resolve_executable(
            config.endaq.ide_converter_path, base_dir
        )

    config.validate()
    return config


def _resolve_path(value: str, base_dir: Path) -> str:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return str(path.resolve())


def _resolve_executable(value: str, base_dir: Path) -> str:
    # Bare command names (for example ideexport) are resolved through PATH by
    # the adapter. Values containing a path separator are config-relative.
    if "/" not in value and "\\" not in value:
        return value
    return _resolve_path(value, base_dir)
