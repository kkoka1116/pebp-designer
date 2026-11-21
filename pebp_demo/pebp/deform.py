"""Deform a baton (velopharyngeal extension) mesh along its centreline.

The published Tübingen standard extension is a fixed STL, so a modification such as "shorten 3 mm"
cannot be made by changing a parameter and regenerating it. Instead the mesh is deformed along a
centreline skeleton: the parametric model fitted to that STL (see `extension.TUEBINGEN_FIT`). Each
modification is the same geometric operation the parametric model performs when its parameter
changes, so the two modes mean the same thing:

  width, thickness   scale the cross-section about the centreline (from the plate border onward)
  length             stretch the straight part of the tail; the rounded tip moves rigidly
  distal curve       bend the last third of the tail at constant curvature toward the tongue base
  angle              stretch the arc about its centre, so the tail swings toward the tongue base (+)
                     or the back wall of the throat (-); this is what changing `tilt_deg` does

`selftest()` checks this against the parametric model, where the exact answer is known.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
import trimesh
from scipy.spatial import cKDTree

from .meshutil import path_frames, arclength

X = np.array([1.0, 0.0, 0.0])


@dataclass
class Skeleton:
    path: np.ndarray      # fine centreline, from inside the plate to the end of the tail
    s: np.ndarray         # arclength along path
    s_root: float         # where the centreline crosses the plate's posterior border
    s_arc0: float         # start of the arc
    s_arc1: float         # end of the arc = start of the straight tail
    centre: np.ndarray    # arc centre
    radius: float
    phi_end: float        # arc sweep, rad
    tail_len: float       # straight tail length
    tip_zone: float       # distal length treated as the rigid rounded tip when the tail is stretched


def skeleton(ep, border_y: float, z_mid: float, step: float = 0.1, tip_zone: float | None = None) -> Skeleton:
    """Skeleton of the straight-tailed extension described by `ep` (its distal curve is ignored).
    `tip_zone`: distal length kept rigid when the tail is stretched (default: the model's tip rounding + 0.5 mm)."""
    from .extension import extension_path
    base = replace(ep, step=step, distal_curve_deg=0.0)
    path = extension_path(base, border_y, z_mid)
    s = arclength(path)
    s_arc0 = ep.embed_depth + ep.cleft_section_length
    phi_end = float(np.radians(90.0 + ep.tilt_deg))
    s_arc1 = s_arc0 + ep.bend_radius * phi_end
    B = np.array([ep.lateral_offset, border_y - ep.cleft_section_length + ep.ap_offset, z_mid])
    return Skeleton(path=path, s=s, s_root=ep.embed_depth, s_arc0=s_arc0, s_arc1=s_arc1,
                    centre=B + np.array([0.0, 0.0, -ep.bend_radius]), radius=ep.bend_radius, phi_end=phi_end,
                    tail_len=ep.effective_length, tip_zone=ep.tip_round_mm + 0.5 if tip_zone is None else tip_zone)


def _point_at(sk: Skeleton, s_target: float) -> np.ndarray:
    return np.array([np.interp(s_target, sk.s, sk.path[:, k]) for k in range(3)])


def deform(mesh: trimesh.Trimesh, sk: Skeleton, d_length: float = 0.0, d_angle_deg: float = 0.0,
           d_curve_deg: float = 0.0, k_width: float = 1.0, k_thickness: float = 1.0) -> trimesh.Trimesh:
    """Return a deformed copy of `mesh`. Vertices in front of the plate border are never moved."""
    if d_length <= -(sk.tail_len - sk.tip_zone):
        raise ValueError(f"cannot shorten by {-d_length:.1f} mm: the straight part of the tail is only "
                         f"{sk.tail_len - sk.tip_zone:.1f} mm long")
    V = mesh.vertices.copy()
    idx = cKDTree(sk.path).query(V)[1]
    sv = sk.s[idx]
    T, N = path_frames(sk.path)

    # 1. cross-section: width along x, thickness along the sagittal normal, ramped in over 1 mm behind the border
    if k_width != 1.0 or k_thickness != 1.0:
        r = np.clip((sv - sk.s_root) / 1.0, 0.0, 1.0)
        d = V - sk.path[idx]
        n = N[idx]
        dn = np.einsum("ij,ij->i", d, n)
        V[:, 0] += d[:, 0] * (k_width - 1.0) * r
        V += (dn * (k_thickness - 1.0) * r)[:, None] * n

    E = _point_at(sk, sk.s_arc1)
    tt = sk.path[-1] - E
    tt /= np.linalg.norm(tt)
    b = -np.cross(tt, X); b /= np.linalg.norm(b)          # toward the tongue base (anterior side of the tail)
    tail = sv >= sk.s_arc1 - 1e-9

    # 2. length: stretch the straight part of the tail, carry the tip zone rigidly
    L = sk.tail_len
    if d_length != 0.0:
        uz = L - sk.tip_zone
        u = (V[tail] - E) @ tt
        shift = np.where(u >= uz, d_length, d_length * np.clip(u, 0.0, None) / uz)
        V[tail] += shift[:, None] * tt
        L = L + d_length

    # 3. distal curve: constant-curvature bend over the last third of the (new) tail
    if d_curve_deg != 0.0:
        th = np.radians(d_curve_deg)
        Lc = L / 3.0
        u0 = L - Lc
        rho = Lc / th
        P = V[tail] - E
        u = P @ tt
        w = P @ b
        xl = P @ X
        sel = u > u0
        uu = u[sel] - u0
        ww = w[sel]
        a = np.minimum(uu, Lc) / rho
        beyond = np.maximum(uu - Lc, 0.0)
        Q = E + u0 * tt + rho * b
        new = (Q + (rho - ww)[:, None] * (-np.cos(a)[:, None] * b + np.sin(a)[:, None] * tt)
               + beyond[:, None] * (np.sin(th) * b + np.cos(th) * tt) + xl[sel][:, None] * X)
        Pt = V[tail]
        Pt[sel] = new
        V[tail] = Pt

    # 4. angle: stretch the arc about its centre; the tail swings by the full angle
    if d_angle_deg != 0.0:
        D = np.radians(d_angle_deg)
        alpha = np.zeros(len(V))
        arc = (sv >= sk.s_arc0) & ~tail
        alpha[arc] = D * np.clip((sv[arc] - sk.s_arc0) / (sk.s_arc1 - sk.s_arc0), 0.0, 1.0)
        alpha[tail] = D
        m = alpha != 0.0
        dy = V[m, 1] - sk.centre[1]
        dz = V[m, 2] - sk.centre[2]
        ca, sa = np.cos(alpha[m]), np.sin(alpha[m])
        V[m, 1] = sk.centre[1] + dy * ca - dz * sa
        V[m, 2] = sk.centre[2] + dy * sa + dz * ca

    out = trimesh.Trimesh(V, mesh.faces.copy(), process=False)
    return out


def deltas(target, base) -> dict:
    """Deformation that turns the `base` extension into `target` (both ExtensionParams of the same family)."""
    for f in ("cleft_section_length", "bend_radius", "embed_depth", "tip_round_mm"):
        if abs(getattr(target, f) - getattr(base, f)) > 1e-6:
            raise ValueError(f"'{f}' cannot be changed on the published Tübingen extension")
    if abs((target.width_tip - target.width_base) - (base.width_tip - base.width_base)) > 1e-6:
        raise ValueError("the published Tübingen extension can only be widened or narrowed uniformly")
    return {"d_length": target.effective_length - base.effective_length,
            "d_angle_deg": target.tilt_deg - base.tilt_deg,
            "d_curve_deg": target.distal_curve_deg - base.distal_curve_deg,
            "k_width": target.width_base / base.width_base,
            "k_thickness": target.thickness / base.thickness}


def _arm_distance(a: trimesh.Trimesh, b: trimesh.Trimesh, border_y: float, n: int = 6000,
                  near: np.ndarray | None = None, radius: float = 7.0) -> np.ndarray:
    """Symmetric surface distance over the arm (behind the plate border); with `near`, only points
    within `radius` of that point (used for the tip region)."""
    def pts(m, k, seed):
        p, _ = trimesh.sample.sample_surface_even(m, k, seed=seed)
        p = p[p[:, 1] < border_y - 1.5]
        return p if near is None else p[np.linalg.norm(p - near, axis=1) < radius]
    d1 = cKDTree(pts(b, 20 * n, 2)).query(pts(a, n, 1))[0]
    d2 = cKDTree(pts(a, 20 * n, 3)).query(pts(b, n, 4))[0]
    return np.concatenate([d1, d2])


def selftest(verbose: bool = True) -> list[dict]:
    """Deform the parametric extension and compare with the parametric extension regenerated with the
    changed parameter, which is the exact answer. Returns one row per case."""
    from .extension import ExtensionParams, build_extension
    base = ExtensionParams(step=0.25)
    m0, _ = build_extension(base, 0.0, 0.0)
    sk = skeleton(base, 0.0, 0.0)
    cases = {"shorten 3 mm": dict(effective_length=9.0), "lengthen 2 mm": dict(effective_length=14.0),
             "angle +8 deg": dict(tilt_deg=base.tilt_deg + 8), "angle -6 deg": dict(tilt_deg=base.tilt_deg - 6),
             "curve +15 deg": dict(distal_curve_deg=15.0), "curve -10 deg": dict(distal_curve_deg=-10.0),
             "widen 1.5 mm": dict(width_base=11.9, width_tip=11.9), "narrow 3 mm": dict(width_base=7.4, width_tip=7.4),
             "thin 0.4 mm": dict(thickness=3.1),
             "shorten 2, curve 10, angle 5": dict(effective_length=10.0, distal_curve_deg=10.0, tilt_deg=base.tilt_deg + 5)}
    rows = []
    for name, ch in cases.items():
        tgt = replace(base, **ch)
        exact, epath = build_extension(tgt, 0.0, 0.0)
        got = deform(m0, sk, **deltas(tgt, base))
        d = _arm_distance(got, exact, 0.0)
        moved = _arm_distance(m0, exact, 0.0)
        dt = _arm_distance(got, exact, 0.0, near=epath[-1])
        mt = _arm_distance(m0, exact, 0.0, near=epath[-1])
        row = {"case": name, "mean_mm": round(float(d.mean()), 3), "p95_mm": round(float(np.percentile(d, 95)), 3),
               "max_mm": round(float(d.max()), 3), "tip_region_max_mm": round(float(dt.max()), 3),
               "change_at_tip_max_mm": round(float(mt.max()), 2), "watertight": bool(got.is_watertight)}
        rows.append(row)
        if verbose:
            print(row)
    return rows
