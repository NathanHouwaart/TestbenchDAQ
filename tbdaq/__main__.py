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


def _setup_console_logging(verbose: bool, quiet: bool) -> None:
    level = logging.DEBUG if verbose else logging.WARNING if quiet else logging.INFO
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
    _set_if_not_none(values, "output_root", args.output_root)
    _set_if_not_none(values, "run_count", args.run_count)
    if args.manual:
        values["run_duration_s"] = None
    else:
        _set_if_not_none(values, "run_duration_s", args.run_duration_s)
    _set_if_not_none(values, "run_period_s", args.run_period_s)
    _set_if_not_none(values, "missed_start_tolerance_s", args.missed_start_tolerance_s)
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

    return session_config_from_mapping(values, base_dir=base_dir)


def _make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tbdaq",
        description="Coordinated Gator and enDAQ test-bench acquisition",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--config", metavar="PATH", help="JSON configuration file")
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
        "--allow-partial",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Continue when an enabled sensor is unavailable or fails to start",
    )
    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument("--verbose", action="store_true")
    verbosity.add_argument("--quiet", action="store_true")

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
    endaq.add_argument("--endaq-ide-converter", metavar="COMMAND_OR_PATH")
    endaq.add_argument("--endaq-command-timeout-s", type=float, metavar="SECONDS")
    endaq.add_argument("--endaq-remount-timeout-s", type=float, metavar="SECONDS")
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
