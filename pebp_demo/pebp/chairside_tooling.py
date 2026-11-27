"""Printed tooling for the two-visit chairside route (research route, untested).

  base_tooling          visit 1: a two-piece mold (working model + oral counter) and a tunnel core for casting
                        the case's final base in cold-cure PMMA; the pocket window is a post on the counter
  stock_baton_tooling   stock (patient-independent): a two-half mold for casting one wired final baton, with
                        wire-locating pins, a clamp channel for the hooked tail, sprue, vent and registration
                        pegs, and a 1:1 bending jig for the hooked wire
  export_stock_tooling  the same for every kit baton

Every piece is checked as the cast-plate tooling is (mold.py): watertight, one body, no overlaps, the cavity
equals the part, and each piece slides free along its pull direction. Flow, shrinkage and release are not
simulated; nothing here has been printed or poured.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import numpy as np
import trimesh
from shapely.geometry import Polygon, box as sbox

from .meshutil import manifold_boolean, solid_between, sweep_tube
from .mold import _box, _cyl, _diff, _inter, _largest, _prism, _union, _vol, _strip_poly, demold_sweep

LABEL = "project heuristic: printed tooling for the chairside research route; not printed, poured or demolded yet"


def _check_pieces(pieces: dict, block, part, seq, extra_tol=None) -> dict:
    """Shared validation: watertight, overlaps, cavity vs part, and the demold sequence."""
    names = list(pieces)
    void = _diff(block, _union(list(pieces.values())))
    vb = sorted(void.split(only_watertight=False), key=lambda q: -_vol(q))
    main = vb[0]
    tol = extra_tol or max(1.0, 5e-4 * _vol(part))
    val = {"watertight": {k: bool(v.is_watertight) for k, v in pieces.items()},
           "body_counts": {k: int(v.body_count) for k, v in pieces.items()},
           "piece_overlaps_mm3": {f"{a}~{b}": round(_vol(_inter(pieces[a], pieces[b])), 3)
                                  for i, a in enumerate(names) for b in names[i + 1:]},
           "part_inside_piece_mm3": {k: round(_vol(_inter(part, v)), 3) for k, v in pieces.items()},
           "part_outside_cavity_mm3": round(_vol(_diff(part, main)), 3),
           "part_volume_mm3": round(_vol(part), 1), "cavity_volume_mm3": round(_vol(main), 1),
           "stray_empty_pieces_mm3": [round(_vol(q), 2) for q in vb[1:] if _vol(q) > 0.5],
           "tolerance_mm3": round(tol, 2),
           "note": "stray empty pieces are the clearance round the registration pegs"}
    demold = []
    for name, piece, dirn, obst in seq(main):
        sw = demold_sweep(piece, dirn, obst)
        demold.append({"piece": name, "pull_direction_design_frame": [round(float(v), 3) for v in dirn],
                       "overlap_mm3_at_mm": dict(zip(["0.1", "0.3", "1", "3", "10", "40"], sw)), "free": bool(max(sw) <= tol)})
    val["demold"] = demold
    val["ok"] = (all(val["watertight"].values()) and all(v == 1 for v in val["body_counts"].values())
                 and max(val["piece_overlaps_mm3"].values()) <= tol and max(val["part_inside_piece_mm3"].values()) <= tol
                 and val["part_outside_cavity_mm3"] <= tol and all(d["free"] for d in demold))
    return val, main


# ---------------------------------------------------------------- visit 1: the final base

def two_piece(part, S, rect, step: float, wall: float, base_h: float, cores: list, cutters: list,
              cap_poly=None, counter_extra=None):
    """Working model above the tissue surface S (design frame: z superior), oral counter below it, and cores that
    pass through the counter. Returns (block, model, counter, cores_union, zt, zb)."""
    x0, y0 = rect[0]; x1, y1 = rect[2]
    gx, gy = np.meshgrid(np.linspace(x0, x1, 60), np.linspace(y0, y1, 60))
    sg = S(np.column_stack([gx.ravel(), gy.ravel()]))
    zt = float(sg.max() + base_h)
    zb = float(min(part.bounds[0, 2], sg.min()) - wall)
    upper = solid_between(rect, step, S, lambda xy: np.full(len(xy), zt))
    lower = solid_between(rect, step, lambda xy: np.full(len(xy), zb), S)
    block = solid_between(rect, step, lambda xy: np.full(len(xy), zb), lambda xy: np.full(len(xy), zt))
    core = _union(cores) if cores else None
    model = _diff(upper, part)
    counter = _diff(lower, part)
    if cap_poly is not None:
        big = np.array([[x0 - 4, y0 - 4], [x1 + 4, y0 - 4], [x1 + 4, y1 + 4], [x0 - 4, y1 + 4]])
        band = solid_between(big, step, lambda xy: S(xy) - 0.6, lambda xy: S(xy) + 0.3)
        counter = _diff(counter, _inter(_prism(cap_poly), band))
    if core is not None:
        counter = _diff(counter, core)
        model = _diff(model, core)
        core = _inter(core, block)
    for c in cutters:
        counter = _diff(counter, c)
    return block, _largest(model), _largest(counter), core, zt, zb


def base_tooling(ctx: dict, base, kp, cp, border_y: float, z_mid: float, binfo: dict, out: Path, debug: dict | None = None,
                 rel: str = "kit/chairside/base_tooling") -> dict:
    """Two-piece mold and tunnel core for the case's final base. Writes `out`/*.stl and manifest.json."""
    from .graded import bow_anchors
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    hfb, outline = ctx["hfb"], ctx["outline"]
    t = ctx["pp"].thickness
    hx, zb_t, zt_t, yf = kp.tunnel()
    wall, step = 6.0, 0.7
    # draft the plate rim to vertical walls (as mold.py), keeping the socket block whole
    rim = Polygon(outline).buffer(-0.05)
    if rim.geom_type == "MultiPolygon":
        rim = max(rim.geoms, key=lambda g: g.area)
    wx = hx + kp.side_wall
    keep = rim.union(sbox(-wx - 0.5, border_y - 0.5, wx + 0.5, binfo["pocket_front_y"] + 8.0))
    part = _inter(base, _prism(keep))
    draft_removed = _vol(base) - _vol(part)
    y_floor = float(hfb.ys[1]) if hasattr(hfb, "ys") else 0.0

    def S(xy):
        q = np.array(xy, float, copy=True)
        if hasattr(hfb, "xs"):            # a scan's height field: held flat beyond its grid
            q[:, 0] = np.clip(q[:, 0], hfb.xs[1], hfb.xs[-2])
            q[:, 1] = np.clip(q[:, 1], y_floor, hfb.ys[-2])
        return hfb(q)

    lo, hi = part.bounds
    x0, x1, y0, y1 = lo[0] - wall, hi[0] + wall, lo[1] - wall, hi[1] + wall
    rect = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]])
    # the tunnel core: the tunnel's section, from outside the block's back wall to the tunnel's front end
    core = _box([-hx, y0 - 8.0, z_mid + zb_t], [hx, border_y + yf, z_mid + zt_t])
    # sprue at the block's floor behind the window, vents at the rim (high ground when poured, as mold.py)
    zfloor = z_mid + kp.boss_bottom() - cp.extra_floor   # the worn base's block floor (chairside.final_base)
    cutters, features = [], {}
    zb_guess = float(min(lo[2], S(rect).min()) - wall)
    ys = border_y + min(3.5, yf - 3.3)   # behind the pocket window (which starts 0.5 mm before the tunnel's front end)
    p_in, p_out = np.array([0.0, ys, zfloor + 0.4]), np.array([0.0, ys, zb_guess - 1.0])
    cutters += [_cyl(p_in, p_out, 2.0)]   # straight: a funnel would lock the counter as it lifts
    features["sprue"] = {"at": p_in.round(2).tolist(), "radius_mm": 2.5, "note": "on the block's floor, behind the window"}
    cen = outline.mean(axis=0)
    apex = outline[np.argmax(outline[:, 1])]
    left = outline[outline[:, 0] < cen[0]]; right = outline[outline[:, 0] >= cen[0]]
    vents = []
    for q in [apex, left[np.argmin(left[:, 1])], right[np.argmin(right[:, 1])]]:
        xy = q + 1.2 * (cen - q) / np.linalg.norm(cen - q)
        z = float(S(xy[None, :])[0]) - t / 2.0
        cutters.append(_cyl([xy[0], xy[1], z], [xy[0], xy[1], zb_guess - 1.0], 0.75, sections=16))
        vents.append([round(float(xy[0]), 2), round(float(xy[1]), 2)])
    features["vents"] = {"xy": vents, "radius_mm": 0.75}
    # bow-wire grooves on the parting line at the canines (as mold.py)
    bows = []
    big = np.array([[x0 - 4, y0 - 4], [x1 + 4, y0 - 4], [x1 + 4, y1 + 4], [x0 - 4, y1 + 4]])
    groove_band = solid_between(big, step, lambda xy: S(xy) - 1.3, lambda xy: S(xy) + 0.3)
    lm = ctx.get("lm")
    if lm is not None and getattr(lm, "crest_ring", None) is not None:
        for c, u in bow_anchors(lm.crest_ring, outline):
            ts = [((x0 if u[0] < 0 else x1) - c[0]) / u[0] if abs(u[0]) > 1e-9 else np.inf,
                  ((y0 if u[1] < 0 else y1) - c[1]) / u[1] if abs(u[1]) > 1e-9 else np.inf]
            cutters.append(_inter(_prism(_strip_poly(c - 3.0 * u, c + u * (min(ts) + 2.0), 0.65)), groove_band))
            bows.append({"canine_xy": c.round(2).tolist(), "direction_xy": u.round(3).tolist()})
    features["bow_grooves"] = {"grooves": bows, "width_mm": 1.3, "depth_mm": 1.3}
    cap = Polygon(outline).buffer(-0.4)
    if cap.geom_type == "MultiPolygon":
        cap = max(cap.geoms, key=lambda g: g.area)
    block, model, counter, core_m, zt, zb = two_piece(part, S, rect, step, wall, 5.0, [core], cutters, cap_poly=cap)

    def seq(shot):
        return [("tunnel_core", core_m, [0.0, -1.0, 0.0], [model, counter, shot]),
                ("counter_oral", counter, [0.0, 0.0, -1.0], [model, shot]),
                ("cast final base", shot, [0.0, 0.0, -1.0], [model])]
    val, main = _check_pieces({"working_model": model, "counter_oral": counter, "tunnel_core": core_m}, block, part, seq,
                              extra_tol=max(2.0, 5e-4 * _vol(part)))
    val["draft_removed_mm3"] = round(draft_removed, 1)
    val["window_post_on_counter"] = True
    if debug is not None:
        debug.update(tool_block=block, tool_model=model, tool_counter=counter, tool_core=core_m, tool_part=part)
    # export in pour orientation (model on the bench, counter on top, sprue up), as mold.py
    Tm = trimesh.transformations.rotation_matrix(np.pi, [1, 0, 0]); Tm[2, 3] = zt
    files = {}
    for name, m in {"working_model": model, "counter_oral": counter, "tunnel_core": core_m}.items():
        mm = m.copy(); mm.apply_transform(Tm); mm.export(out / f"{name}.stl"); files[name] = f"{name}.stl"
    man = {"schema": "pebp-chairside-base-tooling/0.1", "flag": LABEL, "files": files, "features": features,
           "pmma_needed_ml": round(_vol(main) / 1000.0 * 1.3, 1),
           "assembly": ["seat the tunnel core through the counter's back wall until it stops on the window post",
                        "close the counter onto the working model (separator on every face)",
                        "pour cold-cure PMMA through the sprue; cure under pressure as the resin's instructions say"],
           "demold_sequence": ["pull the tunnel core out backward", "lift the counter (its post leaves the pocket window)",
                               "lift the base off the working model"],
           "pour_orientation": "model base on the bench (z = 0), counter on top, sprue opening upward",
           "validation": val}
    (out / "manifest.json").write_text(json.dumps(man, indent=2), encoding="utf-8")
    return {"files": {k: f"{rel}/{v}" for k, v in files.items()}, "pmma_needed_ml": man["pmma_needed_ml"],
            "validation": val}


