"""Entry point of the downloadable PEBP Studio (the PyInstaller bundle).

With no arguments it opens PEBP Studio: a starter folder in the user's home (walkthrough, intake sheet, two
synthetic sample scans, Cases/) and the studio on 127.0.0.1, in its own window on macOS and in the default
browser elsewhere. With arguments it is the `pebp` command line, which is also how the studio runs its builds
(pebp.runtime.pebp_command).
"""
from __future__ import annotations

import multiprocessing
import sys
from pathlib import Path


def _home() -> Path:
    from pebp.studio import desktop
    if sys.platform == "darwin" and not desktop.icloud_desktop_synced():
        return desktop.DEFAULT_HOME                          # ~/Desktop/PEBP Studio, as the source install
    # Windows Desktops are often redirected into OneDrive, and an iCloud Desktop syncs: real scans must not
    # leave the computer, so the folder goes straight in the home folder there.
    return Path.home() / desktop.APP_NAME


def _log_to_file() -> None:
    """A windowed macOS app has no terminal: keep its output in ~/Library/Logs/PEBP Studio.log."""
    from pebp.studio import desktop
    if sys.platform == "darwin" and (sys.stdout is None or not sys.stdout.isatty()):
        desktop.LOG.parent.mkdir(parents=True, exist_ok=True)
        f = open(desktop.LOG, "a", buffering=1, encoding="utf-8")
        sys.stdout = sys.stderr = f


def launch() -> int:
    from pebp import __version__
    from pebp.studio import desktop
    _log_to_file()
    home = _home()
    print(f"PEBP Studio {__version__}. Research use only, not for clinical use.", flush=True)
    try:
        desktop.make_starter(home)
    except Exception as e:                                   # the studio still opens without the samples
        print(f"could not finish the starter folder {home}: {e}", file=sys.stderr, flush=True)
    ws = home / "Cases"
    if sys.platform == "darwin":
        try:
            import webview  # noqa: F401
            return desktop.run_app(ws)
        except ImportError:
            pass
    from pebp.studio.server import serve
    print(f"Your cases are in {ws}\nKeep this window open while you work. Close it to stop PEBP Studio.", flush=True)
    serve(ws, port=desktop._free_port(8780), open_browser=True)
    return 0


def main() -> int:
    multiprocessing.freeze_support()
    for stream in (sys.stdout, sys.stderr):         # the studio reads its build steps' output as UTF-8
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = [a for a in sys.argv[1:] if not a.startswith("-psn_")]   # old macOS Finder launches add -psn_…
    if args:
        from pebp.cli import main as cli
        cli(args)
        return 0
    return launch()


if __name__ == "__main__":
    raise SystemExit(main())
