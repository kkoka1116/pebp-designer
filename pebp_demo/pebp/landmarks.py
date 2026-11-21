"""Orient an infant maxillary scan and find the landmarks the plate design needs.

Landmarks (all in the oriented frame, mm):
  crest_ring      ordered alveolar ridge crest polyline, left tuberosity (-x) -> apex -> right tuberosity (+x)
  apex            most anterior midline crest point
  incisive_papilla palate point 2.5 mm posterior of the apex on the midline
  tuberosity_r/l  posterior ends of the crest ring
  occlusal_plane  z = 0 after orientation (least-squares plane through the crest)
  cleft           dict with present flag, margin level, width profile, region polygon
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import trimesh
from scipy.interpolate import griddata, RegularGridInterpolator
from scipy.ndimage import gaussian_filter
from shapely.geometry import Polygon, Point, LineString


@dataclass
class Landmarks:
    transform: np.ndarray                 # 4x4 scan -> oriented frame
    crest_ring: np.ndarray                # N x 3
    apex: np.ndarray
    incisive_papilla: np.ndarray
    tuberosity_r: np.ndarray
    tuberosity_l: np.ndarray
    cleft_present: bool
    cleft_margin_z: float
    cleft_width_profile: list             # [(y, width_mm)]
    cleft_polygon: object                 # (M,2) array, a list of them, or None
    notes: list = field(default_factory=list)
    holes: list = field(default_factory=list)  # [{"polygon": (M,2), "rim_z": z, "area_mm2": a}] enclosed gaps in the scan

    @property
    def blockout_present(self) -> bool:
        """True if any gap in the palatal surface is blocked out: a raised gap (cleft) or a hole in the scan."""
        return bool(self.cleft_present or self.holes)

    def to_json(self) -> dict:
        return {
            "transform_scan_to_oriented": self.transform.tolist(),
            "apex": self.apex.round(3).tolist(),
            "incisive_papilla": self.incisive_papilla.round(3).tolist(),
            "tuberosity_right": self.tuberosity_r.round(3).tolist(),
            "tuberosity_left": self.tuberosity_l.round(3).tolist(),
            "intertuberosity_width_mm": round(float(np.linalg.norm(self.tuberosity_l[:2] - self.tuberosity_r[:2])), 2),
            "arch_depth_mm": round(float(self.apex[1] - 0.5 * (self.tuberosity_l[1] + self.tuberosity_r[1])), 2),
            "cleft_present": self.cleft_present,
            "cleft_margin_z_mm": round(self.cleft_margin_z, 2),
            "cleft_width_profile": [(round(float(y), 1), round(float(w), 2)) for y, w in self.cleft_width_profile],
            "crest_ring_points": len(self.crest_ring),
            "scan_holes": [{"area_mm2": round(float(h["area_mm2"]), 1), "rim_z_mm": round(float(h["rim_z"]), 2),
                            "centroid": np.asarray(h["polygon"]).mean(axis=0).round(1).tolist()} for h in self.holes],
            "blockout_present": self.blockout_present,
            "notes": self.notes,
        }


class HeightField:
    """Smoothed regular-grid height field z(x, y) of the oriented scan."""

    def __init__(self, verts: np.ndarray, step: float = 0.4, smooth_mm: float = 0.5):
        self.step = step
        x0, y0 = verts[:, :2].min(axis=0) - step
        x1, y1 = verts[:, :2].max(axis=0) + step
        self.xs = np.arange(x0, x1, step)
        self.ys = np.arange(y0, y1, step)
        gx, gy = np.meshgrid(self.xs, self.ys, indexing="ij")
        z = griddata(verts[:, :2], verts[:, 2], (gx, gy), method="linear")
        zn = griddata(verts[:, :2], verts[:, 2], (gx, gy), method="nearest")
        z = np.where(np.isnan(z), zn, z)
        self.z_raw = z
        # plan distance from each cell to the nearest scan vertex: tells measured surface from fill
        from scipy.spatial import cKDTree
        self.data_dist = cKDTree(verts[:, :2]).query(np.column_stack([gx.ravel(), gy.ravel()]))[0].reshape(gx.shape)
        self.block_mask = None
        self.gap_mask = None
        self.z = gaussian_filter(z, sigma=smooth_mm / step)
        self._interp = RegularGridInterpolator((self.xs, self.ys), self.z, bounds_error=False, fill_value=None)
        gzx, gzy = np.gradient(self.z, step, step)
        self._gx = RegularGridInterpolator((self.xs, self.ys), gzx, bounds_error=False, fill_value=None)
        self._gy = RegularGridInterpolator((self.xs, self.ys), gzy, bounds_error=False, fill_value=None)

    def __call__(self, xy: np.ndarray) -> np.ndarray:
        return self._interp(xy)

    def support(self, xy: np.ndarray, tol: float = 1.0) -> np.ndarray:
        """Per point: 'scan' if a scan vertex lies within `tol` mm in plan, 'block-out' if the point is in
        the digital wax over the cleft (the surface there is set by the block-out, not the scan), else
        'fill' (outside the scan: the surface is extrapolated)."""
        xy = np.atleast_2d(xy)
        i = np.clip(np.rint((xy[:, 0] - self.xs[0]) / self.step).astype(int), 0, len(self.xs) - 1)
        j = np.clip(np.rint((xy[:, 1] - self.ys[0]) / self.step).astype(int), 0, len(self.ys) - 1)
        out = np.full(len(xy), "fill", dtype=object)
        if getattr(self, "block_mask", None) is not None:
            out[self.block_mask[i, j]] = "block-out"
        if getattr(self, "data_dist", None) is not None:
            out[self.data_dist[i, j] <= tol] = "scan"
        return out

    def blocked(self, poly_xy, floor_z: float, margin: float = 0.8, resmooth_mm: float = 0.8,
                follow_gap: dict | None = None, extra_regions: list | None = None) -> "HeightField":
        """Copy of this field with the cleft region(s) clamped to a flat floor (digital wax block-out).

        poly_xy: one (N,2) polygon or a list of them.
        extra_regions: [(polygon, floor_z)], each region clamped to its own floor (holes in the scan).
        follow_gap: {"region": k, "from_y": y0, "to_y": y1, "tol": t}. The open gap of region k (cells in its
            block-out with no scan vertex within t mm in plan, behind y0) is carried straight back, column by
            column, through cells that are also unscanned, as far as y1. Those cells are set to the floor: the
            cleft continues past the end of the scan, and the wax floor, not extrapolated surface, is under the
            plate there. A column stops at its first scanned cell, so no measured tissue is raised.
        """
        hf = HeightField.__new__(HeightField)
        hf.step, hf.xs, hf.ys, hf.z_raw = self.step, self.xs, self.ys, self.z_raw
        extra = list(extra_regions or [])
        hf.data_dist = getattr(self, "data_dist", None)
        hf.block_mask = None
        hf.gap_mask = None
        z = self.z.copy()
        polys = [] if poly_xy is None else (poly_xy if isinstance(poly_xy, list) else [poly_xy])
        polys = [Polygon(q).buffer(margin) for q in polys if q is not None and len(q) >= 3]
        if polys:
            from shapely.ops import unary_union
            from shapely import contains_xy
            poly = unary_union(polys)
            gx, gy = np.meshgrid(self.xs, self.ys, indexing="ij")
            pts = np.column_stack([gx.ravel(), gy.ravel()])
            mask = np.array([poly.contains(Point(p)) for p in pts]).reshape(gx.shape)
            z[mask] = np.minimum(z[mask], floor_z)
            if follow_gap is not None and hf.data_dist is not None:
                src_poly = polys[follow_gap["region"]]
                open_ = hf.data_dist > follow_gap["tol"]
                src = contains_xy(src_poly, gx, gy) & open_ & (gy <= follow_gap["from_y"])
                j_stop = int(np.searchsorted(self.ys, follow_gap["to_y"]))
                gap = np.zeros_like(mask)
                for i in np.where(src.any(axis=1))[0]:
                    j = int(np.argmax(src[i])) - 1          # ys ascend with j: walk posteriorly from the rearmost gap cell
                    while j >= j_stop and open_[i, j]:
                        gap[i, j] = not mask[i, j]
                        j -= 1
                z[gap] = floor_z
                mask |= gap
                hf.gap_mask = gap
        else:
            mask = None
        if extra:
            # further regions with their own floors (holes in the scan: their rim level plus the block-out)
            from shapely import contains_xy
            gx, gy = np.meshgrid(self.xs, self.ys, indexing="ij")
            mask = np.zeros(z.shape, bool) if mask is None else mask
            for poly_k, floor_k in extra:
                mk = contains_xy(Polygon(poly_k).buffer(margin), gx, gy)
                z[mk] = np.minimum(z[mk], floor_k)
                mask |= mk
        if mask is not None:
            z = gaussian_filter(z, sigma=resmooth_mm / self.step)
            hf.block_mask = mask
        hf.z = z
        hf._interp = RegularGridInterpolator((hf.xs, hf.ys), hf.z, bounds_error=False, fill_value=None)
        gzx, gzy = np.gradient(hf.z, hf.step, hf.step)
        hf._gx = RegularGridInterpolator((hf.xs, hf.ys), gzx, bounds_error=False, fill_value=None)
        hf._gy = RegularGridInterpolator((hf.xs, hf.ys), gzy, bounds_error=False, fill_value=None)
        return hf

    def normal(self, xy: np.ndarray) -> np.ndarray:
        gx = self._gx(xy)
        gy = self._gy(xy)
        n = np.column_stack([-gx, -gy, np.ones_like(gx)])
        return n / np.linalg.norm(n, axis=1)[:, None]


def _pca_axes(v: np.ndarray):
    c = v.mean(axis=0)
    u, s, vt = np.linalg.svd(v - c, full_matrices=False)
    return c, vt  # rows: principal axes, descending variance


def _crest_ring_from_oriented(v: np.ndarray, deg_step: float = 2.0, tol: float = 1.6):
    """Polar sweep: for each direction from the arch centre, the lowest point is the crest."""
    zmin = np.percentile(v[:, 2], 1)
    low = v[v[:, 2] < zmin + 1.0]
    centre = low[:, :2].mean(axis=0)
    ang = np.degrees(np.arctan2(v[:, 1] - centre[1], v[:, 0] - centre[0]))
    pts, angs = [], []
    for a in np.arange(-180, 180, deg_step):
        sel = np.abs(((ang - a + 180) % 360) - 180) < deg_step
        if sel.sum() < 3:
            continue
        cand = v[sel]
        i = np.argmin(cand[:, 2])
        if cand[i, 2] < zmin + tol:
            pts.append(cand[i])
            angs.append(a)
    pts = np.array(pts)
    angs = np.array(angs)
    # find the largest angular gap (the open posterior side) and order the ring from one end to the other
    order = np.argsort(angs)
    angs, pts = angs[order], pts[order]
    gaps = np.diff(np.concatenate([angs, [angs[0] + 360]]))
    k = int(np.argmax(gaps))
    pts = np.roll(pts, -(k + 1), axis=0)
    pts = _extend_ring_end(v, pts, zmin + tol, end="start")
    pts = _extend_ring_end(v, pts, zmin + tol, end="end")
    return pts, centre


def _extend_ring_end(v: np.ndarray, ring: np.ndarray, zmax: float, end: str, step: float = 1.0, across: float = 3.0):
    """March past a ring end along the arm direction while low (crest) points keep appearing."""
    pts = list(ring) if end == "end" else list(ring[::-1])
    for _ in range(40):
        p0, p1 = pts[-4], pts[-1]
        d = p1[:2] - p0[:2]
        if np.linalg.norm(d) < 1e-6:
            break
        d /= np.linalg.norm(d)
        probe = p1[:2] + step * d
        perp = np.array([-d[1], d[0]])
        rel = v[:, :2] - probe
        along = rel @ d
        across_d = rel @ perp
        sel = (np.abs(along) < 0.6 * step) & (np.abs(across_d) < across)
        if sel.sum() < 2:
            break
        cand = v[sel]
        i = np.argmin(cand[:, 2])
        if cand[i, 2] > zmax:
            break
        pts.append(cand[i])
    arr = np.array(pts)
    return arr if end == "end" else arr[::-1]


class _PlanGrid:
    """Smoothed plan-view height grid of a roughly levelled scan (z up), for the anatomical cues below.

    `inside` is the scan's footprint with interior holes filled: a hole in the scan (missing data) is bridged
    by linear interpolation, so it reads as surface at its rim level, never as an edge of the scan."""

    def __init__(self, v: np.ndarray, step: float = 0.5, smooth_mm: float = 1.0):
        from scipy.ndimage import binary_fill_holes, binary_closing, distance_transform_edt
        from scipy.spatial import cKDTree
        self.step = step
        lo = v[:, :2].min(axis=0) - 2 * step
        hi = v[:, :2].max(axis=0) + 2 * step
        self.xs = np.arange(lo[0], hi[0], step)
        self.ys = np.arange(lo[1], hi[1], step)
        gx, gy = np.meshgrid(self.xs, self.ys, indexing="ij")
        dd = cKDTree(v[:, :2]).query(np.column_stack([gx.ravel(), gy.ravel()]))[0].reshape(gx.shape)
        data = binary_closing(dd <= 1.0, iterations=2)
        self.inside = binary_fill_holes(data)
        self.hole = self.inside & ~(dd <= 1.0)
        z = griddata(v[:, :2], v[:, 2], (gx, gy), method="linear")
        zn = griddata(v[:, :2], v[:, 2], (gx, gy), method="nearest")
        z = np.where(np.isnan(z), zn, z)
        z = gaussian_filter(z, sigma=smooth_mm / step)
        self.z = np.where(self.inside, z, np.nan)
        self._zfill = z
        self.depth = distance_transform_edt(self.inside) * step      # mm in from the scan's outer edge

    def at(self, xy: np.ndarray) -> np.ndarray:
        from scipy.ndimage import map_coordinates
        i = (xy[:, 0] - self.xs[0]) / self.step
        j = (xy[:, 1] - self.ys[0]) / self.step
        z = map_coordinates(self._zfill, [i, j], order=1, mode="nearest")
        m = map_coordinates(self.inside.astype(float), [i, j], order=1, cval=0.0)
        return np.where(m > 0.999, z, np.nan)

    def vault_centre(self) -> np.ndarray:
        """Centroid of the upper half of the interior surface (at least 3 mm in from the scan's edge): the palatal
        dome, concave seen from the mouth. Ragged soft tissue at the scan's border is left out by the edge margin."""
        m = self.inside & (self.depth >= 3.0)
        if m.sum() < 20:
            m = self.inside
        zz = self.z[m]
        sel = np.zeros_like(m)
        sel[m] = zz >= np.median(zz)
        gx, gy = np.meshgrid(self.xs, self.ys, indexing="ij")
        return np.array([gx[sel].mean(), gy[sel].mean()])


def _ray_crest(r: np.ndarray, z: np.ndarray, drop: float = 1.2, rise: float = 0.7, reach: float = 8.0):
    """Index of the alveolar crest along one ray from the vault outward, or None.

    Going out from the palatal dome the surface falls to the ridge crest and then rises again over the buccal
    (or labial) slope toward the vestibule. The crest is the first local low point at least `drop` mm below the
    highest point passed so far that is followed by a rise of `rise` mm within `reach` mm. Beyond the vestibule a
    raw scan often carries lip or cheek tissue that hangs lower than the crest; taking the first such low point
    (not the lowest point on the ray) keeps the crest on the ridge. Rays through the open posterior side of the
    arch find no rise and return None."""
    ok = ~np.isnan(z)
    if ok.sum() < 8:
        return None
    n = int(np.argmin(ok)) if not ok.all() else len(z)
    r, z = r[:n], z[:n]
    if n < 8:
        return None
    dr = r[1] - r[0]
    w = max(1, int(round(1.0 / dr)))
    L = int(round(reach / dr))
    run_max = np.maximum.accumulate(z)
    for i in range(w, n - 1):
        if z[i] > z[max(0, i - w):i + w + 1].min() + 1e-9:
            continue
        if run_max[i] - z[i] < drop:
            continue
        ahead = z[i + 1:i + 1 + L]
        if len(ahead) and ahead.max() - z[i] >= rise:
            return i
    return None


def _crest_ring_radial(v: np.ndarray, deg_step: float = 2.0, grid: "_PlanGrid | None" = None):
    """Alveolar crest as an open ring, from rays cast out of the palatal vault (see _ray_crest).

    Returns (ring N x 3 ordered from one posterior end round the arch to the other, vault centre xy, gap
    direction): the arch is a horseshoe, and the widest run of angles with no crest is its open posterior side.
    None if fewer than a quarter of the rays find a crest."""
    g = grid or _PlanGrid(v)
    c = g.vault_centre()
    rr = np.arange(0.0, 45.0, 0.25)
    angs, pts = [], []
    for a in np.arange(-180.0, 180.0, deg_step):
        d = np.array([np.cos(np.radians(a)), np.sin(np.radians(a))])
        xy = c + rr[:, None] * d
        z = g.at(xy)
        i = _ray_crest(rr, z)
        if i is not None:
            angs.append(a)
            pts.append([xy[i, 0], xy[i, 1], z[i]])
    if len(pts) < 0.25 * 360 / deg_step:
        return None
    angs, pts = np.array(angs), np.array(pts)
    if v is not None and len(v):
        # the grid is smoothed: put each crest point on the lowest scan vertex within 0.8 mm in plan
        from scipy.spatial import cKDTree
        tree = cKDTree(v[:, :2])
        for k, nb in enumerate(tree.query_ball_point(pts[:, :2], 0.8)):
            if nb:
                q = v[nb][np.argmin(v[nb, 2])]
                pts[k] = q
    # drop isolated jumps in crest distance: a ray that slipped past the ridge onto vestibular tissue
    rad = np.linalg.norm(pts[:, :2] - c, axis=1)
    k = 5
    med = np.array([np.median(rad[np.abs(((angs - a + 180) % 360) - 180) <= k * deg_step]) for a in angs])
    keep = np.abs(rad - med) < 3.0
    angs, pts = angs[keep], pts[keep]
    order = np.argsort(angs)
    angs, pts = angs[order], pts[order]
    gaps = np.diff(np.concatenate([angs, [angs[0] + 360]]))
    j = int(np.argmax(gaps))
    gap_mid = np.radians(angs[j] + gaps[j] / 2)
    pts = np.roll(pts, -(j + 1), axis=0)
    # rays that graze the ends of the arms land on the slope behind the tuberosity: trim ends standing proud
    zt = np.median(pts[:, 2]) + 1.0
    a, b = 0, len(pts)
    while b - a > 10 and pts[a, 2] > zt:
        a += 1
    while b - a > 10 and pts[b - 1, 2] > zt:
        b -= 1
    return pts[a:b], c, np.array([np.cos(gap_mid), np.sin(gap_mid)])


def _level_on(pts: np.ndarray):
    """Rotation (rows) taking the least-squares plane through pts to z = const, z kept upward, and its centroid."""
    cc = pts.mean(axis=0)
    _, _, vt = np.linalg.svd(pts - cc)
    zn = vt[2] if vt[2][2] >= 0 else -vt[2]
    zn = zn / np.linalg.norm(zn)
    ref = np.array([1.0, 0, 0]) if abs(zn[0]) < 0.9 else np.array([0, 1.0, 0])
    xn = ref - zn * (zn @ ref)
    xn /= np.linalg.norm(xn)
    return np.vstack([xn, np.cross(zn, xn), zn]), cc


def _lower_hull_plane(v: np.ndarray):
    """Rotation + offset that levels the plane fitted to the lower convex hull vertices."""
    from scipy.spatial import ConvexHull
    hull = ConvexHull(v)
    eq = hull.equations  # a x + b y + c z + d = 0, outward normals
    lower = eq[:, 2] < -0.4
    idx = np.unique(hull.simplices[lower].ravel())
    pts = v[idx]
    cc = pts.mean(axis=0)
    _, _, vt = np.linalg.svd(pts - cc)
    n = vt[2]
    if n[2] < 0:
        n = -n
    zn = n / np.linalg.norm(n)
    xn = np.array([1.0, 0, 0]) - zn * zn[0]
    xn /= np.linalg.norm(xn)
    yn = np.cross(zn, xn)
    Rl = np.vstack([xn, yn, zn])
    off = Rl @ cc
    return Rl, off


def _affine(R: np.ndarray, t: np.ndarray) -> np.ndarray:
    M = np.eye(4)
    M[:3, :3] = R
    M[:3, 3] = t
    return M


def _apply(T: np.ndarray, v: np.ndarray) -> np.ndarray:
    return v @ T[:3, :3].T + T[:3, 3]


def _rot_z_to_y(d: np.ndarray) -> np.ndarray:
    """Rotation about z taking the plan direction d to +y."""
    th = np.arctan2(d[0], d[1])
    return np.array([[np.cos(th), -np.sin(th), 0], [np.sin(th), np.cos(th), 0], [0, 0, 1.0]])


def _trim_hooks(ring: np.ndarray, k: int = 5) -> np.ndarray:
    """Drop points at the end of the ring that turn back from the arm's direction (rays grazing the inside of the
    arm near the tuberosity)."""
    while len(ring) > 2 * k + 2:
        arm = ring[-2, :2] - ring[-2 - k, :2]
        last = ring[-1, :2] - ring[-2, :2]
        na, nl = np.linalg.norm(arm), np.linalg.norm(last)
        if na < 1e-9 or nl < 1e-9 or (arm @ last) / (na * nl) >= 0.3:
            break
        ring = ring[:-1]
    return ring


def _is_trough(g: "_PlanGrid", p: np.ndarray, d: np.ndarray, off: float = 2.0, depth: float = 0.3) -> bool:
    """The crest is a trough across the arm (z up): the surface `off` mm to either side, across the arm direction d,
    stands at least `depth` mm above it. Behind the end of the ridge the surface no longer does."""
    perp = np.array([-d[1], d[0]])
    z = g.at(np.array([p[:2] + off * perp, p[:2] - off * perp]))
    zc = g.at(p[None, :2])[0]
    return bool(np.all(np.isfinite(z)) and np.isfinite(zc) and np.all(z >= zc + depth))


def _trim_ends(g: "_PlanGrid", ring: np.ndarray, k: int = 4) -> np.ndarray:
    """Drop end points of the ring that are not on a trough across the arm."""
    def arm_dir(r, i, j):
        d = r[i, :2] - r[j, :2]
        n = np.linalg.norm(d)
        return d / n if n > 1e-9 else np.array([1.0, 0.0])
    a, b = 0, len(ring)
    while b - a > 2 * k + 2 and not _is_trough(g, ring[a], arm_dir(ring, a, a + k)):
        a += 1
    while b - a > 2 * k + 2 and not _is_trough(g, ring[b - 1], arm_dir(ring, b - 1, b - 1 - k)):
        b -= 1
    return ring[a:b]


def _extend_on_ridge(v: np.ndarray, g: "_PlanGrid", ring: np.ndarray, end: str, step: float = 0.5,
                     max_rise: float = 0.5, across: float = 1.5) -> np.ndarray:
    """March past a ring end along the arm while the lowest point ahead is still on a trough across the arm and
    has not risen more than `max_rise` mm in one step: the ridge often climbs gently toward the tuberosity, so the
    test is local, not against one level for the whole arch."""
    pts = list(ring) if end == "end" else list(ring[::-1])
    for _ in range(int(20 / step)):
        d = pts[-1][:2] - pts[-5][:2]
        if np.linalg.norm(d) < 1e-6:
            break
        d /= np.linalg.norm(d)
        probe = pts[-1][:2] + step * d
        rel = v[:, :2] - probe
        sel = (np.abs(rel @ d) < 0.6 * step) & (np.abs(rel @ np.array([-d[1], d[0]])) < across)
        if sel.sum() < 2:
            break
        cand = v[sel][np.argmin(v[sel][:, 2])]
        if cand[2] - pts[-1][2] > max_rise or (cand[:2] - pts[-1][:2]) @ d < 0.2 * step or not _is_trough(g, cand, d):
            break
        pts.append(cand)
    arr = np.array(pts)
    return arr if end == "end" else arr[::-1]


def crest_ring(v: np.ndarray):
    """Ordered crest ring of an oriented scan: the anatomical (radial) finder, else the older polar sweep."""
    g = _PlanGrid(v)
    res = _crest_ring_radial(v, grid=g)
    if res is not None:
        ring, centre, _ = res
        ring = _trim_hooks(_trim_hooks(ring)[::-1])[::-1]
        ring = _trim_ends(g, ring)
        ring = _extend_on_ridge(v, g, ring, "start")
        ring = _extend_on_ridge(v, g, ring, "end")
        return ring, centre, "radial"
    ring, centre = _crest_ring_from_oriented(v)
    return ring, centre, "polar"


def _orient_pca(mesh: trimesh.Trimesh) -> np.ndarray:
    """Starting frame: principal axes, z the least-variance axis turned away from the scanner normals."""
    v = mesh.vertices
    c, axes = _pca_axes(v)
    zax = axes[2]
    if np.mean(mesh.face_normals @ zax) > 0:      # scanner normals face the oral cavity, which is -z
        zax = -zax
    xax = axes[0] - zax * (axes[0] @ zax)
    xax /= np.linalg.norm(xax)
    R = np.vstack([xax, np.cross(zax, xax), zax])
    return _affine(R, -R @ c)


def _orient_anatomical(mesh: trimesh.Trimesh, T0: np.ndarray):
    """Refine a starting frame from the anatomy rather than the raw principal axes.

    The principal axes of a raw scan follow its outline, and its outline follows whatever soft tissue the scan
    caught at the border: a scan that is about as wide as it is deep, or has a ragged lip or cheek flap, can put
    the first axis front to back. Here instead:
      - z: the palatal dome is concave seen from the mouth; of the two senses of the starting z, the one in which
        rays out of the dome find a crest (fall, then rise over the buccal slope) on more of the arch is kept;
      - level: least-squares plane through the crest found that way;
      - y: the arch is a horseshoe; its widest run with no crest is the open posterior side, so +y points away
        from it.
    Two passes. Returns (T, centre) or None when no horseshoe is found."""
    v = mesh.vertices
    best = None
    for flip in (False, True):
        T = T0 if not flip else _affine(np.diag([1.0, -1.0, -1.0]), np.zeros(3)) @ T0
        res = _crest_ring_radial(_apply(T, v))
        if res is not None and (best is None or len(res[0]) > len(best[1][0])):
            best = (T, res)
    if best is None:
        return None
    T, res = best
    for _ in range(4):
        ring, centre, gapdir = res
        Rl, cc = _level_on(ring)
        T = _affine(Rl, -Rl @ cc) @ T
        res = _crest_ring_radial(_apply(T, v))
        if res is None:
            return None
        Rz = _rot_z_to_y(-res[2])
        T = _affine(Rz, np.zeros(3)) @ T
        res = _crest_ring_radial(_apply(T, v))
        if res is None:
            return None
    return T, res[1]


def _mirror_axis(xy: np.ndarray, span_deg: float = 25.0) -> float:
    """Rotation angle about z (radians) that makes the arch most nearly mirror-symmetric about a line x = const.
    xy: crest points with +y already roughly anterior."""
    from scipy.spatial import cKDTree
    best = (np.inf, 0.0)
    for th in np.radians(np.arange(-span_deg, span_deg + 1e-9, 0.5)):
        c, s_ = np.cos(th), np.sin(th)
        q = xy @ np.array([[c, s_], [-s_, c]])
        tree = cKDTree(q)
        x_mid = 0.5 * (q[:, 0].min() + q[:, 0].max())
        for x0 in x_mid + np.arange(-3.0, 3.01, 0.25):
            m = np.column_stack([2 * x0 - q[:, 0], q[:, 1]])
            d = np.mean(np.minimum(tree.query(m)[0], 3.0) ** 2)
            if d < best[0]:
                best = (d, th)
    return best[1]


def orient(mesh: trimesh.Trimesh) -> tuple[trimesh.Trimesh, np.ndarray, dict]:
    """Return (oriented mesh, 4x4 transform, debug) with z up, y anterior, occlusal z=0, origin between the ring ends."""
    v = mesh.vertices
    T0 = _orient_pca(mesh)
    an = _orient_anatomical(mesh, T0)
    if an is not None:
        T, centre = an
        method = "anatomical"
    else:
        # older route: lower convex hull to level, then the U's closed end for anterior
        T = T0
        v1 = _apply(T, v)
        R_lvl, off = _lower_hull_plane(v1)
        T = _affine(R_lvl, -off) @ T
        v1 = _apply(T, v)
        crest, centre = _crest_ring_from_oriented(v1)

        def spread(sign):
            yy = sign * crest[:, 1]
            sel = crest[yy > yy.max() - 3.0]
            return sel[:, 0].std() if len(sel) > 1 else 0.0
        if spread(+1) > spread(-1):
            T = _affine(np.diag([-1.0, -1.0, 1.0]), np.zeros(3)) @ T
        method = "principal axes (no horseshoe found)"
    v1 = _apply(T, v)
    crest, centre, _ = crest_ring(v1)
    # least-squares occlusal plane through the crest, then the midline: apex (max y) to the midpoint of the ends
    Rl, cc = _level_on(crest)
    T = _affine(Rl, -Rl @ cc) @ T
    crest = (crest - cc) @ Rl.T
    # midline: the arch's mirror axis (the most anterior crest point alone wanders on a rounded arch, and a ring
    # end that stops early would turn a line through the ends); origin midway between the ring ends
    th = _mirror_axis(crest[:, :2])
    Rz = np.array([[np.cos(th), -np.sin(th), 0], [np.sin(th), np.cos(th), 0], [0, 0, 1.0]])
    crest = crest @ Rz.T
    mid = 0.5 * (crest[0] + crest[-1])
    mid3 = np.array([mid[0], mid[1], 0.0])
    T = _affine(np.eye(3), -mid3) @ _affine(Rz, np.zeros(3)) @ T
    out = mesh.copy()
    out.apply_transform(T)
    return out, T, {"centre": centre, "method": method}


def find_holes(hf: "HeightField", region: Polygon, tol: float = 1.0, min_area: float = 2.0) -> list:
    """Gaps in the scan inside `region`: connected cells with no scan vertex within `tol` mm in plan that are
    enclosed by scanned surface (they do not reach the edge of the scan). Missing data, not anatomy, as far as the
    software can tell; a cleft that was not scanned looks the same. Each is blocked out like a cleft: the intaglio
    over it may not rise above its rim (see HeightField.blocked), so the plate never reaches into it."""
    from scipy.ndimage import label, binary_dilation
    from shapely import contains_xy
    from shapely.geometry import box
    from shapely.ops import unary_union
    if hf.data_dist is None:
        return []
    open_ = hf.data_dist > tol
    lab, n = label(open_)
    border = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))) - {0}
    gx, gy = np.meshgrid(hf.xs, hf.ys, indexing="ij")
    cell = hf.step * hf.step
    out = []
    for k in range(1, n + 1):
        if k in border:
            continue
        m = lab == k
        if m.sum() * cell < min_area:
            continue
        cx, cy = gx[m].mean(), gy[m].mean()
        if not region.contains(Point(cx, cy)):
            continue
        rim = binary_dilation(m, iterations=max(1, int(round(1.0 / hf.step)))) & ~open_
        rim_z = float(np.median(hf.z_raw[rim])) if rim.any() else float(np.median(hf.z_raw[m]))
        h = hf.step / 2
        poly = unary_union([box(x - h, y - h, x + h, y + h) for x, y in zip(gx[m], gy[m])]).buffer(0.2).buffer(-0.2)
        if poly.geom_type == "MultiPolygon":
            poly = max(poly.geoms, key=lambda g: g.area)
        out.append({"polygon": np.array(poly.simplify(0.1).exterior.coords), "rim_z": rim_z, "area_mm2": float(m.sum() * cell)})
    return out


