"""Command-line entry point for TestbenchDAQ."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

from tbdaq.config import ConfigError, SessionConfig, session_config_from_mapping


def _setup_console_logging(verbose: int, quiet: int) -> None:
    if verbose:
        level = logging.DEBUG
    elif quiet >= 2:
        level = logging.ERROR
    elif quiet:
        level = logging.WARNING
    else:
        level = logging.INFO
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        level=level,
    )


def _load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as handle:
            value = json.load(handle)
    except OSError as exc:
        raise ConfigError(f"Could not read config '{path}': {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON in '{path}': {exc}") from exc
    if not isinstance(value, dict):
        raise ConfigError("The configuration root must be a JSON object.")
    return value


def _set_if_not_none(target: dict[str, Any], key: str, value: Any) -> None:
    if value is not None:
        target[key] = value


def _build_config(args: argparse.Namespace) -> SessionConfig:
    config_path = Path(args.config).expanduser().resolve() if args.config else None
    values: dict[str, Any] = _load_json(config_path) if config_path else {}
    values = deepcopy(values)
    base_dir = config_path.parent if config_path else Path.cwd()

    _set_if_not_none(values, "mode", args.mode)
    _set_if_not_none(values, "name", args.name)
    _set_if_not_none(values, "output_root", args.output_root)
    _set_if_not_none(values, "run_count", args.run_count)
    if args.manual:
        values["run_duration_s"] = None
    else:
        _set_if_not_none(values, "run_duration_s", args.run_duration_s)
    _set_if_not_none(values, "run_period_s", args.run_period_s)
    _set_if_not_none(values, "missed_start_tolerance_s", args.missed_start_tolerance_s)
    _set_if_not_none(values, "missed_start_policy", args.missed_start_policy)
    _set_if_not_none(values, "allow_partial", args.allow_partial)

    gator = values.setdefault("gator", {})
    if not isinstance(gator, dict):
        raise ConfigError("gator must be a JSON object.")
    _set_if_not_none(gator, "enabled", args.gator_enabled)
    _set_if_not_none(gator, "binary_path", args.gator_binary)
    _set_if_not_none(gator, "library_path", args.gator_library)
    _set_if_not_none(gator, "channel", args.gator_channel)
    _set_if_not_none(gator, "samplerate", args.gator_samplerate)
    _set_if_not_none(gator, "fullscale", args.gator_fullscale)
    _set_if_not_none(gator, "threshold", args.gator_threshold)
    _set_if_not_none(gator, "start_timeout_s", args.gator_start_timeout_s)
    _set_if_not_none(gator, "stop_timeout_s", args.gator_stop_timeout_s)

    endaq = values.setdefault("endaq", {})
    if not isinstance(endaq, dict):
        raise ConfigError("endaq must be a JSON object.")
    _set_if_not_none(endaq, "enabled", args.endaq_enabled)
    _set_if_not_none(endaq, "serial", args.endaq_serial)
    _set_if_not_none(endaq, "model", args.endaq_model)
    _set_if_not_none(endaq, "mount_path", args.endaq_mount_path)
    _set_if_not_none(endaq, "ide_converter_path", args.endaq_ide_converter)
    _set_if_not_none(endaq, "command_timeout_s", args.endaq_command_timeout_s)
    _set_if_not_none(endaq, "remount_timeout_s", args.endaq_remount_timeout_s)
    _set_if_not_none(endaq, "minimum_free_space_bytes", args.endaq_minimum_free_space_bytes)
    _set_if_not_none(endaq, "estimated_bytes_per_second", args.endaq_estimated_bytes_per_second)
    _set_if_not_none(endaq, "recording_time_limit_s", args.endaq_recording_time_limit_s)
    _set_if_not_none(endaq, "recording_size_limit_bytes", args.endaq_recording_size_limit_bytes)
    _set_if_not_none(
        endaq,
        "delete_after_verified_offload",
        args.endaq_delete_after_verified_offload,
    )

    return session_config_from_mapping(values, base_dir=base_dir)


def _make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tbdaq",
        description="Coordinated Gator and enDAQ test-bench acquisition",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "action",
        nargs="?",
        choices=("run", "show-config", "endaq-info", "endaq-stop"),
        default="run",
        help="Run, show resolved config, inspect enDAQ, or stop/remount an enDAQ",
    )
    parser.add_argument("--config", metavar="PATH", help="JSON configuration file")
    parser.add_argument("--name", metavar="NAME", help="Human-readable session name prefix")
    parser.add_argument("--mode", choices=("diagnostic", "prognostic"))
    parser.add_argument("--output-root", metavar="DIR")
    parser.add_argument("--run-count", type=int, metavar="N")
    duration = parser.add_mutually_exclusive_group()
    duration.add_argument("--run-duration-s", type=float, metavar="SECONDS")
    duration.add_argument(
        "--manual",
        action="store_true",
        help="Run until Enter is pressed (diagnostic mode only)",
    )
    parser.add_argument("--run-period-s", type=float, metavar="SECONDS")
    parser.add_argument("--missed-start-tolerance-s", type=float, metavar="SECONDS")
    parser.add_argument(
        "--missed-start-policy",
        choices=("abort", "start_late"),
        help="Abort a late prognostic run or start it immediately with a warning",
    )
    parser.add_argument(
        "--allow-partial",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Continue when an enabled sensor is unavailable or fails to start",
    )
    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument(
        "-v", "--verbose", action="count", default=0,
        help="Show debug output; includes native Gator library messages",
    )
    verbosity.add_argument(
        "-q", "--quiet", action="count", default=0,
        help="Reduce output (-q warnings/errors, -qq errors only)",
    )

    gator = parser.add_argument_group("Gator")
    gator.add_argument(
        "--gator",
        dest="gator_enabled",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable or disable Gator acquisition",
    )
    gator.add_argument("--gator-binary", metavar="PATH")
    gator.add_argument("--gator-library", metavar="DIR")
    gator.add_argument("--gator-channel", type=int, metavar="1-8")
    gator.add_argument(
        "--gator-samplerate",
        type=int,
        choices=(1000, 5000, 10000, 19000),
        metavar="HZ",
    )
    gator.add_argument("--gator-fullscale", type=int, metavar="8-127")
    gator.add_argument("--gator-threshold", type=float, metavar="0-1")
    gator.add_argument("--gator-start-timeout-s", type=float, metavar="SECONDS")
    gator.add_argument("--gator-stop-timeout-s", type=float, metavar="SECONDS")

    endaq = parser.add_argument_group("enDAQ")
    endaq.add_argument(
        "--endaq",
        dest="endaq_enabled",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable or disable enDAQ acquisition",
    )
    endaq.add_argument("--endaq-serial", metavar="SERIAL")
    endaq.add_argument("--endaq-model", metavar="MODEL")
    endaq.add_argument("--endaq-mount-path", metavar="PATH")
    endaq.add_argument(
        "--endaq-ide-converter",
        metavar="COMMAND_OR_PATH",
        help="Deprecated compatibility option; direct IDE export is built in",
    )
    endaq.add_argument("--endaq-command-timeout-s", type=float, metavar="SECONDS")
    endaq.add_argument("--endaq-remount-timeout-s", type=float, metavar="SECONDS")
    endaq.add_argument("--endaq-minimum-free-space-bytes", type=int, metavar="BYTES")
    endaq.add_argument("--endaq-estimated-bytes-per-second", type=int, metavar="BYTES")
    endaq.add_argument("--endaq-recording-time-limit-s", type=int, metavar="SECONDS")
    endaq.add_argument("--endaq-recording-size-limit-bytes", type=int, metavar="BYTES")
    endaq.add_argument(
        "--endaq-delete-after-verified-offload",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Delete recorder-side IDE only after size and SHA-256 verification",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _make_parser()
    args = parser.parse_args(argv)
    _setup_console_logging(args.verbose, args.quiet)

    try:
        config = _build_config(args)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    try:
        if args.action == "show-config":
            print(json.dumps(config.to_dict(), indent=2, sort_keys=True))
            return 0
        if args.action != "run":
            if not config.endaq.enabled:
                raise ConfigError(
                    f"endaq.enabled must be true for the {args.action} action."
                )
            from tbdaq.adapters.endaq import EndaqAdapter

            adapter = EndaqAdapter(config.endaq, run_duration_s=config.run_duration_s)
            if args.action == "endaq-info":
                print(json.dumps(adapter.describe(), indent=2, sort_keys=True))
            else:
                print(adapter.stop_recording_and_remount())
            return 0

        from tbdaq.session import Session

        manifest = Session(config).run()
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except Exception as exc:
        logging.getLogger(__name__).exception("Unhandled session failure")
        print(f"Session failed: {exc}", file=sys.stderr)
        return 1

    print(f"Session ID: {manifest['session_id']}")
    print(f"Session status: {manifest['status']}")
    print(f"Output: {manifest['session_dir']}")
    return 0 if manifest["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
