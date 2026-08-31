"""TestbenchDAQ CLI entry point."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from tbdaq.config import EndaqConfig, GatorConfig, SessionConfig
from tbdaq.session import Session


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        level=level,
    )


def _load_json(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _build_config(args: argparse.Namespace) -> SessionConfig:
    base: dict[str, Any] = {}
    if args.config:
        base = _load_json(args.config)
        # Strip comment keys
        base = {k: v for k, v in base.items() if not k.startswith("_")}

    def get(key: str, default: Any = None) -> Any:
        cli_val = getattr(args, key, None)
        return cli_val if cli_val is not None else base.get(key, default)

    gator_src = base.get("gator", {}) or {}
    gator = GatorConfig(
        enabled=get("gator_enabled", gator_src.get("enabled", True)),
        binary_path=get("gator_binary", gator_src.get("binary_path", "")),
        library_path=get("gator_library", gator_src.get("library_path", "")),
        channel=get("gator_channel", gator_src.get("channel", 8)),
        samplerate=get("gator_samplerate", gator_src.get("samplerate")),
        fullscale=get("gator_fullscale", gator_src.get("fullscale")),
        threshold=get("gator_threshold", gator_src.get("threshold")),
    )

    endaq_src = base.get("endaq", {}) or {}
    endaq = EndaqConfig(
        enabled=get("endaq_enabled", endaq_src.get("enabled", True)),
        ide2csv_path=get("ide2csv", endaq_src.get("ide2csv_path")),
    )

    return SessionConfig(
        output_root=get("output_root", "./csv-output"),
        run_count=int(get("run_count", 1)),
        run_duration_s=get("run_duration_s"),
        run_period_s=get("run_period_s"),
        gator=gator,
        endaq=endaq,
    )


def _make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tbdaq",
        description="TestbenchDAQ — unified Gator (C++) + Endaq (Python) acquisition",
    )
    p.add_argument("--config", metavar="PATH", help="JSON config file (see config_example.json)")
    p.add_argument("--output-root", dest="output_root", metavar="DIR", help="Output root directory")
    p.add_argument("--run-count", dest="run_count", type=int, metavar="N", help="Number of runs")
    p.add_argument("--run-duration-s", dest="run_duration_s", type=float, metavar="S",
                   help="Run duration in seconds (omit for manual stop)")
    p.add_argument("--run-period-s", dest="run_period_s", type=float, metavar="S",
                   help="Start-to-start period between runs")
    p.add_argument("--verbose", action="store_true", help="Verbose logging")

    g = p.add_argument_group("Gator (C++ binary)")
    g.add_argument("--gator", dest="gator_enabled", action="store_true", default=None)
    g.add_argument("--no-gator", dest="gator_enabled", action="store_false")
    g.add_argument("--gator-binary", dest="gator_binary", metavar="PATH")
    g.add_argument("--gator-library", dest="gator_library", metavar="DIR",
                   help="Directory containing libgtrlib .so (prepended to LD_LIBRARY_PATH)")
    g.add_argument("--gator-channel", dest="gator_channel", type=int, metavar="N")
    g.add_argument("--gator-samplerate", dest="gator_samplerate", type=int, metavar="HZ",
                   choices=[1000, 5000, 10000, 19000])
    g.add_argument("--gator-fullscale", dest="gator_fullscale", type=int, metavar="N")
    g.add_argument("--gator-threshold", dest="gator_threshold", type=float, metavar="F")

    e = p.add_argument_group("Endaq")
    e.add_argument("--endaq", dest="endaq_enabled", action="store_true", default=None)
    e.add_argument("--no-endaq", dest="endaq_enabled", action="store_false")
    e.add_argument("--ide2csv", dest="ide2csv", metavar="PATH",
                   help="Path to ide2csv executable for IDE→CSV conversion")

    return p


def main(argv: list[str] | None = None) -> int:
    parser = _make_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)

    try:
        config = _build_config(args)
    except Exception as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    if not config.gator.enabled and not config.endaq.enabled:
        print("Both gator and endaq are disabled — nothing to do.", file=sys.stderr)
        return 1

    session = Session(config)
    manifest = session.run()

    status = "success" if all(r.get("status") == "success" for r in manifest.get("runs", [])) else "partial/failed"
    print(f"\nSession status: {status}")
    return 0 if status == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