# ---------------------------------------------------------------- stock: wired final batons

def xsplit_mold(body, wire, channel, pin_seg, pin_fracs, tail, cp, wall: float = 5.0) -> dict:
    """Two-half mold split at x = 0 (halves pulled along -x and +x) for a body symmetric about x = 0 with a wire in
    the plane x = 0. Sprue at the body's extreme point along `tail` (pour that end up), one vent beside it, pins at
    `pin_fracs` of `pin_seg` touching the wire's flat faces, four registration pegs."""
    cast = _diff(body, wire)                       # the PMMA
    lo = np.minimum(body.bounds[0], wire.bounds[0]) - wall
    hi = np.maximum(body.bounds[1], wire.bounds[1]) + wall
    block = _box(lo, hi)
    L = _box(lo, [0.0, hi[1], hi[2]]); R = _box([0.0, lo[1], lo[2]], hi)
    v = np.asarray(body.vertices)
    tip = v[np.argmax(v @ tail)]
    sprue = _union([_cyl(tip - 1.5 * tail, tip + 60.0 * tail, 1.75), _cyl(tip + 4.0 * tail, tip + 60.0 * tail, 3.5)])
    side = np.cross(tail, [1.0, 0.0, 0.0]); side /= np.linalg.norm(side)
    vp = tip - 4.0 * tail + 1.2 * side
    vent = _cyl(vp, vp + 60.0 * tail, 0.6, sections=16)
    pins_l, pins_r, stations = [], [], []
    for f in pin_fracs:
        p = pin_seg[int(round(f * (len(pin_seg) - 1)))]
        pins_l.append(_cyl([-(cp.wire_w / 2 + 0.08), p[1], p[2]], [lo[0] - 1.0, p[1], p[2]], 0.8, sections=20))
        pins_r.append(_cyl([cp.wire_w / 2 + 0.08, p[1], p[2]], [hi[0] + 1.0, p[1], p[2]], 0.8, sections=20))
        stations.append(p.round(2).tolist())
    pegs, holes = [], []
    for y in (lo[1] + 3.0, hi[1] - 3.0):
        for z in (lo[2] + 3.0, hi[2] - 3.0):
            pegs.append(_cyl([-2.0, y, z], [2.5, y, z], 1.4, sections=20))
            holes.append(_cyl([-1.0, y, z], [2.8, y, z], 1.55, sections=20))
    left = _diff(_union([_inter(_diff(_diff(L, body), channel), block)] + pins_l), sprue)
    left = _largest(_diff(_union([left] + [_inter(pg, block) for pg in pegs]), vent))
    right = _diff(_diff(_diff(R, body), channel), _union([sprue, vent] + holes))
    right = _largest(_inter(_union([right] + pins_r), block))
    pins = _union(pins_l + pins_r)

    def seq(shot):
        return [("mold_right", right, [1.0, 0.0, 0.0], [left, shot]),
                ("cast part with wire", _union([shot, wire]), [1.0, 0.0, 0.0], [left])]
    val, main = _check_pieces({"mold_left": left, "mold_right": right}, block, _diff(cast, pins), seq)   # pins leave holes
    return {"left": left, "right": right, "pins": pins, "val": val, "main": main, "cast": cast, "lo": lo, "hi": hi,
            "stations": stations, "block": block}


