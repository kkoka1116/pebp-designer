"""Build the downloadable PEBP Studio for this computer's system, smoke-test it, and zip it.

    cd pebp_demo
    .venv/bin/pip install -r requirements.txt -r packaging/requirements-build.txt   (+ requirements-app.txt on macOS)
    .venv/bin/python packaging/build.py            # -> dist/PEBP-Studio-<version>-<system>-<arch>.zip

The release workflow (.github/workflows/release.yml) runs this on macOS and Windows. The smoke test uses only
synthetic scans: it runs the frozen command line (synth, design) and starts the studio on 127.0.0.1 to check
that the page loads with its research-use notice.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG = HERE.parent
sys.path.insert(0, str(PKG))
from pebp import __version__                      # noqa: E402
from pebp.studio import desktop                   # noqa: E402

DIST, WORK = PKG / "dist", HERE / "build"


def system_tag() -> str:
    osname = {"darwin": "macos", "win32": "windows"}.get(sys.platform, sys.platform)
    arch = platform.machine().lower()
    arch = {"amd64": "x64", "x86_64": "x64" if osname != "macos" else "intel", "arm64": "arm64",
            "aarch64": "arm64"}.get(arch, arch)
    if osname == "macos" and arch == "arm64":
        arch = "apple-silicon"
    return f"{osname}-{arch}"


def make_icon() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    png = WORK / "icon.png"
    desktop._icon_png(png)
    if sys.platform == "darwin":
        desktop._icns(png, WORK / "PEBP Studio.icns")
    else:
        from PIL import Image
        Image.open(png).save(WORK / "PEBP Studio.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


def frozen_exe() -> Path:
    if sys.platform == "darwin":
        return DIST / "PEBP Studio.app" / "Contents" / "MacOS" / "PEBP Studio"
    return DIST / "PEBP Studio" / ("PEBP Studio.exe" if sys.platform == "win32" else "pebp-studio")


def run(cmd, **kw) -> str:
    print("$", " ".join(map(str, cmd)), flush=True)
    p = subprocess.run(list(map(str, cmd)), capture_output=True, text=True, **kw)
    if p.returncode != 0:
        print(p.stdout[-4000:], p.stderr[-4000:], sep="\n")
        raise SystemExit(f"failed ({p.returncode}): {cmd[1:3]}")
    return p.stdout


def smoke_test() -> None:
    exe = frozen_exe()
    out = run([exe, "--version"])
    assert __version__ in out and "not for clinical use" in out, out
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        run([exe, "synth", "--out", td / "case", "--preset", "prs"])
        run([exe, "design", td / "case" / "scan.stl", "--out", td / "case"])
        rec = json.loads((td / "case" / "design.json").read_text())
        ext = json.dumps(rec)
        assert "bioengineering12101063" in ext, "the published Tübingen extension did not load from the bundle"
        devices = sorted(p.name for p in (td / "case").glob("*.stl"))
        print("devices:", ", ".join(devices))
        assert {"prototype_1.stl", "prototype_2.stl", "prototype_3.stl"} <= set(devices), devices

        with socket.socket() as s:
            s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]
        srv = subprocess.Popen([str(exe), "studio", "--workspace", str(td / "ws"), "--port", str(port), "--no-browser"],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            page = ""
            for _ in range(60):
                try:
                    page = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2).read().decode()
                    break
                except OSError:
                    time.sleep(1)
            assert "Research use only" in page and __version__ in page, "studio page did not load"
            js = urllib.request.urlopen(f"http://127.0.0.1:{port}/static/app.js", timeout=5).read()
            assert len(js) > 1000
        finally:
            srv.terminate()
            try:
                srv.wait(10)
            except subprocess.TimeoutExpired:
                srv.kill()
    print("smoke test passed")


def package() -> Path:
    name = f"PEBP-Studio-{__version__}-{system_tag()}"
    zpath = DIST / f"{name}.zip"
    zpath.unlink(missing_ok=True)
    if sys.platform == "darwin":
        stage = DIST / name
        shutil.rmtree(stage, ignore_errors=True)
        stage.mkdir()
        # ditto keeps the bundle's symlinks and ad-hoc signature intact
        run(["ditto", DIST / "PEBP Studio.app", stage / "PEBP Studio.app"])
        for f in ("RESEARCH_USE_ONLY.txt",):
            shutil.copy(HERE / f, stage / f)
        shutil.copy(PKG.parent / "docs" / "INSTALL.md", stage / "INSTALL.md")
        run(["ditto", "-c", "-k", "--keepParent", stage, zpath])
    else:
        stage = DIST / name
        shutil.rmtree(stage, ignore_errors=True)
        shutil.copytree(DIST / "PEBP Studio", stage / "PEBP Studio")
        shutil.copy(HERE / "RESEARCH_USE_ONLY.txt", stage / "RESEARCH_USE_ONLY.txt")
        shutil.copy(PKG.parent / "docs" / "INSTALL.md", stage / "INSTALL.md")
        shutil.make_archive(str(zpath.with_suffix("")), "zip", DIST, name)
    print(f"wrote {zpath} ({zpath.stat().st_size / 1e6:.0f} MB)")
    return zpath


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skip-build", action="store_true", help="reuse dist/ from the last build")
    ap.add_argument("--skip-test", action="store_true")
    a = ap.parse_args()
    os.chdir(PKG)
    if not a.skip_build:
        make_icon()
        shutil.rmtree(DIST, ignore_errors=True)
        run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", DIST,
             "--workpath", WORK / "pyinstaller", HERE / "pebp_studio.spec"])
    if not a.skip_test:
        smoke_test()
    package()


if __name__ == "__main__":
    main()
