"""Base plate: covers the hard palate and alveolar ridge, obturates the cleft."""
from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import trimesh
from shapely.geometry import LineString, Polygon, box

from .landmarks import Landmarks, HeightField
from .meshutil import TISSUE_CLIP_CLEARANCE_MM, slab_from_heightfields, lower_offset, clean_ring
from .graded import ThicknessField, bow_anchors, measured_thickness


@dataclass
class PlateParams:
    thickness: float = 2.0          # Tübingen CAD default 2 mm; final plate 2.5-3 mm
    flange: float = 3.0             # buccal rim beyond the ridge crest, mm (1.5 until 2026-11-21: widened for more grip
                                    # on the segments; the rim only runs over scanned tissue, see plate_outline)
    block_out: float = 2.5          # cleft floor sits this far above the shelf margins, mm
    posterior_bulge: float = 2.0    # crescent bulge of the posterior border at the midline, mm
    grid_step: float = 0.6
    # thickness profile (see graded.py). "uniform" is the Tübingen plate: `thickness` everywhere.
    # "graded" (project heuristic) keeps `thickness` at the baton root, over the cleft and at the bow-wire
    # tags, and lets the rest fall to `thin_thickness` with |grad T| <= 1/3.
    profile: str = "uniform"
    thin_thickness: float = 1.5
    root_rigid_mm: float = 10.0     # graded: plate held at full thickness this far in front of the posterior border
    rigid_margin: float = 2.0       # graded: the cleft and bow-wire keep-outs are widened by this much
    # border relief (see relief.py; project heuristic): vestibular flange cap, labial frenulum notch,
    # tuberosity relief. Off by default.
    border_relief: bool = False
    flange_rise_mm: float = 2.0     # rim trimmed where the tissue is more than this above the crest (1.0 until 2026-11-21)
    frenum_notch_mm: float = 3.0    # width of the labial frenulum notch
    tuberosity_relief_mm: float = 0.3
    tuberosity_relief_radius: float = 4.0


# The baton leaves the plate across the published extension's width at mid-thickness (11.8 mm, measured
# from the supplement STLs; the connector into the plate is 10.4 mm). The posterior border is a crescent
# through the tuberosity line, but where the crest ring closes in front of it (always a little at the
# midline; up to 6 mm on one side of an oblique unilateral-cleft arch) part of the root hangs past the
# plate edge, which leaves an undercut at the root in the mold and an unsupported junction. The plate is
# therefore extended back to the crescent across the root width plus a margin each side.
ROOT_HALF_WIDTH = 5.9        # mm
ROOT_PAD_MARGIN = 1.5        # mm each side; also covers a lateral offset of the extension up to this much
ROOT_PAD_FILLET = 1.5        # mm: radius of the fill where the pad meets the existing edge
ROOT_PAD_ROUND = 1.0         # mm: radius of the pad's own corners on the border
DATA_TOL_MM = 1.0            # a point counts as scanned if a scan vertex is within this distance in plan
FILL_LIMIT = 0.05            # largest share of the pad allowed over extrapolated surface before it is cut back
GAP_WINDOW_MM = 5.0          # the cleft gap carried past the end of the scan is read from the region's last 5 mm


def tuberosity_line_y(lm: Landmarks) -> float:
    return float(0.5 * (lm.tuberosity_l[1] + lm.tuberosity_r[1]))


def _one_polygon(g) -> Polygon:
    if g.geom_type == "MultiPolygon":
        g = max(g.geoms, key=lambda q: q.area)
    elif g.geom_type == "GeometryCollection":
        g = max((q for q in g.geoms if q.geom_type == "Polygon"), key=lambda q: q.area)
    return Polygon(g.exterior)


def _cells(poly: Polygon, step: float) -> np.ndarray:
    """Centres of a regular grid inside `poly`."""
    minx, miny, maxx, maxy = poly.bounds
    gx, gy = np.meshgrid(np.arange(minx + step / 2, maxx, step), np.arange(miny + step / 2, maxy, step), indexing="ij")
    pts = np.column_stack([gx.ravel(), gy.ravel()])
    if not len(pts):
        return pts
    from shapely import contains_xy
    return pts[contains_xy(poly, pts[:, 0], pts[:, 1])]


