"""PEBP Studio as a desktop app: the same studio in its own window instead of a browser tab.

`python -m pebp app` starts the studio server on 127.0.0.1 and opens it in a native window
(pywebview: WKWebView on macOS). Closing the window stops the server. Nothing else changes:
the window talks to the same local server, under the same Host and header checks, so the
rules in docs/STUDIO.md hold unchanged.

`python -m pebp app --install` (macOS) writes a double-clickable "PEBP Studio.app" that runs this
checkout's virtual environment, and a starter folder with sample scans, an intake sheet and
the walkthrough (docs/STUDIO_WALKTHROUGH.md). The .app is a small launcher, not a frozen copy:
it always runs the code in this checkout, so `git pull` updates it.
"""
from __future__ import annotations

import os
import plistlib
import shutil
import socket
import subprocess
import sys
import threading
from pathlib import Path

from ..runtime import PKG_ROOT, REPO_ROOT, pebp_command, work_dir
DOCS = REPO_ROOT / "docs"
APP_NAME = "PEBP Studio"
BUNDLE_ID = "org.pebp.studio"
DEFAULT_PORT = 8781                                     # the browser studio uses 8780
DEFAULT_HOME = Path.home() / "Desktop" / APP_NAME
SUPPORT = Path.home() / "Library" / "Application Support" / APP_NAME
LOG = Path.home() / "Library" / "Logs" / f"{APP_NAME}.log"


def _free_port(preferred: int) -> int:
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]


def _macos_branding(icon: Path | None) -> None:
    """Name the menu bar and Dock entry "PEBP Studio" rather than "Python"."""
    try:
        from Foundation import NSBundle
        from AppKit import NSApplication, NSImage
        info = NSBundle.mainBundle().localizedInfoDictionary() or NSBundle.mainBundle().infoDictionary()
        if info is not None:
            info["CFBundleName"] = APP_NAME
        if icon and icon.is_file():
            img = NSImage.alloc().initWithContentsOfFile_(str(icon))
            if img is not None:
                NSApplication.sharedApplication().setApplicationIconImage_(img)
    except Exception:
        pass


def run_app(workspace: Path, port: int = DEFAULT_PORT, debug: bool = False) -> int:
    try:
        import webview
    except ImportError:
        print("PEBP Studio's app window needs pywebview. Install it into this virtual environment:\n"
              "  .venv/bin/pip install -r requirements-app.txt\n"
              "or run the same studio in the browser with:  python -m pebp studio", file=sys.stderr)
        return 2
    from .server import make_server

    workspace = Path(workspace).expanduser()
    workspace.mkdir(parents=True, exist_ok=True)
    httpd = make_server(workspace, _free_port(port))
    ws = httpd.RequestHandlerClass.ws
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    print(f"{APP_NAME}: {url} (workspace {ws.root}; this computer only)", flush=True)

    if sys.platform == "darwin":
        _macos_branding(SUPPORT / "icon.png")

    win = webview.create_window(APP_NAME, url, width=1440, height=920, min_size=(1000, 680), text_select=True)

    def closing():
        running = [j for j in ws.jobs.values() if j.get("status") == "running"]
        if running:
            return win.create_confirmation_dialog(
                "A build is still running",
                f"\"{running[0].get('kind', 'a build')}\" has not finished. If you quit now its files may be "
                "incomplete and the step will have to be built again. Quit anyway?")
        return True

    win.events.closing += closing
    webview.settings["ALLOW_DOWNLOADS"] = True              # download links open a Save dialog
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    SUPPORT.mkdir(parents=True, exist_ok=True)
    webview.start(private_mode=False, storage_path=str(SUPPORT / "webdata"), debug=debug)
    httpd.shutdown()
    httpd.server_close()
    return 0


# ---------------------------------------------------------------- install (macOS)

def _icon_png(path: Path) -> None:
    """A plain 1024 px icon: a palatal plate outline with its baton, drawn here (no external art)."""
    from PIL import Image, ImageDraw
    n = 1024
    im = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([64, 64, n - 64, n - 64], radius=200, fill=(47, 111, 237, 255))
    # the plate: a horseshoe seen from below
    d.ellipse([232, 170, 792, 690], fill=(255, 255, 255, 255))
    d.ellipse([352, 300, 672, 640], fill=(47, 111, 237, 255))
    d.rectangle([232, 560, 792, 700], fill=(47, 111, 237, 255))
    d.rounded_rectangle([232, 430, 352, 600], radius=50, fill=(255, 255, 255, 255))
    d.rounded_rectangle([672, 430, 792, 600], radius=50, fill=(255, 255, 255, 255))
    # the baton: down the midline from the posterior border
    d.rounded_rectangle([462, 470, 562, 880], radius=50, fill=(255, 214, 102, 255))
    im.save(path)


def _icns(png: Path, out: Path) -> bool:
    if not shutil.which("iconutil") or not shutil.which("sips"):
        return False
    iconset = out.with_suffix(".iconset")
    shutil.rmtree(iconset, ignore_errors=True)
    iconset.mkdir(parents=True)
    for s in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            px = s * scale
            name = f"icon_{s}x{s}{'@2x' if scale == 2 else ''}.png"
            subprocess.run(["sips", "-z", str(px), str(px), str(png), "--out", str(iconset / name)],
                           check=True, capture_output=True)
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(out)], check=True, capture_output=True)
    shutil.rmtree(iconset, ignore_errors=True)
    return True


