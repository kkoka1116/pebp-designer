"""Border relief: take the plate off the three commonest ulcer sites.

PROJECT HEURISTIC, not a published method. Tübingen's own data put mucosal ulcers in 61 of 69 Robin
infants (88%) treated with a palatal plate, mostly where the border overreaches the vestibular fold or
the labial frenulum, and at the maxillary tuberosity, which the authors tie to overextension and to the
plate pivoting there (Weismann et al. 2026, Clin Exp Dent Res, doi:10.1002/cre2.70291). Knechtel 2023
(Children 10:1628, doi:10.3390/children10101628) lists the same sites for pressure marks: the tuberosity,
the mucobuccal fold and the frenulum. Neither paper gives relief distances; every number below is a
starting value to tune on the bench and at try-in.

Three measures, applied together by `PlateParams.border_relief`:

* **Vestibular flange cap.** The rim already follows the crest ring plus `flange` (3 mm since 2026-11-21, was 1.5 mm). On the buccal
  side the tissue rises from the crest into the vestibular fold (in this frame the fold is superior to the
  crest), so the rim climbs the slope. The cap trims the outline wherever the tissue under it is more
  than `flange_rise_mm` above the nearest crest point. It never trims inside the crest ring (the plate
  always covers the ridge), never within `POSTERIOR_KEEP_MM` of the tuberosity line (Tübingen: do not cut
  the posterior border short, it carries the retention), and never within the bow-wire exits.
* **Labial frenulum notch.** A rounded U notch, `frenum_notch_mm` wide, at the front of the arch (the crest
  apex), cut through the labial flange back to `FRENUM_NOTCH_BEHIND_CREST_MM` in front of the crest, so the
  rim clears the frenulum's attachment but the ridge stays covered.
* **Tuberosity relief.** The tissue surface the plate is built against is lowered by `tuberosity_relief_mm`
  over a disc of radius `tuberosity_relief_radius` at each tuberosity, with a smooth (C1) taper: a gap
  between plate and tissue where the plate would pivot, like relief wax on a working model. The border
  stays where it was. The working model in `mold.py` is built from the same relieved surface, so the
  cast plate carries the relief.

The buccal frenula usually sit near the canines, where the bow wires leave the plate; they are not
notched automatically (inference: a notch there would weaken the wire exit), and are flagged instead.
"""
from __future__ import annotations

import numpy as np
from scipy.interpolate import RegularGridInterpolator
from scipy.spatial import cKDTree
from shapely import contains_xy
from shapely.geometry import LineString, Point, Polygon

POSTERIOR_KEEP_MM = 4.0              # no flange trim this close to the tuberosity line
FRENUM_NOTCH_BEHIND_CREST_MM = 0.5   # notch bottom this far labial of the crest (the ridge stays covered)
BOW_KEEP_RADIUS_MM = 3.0             # no flange trim this close to a bow-wire exit
LABEL = ("project heuristic: border relief distances are not published (sites from Weismann 2026 and "
         "Knechtel 2023); tune on the bench and confirm retention at try-in")


def _one(g):
    if g.geom_type == "MultiPolygon":
        g = max(g.geoms, key=lambda q: q.area)
    elif g.geom_type == "GeometryCollection":
        g = max((q for q in g.geoms if q.geom_type == "Polygon"), key=lambda q: q.area)
    return Polygon(g.exterior)


