"""Uniform (Tübingen) versus graded final plate on synthetic palates only.

    .venv/bin/python scripts/graded_compare.py            (about 30 s)

Builds a synthetic Robin-sequence palate with a cleft and one without, designs the final plate at
2.5 mm both ways (uniform, and graded 2.5 -> 1.5 mm), and writes to out/graded_compare/:
  graded_compare.png    thickness maps and sections, before (uniform) and after (graded)
  graded_compare.json   volume, mean thickness, thin area, measured wall per case
Never touches real patient scans.
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
OUT = ROOT / "out" / "graded_compare"

from pebp.landmarks import find_landmarks          # noqa: E402
from pebp.plate import PlateParams, design_base_plate, _cells  # noqa: E402

CASES = [("Synthetic Robin palate, cleft", []), ("Synthetic Robin palate, no cleft", ["--no-cleft"])]
BASE, THIN = 2.5, 1.5


def synth(name: str, extra: list[str]) -> Path:
    d = OUT / name
    subprocess.run([sys.executable, "-m", "pebp", "synth", "--preset", "prs", "--out", str(d), *extra],
                   cwd=ROOT, check=True, capture_output=True)
    return d / "scan.stl"


def section(mesh, origin, normal, axis):
    s = mesh.section(plane_origin=origin, plane_normal=normal)
    if s is None:
        return []
    return [s.vertices[e.points][:, [axis, 2]] for e in s.entities]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    rows, figs = [], []
    fig, ax = plt.subplots(len(CASES), 4, figsize=(17, 4.3 * len(CASES)), constrained_layout=True)
    for i, (title, extra) in enumerate(CASES):
        scan = trimesh.load(synth(f"case{i + 1}", extra), force="mesh")
        om, lm, hf = find_landmarks(scan)
        pu = PlateParams(thickness=BASE)
        pg = replace(pu, profile="graded", thin_thickness=THIN)
        mu, hfb, outline, iu = design_base_plate(lm, hf, pu)
        mg, _, _, ig = design_base_plate(lm, hf, pg)
        mu.export(OUT / f"case{i + 1}" / "plate_uniform.stl"); mg.export(OUT / f"case{i + 1}" / "plate_graded.stl")
        field = mg.metadata["thickness_field"]
        tp = ig["thickness_profile"]
        rows.append({"case": title, "uniform_volume_mm3": iu["volume_mm3"], "graded_volume_mm3": ig["volume_mm3"],
                     "volume_saved_pct": round(100 * (1 - ig["volume_mm3"] / iu["volume_mm3"]), 1),
                     "graded_mean_thickness_mm": tp["mean_thickness_mm"], "area_mm2": tp["area_mm2"],
                     "rigid_zones_mm2": tp["rigid_zones_mm2"], "measured_wall_graded": tp["measured_wall"],
                     "max_gradient": tp["measured_gradient_max"], "watertight": [iu["watertight"], ig["watertight"]]})

        foot = Polygon(outline).buffer(0)
        gx, gy = np.meshgrid(np.arange(foot.bounds[0], foot.bounds[2], 0.2), np.arange(foot.bounds[1], foot.bounds[3], 0.2))
        from shapely import contains_xy
        inside = contains_xy(foot, gx, gy)
        tg = field(np.column_stack([gx.ravel(), gy.ravel()])).reshape(gx.shape)
        for k, (lab, t) in enumerate([("before: uniform 2.5 mm (Tübingen)", np.full(gx.shape, BASE)),
                                      (f"after: graded {BASE} -> {THIN} mm", tg)]):
            a = ax[i, k]
            sc = a.pcolormesh(gx, gy, np.ma.masked_where(~inside, t), cmap="viridis_r", vmin=THIN, vmax=BASE, shading="auto")
            a.plot(*np.vstack([outline, outline[:1]]).T, color="k", lw=0.8)
            a.plot(lm.crest_ring[:, 0], lm.crest_ring[:, 1], color="w", lw=0.8, ls="--")
            if k == 1:
                for name, z in field.zones.items():
                    gs = z.geoms if z.geom_type == "MultiPolygon" else [z]
                    for g in gs:
                        a.plot(*np.array(g.exterior.coords).T, color="#d33", lw=0.9)
            a.set_aspect("equal"); a.set_title(f"{title}\n{lab}", fontsize=10)
            a.set_xlabel("x, patient's right (mm)"); a.set_ylabel("y, anterior (mm)")
            a.invert_xaxis()
        fig.colorbar(sc, ax=ax[i, 1], shrink=0.8, label="wall thickness (mm)")

        # coronal section through the mid-palate (y halfway between the tuberosities and the apex)
        y0 = 0.5 * (lm.apex[1] + 0.5 * (lm.tuberosity_l[1] + lm.tuberosity_r[1]))
        a = ax[i, 2]
        for m, col, lab in [(om, "#9aa3ad", "scan"), (mu, "#e0b84a", "uniform"), (mg, "#2f6fed", "graded")]:
            for j, e in enumerate(section(m, [0, y0, 0], [0, 1, 0], 0)):
                a.plot(e[:, 0], e[:, 1], color=col, lw=1.4 if m is not om else 0.8, label=lab if j == 0 else None)
        a.set_aspect("equal"); a.invert_xaxis(); a.legend(fontsize=8, loc="lower center")
        a.set_title(f"coronal section, y = {y0:.1f} mm", fontsize=10); a.set_xlabel("x (mm)"); a.set_ylabel("z, superior (mm)")
        xr = np.abs(outline[:, 0]).max() + 2; a.set_xlim(xr, -xr)
        # sagittal section off the midline (clear of a midline cleft): 12 mm to the patient's right
        a = ax[i, 3]
        for m, col, lab in [(om, "#9aa3ad", "scan"), (mu, "#e0b84a", "uniform"), (mg, "#2f6fed", "graded")]:
            for j, e in enumerate(section(m, [12.0, 0, 0], [1, 0, 0], 1)):
                a.plot(e[:, 0], e[:, 1], color=col, lw=1.4 if m is not om else 0.8, label=lab if j == 0 else None)
        a.set_aspect("equal"); a.legend(fontsize=8, loc="lower center")
        a.set_title("sagittal section, x = 12 mm", fontsize=10); a.set_xlabel("y, anterior (mm)"); a.set_ylabel("z (mm)")
        a.set_xlim(outline[:, 1].min() - 2, outline[:, 1].max() + 2)
    fig.suptitle("Final base plate before (uniform, Tübingen 2.5 mm) and after (graded, project heuristic). "
                 "Red outlines: zones held at full thickness (baton root, cleft, bow-wire tags). Synthetic palates only.", fontsize=11)
    fig.savefig(OUT / "graded_compare.png", dpi=110)
    (OUT / "graded_compare.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    for r in rows:
        print(f"{r['case']}: volume {r['uniform_volume_mm3']} -> {r['graded_volume_mm3']} mm3 (-{r['volume_saved_pct']}%), "
              f"mean wall {r['graded_mean_thickness_mm']} mm, at thin {r['area_mm2']['at_thin']} of {r['area_mm2']['total']} mm2, "
              f"measured wall min {r['measured_wall_graded']['min_mm']} mm, |grad T| max {r['max_gradient']}")
    print(f"-> {OUT}/graded_compare.png")


if __name__ == "__main__":
    main()
