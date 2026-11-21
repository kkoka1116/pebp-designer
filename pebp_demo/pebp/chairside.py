"""Chairside fixation: the baton chosen with the try-in kit, fixed into the patient's worn plate at the second visit.

Proposed two-visit pathway (research route, untested):
  visit 1   intraoral scan; `pebp chairside CASE --base` makes the worn plate (the "final base", 2.5 mm) with the
            kit socket and an anchor pocket in front of the tunnel, and the printed tooling to cast it in cold-cure
            PMMA between visits (chairside_tooling.base_tooling)
  visit 2   awake endoscopic try-in with the slide-in kit batons (`pebp kit`); the chosen K<n> is matched to a
            stock *final* baton of the same shape: cold-cure PMMA cast around the safety wire, the wire running
            on past the root, down through the plug and out of its front end as a hooked tail
            -> `pebp chairside CASE --kit K<n>`: air-abrade and prime the plug, fill the tunnel and pocket with cold-cure PMMA, seat the baton,
               cure under pressure (the "pick-up"), trim and polish, then soak as the resin's instructions say

The wire crosses the joint and its hook sits in the pick-up resin inside the pocket, which reaches higher than the
tunnel roof. The pocket is open downward through the block's floor (a window): that lets the base be cast over
removable cores, and is how the pocket is filled at the pick-up. Pulled backward, the hook meets the plate
behind the pocket. Even if the bond failed completely, the baton could leave only by bending the wire straight or by
breaking the plate around the pocket. This joint adds one thing the one-piece plate does not have: the root is
reinforced by the wire.

Status: project heuristic, untested. Nothing here has been cast, bonded, fatigue-tested or worn. The evidence
and the bench tests are in docs/MODULAR_KIT.md.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import numpy as np
import trimesh

from .extension import ExtensionParams, make_extension
from .kit import KitParams, ROOF_MARGIN, baton_piece, drop_check, library, socket_base, socket_drop, _box, _xcyl
from .meshutil import arclength, centreline_through, drop_slivers, manifold_boolean, sweep_tube, tip_arclength

LABEL = ("project heuristic: chairside pick-up of a stock wired baton into the worn plate, untested "
         "(no bond, fatigue, fracture, pull-out or cleaning data); research route, not for clinical use")


@dataclass
class ChairsideParams:
    final_thickness: float = 2.5          # worn plate, mm (Tübingen 2.5-3)
    wire_w: float = 1.8                   # flat twisted stainless wire, as the cast route
    wire_t: float = 0.8
    wire_start_after_root: float = 10.0   # Tübingen: about 1 cm after the root ...
    wire_end_before_tip: float = 2.0      # ... to 1.5-2 mm before the tip
    tail_beyond_plug: float = 3.0         # wire runs this far past the plug's front end
    hook_below_floor: float = 0.45        # then bends DOWN (toward the tongue) into the pocket: wire centre ends this far below
                                          # the tunnel floor, so the whole wire is clear of the tunnel's section and locks the
                                          # baton against sliding back. Down, not up (until 2026-11-21): an upward hook needed a
                                          # pocket reaching above the tunnel roof, which on curved palates left too little plate
                                          # over it unless the baton was lowered below the try-in height
    wire_run_above_floor: float = 0.8     # the wire runs forward through the plug this far above the tunnel floor (low in the
                                          # plug: the pocket over it can then sit lower where the palate falls away)
    extra_floor: float = 0.3              # the worn base's block floor sits this much deeper than the try-in base's, so the
                                          # pick-up resin covers the hook (min_cover) where the window is filled
    pocket_half_width: float = 2.0
    pocket_length: float = 5.0            # in front of the tunnel
    pocket_top: float = -1.0              # relative to the prototype mid-thickness (z_mid): the tunnel roof; lowered per case
                                          # (fit_pocket) where the palate falls away in front of the tunnel
    min_cover: float = 0.25               # resin around the wire, mm (checked)


def _smooth(poly: np.ndarray, step: float = 0.25, passes: int = 6) -> np.ndarray:
    """Densify a polyline and round its corners, keeping the ends fixed."""
    pts = [poly[0]]
    for a, b in zip(poly[:-1], poly[1:]):
        n = max(int(np.ceil(np.linalg.norm(b - a) / step)), 1)
        pts += [a + (b - a) * k / n for k in range(1, n + 1)]
    p = np.array(pts)
    for _ in range(passes):
        p[1:-1] = 0.25 * p[:-2] + 0.5 * p[1:-1] + 0.25 * p[2:]
    return p


def wire_path(ep: ExtensionParams, arm: trimesh.Trimesh, centre: np.ndarray, kp: KitParams, cp: ChairsideParams,
              border_y: float, z_mid: float) -> tuple[np.ndarray, dict]:
    """The safety wire of a stock final baton: from 2 mm before the tip, along the baton's measured centreline to
    3 mm behind the border, down into the plug, forward out of the plug's front end, then up into the pocket."""
    path = centreline_through(arm, centre[centre[:, 1] < border_y - 0.5])
    s = arclength(path)
    s_tip = tip_arclength(arm, path)
    i_root = int(np.argmin(np.abs(path[:, 1] - border_y)))
    s_root = float(s[i_root])
    # the baton part of the wire, tip first
    st = np.linspace(s_tip - cp.wire_end_before_tip, s_root + 3.0, 200)
    bat = np.column_stack([np.interp(st, s, path[:, k]) for k in range(3)])
    over = st > s[-1]
    if over.any():
        t = path[-1] - path[-4]; t /= np.linalg.norm(t)
        bat[over] = path[-1] + (st[over] - s[-1])[:, None] * t
    hx, zb, zt, yf = kp.tunnel()
    zc = z_mid + (zb + zt) / 2.0
    y_front = border_y + kp.plug_length
    zr = z_mid + zb + cp.wire_run_above_floor
    bridge = np.array([[0.0, border_y - 3.5, z_mid - 0.3], [0.0, border_y - 0.5, zc], [0.0, border_y + 2.0, zr],
                       [0.0, y_front + cp.tail_beyond_plug, zr],
                       [0.0, y_front + cp.tail_beyond_plug, z_mid + zb - cp.hook_below_floor]])
    full = _smooth(np.vstack([bat, bridge]))
    s_full = arclength(full)
    info = {"length_mm": round(float(s_full[-1]), 1),
            "end_mm_before_tip": cp.wire_end_before_tip,
            "standard_span_mm_after_root": cp.wire_start_after_root,
            "crosses_joint_by_mm": round(float(full[-1, 1] - border_y), 1),
            "note": "one continuous wire: the Tübingen span (about 1 cm after the root to 1.5-2 mm before the tip) "
                    "plus a bridge through the root and the plug into the pocket, where it is hooked"}
    return full, info