def relieve_outline(outline: np.ndarray, lm, hf, p, tub_y: float) -> tuple[np.ndarray, dict]:
    """The plate outline with the vestibular flange capped and the labial frenulum notched."""
    from .graded import bow_anchors
    foot = Polygon(outline).buffer(0)
    ring = lm.crest_ring
    crest_poly = Polygon(np.vstack([ring[:, :2], [ring[-1, 0], tub_y - 10.0], [ring[0, 0], tub_y - 10.0]])).buffer(0)
    info = {"label": LABEL}

    # --- vestibular flange cap: cells outside the crest ring whose tissue rises too far above the crest
    step = 0.2
    minx, miny, maxx, maxy = foot.bounds
    gx, gy = np.meshgrid(np.arange(minx, maxx + step, step), np.arange(miny, maxy + step, step), indexing="ij")
    pts = np.column_stack([gx.ravel(), gy.ravel()])
    inside = contains_xy(foot, pts[:, 0], pts[:, 1]) & ~contains_xy(crest_poly, pts[:, 0], pts[:, 1])
    d, k = cKDTree(ring[:, :2]).query(pts)
    rise = hf(pts) - ring[k, 2]
    keep = np.ones(len(pts), bool)
    bows = bow_anchors(ring, outline)
    near_bow = np.zeros(len(pts), bool)
    for c, _ in bows:
        near_bow |= np.linalg.norm(pts - c, axis=1) <= BOW_KEEP_RADIUS_MM
    cut = inside & (rise > p.flange_rise_mm) & (pts[:, 1] > tub_y + POSTERIOR_KEEP_MM) & ~near_bow
    keep[cut] = False
    removed_area = float(cut.sum()) * step * step
    if cut.any():
        # rebuild the footprint from the kept cells: keep only the trim region's complement, smoothed
        from shapely.ops import unary_union
        sq = [Point(q).buffer(step * 0.75, cap_style="square") for q in pts[cut]]
        trim = unary_union(sq).buffer(0.3).buffer(-0.3)
        foot = _one(foot.difference(trim).buffer(-0.4, join_style="round").buffer(0.4, join_style="round"))
        foot = _one(foot.union(crest_poly.intersection(Polygon(outline).buffer(0))))   # never uncover the ridge
    info["vestibular_cap"] = {"flange_rise_mm": p.flange_rise_mm, "trimmed_mm2": round(removed_area, 1),
                              "rule": f"rim trimmed where the tissue is over {p.flange_rise_mm} mm above the nearest crest point, "
                                      f"outside the crest ring, more than {POSTERIOR_KEEP_MM} mm in front of the tuberosity "
                                      f"line and over {BOW_KEEP_RADIUS_MM} mm from the bow-wire exits"}

    # --- labial frenulum notch at the front of the arch
    apex = ring[np.argmax(ring[:, 1]), :2]
    half = p.frenum_notch_mm / 2.0
    y_bottom = apex[1] + FRENUM_NOTCH_BEHIND_CREST_MM
    notch = LineString([(apex[0], y_bottom + half), (apex[0], maxy + 5.0)]).buffer(half, cap_style="round")
    before = foot.area
    foot = _one(foot.difference(notch).buffer(-0.3, join_style="round").buffer(0.3, join_style="round"))
    info["labial_frenulum_notch"] = {"x_mm": round(float(apex[0]), 2), "width_mm": p.frenum_notch_mm,
                                     "bottom_y_mm": round(float(y_bottom), 2), "removed_mm2": round(before - foot.area, 1)}
    info["buccal_frenula"] = "not notched: they sit near the bow-wire exits; check at try-in and grind by hand if needed"
    foot = _one(foot.simplify(0.05, preserve_topology=True))
    from .meshutil import clean_ring
    return clean_ring(np.array(foot.exterior.coords)), info


def tuberosity_relief(hf, lm, p) -> tuple[object, dict]:
    """A copy of the (blocked) height field lowered by up to tuberosity_relief_mm at each tuberosity."""
    t, r = p.tuberosity_relief_mm, p.tuberosity_relief_radius
    gx, gy = np.meshgrid(hf.xs, hf.ys, indexing="ij")
    w = np.zeros_like(gx)
    for c in (lm.tuberosity_l, lm.tuberosity_r):
        s = np.clip(1.0 - np.hypot(gx - c[0], gy - c[1]) / r, 0.0, 1.0)
        w = np.maximum(w, s * s * (3.0 - 2.0 * s))          # smoothstep: full depth at the centre, C1 at r
    out = hf.__class__.__new__(hf.__class__)
    out.__dict__.update(hf.__dict__)
    out.z = hf.z - t * w
    out._interp = RegularGridInterpolator((out.xs, out.ys), out.z, bounds_error=False, fill_value=None)
    gzx, gzy = np.gradient(out.z, out.step, out.step)
    out._gx = RegularGridInterpolator((out.xs, out.ys), gzx, bounds_error=False, fill_value=None)
    out._gy = RegularGridInterpolator((out.xs, out.ys), gzy, bounds_error=False, fill_value=None)
    out.relief_mask = w
    return out, {"depth_mm": t, "radius_mm": r,
                 "centres": [np.round(np.asarray(lm.tuberosity_l[:2], float), 2).tolist(),
                             np.round(np.asarray(lm.tuberosity_r[:2], float), 2).tolist()],
                 "rule": "tissue surface lowered (relief, like wax on the model) with a smooth taper; the border is not moved"}
