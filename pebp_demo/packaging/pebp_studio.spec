# PyInstaller spec for the downloadable PEBP Studio. Run through packaging/build.py, which makes the icon first
# and zips the result:  .venv/bin/python packaging/build.py
# One folder (not one file): a one-file build unpacks itself on every start and every build step the studio runs.
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

HERE = Path(SPECPATH)                       # pebp_demo/packaging
PKG = HERE.parent                           # pebp_demo
REPO = PKG.parent
sys.path.insert(0, str(PKG))
from pebp import __version__                # noqa: E402

datas = [
    (str(PKG / "pebp" / "studio" / "static"), "pebp/studio/static"),
    (str(REPO / "external" / "tuebingen_supplement"), "external/tuebingen_supplement"),
    (str(REPO / "docs" / "STUDIO_WALKTHROUGH.md"), "docs"),
    (str(REPO / "docs" / "STUDIO_INTAKE.md"), "docs"),
    (str(REPO / "LICENSE.md"), "."),
    (str(REPO / "NOTICE"), "."),
    (str(HERE / "RESEARCH_USE_ONLY.txt"), "."),
]
datas += collect_data_files("trimesh")

hidden = collect_submodules("pebp") + collect_submodules("trimesh") + ["manifold3d", "shapely", "networkx",
                                                                        "scipy.spatial", "scipy.sparse.csgraph"]
if sys.platform == "darwin":
    hidden += collect_submodules("webview")

a = Analysis(
    [str(HERE / "pebp_studio_entry.py")],
    pathex=[str(PKG)],
    datas=datas,
    hiddenimports=hidden,
    excludes=["tkinter", "PyQt5", "PyQt6", "PySide2", "PySide6", "IPython", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

icon = HERE / "build" / ("PEBP Studio.icns" if sys.platform == "darwin" else "PEBP Studio.ico")
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="PEBP Studio" if sys.platform != "linux" else "pebp-studio",
    console=sys.platform != "darwin",       # Windows/Linux: the console window is the "close me to stop" handle
    icon=str(icon) if icon.exists() else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="PEBP Studio")

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="PEBP Studio.app",
        icon=str(icon) if icon.exists() else None,
        bundle_identifier="org.pebp.studio",
        version=__version__,
        info_plist={
            "CFBundleDisplayName": "PEBP Studio",
            "CFBundleShortVersionString": __version__,
            "CFBundleVersion": __version__,
            "LSMinimumSystemVersion": "11.0",
            "NSHighResolutionCapable": True,
            "LSApplicationCategoryType": "public.app-category.medical",
            "NSHumanReadableCopyright": "PolyForm Noncommercial 1.0.0. Research use only, not for clinical use.",
        },
    )