def fit_pocket(hfb, border_y: float, z_mid: float, kp: KitParams, cp: ChairsideParams) -> tuple[ChairsideParams, float, dict]:
    """Keep the worn plate's baton where the try-in put it: the socket gets only the kit's own drop (usually 0),
    and the anchor pocket adapts to the palate instead, its top lowered and, if needed, the pocket shortened, as long
    as the hook can still rise clear of the tunnel roof. Only if no pocket fits is the attachment lowered for it.
    Returns (adjusted params, drop, info) [project heuristic]."""
    from dataclasses import replace as _rep
    hx, zb, zt, yf = kp.tunnel()
    d0 = socket_drop(hfb, border_y, z_mid, kp)
    zm = z_mid - d0
    lowest = zb + cp.wire_run_above_floor + cp.wire_t / 2.0 + cp.min_cover   # cover over the wire's forward run
    for L in (cp.pocket_length, 4.0, 3.5, 3.0, 2.5):
        y_pf = border_y + yf + L
        g = np.array([[x, y] for x in np.linspace(-cp.pocket_half_width, cp.pocket_half_width, 9) for y in np.linspace(border_y + yf - 0.5, y_pf, 9)])
        h = np.asarray(hfb(g), dtype=float); h = h[np.isfinite(h)]
        allowed = (float(h.min()) - 1.0 - ROOF_MARGIN) - zm if len(h) else cp.pocket_top
        top = min(cp.pocket_top, allowed)
        if top >= lowest:
            cp2 = _rep(cp, pocket_length=L, pocket_top=round(top, 2), tail_beyond_plug=min(cp.tail_beyond_plug, L - 1.0))
            return cp2, d0, {"pocket_length_mm": L, "pocket_top_vs_tunnel_roof_mm": round(top - zt, 2), "pocket_adapted": (L, round(top, 2)) != (cp.pocket_length, cp.pocket_top),
                             "socket_drop_mm": d0, "rule": "pocket lowered/shortened to keep 1 mm of plate over it; the baton stays at the try-in height"}
    d = attachment_drop(hfb, border_y, z_mid, kp, cp)
    return cp, d, {"pocket_length_mm": cp.pocket_length, "pocket_adapted": False, "socket_drop_mm": d,
                   "rule": "no pocket fits at the try-in height: the attachment is lowered (baton sits lower than at the try-in)"}


