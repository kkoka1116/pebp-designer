"""Measure a physical device against its design.

Two uses:
  bench    a scan of a cast or printed part against the design it was made from: surface deviation
           (signed: + is extra material, - is missing material) and the share of the surface within
           0.1 / 0.25 / 0.5 mm. This is how the Phase 0 bench tests compare printed tooling, cast plates and
           silicone-index plates.
  bedside  a scan of a prototype or plate after it was ground or bent at the bedside, against the design
           of that device: how much the tip was actually shortened, and the baton's width and thickness
           along its length, so the record holds what was done rather than what was intended.

Registration is rigid: principal axes for a coarse start (and the identity, for a scan already in the
design frame), then trimmed ICP. Trimming (default: worst 10% ignored) lets a shortened or partly
scanned part register on the parts that are unchanged. Distances are exact point-to-triangle.
No rtree dependency: nearest triangles come from a KD-tree on triangle centroids and vertices.
"""
from __future__ import annotations

import numpy as np
import trimesh
from scipy.spatial import cKDTree

from .meshutil import arclength, path_frames


def _samples(mesh: trimesh.Trimesh, n: int, seed: int) -> np.ndarray:
    pts, _ = trimesh.sample.sample_surface(mesh, n, seed=seed)
    return np.asarray(pts)


def _kabsch(A: np.ndarray, B: np.ndarray):
    ca, cb = A.mean(axis=0), B.mean(axis=0)
    H = (A - ca).T @ (B - cb)
    U, _, Vt = np.linalg.svd(H)
    d = np.sign(np.linalg.det(Vt.T @ U.T))
    R = Vt.T @ np.diag([1.0, 1.0, d]) @ U.T
    return R, cb - R @ ca


def _icp(P, tree, Q, R, t, iters, trim):
    err = np.inf
    for _ in range(iters):
        X = P @ R.T + t
        d, j = tree.query(X)
        keep = d <= np.quantile(d, trim)
        R2, t2 = _kabsch(X[keep], Q[j[keep]])
        R, t = R2 @ R, R2 @ t + t2
        new = float(d[keep].mean())
        if abs(err - new) < 1e-6:
            break
        err = new
    d, _ = tree.query(P @ R.T + t)
    return R, t, float(np.sort(d)[: int(trim * len(d))].mean())


def register(scan: trimesh.Trimesh, ref: trimesh.Trimesh, n: int = 6000, trim: float = 0.9, seed: int = 0):
    """Rigid transform (4x4) that moves `scan` onto `ref`, and a summary."""
    P = _samples(scan, n, seed + 1)
    Q = _samples(ref, 4 * n, seed + 2)
    tree = cKDTree(Q)
    cp, cq = P.mean(axis=0), Q.mean(axis=0)
    _, _, Vp = np.linalg.svd(P - cp, full_matrices=False)
    _, _, Vq = np.linalg.svd(Q - cq, full_matrices=False)
    cands = [("identity", np.eye(3), np.zeros(3))]
    sgn = np.linalg.det(Vq) * np.linalg.det(Vp)
    for sx, sy in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
        R0 = Vq.T @ np.diag([sx, sy, sx * sy * sgn]) @ Vp     # scan's principal axes onto the reference's
        cands.append((f"principal axes {sx:+d}{sy:+d}", R0, cq - R0 @ cp))
    trials = [(name, *_icp(P, tree, Q, R0, t0, 25, trim)) for name, R0, t0 in cands]
    name, R, t, _ = min(trials, key=lambda r: r[3])
    R, t, err = _icp(P, tree, Q, R, t, 150, trim)
    T = np.eye(4); T[:3, :3] = R; T[:3, 3] = t
    return T, {"start": name, "residual_to_samples_mm": round(err, 4), "trim": trim,
               "residual_note": "trimmed mean distance to sampled reference points; includes the sample spacing, so it "
                                "overstates the true fit (see deviation for exact surface distances)",
               "candidates_mm": {nm: round(e, 3) for nm, _, _, e in trials}}


