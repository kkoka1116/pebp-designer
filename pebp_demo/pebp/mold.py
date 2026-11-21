"""Digital flask: printable tooling for casting the final appliance in cold-cure PMMA.

Why a multi-piece mold: the velopharyngeal extension leaves the plate heading posteriorly and then
curves down toward the epiglottis. A one-piece rigid counter-mold cannot be pulled off that curve,
so the counter is split into three rigid pieces that each come away along one straight line:

  working_model  patient's palate with the cleft blocked out; forms the plate's tissue (intaglio) side
  counter_oral   forms the plate's oral surface; lifts straight off (pour frame +z); carries the air
                 vents and the two bow-wire grooves on the parting line
  counter_left / counter_right   enclose the extension, split on its mid-sagittal plane; slide off
                 sideways (-x / +x); carry the pour sprue at the extension tip and the notched pins that
                 hold the safety wire centred in the extension during the pour
  cradle         open tray that keeps all four pieces closed during the pour and the pressure cure
  wire_jig       1:1 bending template for the 1.8 x 0.8 mm safety wire
  device_master  positive of the final appliance (try-in print, and reference for checking the cast part)
  safety_wire    the wire's designed shape and position, for checking the bend
  alternatives/counter_onepiece_flex  the counter as one piece, for printing in a flexible resin
                 (Silicone 40A class) or as a master for a silicone putty index, Tübingen-style

All parts are exported in pour orientation: model base on the bench (z = 0), counters on top, sprue
opening upward. The build verifies the partition (no overlaps, one connected cavity that contains the
whole device) and simulates demolding by sliding every piece along its pull direction.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import trimesh
from shapely.geometry import Polygon, LineString

from . import record as rec_io
from .context import rebuild, chosen_extension
from .extension import extension_path, make_extension
from .meshutil import solid_between, sweep_tube, path_frames, boolean_union, arclength, tip_arclength, centreline_through, manifold_boolean, extrude_clean
from .plate import tissue_clip_solid
from .qc import qc_prototype, root_span


@dataclass
class MoldParams:
    final_plate_thickness: float = 2.5   # Tübingen: final plate 2.5-3 mm (prototypes 2 mm)
    wall: float = 6.0                    # minimum mold wall around the device, mm
    base: float = 5.0                    # model base above the highest palate point, mm
    step: float = 0.7                    # surface sampling of the parting surface, mm
    sprue_r: float = 2.5                 # pour sprue radius at the tip, mm
    funnel_r: float = 4.5
    vent_r: float = 0.75
    bow_groove_w: float = 1.3            # groove for the 1.1 mm fixation bow wire
    bow_groove_d: float = 1.3
    wire_w: float = 1.8                  # safety wire 1.8 x 0.8 mm
    wire_t: float = 0.8
    wire_start_mm: float = 10.0          # wire starts this far along the extension (Tübingen ~1 cm)
    wire_end_mm: float = 2.0             # and ends this far before the tip (Tübingen 1.5-2 mm)
    pin_r: float = 1.0                   # wire-locating pins
    pin_stations: tuple = (0.12, 0.5, 0.88)
    clearance: float = 0.05
    proto: int = 2
    device: str | None = None            # device id to cast (default: the last adjusted design, else P<proto>)


def _inter(a, b):
    return manifold_boolean("intersection", [a, b])


def _diff(a, b):
    return manifold_boolean("difference", [a, b])


def _union(ms):
    return manifold_boolean("union", ms)


def _box(lo, hi):
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    b = trimesh.creation.box(extents=hi - lo); b.apply_translation((lo + hi) / 2); return b


def _half(normal, point, size=400.0):
    """Big box filling the side of the plane (point, normal) that the normal points away from."""
    n = np.asarray(normal, float) / np.linalg.norm(normal)
    b = trimesh.creation.box(extents=[2 * size] * 3)
    M = trimesh.geometry.align_vectors([0, 0, 1], n)
    M[:3, 3] = np.asarray(point, float) - n * size
    b.apply_transform(M)
    return b


def _cyl(p0, p1, r, sections=24):
    return trimesh.creation.cylinder(radius=r, segment=[np.asarray(p0, float), np.asarray(p1, float)], sections=sections)


def _largest(m):
    if m.body_count <= 1:
        return m
    parts = m.split(only_watertight=False)
    return max(parts, key=lambda q: abs(q.volume))


def _vol(m):
    return 0.0 if m is None or len(m.faces) == 0 else abs(float(m.volume))


def _prism(poly_or_ring, z0=-100.0, z1=100.0):
    poly = poly_or_ring if hasattr(poly_or_ring, "exterior") else Polygon(poly_or_ring)
    p = extrude_clean(poly, z1 - z0); p.apply_translation([0, 0, z0]); return p


def _strip_poly(a, b, half_w):
    a, b = np.asarray(a, float), np.asarray(b, float)
    u = (b - a) / np.linalg.norm(b - a); n = np.array([-u[1], u[0]]) * half_w
    return Polygon([a + n, b + n, b - n, a - n])


def demold_sweep(piece, direction, obstacles, steps=(0.1, 0.3, 1.0, 3.0, 10.0, 40.0)):
    """Overlap volume (mm3) between a piece slid along `direction` and everything left in the flask
    (each obstacle tested separately, which is more robust than testing against their union)."""
    out = []
    for d in steps:
        q = piece.copy(); q.apply_translation(np.asarray(direction, float) * d)
        out.append(round(sum(_vol(_inter(q, o)) for o in obstacles), 3))
    return out


def baton_plane(ext, path, y_lim: float, mode: str, x_c: float):
    """The plane the baton is swept in, as (normal w, a point c0, mid-line planarity rms in mm).

    The extension is a sweep in one plane. For the parametric baton w = x. A real STL can sit a degree or
    two off, so fit the plane of its traced mid-line behind y_lim (the posterior plate edge)."""
    if mode == "parametric":
        return np.array([1.0, 0.0, 0.0]), np.array([x_c, 0.0, 0.0]), 0.0
    cl = centreline_through(ext, path[path[:, 1] < y_lim + 3.0])
    c0 = cl.mean(axis=0)
    _, sv, vt = np.linalg.svd(cl - c0)
    w = vt[2] if vt[2][0] > 0 else -vt[2]
    planarity = float(sv[2] / np.sqrt(len(cl)))
    if np.degrees(np.arccos(min(1.0, abs(w[0])))) < 0.5:
        # within half a degree of x (the published extension is placed on the midline): use x itself.
        # Tilted split planes made the pieces meet on nearly coplanar faces, which the boolean
        # engine misreads; on axis-aligned planes it is exact.
        w, c0 = np.array([1.0, 0.0, 0.0]), np.array([float(cl[:, 0].mean()), 0.0, 0.0])
    return w, c0, planarity


def safety_wire(ext, path, ep, border_y: float, mode: str, w, start_mm: float, end_mm: float,
                wire_w: float = 1.8, wire_t: float = 0.8) -> dict:
    """The safety wire's centreline and solid: from start_mm after the extension root to end_mm before the
    actual tip, centred in the baton, its 1.8 mm face across the baton plane normal w (bent flatwise).

    Shared by the cast route (mold.build) and the direct-print route (directprint.build), so both put the
    wire in the same place. Rw rotates w onto x; wloc is the centreline in that frame."""
    s = arclength(path)
    s_tip = tip_arclength(ext, path)
    tail = path[-1] - path[-4]; tail /= np.linalg.norm(tail)
    s_border = float(s[int(np.argmin(np.abs(path[:, 1] - (border_y + ep.ap_offset))))])
    w0, w1 = s_border + start_mm, s_tip - end_mm
    st = np.linspace(w0, w1, max(int(round((w1 - w0) / 0.25)), 8) + 1)
    wpath = np.column_stack([np.interp(st, s, path[:, k]) for k in range(3)])
    past = st > s[-1]
    wpath[past] = path[-1] + (st[past] - s[-1])[:, None] * tail
    if mode != "parametric":
        wpath = centreline_through(ext, wpath)
    Rw = trimesh.geometry.align_vectors(w, [1.0, 0.0, 0.0])                      # w -> x
    wloc = trimesh.transform_points(wpath, Rw)
    wire = sweep_tube(wloc, np.full(len(wloc), wire_w), np.full(len(wloc), wire_t), n_round=3)
    wire.apply_transform(np.linalg.inv(Rw))
    return {"s": s, "s_tip": s_tip, "tail": tail, "s_border": s_border, "w0": w0, "w1": w1, "st": st,
            "wpath": wpath, "Rw": Rw, "wloc": wloc, "wire": wire}


def build(case_dir: Path, mp: MoldParams | None = None, debug: dict | None = None) -> Path:
    mp = mp or MoldParams()
    case_dir = Path(case_dir)
    ctx = rebuild(case_dir, plate_thickness=mp.final_plate_thickness)
    rec, hfb, outline, lm = ctx["rec"], ctx["hfb"], ctx["outline"], ctx["lm"]
    border_y, z_mid, t = ctx["border_y"], ctx["z_mid"], ctx["pp"].thickness
    ep, chosen, device_id = chosen_extension(rec, mp.proto, mp.device)
    mode = rec.get("extension_mode", "parametric")
    notes = [f"design sent to tooling: {chosen}; extension mode {mode}; final plate thickness {t} mm"]

    # ---------------- the appliance to cast (no number dimples, no groove: the wire is cast in)
    ext, path, xinfo = make_extension(ep, border_y, z_mid, mode)
    if path is None:
        path = extension_path(ep, border_y, z_mid)
        notes.append("custom extension STL: wire and pins follow the parametric centreline at the same position")
    elif mode != "parametric":
        notes.append("published Tübingen extension" + (f", deformed along its fitted skeleton by {xinfo['deformation']}"
                                                         if xinfo.get("deformation") else "")
                     + "; wire and pins are centred in its measured cross-sections")
    s = arclength(path)
    s_tip = tip_arclength(ext, path)                  # the actual tip, which can sit a little past the centreline's end
    tail = path[-1] - path[-4]; tail /= np.linalg.norm(tail)
    tip = path[-1] + tail * (s_tip - s[-1])
    # Draft: the plate is the palate offset along its normals, so on the buccal slope of the ridge its rim
    # leans outward and would lock the oral counter. Trim the rim to vertical walls on the plate outline.
    plate = ctx["plate"]
    rim_poly = Polygon(outline).buffer(-0.05)
    if rim_poly.geom_type == "MultiPolygon":
        rim_poly = max(rim_poly.geoms, key=lambda g: g.area)
    plate_c = _inter(plate, _prism(rim_poly))
    draft_removed = _vol(plate) - _vol(plate_c)
    notes.append(f"rim drafted to vertical walls for demolding: removed {draft_removed:.1f} mm3 "
                 f"({100 * draft_removed / _vol(plate):.1f}% of the plate) from the outward-leaning buccal edge")
    device = boolean_union(plate_c, ext)
    if mode != "parametric":
        device = _diff(device, tissue_clip_solid(outline, hfb))
    x_c = float(np.mean(ext.bounds[:, 0]))
    W = float(ext.bounds[1, 0] - ext.bounds[0, 0])

    # ---------------- parting surface S: the blocked-out palate, held flat behind the scan
    y_floor = float(hfb.ys[1])
    def S(xy):
        q = np.array(xy, float, copy=True)
        q[:, 0] = np.clip(q[:, 0], hfb.xs[1], hfb.xs[-2])
        q[:, 1] = np.clip(q[:, 1], y_floor, hfb.ys[-2])
        return hfb(q)

    lo, hi = device.bounds
    x0, x1 = lo[0] - mp.wall, hi[0] + mp.wall
    y0, y1 = lo[1] - mp.wall, hi[1] + mp.wall
    rect = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]])
    gx, gy = np.meshgrid(np.linspace(x0, x1, 80), np.linspace(y0, y1, 80))
    s_grid = S(np.column_stack([gx.ravel(), gy.ravel()]))
    zt = float(s_grid.max() + mp.base)
    zb = float(min(lo[2], s_grid.min()) - mp.wall)
    upper = solid_between(rect, mp.step, S, lambda xy: np.full(len(xy), zt))
    lower = solid_between(rect, mp.step, lambda xy: np.full(len(xy), zb), S)
    block = solid_between(rect, mp.step, lambda xy: np.full(len(xy), zb), lambda xy: np.full(len(xy), zt))

    # where the extension leaves the plate
    poly = Polygon(outline)
    def edge_y(x):
        g = LineString([(x, y0 - 5), (x, y1 + 5)]).intersection(poly)
        ys = [c[1] for c in (g.coords if g.geom_type == "LineString" else [q for gg in g.geoms for q in gg.coords])]
        return min(ys) if ys else np.nan
    xs_bar = np.linspace(x_c - W / 2 - 3, x_c + W / 2 + 3, 15)
    edges = np.array([edge_y(x) for x in xs_bar])
    y_cut = float(np.nanmin(edges) - 0.8)          # oral piece / extension halves interface (plane)
    y_r = float(np.nanmax(edges) + 0.3)
    s_post = S(np.column_stack([np.repeat(np.linspace(x0, x1, 60), 12), np.tile(np.linspace(y0, y_cut, 12), 60)]))
    ext_post = _inter(ext, _box([x0 - 9, y0 - 9, zb - 9], [x1 + 9, y_cut, zt + 9]))
    z_flat = float(max(s_post.max() + 0.5, ext_post.bounds[1, 2] + 1.0))
    if z_flat > zt - 2.0:
        zt = z_flat + 3.0
        upper = solid_between(rect, mp.step, S, lambda xy: np.full(len(xy), zt))
        block = solid_between(rect, mp.step, lambda xy: np.full(len(xy), zb), lambda xy: np.full(len(xy), zt))

    big_rect = np.array([[x0 - 4, y0 - 4], [x1 + 4, y0 - 4], [x1 + 4, y1 + 4], [x0 - 4, y1 + 4]])
    def band(lo_off, hi_off):
        return solid_between(big_rect, mp.step, lambda xy: S(xy) + lo_off, lambda xy: S(xy) + hi_off)

    BIG = 50.0
    # The extension is a sweep in one plane; its halves come off along that plane's normal w. For the
    # parametric baton w = x. A real STL can sit a degree or two off, so fit the plane of its traced
    # mid-line; a pull along x would then leave a thin undercut along the whole baton.
    w, c0, planarity = baton_plane(ext, path, y_cut, mode, x_c)
    n_cut = np.cross([0.0, 0.0, 1.0], w); n_cut /= np.linalg.norm(n_cut)      # ~ +y; contains w and z
    n_shelf = np.cross(w, [0.0, 1.0, 0.0]); n_shelf /= np.linalg.norm(n_shelf)  # ~ +z; contains w and y
    P_cut, P_shelf = np.array([x_c, y_cut, z_mid]), np.array([x_c, y_cut, z_flat])
    Q = _inter(_half(n_cut, P_cut), _half(n_shelf, P_shelf))                     # extension zone, below the shelf
    R = _box([x_c - W / 2 - 3, y_cut - 0.5, z_mid], [x_c + W / 2 + 3, y_r, float(S(np.array([[x_c, y_cut]]))[0]) + 3.0])
    R = _inter(_diff(R, Q), lower)                                                   # tissue-side lip behind the plate edge

    model = _largest(_diff(_union([_diff(upper, Q), R]), device))
    counter = _diff(_union([lower, _inter(upper, Q)]), R)
    counter = _diff(counter, device)
    # hair-thin slivers between the plate's tissue surface and S (same field, different sampling)
    cap_poly = poly.buffer(-0.4)
    if cap_poly.geom_type == "MultiPolygon":
        cap_poly = max(cap_poly.geoms, key=lambda g: g.area)
    cap = _inter(_prism(cap_poly), band(-0.6, 0.3))
    counter = _diff(counter, cap)

    features, cutters = {}, []
    # sprue at the extension tip
    d = tail if abs(tail[2]) >= 0.3 else np.array([0.0, 0.0, -1.0])
    L = (zb - 1.0 - tip[2]) / d[2]
    p_in, p_out = tip - 1.5 * d, tip + L * d
    cutters += [_cyl(p_in, p_out, mp.sprue_r), _cyl(p_out - 6.0 * d, p_out, mp.funnel_r)]
    features["sprue"] = {"at_tip": tip.round(2).tolist(), "radius_mm": mp.sprue_r, "funnel_radius_mm": mp.funnel_r}
    # vents at the plate rim: anterior apex and both posterior corners (the rim is the high ground when poured)
    cen = outline.mean(axis=0)
    apex = outline[np.argmax(outline[:, 1])]
    left = outline[outline[:, 0] < cen[0]]; right = outline[outline[:, 0] >= cen[0]]
    corners = [left[np.argmin(left[:, 1])], right[np.argmin(right[:, 1])]]
    vents = []
    for q in [apex] + corners:
        inward = (cen - q) / np.linalg.norm(cen - q)
        xy = q + 1.2 * inward
        z = float(S(xy[None, :])[0]) - t / 2.0
        cutters.append(_cyl([xy[0], xy[1], z], [xy[0], xy[1], zb - 1.0], mp.vent_r, sections=16))
        vents.append([round(float(xy[0]), 2), round(float(xy[1]), 2)])
    features["vents"] = {"xy": vents, "radius_mm": mp.vent_r}
    # bow-wire grooves on the parting line, canine region, open toward the model
    # (the same canine points the graded plate holds at full thickness: graded.bow_anchors)
    from .graded import bow_anchors
    bows, bow_wires = [], []
    groove_band, wire_band = band(-mp.bow_groove_d, 0.3), band(-1.2, -0.1)
    for c, u in bow_anchors(lm.crest_ring, outline):
        a = c - 3.0 * u
        # distance from c to the block wall along u, plus 2 mm
        ts = [((x0 if u[0] < 0 else x1) - c[0]) / u[0] if abs(u[0]) > 1e-9 else np.inf,
              ((y0 if u[1] < 0 else y1) - c[1]) / u[1] if abs(u[1]) > 1e-9 else np.inf]
        b = c + u * (min(ts) + 2.0)
        cutters.append(_inter(_prism(_strip_poly(a, b, mp.bow_groove_w / 2)), groove_band))
        bow_wires.append(_inter(_prism(_strip_poly(a, c + u * 12.0, 0.5)), wire_band))
        bows.append({"canine_xy": c.round(2).tolist(), "direction_xy": u.round(3).tolist()})
    features["bow_grooves"] = {"grooves": bows, "width_mm": mp.bow_groove_w, "depth_mm": mp.bow_groove_d,
                               "note": "for 1.1 mm Remaloy bow wire; the wire lies on the parting line, so the oral counter lifts off it"}
    for i, c in enumerate(cutters):
        if not c.is_volume:
            raise RuntimeError(f"cutter {i} not a volume: watertight={c.is_watertight} bodies={c.body_count} faces={len(c.faces)} bounds={c.bounds.round(2).tolist()}")
        counter = _diff(counter, c)

    # split the counter
    boxB = _half(n_cut, P_cut)
    boxL = _half(w, c0)
    split_note = (f"plane through the extension mid-line, normal {np.round(w, 4).tolist()} "
                  f"({np.degrees(np.arccos(min(1.0, abs(w[0])))):.2f} deg from x; mid-line planarity {planarity:.3f} mm rms)")
    oral = _largest(_diff(counter, boxB))
    B = _inter(counter, boxB)
    BL, BR = _largest(_inter(B, boxL)), _largest(_diff(B, boxL))

    # safety wire and its locating pins: from wire_start_mm after the extension root to wire_end_mm before the actual tip
    sw = safety_wire(ext, path, ep, border_y, mode, w, mp.wire_start_mm, mp.wire_end_mm, mp.wire_w, mp.wire_t)
    s_border, w0, w1, st, wpath, Rw, wloc, wire = (sw[k] for k in ("s_border", "w0", "w1", "st", "wpath", "Rw", "wloc", "wire"))
    T_, N_ = path_frames(wpath)
    pinsL, pinsR, stations = [], [], []
    for f in mp.pin_stations:
        i = int(round(f * (len(wpath) - 1)))
        p, Tv, Nv = wpath[i], T_[i], N_[i]
        Tv = Tv - (Tv @ w) * w; Tv /= np.linalg.norm(Tv); Nv = np.cross(w, Tv)
        for side, bucket in ((-1, pinsL), (1, pinsR)):
            tip = p + side * (mp.wire_w / 2 - 0.5) * w
            out_p = p + side * (W / 2 + 2.0) * w
            pin = _cyl(tip, out_p, mp.pin_r, sections=20)
            slot = trimesh.creation.box(extents=[0.5 + 0.2 + mp.clearance, 4.0, mp.wire_t + 2 * mp.clearance])
            M = np.eye(4); M[:3, :3] = np.column_stack([w, Tv, Nv])
            M[:3, 3] = p + side * (mp.wire_w / 2 + mp.clearance - (0.7 + mp.clearance) / 2) * w
            slot.apply_transform(M)
            bucket.append(_diff(pin, slot))
        stations.append({"fraction": f, "at": p.round(2).tolist()})
    BL = _union([BL] + pinsL); BR = _union([BR] + pinsR)
    pins = _union(pinsL + pinsR)
    wire_end_s = tip_arclength(wire, path)            # how far the wire actually reaches along the baton
    wire_span = {"start_mm_after_root": round(float(st[0] - s_border), 2), "end_mm_before_tip": round(float(s_tip - wire_end_s), 2)}
    features["safety_wire"] = {"length_mm": round(float(w1 - w0), 1), "starts_mm_after_border": wire_span["start_mm_after_root"],
                               "ends_mm_before_tip": wire_span["end_mm_before_tip"], "pin_stations": stations, "pin_diameter_mm": 2 * mp.pin_r,
                               "stock": "stainless twisted wire 1.8 x 0.8 mm (Dentaurum), bent flatwise on the jig",
                               "note": "pins leave six 2 mm holes in the sides of the extension; fill them with fresh resin after demolding"}

    # ---------------- wire-bending jig: 1:1 sagittal template (wire groove inside the extension silhouette)
    full = trimesh.transform_points(path[s >= s_border], Rw); full[:, 0] = 0.0
    jp = wloc.copy(); jp[:, 0] = 0.0
    pmin, pmax = full.min(axis=0) - 7.0, full.max(axis=0) + 7.0
    jig = _box([-4.0, pmin[1], pmin[2]], [0.0, pmax[1], pmax[2]])
    jig = _diff(jig, sweep_tube(full, np.full(len(full), 0.8), np.full(len(full), ep.thickness), n_round=3))
    jig = _diff(jig, sweep_tube(jp, np.full(len(jp), mp.wire_w + 0.1), np.full(len(jp), mp.wire_t + 0.3), n_round=2))

    flex = counter
    bodies = {"working_model": model, "counter_oral": oral, "counter_left": BL, "counter_right": BR,
              "device_master": device, "safety_wire": wire, "wire_jig": jig}

    # ---------------- validation (design frame)
    # The cavity is the block minus the union of the pieces. The pieces share faces exactly (split
    # planes, parting surface); in float32 those faces were only nearly coplanar and the engine
    # misclassified whole regions (part of a counter came back as cavity), so all
    # booleans now run in double precision (meshutil.manifold_boolean). The check below compares the
    # cavity with plain arithmetic, so a boolean slip shows up as itself, not as a locked counter.
    void = _diff(block, _union([model, oral, BL, BR]))
    cavity_expected = _vol(block) - sum(_vol(m) for m in (model, oral, BL, BR))
    cast = _diff(device, pins)
    pieces = {"working_model": model, "counter_oral": oral, "counter_left": BL, "counter_right": BR}
    names = list(pieces)
    overlaps = {f"{a}~{b}": round(_vol(_inter(pieces[a], pieces[b])), 3) for i, a in enumerate(names) for b in names[i + 1:]}
    intrude = {k: round(_vol(_inter(cast, v)), 3) for k, v in pieces.items()}
    tol = max(2.0, 5e-4 * _vol(device))
    vbodies = sorted(void.split(only_watertight=False), key=lambda q: -_vol(q))
    vparts = [_vol(q) for q in vbodies]
    main_cavity = vbodies[0]
    # Other empty pieces are reported but do not fail the tooling unless appliance material is in them:
    # the boolean engine can leave a sliver notch (about 1 mm3, under 0.5 mm deep) in a counter's outer face.
    stray = [{"mm3": round(_vol(q), 2), "on_block_wall": bool(np.any(np.isclose(q.bounds, block.bounds, atol=1e-3)))}
             for q in vbodies[1:] if _vol(q) > 0.5]
    val = {"watertight": {k: bool(v.is_watertight) for k, v in bodies.items()},
           "body_counts": {k: int(v.body_count) for k, v in bodies.items()},
           "piece_overlaps_mm3": overlaps, "device_inside_piece_mm3": intrude,
           "pins_clear_of_wire_mm3": round(_vol(_inter(pins, wire)), 3),
           "wire_inside_device_mm3": round(_vol(_diff(wire, device)), 3),
           "cavity_pieces_mm3": [round(v, 2) for v in vparts[:5]], "cavity_volume_mm3": round(vparts[0], 1),
           "cavity_vs_block_minus_pieces_mm3": round(sum(vparts) - cavity_expected, 2),
           "device_volume_mm3": round(_vol(device), 1), "cast_volume_mm3": round(_vol(cast), 1),
           "device_outside_cavity_mm3": round(_vol(_diff(cast, main_cavity)), 3),
           "stray_empty_pieces": stray,
           "draft_removed_mm3": round(draft_removed, 1),
           "tolerance_mm3": round(tol, 2),
           "block_mm": [round(x1 - x0, 1), round(y1 - y0, 1), round(zt - zb, 1)], "min_wall_mm": mp.wall,
           "interfaces": {"oral/extension plane y_mm": round(y_cut, 2), "shelf z_mm": round(z_flat, 2), "left/right split": split_note}}

    # demolding: slide each piece along its pull direction past everything still in the flask
    shot = _union([void] + bow_wires)
    seq = [("counter_left", BL, (-w).tolist(), [model, oral, BR, shot]),
           ("counter_right", BR, w.tolist(), [model, oral, shot]),
           ("counter_oral", oral, [0, 0, -1], [model, shot]),
           ("cast appliance", shot, [0, 0, -1], [model])]
    demold = []
    for name, piece, dirn, obst in seq:
        sweep = demold_sweep(piece, dirn, obst)
        pour_dir = [round(float(dirn[0]), 4), round(float(-dirn[1]), 4), round(float(-dirn[2]), 4)]
        demold.append({"piece": name, "pull_direction_pour_frame": pour_dir, "overlap_mm3_at_mm": dict(zip(["0.1", "0.3", "1", "3", "10", "40"], sweep)),
                       "free": bool(max(sweep) <= tol)})
    val["demold"] = demold
    # the Tübingen pre-delivery rules for a plate worn unsupervised: plate thickness, wire present and its span
    fqc = qc_prototype(device, path, ep, ctx["pp"], outline, rec["landmarks"], kind="final", reference=rec.get("standard"),
                       wire=wire_span, root=root_span(ext, border_y, z_mid, ep.lateral_offset), support=hfb)
    val["final_qc"] = fqc
    if debug is not None:
        debug.update({k: v for k, v in locals().items() if isinstance(v, (trimesh.Trimesh, float, int, list, dict, np.ndarray))})
    val["ok"] = (all(val["watertight"].values()) and all(v == 1 for k, v in val["body_counts"].items())
                 and max(overlaps.values()) <= tol and max(intrude.values()) <= tol and val["pins_clear_of_wire_mm3"] <= 0.05
                 and val["device_outside_cavity_mm3"] <= tol
                 and abs(val["cavity_vs_block_minus_pieces_mm3"]) <= tol
                 and all(x["free"] for x in demold) and fqc["overall"] != "fail")

    # ---------------- export in pour orientation
    Tm = trimesh.transformations.rotation_matrix(np.pi, [1, 0, 0]); Tm[2, 3] = zt
    out = case_dir / "mold"; (out / "alternatives").mkdir(parents=True, exist_ok=True)
    for old in out.glob("counter_*"):
        old.unlink()
    files = {}
    for name, m in bodies.items():
        mm = m.copy()
        if name == "wire_jig":
            mm.apply_transform(trimesh.transformations.rotation_matrix(-np.pi / 2, [0, 1, 0])); mm.apply_translation(-mm.bounds[0])
        else:
            mm.apply_transform(Tm)
        mm.export(out / f"{name}.stl"); files[name] = f"{name}.stl"
    fx = flex.copy(); fx.apply_transform(Tm); fx.export(out / "alternatives" / "counter_onepiece_flex.stl")
    files["counter_onepiece_flex"] = "alternatives/counter_onepiece_flex.stl"
    parts_pour = []
    for k in ("working_model", "counter_oral", "counter_left", "counter_right"):
        m = bodies[k].copy(); m.apply_transform(Tm); parts_pour.append(m)
    b0 = np.min([m.bounds[0] for m in parts_pour], axis=0); b1 = np.max([m.bounds[1] for m in parts_pour], axis=0)
    cl, wc, h = 0.4, 3.0, 0.6 * (b1[2] - b0[2])
    cradle = _diff(_box([b0[0] - cl - wc, b0[1] - cl - wc, -wc], [b1[0] + cl + wc, b1[1] + cl + wc, h]),
                   _box([b0[0] - cl, b0[1] - cl, 0.0], [b1[0] + cl, b1[1] + cl, h + 1.0]))
    cradle.export(out / "cradle.stl"); files["cradle"] = "cradle.stl"
    val["watertight"]["cradle"] = bool(cradle.is_watertight)
    for old in ("counter_mold.stl", "counter_liner.stl", "counter_shell.stl", "counter_assembly.3mf"):
        (out / old).unlink(missing_ok=True)

    manifest = {"schema": "pebp-mold/0.3", "created": datetime.now(timezone.utc).isoformat(timespec="seconds"), "case": str(case_dir),
                "params": asdict(mp), "device": device_id, "extension_params": asdict(ep), "extension_mode": mode,
                "extension_info": {k: v for k, v in xinfo.items() if k in ("source", "variant", "sha256", "deformation", "skeleton_fit_residual_mm")},
                "files": files, "features": features,
                "volumes_mm3": {k: round(_vol(v), 1) for k, v in bodies.items()},
                "pmma_needed_ml": round(vparts[0] / 1000.0 * 1.3, 1),
                "demold_sequence": ["slide counter_left off along -x", "slide counter_right off along +x",
                                    "lift counter_oral straight up", "lift the appliance off the working model"],
                "validation": val, "notes": notes,
                "pour_orientation": "model base on the bench (z = 0), counters on top, sprue opening upward",
                "viewer": {"split_normal_pour": [round(float(w[0]), 5), round(float(-w[1]), 5), round(float(-w[2]), 5)],
                           "split_point_pour": [round(float(c0[0]), 3), round(float(-c0[1]), 3), round(float(zt - c0[2]), 3)]}}
    # register the plate this tooling makes as C<n>, so try-ins and sleep studies can name it
    if val["ok"]:
        same = [c for c in rec["cast_plates"] if c["from"] == device_id and c["params"] == asdict(ep) and c["final_plate_thickness"] == t]
        n = same[-1]["n"] if same else max([c["n"] for c in rec["cast_plates"]], default=0) + 1
        manifest["registered_as"] = f"C{n}"
        (out / "mold_manifest.json").write_text(json.dumps(manifest, indent=2))
        fn = f"cast_{n}.stl"
        device.export(case_dir / fn)                   # design frame, for measuring the cast part against
        entry = {"n": n, "id": f"C{n}", "file": fn, "sha256": rec_io.file_sha256(case_dir / fn), "from": device_id,
                 "params": asdict(ep), "final_plate_thickness": t,
                 "wire": {**wire_span, "stock": f"{mp.wire_w} x {mp.wire_t} mm stainless twisted wire"},
                 "tooling": {"dir": "mold", "manifest_sha256": rec_io.file_sha256(out / "mold_manifest.json")},
                 "qc": fqc, "time": rec_io.now(),
                 "note": "registered when the tooling was generated; it is a physical plate only once it has been cast"}
        rec["cast_plates"] = [c for c in rec["cast_plates"] if c["n"] != n] + [entry]
        rec["cast_plates"].sort(key=lambda c: c["n"])
        from . import airway
        airway.update(rec, case_dir, devices=[entry["id"]])
        rec_io.save(rec, case_dir / "design.json")
    else:
        manifest["registered_as"] = None
        (out / "mold_manifest.json").write_text(json.dumps(manifest, indent=2))
    return out