def attachment_drop(hfb, border_y: float, z_mid: float, kp: KitParams, cp: ChairsideParams) -> float:
    """As kit.socket_drop, and also keeping 1 mm of plate over the anchor pocket in front of the tunnel."""
    hx, zb, zt, yf = kp.tunnel()
    y_pf = border_y + yf + cp.pocket_length
    g = np.array([[x, y] for x in np.linspace(-cp.pocket_half_width, cp.pocket_half_width, 9) for y in np.linspace(border_y + yf - 0.5, y_pf, 9)])
    h = np.asarray(hfb(g), dtype=float); h = h[np.isfinite(h)]
    pd = max(0.0, (z_mid + cp.pocket_top) - (float(h.min()) - 1.0 - ROOF_MARGIN)) if len(h) else 0.0
    return round(max(socket_drop(hfb, border_y, z_mid, kp), pd), 2)


def final_base(plate, hfb, outline, border_y: float, z_mid: float, kp: KitParams, cp: ChairsideParams):
    """The worn plate before the try-in: the kit socket on the 2.5 mm plate, plus the anchor pocket in front of the
    tunnel. The pocket is a window open through the block's floor (the tongue side): a cast base can then be
    made over two removable cores (one pulled back out of the tunnel, one pulled down out of the window), and at
    the pick-up the window is how the pocket is filled. Returns (mesh, pocket solid, info)."""
    from dataclasses import replace as _rep
    hx, zb, zt, yf = kp.tunnel()
    y_pf = border_y + yf + cp.pocket_length
    kpf = _rep(kp, floor=kp.floor + cp.extra_floor)
    base, sinfo = socket_base(plate, hfb, outline, border_y, z_mid, kpf, front=y_pf + 1.5, floss=False)
    pocket = _box([-cp.pocket_half_width, border_y + yf - 0.5, z_mid + kpf.boss_bottom() - 2.0],
                  [cp.pocket_half_width, y_pf, z_mid + cp.pocket_top])
    base = manifold_boolean("difference", [base, pocket])
    xs = np.linspace(-cp.pocket_half_width, cp.pocket_half_width, 7)
    ys = np.linspace(border_y + yf, y_pf, 7)
    g = np.array([[x, y] for x in xs for y in ys])
    pocket_roof = float((hfb(g) - (z_mid + cp.pocket_top)).min())
    info = {"socket": sinfo, "pocket_front_y": float(y_pf), "pocket_roof_mm": round(pocket_roof, 2),
            "pocket_mm": {"width": 2 * cp.pocket_half_width, "length": round(y_pf - (border_y + yf - 0.5), 2),
                          "top_above_tunnel_roof": round(cp.pocket_top - zt, 2), "open_through_block_floor": True}}
    return base, pocket, info


