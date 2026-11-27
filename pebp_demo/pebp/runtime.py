"""Where pebp runs from: a source checkout, or the frozen PEBP Studio download.

The downloadable app (packaging/, built by .github/workflows/release.yml) is one PyInstaller bundle. Inside it
there is no `python -m pebp`: the bundle's own executable runs the command line when given arguments and opens
the studio when given none (packaging/pebp_studio_entry.py). Everything that starts a pebp command in a
subprocess, or reads a file shipped beside the code, asks this module instead of working it out from __file__.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))

# Source checkout: <repo>/pebp_demo/pebp/runtime.py. Frozen: PyInstaller unpacks data under sys._MEIPASS,
# laid out as in the repo (external/..., docs/...).
REPO_ROOT = Path(getattr(sys, "_MEIPASS", "")) if FROZEN else Path(__file__).resolve().parents[2]
PKG_ROOT = REPO_ROOT if FROZEN else REPO_ROOT / "pebp_demo"


def pebp_command() -> list[str]:
    """The argv prefix that runs a pebp subcommand in a new process."""
    return [sys.executable] if FROZEN else [sys.executable, "-m", "pebp"]


def work_dir() -> Path:
    """The working directory for those subprocesses (the bundle may sit on a read-only volume)."""
    return Path.home() if FROZEN else PKG_ROOT


def child_env() -> dict[str, str]:
    """Environment for those subprocesses: their output is read back as UTF-8 (Windows defaults to cp1252)."""
    return {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
