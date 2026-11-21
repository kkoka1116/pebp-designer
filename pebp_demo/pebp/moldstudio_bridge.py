"""Optional landmark backend using MoldStudio's classifier (segment-aware arch + cleft detection).

MoldStudio (a separate NAM plate CAD program, not included) orients open infant scans with an arch-gap probe and
traces the ridge crest as a bottleneck path that tolerates alveolar clefts. Its frame is
rows (left, anterior, into-the-mouth); ours is (right, anterior, superior), so x and z flip.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import trimesh
from shapely.geometry import Polygon, Point

from .landmarks import Landmarks, HeightField

MOLDSTUDIO_ROOT = Path(os.environ.get("MOLDSTUDIO_ROOT", "moldstudio-not-installed"))


def _import_moldstudio():
    if str(MOLDSTUDIO_ROOT) not in sys.path:
        sys.path.insert(0, str(MOLDSTUDIO_ROOT))
    from moldstudio.core.model import Mesh
    from moldstudio.classify.pipeline import classify
    return Mesh, classify


def _level(v: np.ndarray, crest: np.ndarray, weights: np.ndarray):
    """Rotate/translate so the weighted least-squares plane through the crest is z = 0."""
    w = weights / weights.sum()
    cc = (crest * w[:, None]).sum(axis=0)
    d = (crest - cc) * np.sqrt(w)[:, None]
    _, _, vt = np.linalg.svd(d)
    n = vt[2]
    if n[2] < 0:
        n = -n
    zn = n / np.linalg.norm(n)
    xn = np.array([1.0, 0, 0]) - zn * zn[0]; xn /= np.linalg.norm(xn)
    yn = np.cross(zn, xn)
    R = np.vstack([xn, yn, zn])
    return (v - cc) @ R.T, (crest - cc) @ R.T, R, cc


def _clusters(pts_xy: np.ndarray, link_mm: float = 1.5, min_pts: int = 30) -> list[np.ndarray]:
    """Connected components of a point set under a distance link."""
    from scipy.spatial import cKDTree
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    if len(pts_xy) == 0:
        return []
    tree = cKDTree(pts_xy)
    pairs = tree.query_pairs(link_mm, output_type="ndarray")
    n = len(pts_xy)
    if len(pairs) == 0:
        return []
    g = coo_matrix((np.ones(len(pairs)), (pairs[:, 0], pairs[:, 1])), shape=(n, n))
    _, lab = connected_components(g, directed=False)
    out = []
    for c in np.unique(lab):
        idx = np.where(lab == c)[0]
        if len(idx) >= min_pts:
            out.append(idx)
    return out


def _band_polygon(pts: np.ndarray, band_mm: float = 1.0):
    """Non-convex outline of a point cluster: min/max x per y band."""
    ys = np.arange(pts[:, 1].min(), pts[:, 1].max() + 0.01, band_mm)
    left, right, profile = [], [], []
    for y in ys:
        b = pts[np.abs(pts[:, 1] - y) < 0.6 * band_mm]
        if len(b) < 2:
            continue
        left.append([b[:, 0].max(), y]); right.append([b[:, 0].min(), y])
        profile.append((float(y), float(b[:, 0].max() - b[:, 0].min())))
    if len(left) < 2:
        return None, profile
    return np.array(left + right[::-1]), profile


def classify_landmarks(mesh: trimesh.Trimesh, cleft_rise_threshold: float = 2.0, vestibule_keep_mm: float = 4.0):
    Mesh, classify = _import_moldstudio()
    V0 = np.asarray(mesh.vertices, float)
    res = classify(Mesh(V=V0, F=np.asarray(mesh.faces, np.int32)), arch="maxillary")
    fr, arch, sheet = res.frame, res.arch, res.sheet
    # work on the classifier's welded sheet so the per-vertex labels line up
    V = np.asarray(sheet.V, float)
    F = None
    for name in ("F", "faces", "tris", "T"):
        if hasattr(sheet, name):
            F = np.asarray(getattr(sheet, name)); break
    if F is None:
        raise RuntimeError("classifier sheet has no face array")
    labels = np.asarray(res.labels)
    from moldstudio.classify.regions import LABEL_ID
    flip = np.diag([-1.0, 1.0, -1.0])            # (left, anterior, into mouth) -> (right, anterior, superior)
    v1 = fr.to_frame(V) @ flip
    path = arch.path
    crest = np.column_stack([-path.xy[:, 0], path.xy[:, 1], -path.h])
    weights = np.ones(len(crest))
    gaps = []
    for it in arch.interruptions:
        i0, i1 = it.span_idx
        if i1 > i0 + 1:
            t = np.linspace(0, 1, i1 - i0 + 1)
            crest[i0:i1 + 1] = (1 - t)[:, None] * crest[i0] + t[:, None] * crest[i1]
            weights[i0:i1 + 1] = 0.05
        gaps.append({"kind": it.kind, "width_mm": round(float(it.width_mm), 2), "theta_deg": round(float(it.theta_deg), 1),
                     "ends": [[-e[0], e[1]] for e in it.ends_xy], "span_idx": [int(i0), int(i1)]})
    v2, crest2, R2, cc = _level(v1, crest, weights)
    if crest2[0, 0] > crest2[-1, 0]:
        crest2 = crest2[::-1]
    apex = crest2[np.argmax(crest2[:, 1])]
    mid = 0.5 * (crest2[0] + crest2[-1])
    d = apex[:2] - mid[:2]
    theta = np.arctan2(d[0], d[1])
    R3 = np.array([[np.cos(theta), -np.sin(theta), 0], [np.sin(theta), np.cos(theta), 0], [0, 0, 1]])
    v3 = (v2 - mid) @ R3.T
    crest3 = (crest2 - mid) @ R3.T
    Vh = np.column_stack([V, np.ones(len(V))])
    A, *_ = np.linalg.lstsq(Vh, v3, rcond=None)
    T = np.eye(4); T[:3, :] = A.T
    om = trimesh.Trimesh(v3, F, process=False)

    # ---- anatomical trim from the classifier labels: keep the arch, palate, cleft walls and
    # margins; keep lip/cheek ("vestibule") tissue only within vestibule_keep_mm of the crest
    from scipy.spatial import cKDTree
    d_crest, _ = cKDTree(crest3[:, :2]).query(v3[:, :2])
    vest = labels == LABEL_ID["vestibule"]
    keep = (~vest) | (d_crest <= vestibule_keep_mm)
    v_keep = v3[keep]
    trimmed = om.submesh([np.where(keep[F].all(axis=1))[0]], append=True) if keep.any() else om
    apex = crest3[np.argmax(crest3[:, 1])]
    tub_l, tub_r = crest3[0], crest3[-1]
    hf = HeightField(v_keep)
    papilla = np.array([0.0, apex[1] - 2.5, float(hf(np.array([[0.0, apex[1] - 2.5]]))[0])])

    # ---- cleft: floor anchored on the labelled cleft margins (the shelf edges facing the gap)
    marg = labels == LABEL_ID["cleft_margin"]
    if marg.sum() < 20 and hasattr(res, "boundaries"):
        marg = np.asarray(res.boundaries.margin, bool)
    ring_poly = Polygon(np.vstack([crest3[:, :2], [tub_r[:2] + [0, -8.0]], [tub_l[:2] + [0, -8.0]]])).buffer(0)
    inside = np.array([ring_poly.contains(Point(p)) for p in v3[:, :2]])
    pal_mask = inside & keep
    if marg.sum() >= 20:
        margin_z = float(np.median(v3[marg, 2]))
        margin_src = f"{int(marg.sum())} cleft_margin vertices"
    else:
        near = pal_mask & (d_crest > 2.0) & (d_crest < 5.0)
        margin_z = float(np.median(v3[near, 2])) if near.any() else 0.0
        margin_src = "palatal slope 2-5 mm inboard of the crest (no margin labels)"
    thresh = margin_z + cleft_rise_threshold
    cl_idx = np.where(pal_mask & (v3[:, 2] > thresh))[0]
    cleft_polys, profile = [], []
    for idx in _clusters(v3[cl_idx, :2]):
        poly, prof = _band_polygon(v3[cl_idx[idx], :2])
        if poly is not None:
            cleft_polys.append(poly); profile += prof
    for g in gaps:
        e = np.array(g["ends"])
        ends2 = (np.column_stack([e, np.zeros(2)]) - cc) @ R2.T
        ends3 = (ends2 - mid) @ R3.T
        chord = Polygon([ends3[0, :2], ends3[1, :2], ends3[1, :2] + [0, -6.0], ends3[0, :2] + [0, -6.0]]).buffer(1.5)
        cleft_polys.append(np.array(chord.exterior.coords))
        g["ends_design_frame"] = ends3[:, :2].round(2).tolist()
    cleft_present = len(cleft_polys) > 0
    notes = [f"moldstudio classifier: cleft_type={arch.cleft_type}, {len(gaps)} ridge interruption(s), "
             f"arch depth {res.measurements.get('arch_depth_mm')} mm, posterior width {res.measurements.get('posterior_width_mm')} mm",
             f"trim: kept {int(keep.sum())}/{len(keep)} vertices (dropped lip/cheek tissue > {vestibule_keep_mm} mm from the crest)",
             f"cleft margin level {margin_z:.2f} mm from {margin_src}; {len(cleft_polys)} block-out region(s)"]
    notes += [f"classifier {f['severity']}: {f['message'][:110]}" for f in res.findings[:3]]
    lm = Landmarks(T, crest3, apex, papilla, tub_r, tub_l, cleft_present, margin_z, profile, cleft_polys, notes)
    extra = {"backend": "moldstudio", "cleft_type": arch.cleft_type, "interruptions": gaps,
             "trim": {"kept_vertices": int(keep.sum()), "total_vertices": int(len(keep)), "vestibule_keep_mm": vestibule_keep_mm},
             "label_counts": {name: int((labels == i).sum()) for name, i in LABEL_ID.items() if (labels == i).any()},
             "measurements": {k: v for k, v in res.measurements.items() if not isinstance(v, (list, dict))}}
    return om, lm, hf, extra, trimmed
