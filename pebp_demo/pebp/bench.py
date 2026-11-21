"""Bench-test specimens for the two-visit chairside route (`pebp bench OUT`).

Everything here is synthetic and patient-independent: a flat 2.5 mm coupon plate carries the kit socket and the
anchor pocket, so the joint can be tested without a patient's palate. The protocol is docs/CHAIRSIDE_BENCH.md.

  coupon_S      flat final base + stock wired final baton K5, joined by the chairside pick-up   (tests 1, 2, 3, 6, 8)
  coupon_C      the same plate and baton cast in one piece with the Tübingen wire span          (test 1 control, 3)
  shear         substrate tray and split collar for a bonded column                             (test 4)
  monomer       disc tray at the pick-up's thickness                                            (test 7)
  pose transfer is measured on a case's own S<n> with `pebp measure`                            (test 5)

Status: project heuristic. Nothing has been printed, cast or tested; pass thresholds are the project's, not published.
"""
from __future__ import annotations

import csv
import json
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import trimesh

from .chairside import ChairsideParams, final_base, wire_path, LABEL as CS_LABEL
from .chairside_tooling import base_tooling, stock_baton_tooling, xsplit_mold
from .extension import ExtensionParams, make_extension
from .kit import KitParams, baton_piece, library
from .meshutil import arclength, centreline_through, drop_slivers, manifold_boolean, sweep_tube, tip_arclength
from .mold import _box, _cyl, _diff, _union, _vol

LABEL = ("project heuristic: synthetic bench specimens for the chairside research route; thresholds are the project's, "
         "not published; nothing printed, cast or tested yet")

TESTS = [   # the table in docs/MODULAR_KIT.md; thresholds are project heuristics or for the team to set
    {"n": 1, "test": "whole-plate fracture, load at the tip", "specimens": "coupon_S x6, coupon_C x6 (then a case's S<n> against its C<n>)",
     "measure": "peak load (N), failure site, fragments held by the wire (y/n)",
     "pass": "S not lower than C, and every fragment held by the wire"},
    {"n": 2, "test": "hook pull-out, bond defeated", "specimens": "coupon_S x6, release agent on the plug before the pick-up",
     "measure": "peak load pulling the baton straight back (N), failure mode",
     "pass": "the hook alone holds above an agreed multiple of the infant tongue load (threshold for the team)"},
    {"n": 3, "test": "fatigue and thermocycling", "specimens": "coupon_S x6, coupon_C x6",
     "measure": "5000 thermocycles 5-55 °C, then cyclic tip load in water at 37 °C; cycles to failure; dye check",
     "pass": "no crack at the seam and no bond loss on dye penetration"},
    {"n": 4, "test": "shear bond of the pick-up to cast PMMA", "specimens": "shear substrates x6 per surface treatment, aged",
     "measure": "bond strength (MPa) and failure mode for: monomer only; air abrasion + monomer; air abrasion + primer",
     "pass": "cohesive or mixed failure, not adhesive"},
    {"n": 5, "test": "pose transfer", "specimens": "a case's S<n>, scanned",
     "measure": "`pebp measure` of the scanned S<n> against kit/K<n>_equivalent.stl",
     "pass": "tip within 0.5 mm, angle within 2 deg (project heuristic)"},
    {"n": 6, "test": "the seam after polishing", "specimens": "coupon_S x3 after test 3's ageing, sectioned",
     "measure": "gap under magnification, Ra across the seam, dye penetration",
     "pass": "no gap, Ra <= 0.2 µm"},
    {"n": 7, "test": "residual monomer and cytotoxicity", "specimens": "monomer discs x6 cured as the pick-up, plus discs of the base resin",
     "measure": "residual MMA (% mass) and cytotoxicity by the analysing lab's methods, after the chosen soak",
     "pass": "no worse than a one-piece cast plate"},
    {"n": 8, "test": "chairside timing", "specimens": "coupon_S x5, then printed palates",
     "measure": "stopwatch per step: abrade, prime, fill, seat, cure, trim, polish",
     "pass": "the real time saved, against the lab cycle of the cast route"},
]