def surface_distance(points: np.ndarray, mesh: trimesh.Trimesh, k: int = 8, closest: bool = False):
    """Exact signed distance from each point to `mesh` (+ outside, - inside).

    Pass 1 takes the k nearest triangle centroids for an upper bound d1. Every point of a triangle lies
    within its circumscribing radius R of its centroid, so pass 2 checks every triangle whose centroid is
    within d1 + R_max: the true nearest triangle is always among them."""
    points = np.asarray(points, float)
    if mesh.edges_unique_length.max() > 1.0:
        mesh = mesh.subdivide_to_size(0.75)                  # keeps R_max (and pass 2) small; the surface is unchanged
    tri = mesh.triangles
    cen = tri.mean(axis=1)
    r_max = float(np.linalg.norm(tri - cen[:, None, :], axis=2).max())
    tree = cKDTree(cen)
    cand = tree.query(points, k=k)[1]
    c = trimesh.triangles.closest_point(tri[cand.ravel()], np.repeat(points, k, axis=0))
    d1 = np.linalg.norm(np.repeat(points, k, axis=0) - c, axis=1).reshape(-1, k).min(axis=1)
    lists = tree.query_ball_point(points, r=d1 + r_max + 1e-9)
    pi = np.repeat(np.arange(len(points)), [len(x) for x in lists])
    fi = np.fromiter((f for x in lists for f in x), dtype=int, count=len(pi))
    best_d = np.full(len(points), np.inf)
    best_f = np.zeros(len(points), dtype=int)
    best_c = np.zeros_like(points)
    for lo in range(0, len(pi), 400000):                     # in chunks, to bound memory
        p_, f_ = pi[lo:lo + 400000], fi[lo:lo + 400000]
        cc = trimesh.triangles.closest_point(tri[f_], points[p_])
        dd = np.linalg.norm(points[p_] - cc, axis=1)
        order = np.lexsort((dd, p_))                         # per point, nearest first
        p_s, first = np.unique(p_[order], return_index=True)
        j = order[first]
        better = dd[j] < best_d[p_s]
        best_d[p_s[better]], best_f[p_s[better]], best_c[p_s[better]] = dd[j][better], f_[j][better], cc[j][better]
    nrm = mesh.face_normals[best_f]
    sign = np.sign(np.einsum("ij,ij->i", points - best_c, nrm))
    sign[sign == 0] = 1.0
    return (sign * best_d, best_c, nrm) if closest else sign * best_d


def _stats(d: np.ndarray) -> dict:
    a = np.abs(d)
    return {"mean_abs_mm": round(float(a.mean()), 3), "p95_abs_mm": round(float(np.percentile(a, 95)), 3),
            "max_abs_mm": round(float(a.max()), 3), "mean_signed_mm": round(float(d.mean()), 3),
            "within_0.1_mm": round(float((a <= 0.1).mean()), 3), "within_0.25_mm": round(float((a <= 0.25).mean()), 3),
            "within_0.5_mm": round(float((a <= 0.5).mean()), 3), "n_points": int(len(d))}


def regions(points: np.ndarray, normals: np.ndarray, border_y: float) -> dict:
    """Split design-frame surface points into the plate's tissue side (intaglio: faces up, in front of the
    posterior border), the rest of the plate (oral surface and rim), and the baton (behind the border)."""
    baton = points[:, 1] < border_y - 0.5
    intaglio = ~baton & (normals[:, 2] > 0.3)
    return {"intaglio": intaglio, "plate_other": ~baton & ~intaglio, "baton": baton}


def deviation(scan: trimesh.Trimesh, ref: trimesh.Trimesh, n: int = 8000, seed: int = 5, border_y: float | None = None) -> dict:
    """scan_to_design: every scanned point against the design surface (+ extra material, - missing).
    design_to_scan: every design point against the scan (large values: missing material, or not scanned).
    With `border_y` (the design is in the design frame), each is also split by region: palatal fit
    (intaglio), the rest of the plate, and the baton."""
    P = _samples(scan, n, seed)
    d1, c1, n1 = surface_distance(P, ref, closest=True)
    Q, fi = trimesh.sample.sample_surface(ref, n, seed=seed + 1)
    d2 = surface_distance(np.asarray(Q), scan)
    out = {"scan_to_design": _stats(d1), "design_to_scan": _stats(d2)}
    if border_y is not None:
        for key, d, (pts, nrm) in (("scan_to_design", d1, (c1, n1)), ("design_to_scan", d2, (np.asarray(Q), ref.face_normals[fi]))):
            out[key]["regions"] = {k: _stats(d[m]) for k, m in regions(pts, nrm, border_y).items() if m.sum() >= 20}
    return out


