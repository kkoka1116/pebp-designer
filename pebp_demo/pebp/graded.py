"""Graded plate thickness: a low-profile base plate that stays at full thickness only where it has to.

PROJECT HEURISTIC, not a published method. The Tübingen 2025 protocol (Aretxabaleta et al.,
Bioengineering 12:1063) makes the final plate 2.5-3 mm thick everywhere and warns that below that
the acrylic-plus-wire combination becomes unstable. This module keeps that thickness where the plate
carries load or holds wire, and lets it fall to a thinner floor elsewhere, so the plate is less bulky
in the mouth while its tissue surface still follows the scan exactly (the intaglio is never changed:
only the oral surface moves).

The approach is taken from MoldStudio's graded-thickness field (a separate program,
not included): a thickness per point, rigid keep-outs
that are never thinned, and a bound on |grad T| (the "taper over at least 3x the thickness difference"
fillet rule, MAX_THICKNESS_GRADIENT = 1/3) so every transition is smooth by construction. What differs:

* The material is cold-cure PMMA, cast, not a printed Dental LT plate, and the PEBP plate is passive
  (it guides nothing), so there is no "guiding" mask. The aim here is less bulk, not compliance: at
  1.5 mm and an ~2.5 GPa modulus a tissue-supported PMMA plate is still essentially rigid (inference).
* The keep-outs (held at the base thickness, never thinned) are the PEBP load paths and wire anchors:
    - the baton root: the extension is a cantilever loaded by the tongue base, and its bending moment
      is carried into the plate across the root (published width 11.8 mm, plus the root-pad margin);
    - the cleft block-out: there the plate bridges the gap with no tissue behind it;
    - the two canine bow-wire retention tags (1.1 mm wire, cast in at 0.1-1.2 mm below the tissue
      surface, see `mold.py`): a 1.5 mm wall would leave about 0.3 mm of acrylic over the wire.
  The safety wire itself lies wholly inside the extension (from about 1 cm after the root), which this
  module never touches; the extension stays at least 3 mm thick (Tübingen 2025).
* Everything else falls to the thin floor: the palatal vault, the slopes, the alveolar flange and the
  rim. The field is T(p) = clip(base - g * d(p), thin, base), with d the plan distance to the keep-outs
  (each already widened by its margin) and g = 1/3. That is exactly the max-plus envelope MoldStudio's
  relaxation converges to, so it satisfies the gradient bound with no iteration.

Minimum thickness (project heuristic, see docs/GRADED_PLATE.md for the reasoning):
  THIN_FLOOR_MM = 1.5   no zone of a graded plate may be thinner. Same floor as MoldStudio's
                        MIN_FLEX_THICKNESS; Choi et al. 2012 (Int J Prosthodont 25:53) found PMMA
                        beams under 2 mm much weaker and more flexible than 2 mm and thicker ones, so
                        1.5 mm is only acceptable where the plate is fully backed by tissue, and only
                        after the bench tests in docs/PHASE0_BENCH_PROTOCOL.md.
  MAX_THICKNESS_RATIO = 1.67  as in MoldStudio: base 2.5 mm -> thin 1.5 mm at most in one transition.
"""
from __future__ import annotations

import numpy as np
from shapely import distance as sh_distance, points as sh_points
from shapely.geometry import LineString, Polygon, box
from shapely.ops import unary_union

THIN_FLOOR_MM = 1.5
MAX_THICKNESS_RATIO = 1.67
MAX_THICKNESS_GRADIENT = 1.0 / 3.0
BOW_FRACTIONS = (0.34, 0.66)          # canine positions along the crest ring (as in mold.py)
BOW_TAG_INSIDE_MM = 3.0               # the tag reaches 3 mm palatal of the crest (mold.py)
LABEL = ("project heuristic: graded thickness is not part of the Tübingen protocol (final plate 2.5-3 mm "
         "everywhere, Aretxabaleta 2025); the thin floor needs bench testing before clinical use")


def bow_anchors(crest_ring: np.ndarray, outline: np.ndarray) -> list[tuple[np.ndarray, np.ndarray]]:
    """Canine points on the crest and their outward directions, where the bow wires enter the plate.
    Shared with mold.py so the keep-outs here and the wire grooves there cannot drift apart."""
    ring = np.asarray(crest_ring)
    seg = np.linalg.norm(np.diff(ring[:, :2], axis=0), axis=1)
    cs = np.concatenate([[0], np.cumsum(seg)]); cs /= cs[-1]
    cen = np.asarray(outline).mean(axis=0)
    out = []
    for frac in BOW_FRACTIONS:
        c = ring[int(np.argmin(np.abs(cs - frac))), :2]
        u = (c - cen) / np.linalg.norm(c - cen)
        out.append((c, u))
    return out


def validate(base: float, thin: float) -> None:
    if thin < THIN_FLOOR_MM - 1e-9:
        raise ValueError(f"graded plate: thin thickness {thin} mm is below the {THIN_FLOOR_MM} mm floor")
    if base < thin:
        raise ValueError(f"graded plate: base {base} mm is thinner than the thin zones ({thin} mm)")
    if base / thin > MAX_THICKNESS_RATIO + 1e-9:
        raise ValueError(f"graded plate: base/thin ratio {base / thin:.2f} exceeds {MAX_THICKNESS_RATIO} "
                         f"(one transition may span at most {MAX_THICKNESS_RATIO}:1, e.g. 2.5 -> 1.5 mm)")


