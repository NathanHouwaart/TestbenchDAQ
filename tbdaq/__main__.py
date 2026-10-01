"""Command-line entry point for TestbenchDAQ."""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

from tbdaq.config import (
    ConfigError,
    SessionConfig,
    session_config_from_machine_and_acquisition,
)


_ANSI_RED = "\033[31m"
_ANSI_YELLOW = "\033[33m"
_ANSI_RESET = "\033[0m"


def _terminal_supports_color(stream: Any) -> bool:
    return (
        hasattr(stream, "isatty")
        and stream.isatty()
        and "NO_COLOR" not in os.environ
        and os.environ.get("TERM", "") != "dumb"
    )


def _colored(text: str, color: str, stream: Any) -> str:
    if not _terminal_supports_color(stream):
        return text
    return f"{color}{text}{_ANSI_RESET}"


class _ConsoleFormatter(logging.Formatter):
    def __init__(self, *, use_color: bool) -> None:
        super().__init__(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        self._use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        if not self._use_color:
            return rendered
        if record.levelno >= logging.ERROR:
            return f"{_ANSI_RED}{rendered}{_ANSI_RESET}"
        if record.levelno >= logging.WARNING:
            return f"{_ANSI_YELLOW}{rendered}{_ANSI_RESET}"
        return rendered


def _setup_console_logging(verbose: int, quiet: int) -> None:
    if verbose:
        level = logging.DEBUG
    elif quiet >= 2:
        level = logging.ERROR
    elif quiet:
        level = logging.WARNING
    else:
        level = logging.INFO
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        _ConsoleFormatter(use_color=_terminal_supports_color(sys.stderr))
    )
    logging.basicConfig(level=level, handlers=[handler], force=True)


def _manifest_messages(manifest: dict[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    def add_unique(target: list[str], value: Any) -> None:
        if isinstance(value, str) and value and value not in target:
            target.append(value)

    for run in manifest.get("runs", []):
        for value in run.get("errors", []):
            add_unique(errors, value)
        for value in run.get("warnings", []):
            add_unique(warnings, value)
    abort_reason = manifest.get("abort_reason")
    generic_run_failure = (
        isinstance(abort_reason, str)
        and re.fullmatch(r"run_\d+ ended with status \w+\.", abort_reason) is not None
    )
    if not errors or not generic_run_failure:
        add_unique(errors, abort_reason)
    return errors, warnings


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


def _build_config(args: argparse.Namespace) -> SessionConfig:
    machine_path = Path(args.machine).expanduser().resolve() if args.machine else None
    acquisition_path = (
        Path(args.acquisition).expanduser().resolve() if args.acquisition else None
    )
    if machine_path is None or acquisition_path is None:
        raise ConfigError("--machine and --acquisition must be provided together.")
    return session_config_from_machine_and_acquisition(
        _load_json(machine_path), _load_json(acquisition_path), base_dir=machine_path.parent,
    )


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
    parser.add_argument("--machine", metavar="PATH", help="Machine installation JSON configuration")
    parser.add_argument("--acquisition", metavar="PATH", help="Portable acquisition JSON configuration")
    parser.add_argument(
        "--allow-local-output",
        action="store_true",
        help="Override the mandatory NFS output check for this run only",
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

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _make_parser()
    args = parser.parse_args(argv)
    _setup_console_logging(args.verbose, args.quiet)

    try:
        config = _build_config(args)
    except ConfigError as exc:
        print(
            _colored(f"Configuration error: {exc}", _ANSI_RED, sys.stderr),
            file=sys.stderr,
        )
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

        manifest = Session(config, allow_local_output=args.allow_local_output).run()
    except KeyboardInterrupt:
        print(_colored("Interrupted.", _ANSI_YELLOW, sys.stderr), file=sys.stderr)
        return 130
    except Exception as exc:
        logging.getLogger(__name__).exception("Unhandled session failure")
        print(
            _colored(f"Session failed: {exc}", _ANSI_RED, sys.stderr),
            file=sys.stderr,
        )
        return 1

    errors, warnings = _manifest_messages(manifest)
    print(f"Session ID: {manifest['session_id']}")
    status_line = f"Session status: {manifest['status']}"
    if manifest["status"] in {"failed", "aborted"}:
        status_line = _colored(status_line, _ANSI_RED, sys.stdout)
    elif manifest["status"] in {"partial", "interrupted"}:
        status_line = _colored(status_line, _ANSI_YELLOW, sys.stdout)
    print(status_line)
    print(f"Output: {manifest['session_dir']}")
    sys.stdout.flush()
    if warnings:
        print(_colored("Warnings:", _ANSI_YELLOW, sys.stderr), file=sys.stderr)
        for warning in warnings:
            print(_colored(f"  - {warning}", _ANSI_YELLOW, sys.stderr), file=sys.stderr)
    if errors:
        print(_colored("Failure reason(s):", _ANSI_RED, sys.stderr), file=sys.stderr)
        for error in errors:
            print(_colored(f"  - {error}", _ANSI_RED, sys.stderr), file=sys.stderr)
    return 0 if manifest["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