def tip_reach(mesh: trimesh.Trimesh, path: np.ndarray, radius: float = 9.0, top: int = 10) -> float:
    """Arclength along `path` of the tip: the mean of the `top` farthest surface samples along the end
    tangent. Equal-density samples and a mean rather than a maximum keep scanner noise from inflating it;
    the design and the scan are measured the same way, so the shape bias cancels in their difference."""
    s = arclength(path)
    end = np.asarray(path[-1], float)
    t = end - np.asarray(path[-4], float); t /= np.linalg.norm(t)
    pts = _samples(mesh, int(max(20000, 25 * mesh.area)), 11)        # ~25 samples per mm2
    near = pts[np.linalg.norm(pts - end, axis=1) < radius]
    return float(s[-1] + np.sort((near - end) @ t)[-top:].mean())


def sections(mesh: trimesh.Trimesh, path: np.ndarray, at_s: list[float], slab: float = 0.2, reach: float = 9.0) -> list[dict]:
    """Width (along x) and thickness (along the sagittal normal) of the baton at arclengths `at_s`."""
    s = arclength(path)
    T, N = path_frames(path)
    pts = _samples(mesh, int(max(40000, 40 * mesh.area)), 13)       # ~40 samples per mm2
    out = []
    for sv in at_s:
        i = int(np.argmin(np.abs(s - sv)))
        rel = pts - path[i]
        m = (np.abs(rel @ T[i]) < slab) & (np.linalg.norm(rel, axis=1) < reach)
        if m.sum() < 10:
            out.append({"s_mm": round(float(sv), 2), "width_mm": None, "thickness_mm": None}); continue
        x, y = rel[m, 0], rel[m] @ N[i]
        span = lambda v: float(np.percentile(v, 99) - np.percentile(v, 1))   # robust to scanner noise
        out.append({"s_mm": round(float(sv), 2), "width_mm": round(span(x), 2), "thickness_mm": round(span(y), 2)})
    return out


def compare(ref: trimesh.Trimesh, scan: trimesh.Trimesh, align: bool = True, path: np.ndarray | None = None,
            root_s: float | None = None, border_y: float | None = None) -> tuple[dict, trimesh.Trimesh]:
    """Register `scan` to `ref` and measure it. With the design centreline `path` (and the arclength of
    the extension root `root_s`), also report the tip shortening and the baton's sections. With the
    plate's posterior border `border_y`, split the deviation by region."""
    out = {}
    aligned = scan.copy()
    if align:
        T, info = register(scan, ref)
        aligned.apply_transform(T)
        out["registration"] = {**info, "transform_scan_to_design": T.round(6).tolist()}
    else:
        out["registration"] = {"start": "none: the scan is taken to be in the design frame"}
    out["deviation"] = deviation(aligned, ref, border_y=border_y)
    if path is not None:
        d_tip, s_tip = tip_reach(ref, path), tip_reach(aligned, path)
        out["tip"] = {"design_reach_mm": round(d_tip, 2), "scan_reach_mm": round(s_tip, 2),
                      "shortened_mm": round(d_tip - s_tip, 2),
                      "note": "arclength along the design centreline; + means the physical baton is shorter than its design"}
        if root_s is not None:
            arm = d_tip - root_s
            at = [root_s + f * arm for f in (0.45, 0.65, 0.8)]
            design_sec, scan_sec = sections(ref, path, at), sections(aligned, path, at)
            out["sections"] = [{"s_after_root_mm": round(a - root_s, 1), "design": dsec, "scan": ssec,
                                "width_change_mm": (round(ssec["width_mm"] - dsec["width_mm"], 2)
                                                    if ssec["width_mm"] is not None and dsec["width_mm"] is not None else None),
                                "thickness_change_mm": (round(ssec["thickness_mm"] - dsec["thickness_mm"], 2)
                                                        if ssec["thickness_mm"] is not None and dsec["thickness_mm"] is not None else None)}
                               for a, dsec, ssec in zip(at, design_sec, scan_sec)]
    return out, aligned
