"""Orientation and landmarks on scan-like synthetic palates (synthetic data only).

    .venv/bin/python scripts/orient_check.py            (about a minute; also run from phase0_check.py)

A raw intraoral scan of an infant maxilla rarely stops at the vestibule. It carries lip and cheek tissue that
folds back down below the ridge crest, with a torn, irregular edge, so the scan can be about as wide as it is
deep, and it can have holes where the scanner caught nothing. The earlier orientation took the crest as the
lowest point in each direction from the arch centre, and levelled on the lower convex hull: on such a scan both
land on the hanging rim tissue, so the arch, the midline and the tuberosities come out wrong. The synthetic
generator reproduces this (MaxillaParams.rim_flap / rim_ragged / hole_radius); these checks hold the anatomical
orientation to it, from several random starting poses.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pebp import landmarks as L                          # noqa: E402
from pebp.plate import design_base_plate, PlateParams    # noqa: E402
from pebp.qc import landmark_check                       # noqa: E402
from pebp.synthetic import prs_published, build_maxilla, random_rigid  # noqa: E402

RIM = dict(rim_flap=6.0, rim_ragged=2.5)
SEEDS = (3, 11, 29)


def _frame_error(T_applied, T_found):
    """Yaw (deg, turn about z) and tilt (deg, of z) of the found frame against the synthetic one."""
    M = T_found @ T_applied
    y, z = M[:3, :3] @ [0.0, 1.0, 0.0], M[:3, :3] @ [0.0, 0.0, 1.0]
    return abs(float(np.degrees(np.arctan2(y[0], y[1])))), float(np.degrees(np.arccos(np.clip(z[2], -1, 1))))


def _tub_error(truth, T_applied, lm):
    """Worst tuberosity position error (mm, in plan) against the synthetic truth mapped into the found frame."""
    M = lm.transform @ T_applied
    tr = [(M[:3, :3] @ np.asarray(t) + M[:3, 3])[:2] for t in truth["tuberosities"]]   # truth: [left (-x), right (+x)]
    return max(np.linalg.norm(tr[0] - lm.tuberosity_l[:2]), np.linalg.norm(tr[1] - lm.tuberosity_r[:2]))


def _case(name, seeds=SEEDS, **kw):
    out = []
    for s in seeds:
        mesh, truth = build_maxilla(prs_published(seed=7, **kw))
        mu, T = random_rigid(mesh, seed=s)
        _, lm, hf = L.find_landmarks(mu)
        yaw, tilt = _frame_error(T, lm.transform)
        out.append((yaw, tilt, _tub_error(truth, T, lm), landmark_check(lm.to_json())["status"], lm, hf, mu))
    return out


def checks():
    """Yield (ok, name, detail)."""
    # 1. the failure mode: the old crest rule (lowest point per direction, after levelling on the lower hull) lands
    #    on the rim flap of a scan-like palate, so the 'arch' it finds is far wider than the palate
    mesh, truth = build_maxilla(prs_published(seed=7, cleft=False, **RIM))
    mu, _ = random_rigid(mesh, seed=SEEDS[0])
    v1 = L._apply(L._orient_pca(mu), mu.vertices)
    R, off = L._lower_hull_plane(v1)
    old_ring, _ = L._crest_ring_from_oriented(v1 @ R.T - off)
    span_old = float(np.ptp(old_ring[:, 0]) if np.ptp(old_ring[:, 0]) > np.ptp(old_ring[:, 1]) else np.ptp(old_ring[:, 1]))
    new_ring, _, how = L.crest_ring(L.orient(mu)[0].vertices)
    span_new = float(np.ptp(new_ring[:, 0]))
    w = truth["params"]["intertuberosity_width"]
    yield (span_old > w + 8.0 and abs(span_new - w) < 4.0, "ragged rim: the old crest rule is fooled, the new one is not",
           f"crest ring span: old {span_old:.1f} mm, new {span_new:.1f} mm ({how}); palate {w:.1f} mm")

    # 2. orientation from random poses, on scan-like palates with and without a cleft, with a hole, and wider than deep
    cases = [("clean synthetic, cleft", dict(asymmetry=1.0)),
             ("ragged rim, cleft", dict(asymmetry=1.0, **RIM)),
             ("ragged rim, no cleft", dict(cleft=False, **RIM)),
             ("ragged rim + hole, no cleft", dict(cleft=False, hole_radius=3.0, **RIM)),
             ("wider than deep, ragged rim", dict(cleft=False, intertuberosity_width=38.0, arch_depth=17.0, **RIM))]
    for name, kw in cases:
        rs = _case(name, **kw)
        yaw = max(r[0] for r in rs); tilt = max(r[1] for r in rs); tub = max(r[2] for r in rs)
        qc = sorted({r[3] for r in rs})
        yield (yaw < 4.0 and tilt < 3.0 and tub < 3.5 and qc == ["pass"], f"orientation: {name}",
               f"over {len(rs)} poses: yaw <= {yaw:.1f} deg, tilt <= {tilt:.1f} deg, tuberosities within {tub:.1f} mm, "
               f"landmark QC {qc}")

    # 3. a hole in the scan is blocked out like a cleft: found, under the block-out mask, and the plate builds
    mesh, truth = build_maxilla(prs_published(seed=7, cleft=False, hole_radius=3.0, **RIM))
    mu, T = random_rigid(mesh, seed=SEEDS[1])
    _, lm, hf = L.find_landmarks(mu)
    M = lm.transform @ T
    hc = (M[:3, :3] @ np.array([*truth["params"]["hole_center"], 0.0]) + M[:3, 3])[:2]
    found = [h for h in lm.holes if np.linalg.norm(np.asarray(h["polygon"]).mean(axis=0) - hc) < 2.0]
    plate, hfb, outline, info = design_base_plate(lm, hf, PlateParams())
    sup = hfb.support(hc[None, :])[0]
    area = found[0]["area_mm2"] if found else 0.0
    yield (bool(found) and not lm.cleft_present and lm.blockout_present and sup == "block-out"
           and plate.is_watertight and "scan_holes_blocked" in info,
           "hole in the scan blocked out, no cleft call needed",
           f"hole found {bool(found)} ({area:.1f} mm2, true {np.pi * 9:.1f}), cleft called {lm.cleft_present}, "
           f"surface at the hole '{sup}', plate watertight {plate.is_watertight}")


def main():
    results = []
    for ok, name, detail in checks():
        results.append(ok)
        print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}", flush=True)
    n_fail = sum(not ok for ok in results)
    print(f"\n{len(results) - n_fail}/{len(results)} checks passed")
    sys.exit(1 if n_fail else 0)


if __name__ == "__main__":
    main()