def install_app(dest: Path, workspace: Path) -> Path:
    """Write "PEBP Studio.app" into dest. It launches this checkout's venv on workspace."""
    py = PKG_ROOT / ".venv" / "bin" / "python"
    if not py.exists():
        raise SystemExit(f"no virtual environment at {py}; make it first (CLAUDE.md, 'Setting up a new machine')")
    app = Path(dest).expanduser() / f"{APP_NAME}.app"
    if app.exists():
        shutil.rmtree(app)
    macos, res = app / "Contents" / "MacOS", app / "Contents" / "Resources"
    macos.mkdir(parents=True)
    res.mkdir(parents=True)

    SUPPORT.mkdir(parents=True, exist_ok=True)
    png = SUPPORT / "icon.png"
    _icon_png(png)
    has_icns = _icns(png, res / "AppIcon.icns")

    launcher = macos / "pebp-studio"
    launcher.write_text(
        "#!/bin/bash\n"
        "# PEBP Studio launcher, written by `python -m pebp app --install`. It runs the code in this checkout.\n"
        f"PEBP={_q(PKG_ROOT)}\n"
        f"WORKSPACE={_q(Path(workspace).expanduser())}\n"
        f"LOG={_q(LOG)}\n"
        'mkdir -p "$(dirname "$LOG")"\n'
        'cd "$PEBP" || { osascript -e "display alert \\"PEBP Studio\\" message \\"The pebp_demo folder has moved: $PEBP\\""; exit 1; }\n'
        'echo "--- $(date) ---" >> "$LOG"\n'
        'exec "$PEBP/.venv/bin/python" -m pebp app --workspace "$WORKSPACE" >> "$LOG" 2>&1\n')
    launcher.chmod(0o755)

    info = {
        "CFBundleName": APP_NAME, "CFBundleDisplayName": APP_NAME, "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleExecutable": launcher.name, "CFBundlePackageType": "APPL", "CFBundleVersion": "1",
        "CFBundleShortVersionString": "0.1", "LSMinimumSystemVersion": "11.0", "NSHighResolutionCapable": True,
        "LSApplicationCategoryType": "public.app-category.medical",
    }
    if has_icns:
        info["CFBundleIconFile"] = "AppIcon"
    with open(app / "Contents" / "Info.plist", "wb") as f:
        plistlib.dump(info, f)
    if shutil.which("touch"):
        subprocess.run(["touch", str(app)], check=False)   # make Finder pick up the icon
    return app


def _q(p: Path) -> str:
    return "'" + str(p).replace("'", "'\\''") + "'"


# ---------------------------------------------------------------- starter folder

def make_starter(home: Path, app: Path | None = None) -> Path:
    """The folder a clinician starts from: walkthrough, intake sheet, sample scans, their cases.

    Existing files are never overwritten except the walkthrough and intake sheet (regenerated from
    docs/), so cases and scans already in the folder are safe.
    """
    home = Path(home).expanduser()
    for sub in ("Cases", "Sample scans", "Scans to design"):
        (home / sub).mkdir(parents=True, exist_ok=True)
    shutil.copyfile(DOCS / "STUDIO_WALKTHROUGH.md", home / "1 START HERE - walkthrough.md")
    shutil.copyfile(DOCS / "STUDIO_INTAKE.md", home / "2 Case intake sheet.md")

    samples = home / "Sample scans"
    for name, extra in (("synthetic cleft palate", []), ("synthetic no cleft", ["--no-cleft"])):
        out = samples / f"{name}.stl"
        if out.exists():
            continue
        tmp = samples / f".{name}.tmp"
        cmd = [*pebp_command(), "synth", "--out", str(tmp), "--preset", "prs",
               "--unoriented", "--asym", "1.0", "--seed", "7", *extra]
        subprocess.run(cmd, cwd=work_dir(), check=True, capture_output=True)
        shutil.move(str(tmp / "scan.stl"), out)
        shutil.rmtree(tmp, ignore_errors=True)
    (samples / "ABOUT THESE SCANS.txt").write_text(
        "These two scans are synthetic. They are made by `pebp synth` from published neonatal palate\n"
        "measurements (Willershausen 2024) and rotated at random, like a raw intraoral scan.\n"
        "They are not from any patient, so they are safe to share. Use them to learn the app.\n")
    (home / "Scans to design" / "READ ME.txt").write_text(
        "Put a real intraoral scan (STL) here before you add it in PEBP Studio.\n\n"
        "Real scans are patient data. They stay on this computer: do not email, upload, sync or paste\n"
        "them anywhere, and do not put this folder in iCloud Drive, Dropbox or OneDrive.\n"
        "When you add a scan in the app it is copied into the case's folder under Cases/.\n")
    if app is not None:
        link = home / f"{APP_NAME}.app"
        if link.is_symlink() or link.exists():
            if link.is_symlink():
                link.unlink()
        if not link.exists():
            os.symlink(app, link)
    return home


def icloud_desktop_synced() -> bool:
    """True when macOS 'Desktop & Documents Folders' sync to iCloud is on (Desktop lives in iCloud Drive)."""
    if sys.platform != "darwin":
        return False
    cloud_desktop = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "Desktop"
    return cloud_desktop.is_dir()
