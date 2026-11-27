"""Graded plate without and with border relief, on synthetic palates only.

    .venv/bin/python scripts/relief_compare.py            (about 20 s)

Writes out/relief_compare/relief_compare.png and relief_compare.json. Never touches real patient scans.
"""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import trimesh
from shapely.geometry import Polygon

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "out" / "relief_compare"

from pebp.landmarks import find_landmarks  # noqa: E402
from pebp.plate import PlateParams, design_base_plate  # noqa: E402

CASES = [("Synthetic Robin palate, cleft", []), ("Synthetic Robin palate, no cleft", ["--no-cleft"])]


def section(mesh, origin, normal, axis):
    s = mesh.section(plane_origin=origin, plane_normal=normal)
    return [] if s is None else [s.vertices[e.points][:, [axis, 2]] for e in s.entities]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    fig, ax = plt.subplots(len(CASES), 3, figsize=(15, 4.6 * len(CASES)), constrained_layout=True)
    for i, (title, extra) in enumerate(CASES):
        d = OUT / f"case{i + 1}"
        subprocess.run([sys.executable, "-m", "pebp", "synth", "--preset", "prs", "--out", str(d), *extra],
                       cwd=ROOT, check=True, capture_output=True)
        om, lm, hf = find_landmarks(trimesh.load(d / "scan.stl", force="mesh"))
        pg = PlateParams(thickness=2.5, profile="graded", thin_thickness=1.5)
        mg, _, og, ig = design_base_plate(lm, hf, pg)
        mr, _, orl, ir = design_base_plate(lm, hf, replace(pg, border_relief=True))
        br = ir["border_relief"]
        rows.append({"case": title, "area_mm2": [round(Polygon(og).area, 1), round(Polygon(orl).area, 1)],
                     "volume_mm3": [ig["volume_mm3"], ir["volume_mm3"]], "flange_trimmed_mm2": br["vestibular_cap"]["trimmed_mm2"],
                     "frenulum_notch_removed_mm2": br["labial_frenulum_notch"]["removed_mm2"],
                     "tuberosity_relief": br["tuberosity_relief"], "watertight": [ig["watertight"], ir["watertight"]]})
        a = ax[i, 0]
        a.fill(*np.vstack([og, og[:1]]).T, color="#e0b84a", alpha=0.35, label="graded, no relief")
        a.plot(*np.vstack([orl, orl[:1]]).T, color="#2f6fed", lw=1.5, label="with border relief")
        a.plot(lm.crest_ring[:, 0], lm.crest_ring[:, 1], color="k", lw=0.8, ls="--", label="ridge crest")
        r = br["tuberosity_relief"]["radius_mm"]
        for c in br["tuberosity_relief"]["centres"]:
            a.add_patch(plt.Circle(c, r, fill=False, color="#d33", lw=1.0))
        a.plot([], [], color="#d33", lw=1.0, label=f"tuberosity relief ({br['tuberosity_relief']['depth_mm']} mm)")
        a.set_aspect("equal"); a.invert_xaxis(); a.legend(fontsize=8, loc="lower center")
        a.set_title(f"{title}\nplan: outline before and after", fontsize=10)
        a.set_xlabel("x, patient's right (mm)"); a.set_ylabel("y, anterior (mm)")
        y0 = 0.5 * (lm.apex[1] + 0.5 * (lm.tuberosity_l[1] + lm.tuberosity_r[1]))
        a = ax[i, 1]
        for m, col, lab in [(om, "#9aa3ad", "scan"), (mg, "#e0b84a", "graded"), (mr, "#2f6fed", "graded + relief")]:
            for j, e in enumerate(section(m, [0, y0, 0], [0, 1, 0], 0)):
                a.plot(e[:, 0], e[:, 1], color=col, lw=0.8 if m is om else 1.4, label=lab if j == 0 else None)
        a.set_aspect("equal"); a.invert_xaxis(); a.legend(fontsize=8, loc="lower center")
        xr = np.abs(og[:, 0]).max() + 3; a.set_xlim(xr, -xr); a.set_ylim(-4, 8)
        a.set_title(f"coronal section y = {y0:.1f} mm: vestibular flange", fontsize=10); a.set_xlabel("x (mm)"); a.set_ylabel("z (mm)")
        a = ax[i, 2]
        tx = float(lm.tuberosity_r[0])
        for m, col, lab in [(om, "#9aa3ad", "scan"), (mg, "#e0b84a", "graded"), (mr, "#2f6fed", "graded + relief")]:
            for j, e in enumerate(section(m, [tx, 0, 0], [1, 0, 0], 1)):
                a.plot(e[:, 0], e[:, 1], color=col, lw=0.8 if m is om else 1.4, label=lab if j == 0 else None)
        a.set_aspect("equal"); a.legend(fontsize=8, loc="upper right")
        a.set_xlim(og[:, 1].min() - 2, og[:, 1].min() + 10); a.set_ylim(-4, 4)
        a.set_title(f"sagittal section through the right tuberosity (x = {tx:.1f} mm)", fontsize=10)
        a.set_xlabel("y, anterior (mm)"); a.set_ylabel("z (mm)")
    fig.suptitle("Border relief (project heuristic): vestibular flange capped 1.0 mm above the crest, labial frenulum notch, "
                 "tuberosities relieved 0.3 mm. Synthetic palates only.", fontsize=11)
    fig.savefig(OUT / "relief_compare.png", dpi=110)
    (OUT / "relief_compare.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    for r in rows:
        print(f"{r['case']}: area {r['area_mm2'][0]} -> {r['area_mm2'][1]} mm2, flange trimmed {r['flange_trimmed_mm2']} mm2, "
              f"notch {r['frenulum_notch_removed_mm2']} mm2, volume {r['volume_mm3'][0]} -> {r['volume_mm3'][1]} mm3, watertight {r['watertight']}")
    print(f"-> {OUT}/relief_compare.png")


if __name__ == "__main__":
    main()