def stock_baton_tooling(entry: dict, kp, cp, out: Path | None = None) -> dict:
    """Two-half mold (split at x = 0, pulled along -x and +x) and wire jig for one stock final baton, in the
    attachment frame (border y = 0, plate mid-thickness z = 0). The wire lies in the parting plane: its hooked
    tail is clamped in a channel between the halves, and two pairs of pins touch its flat faces inside the baton."""
    from .chairside import wire_path
    from .extension import ExtensionParams, make_extension
    from .kit import baton_piece
    ep = ExtensionParams(**entry["params"])
    body = baton_piece(entry, kp, 0.0, 0.0, mark=False, floss=False)
    arm, centre, _ = make_extension(ep, 0.0, 0.0, f"tuebingen-{kp.variant}")
    wpath, winfo = wire_path(ep, arm, centre, kp, cp, 0.0, 0.0)
    wire = sweep_tube(wpath, np.full(len(wpath), cp.wire_w), np.full(len(wpath), cp.wire_t), n_round=3)
    channel = sweep_tube(wpath, np.full(len(wpath), cp.wire_w + 0.1), np.full(len(wpath), cp.wire_t + 0.1), n_round=3)
    path = centre
    tail = path[-1] - path[-4]; tail /= np.linalg.norm(tail)
    xs = xsplit_mold(body, wire, channel, wpath[wpath[:, 1] < -6.0], (0.15, 0.6), tail, cp)
    left, right, pins, val, main, cast, lo, hi = (xs[k] for k in ("left", "right", "pins", "val", "main", "cast", "lo", "hi"))
    stations = xs["stations"]
    val["pins_clear_of_wire_mm3"] = round(_vol(_inter(pins, wire)), 3)
    val["wire_inside_baton_mm3_outside"] = round(_vol(_diff(_inter(wire, _box([-9, lo[1], lo[2]], [9, 0.0, hi[2]])), body)), 3)
    val["ok"] = bool(val["ok"] and val["pins_clear_of_wire_mm3"] <= 0.05)
    # bending jig: 1:1 template in the wire's plane (x = 0), groove open on the +x face; a notch marks the plate border
    pmin, pmax = wpath.min(axis=0) - 6.0, wpath.max(axis=0) + 6.0
    jig = _box([-3.0, pmin[1], pmin[2]], [0.0, pmax[1], pmax[2]])
    jig = _diff(jig, sweep_tube(wpath, np.full(len(wpath), cp.wire_w + 0.1), np.full(len(wpath), cp.wire_t + 0.3), n_round=2))
    jig = _diff(jig, _box([-0.4, -0.3, pmin[2] - 1.0], [0.1, 0.3, pmax[2] + 1.0]))
    val["watertight"]["wire_jig"] = bool(jig.is_watertight)
    res = {"id": entry["id"], "code": entry["code"], "wire": winfo, "pin_stations": stations, "validation": val,
           "pmma_needed_ml": round(_vol(main) / 1000.0 * 1.5, 2), "cast_volume_mm3": round(_vol(cast), 1),
           "pour": "stand the closed mold tip up; pour slowly down the sprue until resin shows at the vent",
           "after_demold": "cut the sprue flush; fill the four 1.6 mm pin holes with fresh resin; polish to a high gloss "
                           "except the plug, which is air-abraded at the pick-up"}
    if out is not None:
        out = Path(out); out.mkdir(parents=True, exist_ok=True)
        files = {"mold_left": left, "mold_right": right, "wire_jig": jig, "final_baton": cast, "safety_wire": wire}
        for k, m in files.items():
            m.export(out / f"{k}.stl")
        res["files"] = {k: f"{k}.stl" for k in files}
        (out / "manifest.json").write_text(json.dumps({**res, "flag": LABEL, "kit_params": asdict(kp), "chairside_params": asdict(cp),
                                                        "frame": "attachment frame: plate border y = 0, plate mid-thickness z = 0"}, indent=2), encoding="utf-8")
    return res


def export_stock_tooling(out: Path, kp, cp, ids: list[str] | None = None) -> dict:
    """Molds and jigs for every stock final baton of the kit (or the ids given)."""
    from .kit import library
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    rows = []
    for e in library(kp):
        if ids and e["id"] not in ids:
            continue
        r = stock_baton_tooling(e, kp, cp, out / e["id"])
        rows.append({"id": r["id"], "code": r["code"], "ok": r["validation"]["ok"], "dir": e["id"],
                     "cast_volume_mm3": r["cast_volume_mm3"], "wire_length_mm": r["wire"]["length_mm"]})
    man = {"kind": "pebp-chairside-stock-tooling", "variant": kp.variant, "flag": LABEL, "batons": rows,
           "kit_params": asdict(kp), "chairside_params": asdict(cp)}
    (out / "stock_tooling_manifest.json").write_text(json.dumps(man, indent=2), encoding="utf-8")
    return man