def _flat_ctx(t: float, half_w: float = 15.0, length: float = 30.0):
    """A flat plate in the attachment frame: border at y = 0, plate mid-thickness at z = 0, tissue face at z = t/2."""
    plate = _box([-half_w, 0.0, -t / 2.0], [half_w, length, t / 2.0])
    outline = np.array([[-half_w, 0.0], [half_w, 0.0], [half_w, length], [-half_w, length]])
    hfb = lambda xy: np.full(len(np.asarray(xy)), t / 2.0)
    return plate, outline, hfb


def _tray(cavities: list, size, depth: float, floor: float = 2.0):
    """Open-top tray with the given solids sunk into its top face."""
    blk = _box([0.0, 0.0, -floor - depth], [size[0], size[1], 0.0])
    return _diff(blk, _union(cavities))


def build(out: Path, kp: KitParams | None = None, cp: ChairsideParams | None = None, baton: str = "K5") -> dict:
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    kp = kp or KitParams(); cp = cp or ChairsideParams()
    t = cp.final_thickness
    entry = next(e for e in library(kp) if e["id"] == baton)
    ep = ExtensionParams(**entry["params"])
    files, val = {}, {}

    def save(name, m):
        m.export(out / f"{name}.stl"); files[name] = f"{name}.stl"

    # ---- coupon S: flat final base + stock final baton, joined by the pick-up
    plate, outline, hfb = _flat_ctx(t)
    base, pocket, binfo = final_base(plate, hfb, outline, 0.0, 0.0, kp, cp)
    ctx = {"hfb": hfb, "outline": outline, "pp": SimpleNamespace(thickness=t), "lm": None}
    tool = base_tooling(ctx, base, kp, cp, 0.0, 0.0, binfo, out / "coupon_S_base_tooling", rel="coupon_S_base_tooling")
    val["coupon_S_base_tooling"] = tool["validation"]["ok"]
    save("coupon_S_base", base)
    body = baton_piece(entry, kp, 0.0, 0.0, mark=False, floss=False)
    arm, centre, _ = make_extension(ep, 0.0, 0.0, f"tuebingen-{kp.variant}")
    wpath, winfo = wire_path(ep, arm, centre, kp, cp, 0.0, 0.0)
    wire = sweep_tube(wpath, np.full(len(wpath), cp.wire_w), np.full(len(wpath), cp.wire_t), n_round=3)
    hx, zb, zt, yf = kp.tunnel()
    y_pf = binfo["pocket_front_y"]
    cavity = _union([_box([-hx, 0.0, zb], [hx, yf, zt]), pocket])
    cavity = manifold_boolean("intersection", [cavity, _box([-30, 0.0, kp.boss_bottom()], [30, y_pf, 30])])
    pickup = _diff(cavity, _union([body, wire]))
    coupon_s = drop_slivers(_union([base, body, pickup, wire]))
    save("coupon_S_assembled", coupon_s); save("coupon_S_pickup", pickup)
    stock = stock_baton_tooling(entry, kp, cp, out / f"coupon_S_baton_{baton}")
    val["coupon_S_baton_tooling"] = stock["validation"]["ok"]
    val["coupon_S_one_body"] = bool(coupon_s.is_watertight and coupon_s.body_count == 1)

    # ---- coupon C: the same plate and baton cast in one piece, wire on the Tübingen span (no joint)
    plate_c, _, _ = _flat_ctx(t)
    keep = _box([-30.0, -80.0, -80.0], [30.0, 0.0, 40.0])
    arm_c = manifold_boolean("intersection", [arm, keep])
    control = _union([plate_c, arm_c])
    path = centreline_through(arm, centre[centre[:, 1] < -0.5])
    s = arclength(path); s_tip = tip_arclength(arm, path)
    s_root = float(s[int(np.argmin(np.abs(path[:, 1])))])
    st = np.linspace(s_root + cp.wire_start_after_root, s_tip - cp.wire_end_before_tip, 120)
    wp_c = np.column_stack([np.interp(st, s, path[:, k]) for k in range(3)])
    wire_c = sweep_tube(wp_c, np.full(len(wp_c), cp.wire_w), np.full(len(wp_c), cp.wire_t), n_round=3)
    chan_c = sweep_tube(wp_c, np.full(len(wp_c), cp.wire_w + 0.1), np.full(len(wp_c), cp.wire_t + 0.1), n_round=3)
    tail = path[-1] - path[-4]; tail /= np.linalg.norm(tail)
    xs = xsplit_mold(control, wire_c, chan_c, wp_c, (0.12, 0.5, 0.88), tail, cp)
    val["coupon_C_tooling"] = xs["val"]["ok"]
    (out / "coupon_C_tooling").mkdir(exist_ok=True)
    for k in ("left", "right"):
        xs[k].export(out / "coupon_C_tooling" / f"mold_{k}.stl"); files[f"coupon_C_mold_{k}"] = f"coupon_C_tooling/mold_{k}.stl"
    wire_c.export(out / "coupon_C_tooling" / "safety_wire.stl"); files["coupon_C_wire"] = "coupon_C_tooling/safety_wire.stl"
    save("coupon_C", _union([xs["cast"], wire_c]))

    # ---- test 4: shear substrates (12 x 12 x 4 mm) and a split collar for a 3 mm bonded column
    subs = [_box([3.0 + 16.0 * i, 3.0 + 16.0 * j, -4.0], [15.0 + 16.0 * i, 15.0 + 16.0 * j, 0.5]) for i in range(3) for j in range(2)]
    shear_tray = _tray(subs, (50.0, 34.0), 4.0)
    save("shear_substrate_tray", shear_tray)
    ring = _diff(_cyl([0, 0, 0], [0, 0, 3.0], 5.0, sections=48), _cyl([0, 0, -1], [0, 0, 4.0], 1.5, sections=48))
    half = manifold_boolean("intersection", [ring, _box([0.0, -6, -1], [6, 6, 4])])
    save("shear_collar_half", half)
    # ---- test 7: discs as thick as the pick-up in the tunnel
    h = round(zt - zb, 2)
    discs = [_cyl([8.0 + 14.0 * i, 8.0, -h], [8.0 + 14.0 * i, 8.0, 0.5], 5.0, sections=48) for i in range(4)]
    save("monomer_disc_tray", _tray(discs, (60.0, 16.0), h))

    for k in ("shear_substrate_tray", "shear_collar_half", "monomer_disc_tray"):
        m = trimesh.load(out / files[k]); val[f"{k}_watertight"] = bool(m.is_watertight)
    with open(out / "bench_results_template.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["test", "specimen_id", "group", "date", "operator", "resin_lot", "wire_lot", "cure", "value", "unit", "failure_mode", "notes"])
        for tst in TESTS:
            w.writerow([tst["n"], "", "", "", "", "", "", "", "", "", "", tst["test"]])
    files["results_template"] = "bench_results_template.csv"
    man = {"kind": "pebp-chairside-bench", "flag": LABEL, "chairside_flag": CS_LABEL, "baton": entry["id"], "code": entry["code"],
           "frame": "attachment frame: plate border y = 0 (y anterior), plate mid-thickness z = 0 (z superior)",
           "coupon": {"plate_mm": [30.0, 30.0, t], "clamp_holes": "drill 2 x 4.2 mm at y = 24, x = +-10 after demolding (cast-in posts would lock the split mold)", "socket": binfo["socket"],
                      "pocket": binfo["pocket_mm"], "pickup_volume_mm3": round(_vol(pickup), 1),
                      "wire_S": winfo, "wire_C_span_mm": [cp.wire_start_after_root, cp.wire_end_before_tip]},
           "files": files, "validation": val, "ok": all(val.values()), "tests": TESTS,
           "kit_params": asdict(kp), "chairside_params": asdict(cp), "protocol": "docs/CHAIRSIDE_BENCH.md"}
    (out / "bench_manifest.json").write_text(json.dumps(man, indent=2))
    return man