def _with_root_pad(clipped: Polygon, keep: Polygon, y_back: float, half: float) -> Polygon:
    """`clipped` extended back to y_back (at most to the crescent) over |x| <= half, with rounded corners.
    Changes nothing outside a window around the root, and never reaches behind the crescent."""
    r, r2 = ROOT_PAD_FILLET, ROOT_PAD_ROUND
    pad = box(-half, y_back, half, y_back + 12.0).intersection(keep)
    if pad.is_empty:
        return clipped
    u = _one_polygon(clipped.union(pad))
    win = box(-half - 2 * r, y_back - 1.0, half + 2 * r, y_back + 12.0 + r)
    sm = u.buffer(r, join_style="round").buffer(-r, join_style="round")        # fill the notch at the old edge
    sm = sm.buffer(-r2, join_style="round").buffer(r2, join_style="round")     # round the pad's corners
    out = u.difference(win).union(sm.intersection(win)).union(clipped).intersection(keep)
    return _one_polygon(out.buffer(0))


def plate_outline(lm: Landmarks, p: PlateParams, support: HeightField | None = None) -> tuple[np.ndarray, dict]:
    """Plate footprint in the occlusal plane. With `support` (the blocked height field), the root pad is
    checked against the scan: the share of it over measured tissue, over the cleft block-out, and over
    extrapolated surface; if too much is extrapolated the pad is cut back to the supported part."""
    ring = lm.crest_ring[:, :2]
    # smooth the ring a little (scanner noise) and close it with a straight posterior line
    k = 5
    pad = np.vstack([np.repeat(ring[:1], k, 0), ring, np.repeat(ring[-1:], k, 0)])
    sm = np.column_stack([np.convolve(pad[:, i], np.ones(2 * k + 1) / (2 * k + 1), mode="valid") for i in range(2)])
    base = Polygon(sm).buffer(0)
    if base.geom_type == "MultiPolygon":
        base = max(base.geoms, key=lambda g: g.area)
    outer = base.buffer(p.flange, join_style="round")
    flange_info = {"flange_mm": p.flange}
    if support is not None and getattr(support, "data_dist", None) is not None:
        # the rim beyond the crest is built only over scanned tissue: where the scan stops short of crest + flange
        # (often on the buccal side) the surface would be extrapolated, so the rim is cut back to the scan's edge
        from shapely.geometry import box as _box
        from shapely.ops import unary_union
        band = outer.difference(base)
        st = float(support.step)
        cells = _cells(band, st) if band.area > 0 else np.zeros((0, 2))
        if len(cells):
            kind = support.support(cells, DATA_TOL_MM)
            bad = cells[kind == "fill"]
            if len(bad):
                h = 0.5 * st + 0.05
                hole = unary_union([_box(x - h, y - h, x + h, y + h) for x, y in bad])
                trimmed = _one_polygon(outer.difference(hole.difference(base)).buffer(-0.3, join_style="round")
                                       .buffer(0.3, join_style="round").union(base))
                flange_info["unscanned_trimmed_mm2"] = round(float(outer.area - trimmed.area), 1)
                outer = trimmed
            flange_info["band_on_scan_share"] = round(float((kind == "scan").sum()) / len(kind), 3)
        flange_info["rule"] = "rim beyond the crest only over scanned tissue (a scan vertex within 1 mm in plan)"
    # posterior border: a crescent through the tuberosity line, bulging posteriorly at the midline
    tub_y = tuberosity_line_y(lm)
    minx, miny, maxx, maxy = outer.bounds
    xs = np.linspace(minx - 1, maxx + 1, 41)
    t = (xs - xs[0]) / (xs[-1] - xs[0])
    border_y = tub_y - p.posterior_bulge * (1 - (2 * t - 1) ** 2)
    keep = Polygon(np.vstack([np.column_stack([xs, border_y]), [[xs[-1], maxy + 5], [xs[0], maxy + 5]]]))
    clipped = outer.intersection(keep)
    if clipped.geom_type == "MultiPolygon":
        clipped = max(clipped.geoms, key=lambda g: g.area)
    clipped = Polygon(clipped.exterior)
    y_post_mid = float(tub_y - p.posterior_bulge)
    half = ROOT_HALF_WIDTH + ROOT_PAD_MARGIN
    info = {"posterior_border_y_mid": y_post_mid, "tuberosity_line_y": round(float(tub_y), 2), "flange": flange_info}
    pad_info = {"half_width_mm": half,
                "rule": "posterior border extended to the crescent across the baton root (published width 11.8 mm "
                        f"plus {ROOT_PAD_MARGIN} mm each side) so the root is not left hanging past the plate edge"}
    # where the surface behind the old edge is not measured (a hole in the scan outside the cleft
    # block-out), the pad's back edge is moved forward until it sits on supported surface
    first = None
    for short in np.arange(0.0, 3.0 + 1e-9, 0.25):
        padded = _with_root_pad(clipped, keep, y_post_mid + short, half)
        added = padded.difference(clipped)
        share = None
        if support is not None and added.area > 0.05:
            cells = _cells(added, 0.2)
            kind = support.support(cells, DATA_TOL_MM)
            share = {k: round(float((kind == k).sum()) / max(len(kind), 1), 3) for k in ("scan", "block-out", "fill")}
        if first is None:
            first = (round(float(added.area), 1), share)
        if share is None or share["fill"] <= FILL_LIMIT:
            break
    else:
        padded, added, short, share = clipped, Polygon(), None, None
    if added.area <= 0.05:
        padded, added = clipped, Polygon()       # leave the outline exactly as it was
    pad_info["area_mm2"] = round(float(added.area), 1)
    if share is not None:
        pad_info["support"] = share
    if support is not None:
        pad_info["support_rule"] = (f"'scan': a scan vertex within {DATA_TOL_MM} mm in plan; 'block-out': in the digital wax over "
                                    f"the cleft; 'fill': extrapolated surface. Cut back if over {FILL_LIMIT:.0%} is fill")
    if short is None or short > 0:
        pad_info["cut_back"] = {"short_of_crescent_mm": None if short is None else round(float(short), 2),
                                "full_pad_mm2": first[0], "full_pad_support": first[1],
                                "note": "the surface behind the old edge is not in the scan; the pad stops "
                                        + ("short of the crescent" if short is not None else "altogether") + " rather than rest on extrapolated surface"}
    info["root_pad_mm2"] = pad_info["area_mm2"]
    info["root_pad"] = pad_info
    outline = clean_ring(np.array(padded.exterior.coords))   # the pad's unions can leave a near-duplicate vertex
    return outline, info