def build_base(case_dir: Path, kp: KitParams | None = None, cp: ChairsideParams | None = None, debug: dict | None = None) -> dict:
    """Visit 1, after the scan: the case's final base (2.5 mm worn plate with the kit socket and the anchor pocket)
    and the printed tooling to cast it in cold-cure PMMA. Stored as record["chairside_base"]."""
    from . import record as rec
    from .context import rebuild
    from .chairside_tooling import base_tooling
    from .qc import _check, overall
    case_dir = Path(case_dir)
    record = rec.load(case_dir / "design.json")
    kit = record.get("kit")
    if not kit:
        raise ValueError("run `pebp kit` for this case first")
    kp = kp or KitParams(**{k: tuple(v) if isinstance(v, list) else v for k, v in kit["params"].items()})
    cp = cp or ChairsideParams()
    ctx = rebuild(case_dir, plate_thickness=cp.final_thickness)
    by, zm0 = ctx["border_y"], ctx["z_mid"]
    ka = kit.get("attachment") or {}
    if "z_mid_plate" in ka:
        zm0 = float(ka["z_mid_plate"])     # the try-in base's level, so the worn baton sits where the tried-in one did
    cp, drop, pinfo = fit_pocket(ctx["hfb"], by, zm0, kp, cp)
    zm = zm0 - drop
    base, pocket, binfo = final_base(ctx["plate"], ctx["hfb"], ctx["outline"], by, zm, kp, cp)
    out = case_dir / "kit" / "chairside"; out.mkdir(parents=True, exist_ok=True)
    tooling = base_tooling(ctx, base, kp, cp, by, zm, binfo, out / "base_tooling", debug=debug)
    base.export(out / "final_base.stl")
    sinfo = binfo["socket"]
    checks = [
        _check("base printable/castable", base.is_watertight and base.body_count == 1, f"{bool(base.is_watertight)}, {base.body_count} body", "one closed solid"),
        _check("plate above the tunnel", sinfo["roof_min_mm"] >= kp.min_roof, sinfo["roof_min_mm"], f">= {kp.min_roof} mm (project heuristic)"),
        _check("plate above the pocket", binfo["pocket_roof_mm"] >= 1.0, binfo["pocket_roof_mm"], ">= 1.0 mm of plate over the pocket (project heuristic)"),
        drop_check(drop),
        _check("tooling valid", tooling["validation"]["ok"], tooling["validation"]["ok"],
               "pieces watertight, no overlaps, cavity matches the base, every piece demolds (base_tooling/manifest.json)"),
    ]
    entry = {"file": "kit/chairside/final_base.stl", "sha256": rec.file_sha256(out / "final_base.stl"),
             "volume_mm3": round(float(base.volume), 1), "final_plate_thickness": cp.final_thickness,
             "kit_params": asdict(kp), "chairside_params": asdict(cp), "socket": sinfo,
             "attachment": {"border_y": round(by, 2), "z_mid_plate": round(zm0, 2), "socket_drop_mm": drop, "z_mid": round(zm, 2)},
             "pocket_fit": pinfo,
             "pocket": binfo["pocket_mm"], "pocket_roof_mm": binfo["pocket_roof_mm"],
             "tooling": {"dir": "kit/chairside/base_tooling", "files": tooling["files"], "pmma_needed_ml": tooling["pmma_needed_ml"],
                         "manifest_sha256": rec.file_sha256(out / "base_tooling" / "manifest.json")},
             "material": "cold-cure PMMA (as the cast route), not a printed resin: printed bases bond worse to repair resin",
             "qc": {"checks": checks, "overall": overall(checks)}, "flag": LABEL, "time": rec.now()}
    record["chairside_base"] = entry
    rec.save(record, case_dir / "design.json")
    return entry