def find_landmarks(mesh: trimesh.Trimesh, cleft_rise_threshold: float = 2.0) -> tuple[trimesh.Trimesh, Landmarks, HeightField]:
    om, T, dbg = orient(mesh)
    v = om.vertices
    crest, _, ring_method = crest_ring(v)
    # order: first point is the -x end. The design frame is right-handed with y anterior and
    # z superior, so +x is the patient's RIGHT (if the export is not mirrored); -x end = left tuberosity.
    if crest[0, 0] > crest[-1, 0]:
        crest = crest[::-1]
    # apex: the anterior crest point on the midline (the most anterior point wanders along a rounded arch)
    front = crest[crest[:, 1] > crest[:, 1].max() - 3.0]
    apex = front[np.argmin(np.abs(front[:, 0]))]
    tub_l, tub_r = crest[0], crest[-1]
    hf = HeightField(v)
    notes = []

    papilla_xy = np.array([[0.0, apex[1] - 2.5]])
    papilla = np.array([0.0, apex[1] - 2.5, float(hf(papilla_xy)[0])])

    # palate region = inside the crest ring
    # palate region: inside the crest ring, extended 8 mm behind the tuberosities so the
    # posterior border of the plate (and the cleft under it) is covered
    ring_poly = Polygon(np.vstack([crest[:, :2], [tub_l[:2] + [0, -8.0]], [tub_r[:2] + [0, -8.0]]]))
    inside = np.array([ring_poly.contains(Point(p)) for p in v[:, :2]])
    pal = v[inside]
    lateral = pal[np.abs(pal[:, 0]) > 8.0]
    vault_ref = np.percentile(lateral[:, 2], 98) if len(lateral) else np.percentile(pal[:, 2], 90)
    thresh = vault_ref + cleft_rise_threshold
    cleft_pts = pal[pal[:, 2] > thresh]
    cleft_present = len(cleft_pts) > 30
    margin_z = float(vault_ref)
    profile = []
    cleft_poly = None
    if cleft_present:
        # margin level: palate points just outside the cleft region
        from scipy.spatial import cKDTree
        tree = cKDTree(cleft_pts[:, :2])
        dist, _ = tree.query(pal[:, :2])
        margin = pal[(dist > 0.6) & (dist < 1.6) & (pal[:, 2] <= thresh)]
        if len(margin):
            margin_z = float(np.median(margin[:, 2]))
        ys = np.arange(cleft_pts[:, 1].min(), cleft_pts[:, 1].max() + 0.01, 1.0)
        left_edge, right_edge = [], []
        for y in ys:
            band = cleft_pts[np.abs(cleft_pts[:, 1] - y) < 0.6]
            if len(band) < 2:
                continue
            profile.append((float(y), float(band[:, 0].max() - band[:, 0].min())))
            left_edge.append([band[:, 0].max(), y])
            right_edge.append([band[:, 0].min(), y])
        if left_edge:
            cleft_poly = np.array(left_edge + right_edge[::-1])
        notes.append(f"cleft detected: {len(profile)} mm-bands, max width {max(w for _, w in profile):.1f} mm")
    else:
        notes.append("no raised gap (cleft) found above the vault")
    holes = find_holes(hf, ring_poly)
    if holes:
        notes.append(f"{len(holes)} gap(s) in the scan inside the arch, {sum(h['area_mm2'] for h in holes):.1f} mm2 in all: "
                     "blocked out like a cleft (the plate does not reach into them)")
    if not cleft_present and not holes:
        notes.append("no gap to block out")
    notes.append(f"orientation: {dbg.get('method', '?')}; crest: {ring_method}")
    lm = Landmarks(T, crest, apex, papilla, tub_r, tub_l, cleft_present, margin_z, profile, cleft_poly, notes, holes)
    return om, lm, hf