class ThicknessField:
    """T(x, y) in mm over the plate footprint."""

    def __init__(self, outline: np.ndarray, base: float, thin: float, *, border_y_mid: float, root_half: float,
                 root_depth: float, cleft_polys: list | None, bow: list, margin: float, bow_radius: float):
        validate(base, thin)
        self.base, self.thin, self.g = float(base), float(thin), MAX_THICKNESS_GRADIENT
        foot = Polygon(outline).buffer(0)
        zones = {"baton_root": box(-root_half, border_y_mid - 5.0, root_half, border_y_mid + root_depth)}
        if cleft_polys:
            cl = unary_union([Polygon(q).buffer(0) for q in cleft_polys if len(q) >= 3]).buffer(margin)
            if not cl.is_empty:
                zones["cleft_block_out"] = cl
        for i, (c, u) in enumerate(bow):
            tag = LineString([c - BOW_TAG_INSIDE_MM * u, c + 12.0 * u]).buffer(bow_radius)
            zones[f"bow_wire_{'right' if c[0] > 0 else 'left'}"] = tag
        self.zones = {k: z.intersection(foot) for k, z in zones.items()}
        self.zones = {k: z for k, z in self.zones.items() if not z.is_empty}
        self.rigid = unary_union(list(self.zones.values()))
        self.foot = foot

    def __call__(self, xy: np.ndarray) -> np.ndarray:
        xy = np.asarray(xy, float)
        if self.base - self.thin < 1e-9:
            return np.full(len(xy), self.base)
        d = sh_distance(self.rigid, sh_points(xy[:, 0], xy[:, 1]))
        return np.clip(self.base - self.g * d, self.thin, self.base)

    def weight(self, xy: np.ndarray) -> np.ndarray:
        """0 where the plate is at the base thickness, 1 where it is at the thin floor."""
        if self.base - self.thin < 1e-9:
            return np.zeros(len(xy))
        return (self.base - self(xy)) / (self.base - self.thin)

    def report(self, step: float = 0.25) -> dict:
        from .plate import _cells
        cells = _cells(self.foot, step)
        t = self(cells)
        a = step * step
        # gradient on the regular grid (finite differences between neighbouring cells)
        gx = np.abs(self(cells + [step, 0.0]) - t) / step
        gy = np.abs(self(cells + [0.0, step]) - t) / step
        return {
            "label": LABEL,
            "base_mm": self.base, "thin_mm": self.thin, "max_gradient": round(self.g, 4),
            "taper_mm": round((self.base - self.thin) / self.g, 2),
            "area_mm2": {"total": round(len(cells) * a, 1), "at_base": round(float((t >= self.base - 1e-6).sum()) * a, 1),
                         "at_thin": round(float((t <= self.thin + 1e-6).sum()) * a, 1)},
            "mean_thickness_mm": round(float(t.mean()), 3),
            "field_min_mm": round(float(t.min()), 3), "field_max_mm": round(float(t.max()), 3),
            "measured_gradient_max": round(float(max(gx.max(), gy.max())), 4),
            "rigid_zones_mm2": {k: round(float(z.area), 1) for k, z in self.zones.items()},
            "rules": {
                "baton_root": "held at base: the baton's bending moment enters the plate across the root",
                "cleft_block_out": "held at base: the plate bridges the cleft with no tissue behind it",
                "bow_wire": "held at base: 1.1 mm bow wire cast in 0.1-1.2 mm below the tissue surface needs the cover",
                "elsewhere": f"falls to {self.thin} mm with |grad T| <= 1/3 (taper over 3x the thickness change)",
            },
        }


def measured_thickness(mesh, top_fn, outline: np.ndarray, step: float = 0.3) -> dict:
    """Wall thickness of a built plate: for every oral-surface vertex, the distance to a dense sampling
    of the tissue surface (so it is the true normal thickness, not the vertical one)."""
    from scipy.spatial import cKDTree
    from .plate import _cells
    foot = Polygon(outline).buffer(0)
    inner = foot.buffer(-1.0)                       # the side walls and their rounding are not wall thickness
    top = _cells(foot.buffer(0.5), 0.15)
    top3 = np.column_stack([top, top_fn(top)])
    v = np.asarray(mesh.vertices)
    zt = top_fn(v[:, :2])
    from shapely import contains_xy
    bottom = v[(v[:, 2] < zt - 0.5) & contains_xy(inner, v[:, 0], v[:, 1])]
    if not len(bottom):
        return {}
    d, _ = cKDTree(top3).query(bottom)
    return {"min_mm": round(float(d.min()), 3), "p05_mm": round(float(np.percentile(d, 5)), 3),
            "median_mm": round(float(np.median(d)), 3), "max_mm": round(float(d.max()), 3), "n_points": int(len(d))}