def build(case_dir: Path, kit_id: str, kp: KitParams | None = None, cp: ChairsideParams | None = None, debug: dict | None = None) -> dict:
    """Final base for the case, the stock final baton matching kit baton `kit_id`, its wire, the pick-up resin,
    and the assembled worn plate, with checks. Registers the assembled plate as S<n>."""
    from . import record as rec
    from .context import rebuild
    from .plate import tissue_clip_solid
    from .qc import _check, overall, qc_prototype, root_span
    case_dir = Path(case_dir)
    record = rec.load(case_dir / "design.json")
    kit = record.get("kit")
    if not kit:
        raise ValueError("run `pebp kit` for this case first")
    kp = kp or KitParams(**{k: tuple(v) if isinstance(v, list) else v for k, v in kit["params"].items()})
    cp = cp or ChairsideParams()
    entry = next((b for b in kit["batons"] if b["id"] == rec.normalise_id(kit_id)), None)
    if entry is None:
        raise ValueError(f"no kit baton {kit_id}")
    ep = ExtensionParams(**entry["params"])
    ctx = rebuild(case_dir, plate_thickness=cp.final_thickness)
    by, zm0 = ctx["border_y"], ctx["z_mid"]
    ka = kit.get("attachment") or {}
    if "z_mid_plate" in ka:
        zm0 = float(ka["z_mid_plate"])     # the try-in base's level, so the worn baton sits where the tried-in one did
    cp, drop, pinfo = fit_pocket(ctx["hfb"], by, zm0, kp, cp)
    zm = zm0 - drop
    out = case_dir / "kit" / "chairside"; out.mkdir(parents=True, exist_ok=True)

    base, pocket, binfo = final_base(ctx["plate"], ctx["hfb"], ctx["outline"], by, zm, kp, cp)
    stored = record.get("chairside_base")
    if not stored:
        raise ValueError("run `pebp chairside CASE --base` first: the final base is made (and cast) before the try-in")
    if abs(stored["volume_mm3"] - round(float(base.volume), 1)) > 0.5 or stored["final_plate_thickness"] != cp.final_thickness:
        raise ValueError("the final base no longer matches the one made with --base (design or parameters changed); "
                         "run `pebp chairside CASE --base` again and cast the new base")
    hx, zb, zt, yf = kp.tunnel()
    y_pf = binfo["pocket_front_y"]
    pocket_roof = binfo["pocket_roof_mm"]
    sinfo = binfo["socket"]

    # stock final baton (PMMA body, no marks: dimples would trap biofilm on a worn plate) and its wire
    body = baton_piece(entry, kp, by, zm, mark=False, floss=False)
    arm, centre, _ = make_extension(ep, by, zm, f"tuebingen-{kp.variant}")
    wpath, winfo = wire_path(ep, arm, centre, kp, cp, by, zm)
    wire = sweep_tube(wpath, np.full(len(wpath), cp.wire_w), np.full(len(wpath), cp.wire_t), n_round=3)
    body_wired = manifold_boolean("difference", [body, wire])                       # the PMMA around the wire

    # pick-up resin: tunnel and pocket, less the plug and the wire
    tunnel = _box([-hx, by, zm + zb], [hx, by + yf, zm + zt])
    cavity = manifold_boolean("union", [tunnel, pocket])
    cavity = manifold_boolean("intersection", [cavity, _box([-30, by, zm + kp.boss_bottom() - cp.extra_floor], [30, y_pf, zm + 30])])
    pickup = manifold_boolean("difference", [cavity, body, wire])
    resin = manifold_boolean("union", [base, body, pickup])
    resin = manifold_boolean("difference", [resin, tissue_clip_solid(ctx["outline"], ctx["hfb"])])
    assembled = drop_slivers(manifold_boolean("union", [resin, wire]))             # the worn plate, wire included

    if debug is not None:
        debug.update(wpath=wpath, base=base, body=body, wire=wire, pickup=pickup, assembled=assembled, resin=resin, pocket=pocket, cavity=cavity, by=by, zm=zm)
    # checks
    # cover: a sleeve min_cover thick round the wire must lie inside resin
    sleeve = sweep_tube(wpath, np.full(len(wpath), cp.wire_w + 2 * cp.min_cover), np.full(len(wpath), cp.wire_t + 2 * cp.min_cover), n_round=3)
    wire_out = float(manifold_boolean("difference", [sleeve, assembled]).volume)
    in_pickup = float(manifold_boolean("intersection", [wire, pocket]).volume)
    hook_top = float(wpath[-1, 2] + cp.wire_t / 2.0)
    tunnel_floor = zm + zb
    qc = qc_prototype(assembled, centre, ep, replace(ctx["pp"], thickness=cp.final_thickness), ctx["outline"], record["landmarks"],
                      kind="final", reference=record.get("standard"),
                      wire={"start_mm_after_root": -winfo["crosses_joint_by_mm"], "end_mm_before_tip": cp.wire_end_before_tip,
                            "check_span": False},
                      root=root_span(arm, by, zm, ep.lateral_offset), support=ctx["hfb"])
    checks = [c for c in qc["checks"]]
    checks += [
        _check("wire enclosed", wire_out < 0.05, round(wire_out, 3), f"at least {cp.min_cover} mm of resin all round the wire (mm3 short)"),
        _check("wire crosses the joint", winfo["crosses_joint_by_mm"] >= 8.0, winfo["crosses_joint_by_mm"],
               ">= 8 mm of wire in front of the plate border (project heuristic)"),
        _check("wire hooked in the pocket", in_pickup > 1.0 and hook_top <= tunnel_floor, round(tunnel_floor - hook_top, 2),
               "the hook drops below the tunnel floor, clear of the tunnel's section, so the baton cannot slide out even with no bond (mm below)"),
        _check("plate above the pocket", pocket_roof >= 1.0, round(pocket_roof, 2), ">= 1.0 mm of plate over the pocket (project heuristic)"),
        _check("plate above the tunnel", sinfo["roof_min_mm"] >= kp.min_roof, sinfo["roof_min_mm"], f">= {kp.min_roof} mm (project heuristic)"),
        _check("stock baton body", body_wired.is_watertight, bool(body_wired.is_watertight), "PMMA around the wire is one closed solid"),
    ]
    qc = {"checks": checks, "overall": overall(checks)}

    # write
    files = {"stock_final_baton": f"{entry['id']}_final_baton.stl", "safety_wire": f"{entry['id']}_wire.stl",
             "pickup_resin": f"{entry['id']}_pickup.stl", "assembled": f"{entry['id']}_assembled.stl"}
    for k, m in {"stock_final_baton": body_wired, "safety_wire": wire, "pickup_resin": pickup, "assembled": assembled}.items():
        m.export(out / files[k])
    records = record.setdefault("chairside_finals", [])
    n = max([c["n"] for c in records], default=0) + 1
    fn = f"kit/chairside/{files['assembled']}"
    s_entry = {"n": n, "id": f"S{n}", "from": entry["id"], "code": entry["code"], "file": fn, "sha256": rec.file_sha256(case_dir / fn),
               "params": entry["params"], "final_plate_thickness": cp.final_thickness,
               "files": {"final_base": stored["file"], **{k: f"kit/chairside/{v}" for k, v in files.items()}},
               "base_sha256": stored["sha256"],
               "wire": {**winfo, "start_mm_after_root": -winfo["crosses_joint_by_mm"]}, "pickup_volume_mm3": round(float(pickup.volume), 1),
               "pocket_roof_mm": round(pocket_roof, 2), "chairside_params": asdict(cp), "qc": qc, "flag": LABEL, "time": rec.now(),
               "steps": ["air-abrade (50 µm alumina) and dry the plug and the socket; apply MMA monomer or the resin's primer",
                         "mix cold-cure PMMA to a flowable stage; fill the tunnel from the back and the pocket through its window in the block's floor",
                         "seat the stock final baton fully against the shoulder (the hook enters the pocket); top up the window, remove the excess",
                         "cure under pressure as the resin's instructions say (Orthocryl: 2.2 bar, 40-46 °C, 20 min or more)",
                         "trim and polish the seam to a high gloss; check under magnification for gaps",
                         "soak in water as decided by the clinical team before the plate is worn"]}
    records.append(s_entry)
    from . import airway
    airway.update(record, case_dir, devices=[s_entry["id"]])
    rec.save(record, case_dir / "design.json")
    return s_entry
