#!/usr/bin/env python3
"""Convenience entry point for ``python scripts/plot_session.py SESSION``."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tbdaq.plotting import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
