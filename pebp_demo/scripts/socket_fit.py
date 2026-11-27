"""Which slide-in plug sizes fit a patient's palate without lowering the baton.

For each case and each plate group (C: hard+soft cleft, N: soft-only / none) this builds the group's plate once,
then for every plug width x length in the grid reports the socket drop `kit.socket_drop` would need (0 = the
tunnel fits under the plate with min_roof + margin of plate over it, baton at plate level) and the plug's bending
section relative to the baton (`kit.plug_strength`). The plug height is chosen per width so the section matches
the current 7 x 2 mm plug where possible (the extra height goes toward the tongue, not into the plate).

    python scripts/socket_fit.py CASE [CASE ...] [--out fit.json]

Prints a table of counts only (how many case-groups fit at each size), so it is safe to read aloud from a real-scan
run; the per-case values go to --out, which stays on the computer when the cases are real.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import trimesh  # noqa: E402

from pebp import cleft as cleftmod  # noqa: E402
from pebp import record as rec  # noqa: E402
from pebp.catalog import GROUPS, _attachment  # noqa: E402
from pebp.extension import tuebingen_params  # noqa: E402
from pebp.kit import KitParams, plug_strength, socket_drop  # noqa: E402
from pebp.landmarks import find_landmarks  # noqa: E402
from pebp.plate import PlateParams, design_base_plate  # noqa: E402

WIDTHS = (7.0, 6.0, 5.0, 4.5, 4.0)
LENGTHS = (8.0, 6.0, 5.0, 4.0)
HEIGHTS = (2.0, 2.5, 3.0)


def height_for(width: float, kp0: KitParams, ep) -> float:
    """Smallest height in HEIGHTS whose section is at least the current plug's (else the tallest)."""
    target = plug_strength(kp0, ep)["ratio"]
    for h in HEIGHTS:
        if plug_strength(replace(kp0, plug_width=width, plug_height=h), ep)["ratio"] >= target - 1e-6:
            return h
    return HEIGHTS[-1]


def fit_case(case: Path) -> list[dict]:
    record = rec.load(case / "design.json")
    if record["landmarks"].get("backend", {}).get("backend") == "moldstudio":
        from pebp.moldstudio_bridge import classify_landmarks
        _, lm0, hf, _, _ = classify_landmarks(trimesh.load(record["source_scan"]["file"], force="mesh"))
    else:
        _, lm0, hf = find_landmarks(trimesh.load(record["source_scan"]["file"], force="mesh"))
    kp0 = KitParams(plug_width=7.0, plug_height=2.0, plug_length=8.0)   # the original plug: the strength reference
    base_pp = PlateParams(**record["plate"]["params"])
    rows = []
    for g, G in GROUPS.items():
        lm, _ = cleftmod.apply(lm0, G["rep"])
        pp = replace(base_pp, thickness=kp0.prototype_thickness, profile="uniform", border_relief=False,
                     **cleftmod.plate_defaults(G["rep"]))
        _, hfb, _, info = design_base_plate(lm, hf, pp)
        by, zm = _attachment(info, hfb, pp.thickness)
        ep = tuebingen_params(G["baton"])
        for w in WIDTHS:
            h = height_for(w, kp0, ep)
            for L in LENGTHS:
                kp = replace(kp0, plug_width=w, plug_length=L, plug_height=h)
                rows.append({"case": case.name, "group": g, "plug_width": w, "plug_length": L, "plug_height": h,
                             "drop_mm": socket_drop(hfb, by, zm, kp), "strength_ratio": plug_strength(kp, ep)["ratio"]})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cases", nargs="+")
    ap.add_argument("--out")
    a = ap.parse_args()
    rows = []
    for c in a.cases:
        rows += fit_case(Path(c))
    n = len({(r["case"], r["group"]) for r in rows})
    print(f"{n} case-groups. Count that fit at each plug size: drop 0 (baton at plate level) / drop <= 0.5 / <= 1.0 mm")
    print(f"{'width x length (height)':<26}{'strength':>9}{'0':>6}{'<=0.5':>7}{'<=1.0':>7}")
    for w in WIDTHS:
        for L in LENGTHS:
            sel = [r for r in rows if r["plug_width"] == w and r["plug_length"] == L]
            c0 = sum(r["drop_mm"] == 0 for r in sel); c5 = sum(r["drop_mm"] <= 0.5 for r in sel); c1 = sum(r["drop_mm"] <= 1.0 for r in sel)
            print(f"{w:>4.1f} x {L:>3.1f} ({sel[0]['plug_height']:.1f}){'':<13}{sel[0]['strength_ratio']:>9.2f}{c0:>6}{c5:>7}{c1:>7}")
    if a.out:
        Path(a.out).write_text(json.dumps(rows, indent=1), encoding="utf-8")
        print("per-case values:", a.out)


if __name__ == "__main__":
    main()