def thickness_field(lm: Landmarks, outline: np.ndarray, p: PlateParams, cleft_polys) -> ThicknessField:
    return ThicknessField(outline, p.thickness, p.thin_thickness, border_y_mid=tuberosity_line_y(lm) - p.posterior_bulge,
                          root_half=ROOT_HALF_WIDTH + ROOT_PAD_MARGIN + p.rigid_margin, root_depth=p.root_rigid_mm,
                          cleft_polys=cleft_polys, bow=bow_anchors(lm.crest_ring, outline), margin=p.rigid_margin,
                          bow_radius=1.0 + p.rigid_margin)


def design_base_plate(lm: Landmarks, hf: HeightField, p: PlateParams | None = None):
    p = p or PlateParams()
    y_post_mid = tuberosity_line_y(lm) - p.posterior_bulge
    floor = lm.cleft_margin_z + p.block_out if lm.cleft_present else None
    cleft_poly, follow = None, None
    if lm.cleft_present and lm.cleft_polygon is not None:
        polys = lm.cleft_polygon if isinstance(lm.cleft_polygon, list) else [lm.cleft_polygon]
        polys = [np.asarray(q) for q in polys if q is not None and len(q) >= 3]
        if polys:
            # extend the most posterior cleft region past the plate border, keeping its last
            # measured width, so the block-out covers the whole posterior edge
            k = int(np.argmin([q[:, 1].min() for q in polys]))
            cp = Polygon(polys[k]).buffer(0)
            miny = cp.bounds[1]
            band = polys[k][np.abs(polys[k][:, 1] - miny) < 1.01]
            x0, x1 = float(band[:, 0].min()), float(band[:, 0].max())
            y_ext = y_post_mid - 3.0
            if y_ext < miny:
                ext = Polygon([(x0, miny + 0.5), (x1, miny + 0.5), (x1, y_ext), (x0, y_ext)])
                u = cp.union(ext)
                if u.geom_type == "MultiPolygon":
                    u = max(u.geoms, key=lambda g: g.area)
                polys[k] = np.array(u.exterior.coords)
            cleft_poly = polys
            # The region is traced from wall points above the rise threshold, so its last bands can be a
            # sliver of one wall (a fraction of a millimetre where the open gap one band forward is much wider)
            # and the rectangle above then misses the gap. Where the gap itself runs to the end of the scan,
            # it is also followed straight back (HeightField.blocked, follow_gap).
            follow = {"region": k, "from_y": miny + GAP_WINDOW_MM, "to_y": y_ext, "tol": DATA_TOL_MM}
    # holes in the scan inside the arch are blocked out the same way, each on its own rim level: the design does
    # not depend on whether a gap is called a cleft
    holes = [(h["polygon"], h["rim_z"] + p.block_out) for h in lm.holes]
    hfb = hf.blocked(cleft_poly, floor, follow_gap=follow, extra_regions=holes) if lm.blockout_present else hf
    holes_info = None
    if holes:
        holes_info = {"count": len(holes), "area_mm2": round(float(sum(h["area_mm2"] for h in lm.holes)), 1),
                      "rule": f"enclosed gap in the scan inside the arch: intaglio held at or below its rim "
                               f"level + {p.block_out} mm, like the cleft block-out"}
    keep_out = ((cleft_poly or []) if lm.cleft_present else []) + [np.asarray(h["polygon"]) for h in lm.holes]
    outline, info = plate_outline(lm, p, support=hfb)
    if holes_info:
        info["scan_holes_blocked"] = holes_info
    if p.border_relief:
        from .relief import relieve_outline, tuberosity_relief
        outline, rinfo = relieve_outline(outline, lm, hfb, p, tuberosity_line_y(lm))
        hfb, tinfo = tuberosity_relief(hfb, lm, p)
        info["border_relief"] = {**rinfo, "tuberosity_relief": tinfo}
    gap = getattr(hfb, "gap_mask", None)
    if gap is not None:
        cells = _cells(Polygon(outline).buffer(0), 0.2)
        i = np.clip(np.rint((cells[:, 0] - hfb.xs[0]) / hfb.step).astype(int), 0, len(hfb.xs) - 1)
        j = np.clip(np.rint((cells[:, 1] - hfb.ys[0]) / hfb.step).astype(int), 0, len(hfb.ys) - 1)
        info["cleft_gap_followed"] = {
            "under_plate_mm2": round(float(gap[i, j].sum()) * 0.04, 1),
            "rule": f"the open cleft gap (no scan vertex within {DATA_TOL_MM} mm, in the last {GAP_WINDOW_MM:.0f} mm of the "
                    "cleft region) carried straight back through unscanned cells to 3 mm behind the posterior border, "
                    "on the wax floor: the cleft is taken to continue past the end of the scan"}
    # the oral surface is the exact offset of the (blocked) intaglio: a normal offset folds over itself at the
    # edge of the cleft block-out, where the surface bends more tightly than the plate is thick
    bounds = (hfb.xs[1], hfb.xs[-2], hfb.ys[1], hfb.ys[-2])
    field = None
    if p.profile == "graded":
        field = thickness_field(lm, outline, p, keep_out or None)
        # the oral surface blends the exact offsets at the two thicknesses: both are fold-free height fields,
        # so their blend is too, and it equals each one exactly where the field is at that thickness
        def bottom(xy):
            w = field.weight(xy)
            z = lower_offset(hfb, xy, p.thickness, bounds)
            m = w > 1e-9
            if m.any():
                z[m] = (1 - w[m]) * z[m] + w[m] * lower_offset(hfb, xy[m], p.thin_thickness, bounds)
            return z
    elif p.profile == "uniform":
        bottom = lambda xy: lower_offset(hfb, xy, p.thickness, bounds)
    else:
        raise ValueError(f"unknown plate profile {p.profile!r}: 'uniform' or 'graded'")
    mesh = slab_from_heightfields(outline, p.grid_step, hfb, hfb.normal, p.thickness, bottom_fn=bottom)
    if field is not None:
        g = field.report()
        g["measured_wall"] = measured_thickness(mesh, hfb, outline)
        info["thickness_profile"] = g
        mesh.metadata["thickness_field"] = field          # not exported; for plots and checks
    info.update({
        "params": asdict(p),
        "cleft_floor_z": None if floor is None else round(float(floor), 2),
        "outline_points": len(outline),
        "volume_mm3": round(float(mesh.volume), 1),
        "watertight": bool(mesh.is_watertight),
    })
    return mesh, hfb, outline, info


def tissue_clip_solid(outline: np.ndarray, hfb: HeightField, height: float = 20.0, margin: float = 3.0, step: float = 0.8,
                      clearance: float = TISSUE_CLIP_CLEARANCE_MM) -> trimesh.Trimesh:
    """Solid occupying the space above the (blocked) intaglio over the plate footprint: anything of an
    extension that falls inside it would sit in tissue (or in the cleft above the wax floor) and is cut away.
    Its floor runs `clearance` (5 µm) above the intaglio rather than on it, so the cut never grazes the
    plate's fitting surface, which pinched the mesh (see meshutil.CUTTER_CLEARANCE_MM). The cut moves by
    5 µm, far below what a scanner or printer resolves (about 2 mm³ on a 3,600 mm³ synthetic device)."""
    poly = Polygon(outline).buffer(margin)
    ring = np.array(poly.exterior.coords)[:-1]
    top = lambda xy: hfb(xy) + height
    nrm = lambda xy: np.tile([0.0, 0.0, 1.0], (len(xy), 1))
    return slab_from_heightfields(ring, step, top, nrm, height - clearance, bottom_fn=lambda xy: hfb(xy) + clearance)
