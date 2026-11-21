"""Parametric velopharyngeal extension (the 'baton' / spur)."""
from __future__ import annotations

from dataclasses import dataclass, asdict, replace
from pathlib import Path

import numpy as np
import trimesh

from .meshutil import sweep_tube, path_frames, boolean_union, boolean_difference, manifold_boolean


@dataclass
class ExtensionParams:
    """Parametric velopharyngeal extension. Defaults are calibrated to the Tübingen standard
    extension for cleft patients (Aretxabaleta et al. 2025, Bioengineering 12:1063, Figure 7 and
    the supplementary technical drawing): width 10.4 mm, thickness 3.5 mm, arc radius 13.4 mm
    through 85.25 deg, 21.55 mm horizontal reach, 28.27 mm overall, rounded tip.
    The no-cleft variant uses a flatter arc (radius 18.9 mm)."""
    cleft_section_length: float = 6.0   # mm straight run before the arc (fitted to the cleft-variant STL)
    bend_radius: float = 13.25          # mm centreline (R15 outer / R11.5 inner, cleft variant); 18.9 for the no-cleft variant
    effective_length: float = 12.0      # mm straight tail after the arc, ending in the rounded tip
    width_base: float = 10.4            # mm, constant width in the standard design
    width_tip: float = 10.4             # mm
    thickness: float = 3.5              # mm; Tübingen rule: never below 3.0
    tilt_deg: float = -4.75             # arc ends at 85.25 deg, i.e. 4.75 deg short of perpendicular
    distal_curve_deg: float = 0.0       # 'C-shape': extra anterior curvature over the distal third
    ap_offset: float = 0.0              # prototype shift along y: -2 posterior, 0, +2 anterior
    lateral_offset: float = 0.0         # mm, for asymmetric pharynx
    embed_depth: float = 4.0            # start buried inside the plate so the union is solid
    tip_round_mm: float = 5.0           # semicircular tip
    step: float = 0.5

    def total_length(self) -> float:
        return self.cleft_section_length + np.radians(90 + self.tilt_deg) * self.bend_radius + self.effective_length


def extension_path(p: ExtensionParams, border_y: float, z_mid: float) -> np.ndarray:
    """Centreline from inside the plate to the tip. Returns N x 3."""
    pts = []
    s = p.step
    start = np.array([0.0, border_y + p.embed_depth, z_mid])
    # cleft section: straight posterior
    L1 = p.embed_depth + p.cleft_section_length
    for d in np.arange(0.0, L1, s):
        pts.append(start + np.array([0.0, -d, 0.0]))
    B = start + np.array([0.0, -L1, 0.0])
    # bend: from heading -y to heading (-z tilted by tilt_deg toward +y)
    R = p.bend_radius
    C = B + np.array([0.0, 0.0, -R])
    phi_end = np.radians(90.0 + p.tilt_deg)
    n_arc = max(int(np.ceil(phi_end * R / s)), 4)
    for phi in np.linspace(0.0, phi_end, n_arc + 1)[1:]:
        pts.append(C + R * np.array([0.0, -np.sin(phi), np.cos(phi)]))
    heading = np.array([0.0, -np.cos(phi_end), -np.sin(phi_end)])
    # effective section: straight, with optional distal curvature over the last third
    L2 = p.effective_length
    L_curve = L2 / 3.0
    n = int(np.ceil(L2 / s))
    cur = pts[-1].copy()
    travelled = 0.0
    for i in range(n):
        ds = min(s, L2 - travelled)
        if p.distal_curve_deg != 0.0 and travelled > L2 - L_curve:
            dtheta = np.radians(p.distal_curve_deg) * ds / L_curve
            # rotate heading in the sagittal (y, z) plane toward +y (anterior)
            hy, hz = heading[1], heading[2]
            heading = np.array([0.0, hy * np.cos(dtheta) - hz * np.sin(dtheta), hy * np.sin(dtheta) + hz * np.cos(dtheta)])
        cur = cur + heading * ds
        travelled += ds
        pts.append(cur.copy())
    path = np.array(pts)
    path[:, 0] += p.lateral_offset
    path[:, 1] += p.ap_offset
    return path


def build_extension(p: ExtensionParams, border_y: float, z_mid: float) -> tuple[trimesh.Trimesh, np.ndarray]:
    path = extension_path(p, border_y, z_mid)
    seg = np.linalg.norm(np.diff(path, axis=0), axis=1)
    s = np.concatenate([[0.0], np.cumsum(seg)])
    u = s / s[-1]
    widths = p.width_base + (p.width_tip - p.width_base) * u
    thick = np.full(len(path), p.thickness)
    tube = sweep_tube(path, widths, thick, round_tip_mm=p.tip_round_mm)
    return tube, path


def marker_centres(mesh: trimesh.Trimesh, path: np.ndarray, number: int, p: ExtensionParams,
                   border_y: float) -> list[np.ndarray]:
    """Points on the inferior (tongue-side) face of the cleft section, one per prototype number.
    Found on the actual surface, so they work for the published STL as well as the parametric baton."""
    from .meshutil import surface_point
    T, nv = path_frames(path)
    out = []
    for k in range(number):
        y = border_y + p.ap_offset - 3.0 - 2.2 * k
        i = int(np.argmin(np.abs(path[:, 1] - y)))
        sp = surface_point(mesh, path[i], T[i], -nv[i])
        out.append(sp if sp is not None else path[i] - nv[i] * (p.thickness / 2.0))
    return out


def engrave_number(mesh: trimesh.Trimesh, path: np.ndarray, number: int, p: ExtensionParams,
                   border_y: float) -> trimesh.Trimesh:
    """Engrave `number` dimples on the inferior face of the cleft section (visible under endoscopy)."""
    out = mesh
    for centre in marker_centres(mesh, path, number, p, border_y):
        sph = trimesh.creation.icosphere(subdivisions=2, radius=0.8)
        sph.apply_translation(centre)
        out = boolean_difference(out, sph)
    return out


def cut_wire_groove(mesh: trimesh.Trimesh, path: np.ndarray, p: ExtensionParams, border_y: float,
                    depth: float = 1.5, width: float = 2.0, start_mm: float = 10.0, end_mm: float = 2.0,
                    refine: bool = False) -> tuple[trimesh.Trimesh, dict]:
    """Groove for the safety wire on the face away from the tongue, from `start_mm` after the extension
    root to `end_mm` before the actual tip (Tübingen: about 1 cm, and 1.5-2 mm). The groove follows the
    real surface and is never deeper than 45% of the local thickness, so it can run into the rounded tip.
    Returns (mesh, measured groove info)."""
    from .meshutil import arclength, centreline_through, surface_point, tip_arclength
    if refine:
        path = centreline_through(mesh, path)
    s = arclength(path)
    s_root = float(s[int(np.argmin(np.abs(path[:, 1] - (border_y + p.ap_offset))))])
    s_tip = tip_arclength(mesh, path)
    a, b = s_root + start_mm, s_tip - end_mm
    st = np.linspace(a, b, max(int(round((b - a) / 0.5)), 1) + 1)     # ~0.5 mm stations, ending exactly at b
    pts = np.column_stack([np.interp(st, s, path[:, k]) for k in range(3)])
    t_end = path[-1] - path[-4]; t_end /= np.linalg.norm(t_end)
    past = st > s[-1]                                   # the actual tip can sit a little past the centreline's end
    pts[past] = path[-1] + (st[past] - s[-1])[:, None] * t_end
    T, N = path_frames(pts)
    g, d = [], []
    for c, t, n in zip(pts, T, N):
        outer = surface_point(mesh, c, t, n)
        inner = surface_point(mesh, c, t, -n)
        if outer is None or inner is None:
            continue
        g.append(outer); d.append(min(depth, 0.45 * float(np.linalg.norm(outer - inner))))
    g, d = np.array(g), np.array(d)
    cutter = sweep_tube(g, np.full(len(g), width), 2 * d, n_round=2)
    info = {"depth_mm": [round(float(d.min()), 2), round(float(d.max()), 2)], "width_mm": width,
            "start_mm_after_root": round(float(st[0] - s_root), 2),
            "end_mm_before_tip": round(float(s_tip - st[-1]), 2), "length_mm": round(float(st[-1] - st[0]), 1)}
    return boolean_difference(mesh, cutter), info


TUEBINGEN_VARIANTS = {
    "cleft": "Tuebingen_Standard_TPP_velopharyngeal_extension_CleftPatient.stl",
    "nocleft": "Tuebingen_Standard_TPP_velopharyngeal_extension_NoCleftPatient.stl",
}
TUEBINGEN_DIR = Path(__file__).resolve().parents[2] / "external" / "tuebingen_supplement" / "s001"
TUEBINGEN_CITATION = ("Aretxabaleta M. et al., Bioengineering 2025;12(10):1063, doi:10.3390/bioengineering12101063, "
                      "supplementary file bioengineering-12-01063-s001.zip (CC BY 4.0)")


# The two supplement files use different axis conventions (no-cleft: block at min y, arm along +y
# curving to +z; cleft: block at max y, arm along -y curving to +z). Rather than trust a convention,
# every proper rotation that keeps x as the width axis is tried and the one that puts the 11.8 mm
# anchor block at the anterior end with the arm descending from it is used.
_ROTATION_CANDIDATES = [
    np.diag([1.0, -1.0, -1.0]), np.diag([-1.0, 1.0, -1.0]), np.diag([1.0, 1.0, 1.0]), np.diag([-1.0, -1.0, 1.0]),
    np.array([[1.0, 0, 0], [0, 0, 1.0], [0, -1.0, 0]]), np.array([[1.0, 0, 0], [0, 0, -1.0], [0, 1.0, 0]]),
    np.array([[-1.0, 0, 0], [0, 0, 1.0], [0, 1.0, 0]]), np.array([[-1.0, 0, 0], [0, 0, -1.0], [0, -1.0, 0]]),
]


def _locate_attachment(v: np.ndarray):
    """Find the attachment end (the 11.8 mm wide foot at the anterior end) and check the arm hangs
    down and back from it. Works in the design orientation (y anterior, z superior).
    Returns (attachment_xyz, ok, info)."""
    y_max = v[:, 1].max()
    front = v[v[:, 1] > y_max - 3.0]
    det = {"foot_width_mm": round(float(np.ptp(front[:, 0])), 2) if len(front) > 3 else 0.0}
    if len(front) < 10 or det["foot_width_mm"] < 11.0:
        det["reason"] = "anterior end is not the 11.8 mm foot"
        return None, False, det
    att = np.array([front[:, 0].mean(), y_max, 0.5 * (front[:, 2].max() + front[:, 2].min())])
    low = v[np.argmin(v[:, 2])]
    det.update({"reach_mm": round(float(y_max - v[:, 1].min()), 2), "drop_mm": round(float(att[2] - low[2]), 2),
                "lowest_point_posterior_of_foot_mm": round(float(y_max - low[1]), 2),
                "rise_above_foot_mm": round(float(v[:, 2].max() - att[2]), 2)})
    ok = det["drop_mm"] > 8.0 and det["lowest_point_posterior_of_foot_mm"] > 10.0 and det["rise_above_foot_mm"] < 3.0
    if not ok:
        det["reason"] = "arm does not hang down and back from the foot in this orientation"
    return att, ok, det


_MIDPLANE_CACHE: dict = {}


def _midplane_alignment(variant: str, mesh: trimesh.Trimesh, border_y: float, z_mid: float,
                        ap_offset: float, lateral_offset: float) -> np.ndarray:
    """Rotation taking the arm's own midplane (fitted to the centroids of its cross-sections) to x = const.
    A property of the file, so it is computed once per variant."""
    if variant not in _MIDPLANE_CACHE:
        from .meshutil import centreline_through
        path = extension_path(tuebingen_params(variant, ap_offset=ap_offset, lateral_offset=lateral_offset), border_y, z_mid)
        cl = centreline_through(mesh, path[path[:, 1] < border_y + ap_offset - 0.5])
        _, _, vt = np.linalg.svd(cl - cl.mean(axis=0))
        w = vt[2] if vt[2][0] > 0 else -vt[2]
        _MIDPLANE_CACHE[variant] = trimesh.geometry.align_vectors(w, [1.0, 0.0, 0.0])[:3, :3]
    return _MIDPLANE_CACHE[variant]


def load_tuebingen_extension(variant_or_path: str, border_y: float, z_mid: float,
                             ap_offset: float = 0.0, lateral_offset: float = 0.0):
    """The real Tübingen standard extension, placed on the plate.

    The arm root (posterior face of the anchor block, mid-thickness) is put on the posterior border
    midline at the plate's mid-thickness; the anchor block stays inside/above the plate for the union
    and is clipped to the intaglio afterwards. Returns (mesh, info)."""
    path = Path(variant_or_path)
    variant = variant_or_path if variant_or_path in TUEBINGEN_VARIANTS else None
    if variant:
        path = TUEBINGEN_DIR / TUEBINGEN_VARIANTS[variant]
    if not path.exists():
        raise FileNotFoundError(f"Tübingen extension STL not found: {path}")
    m = trimesh.load(path, force="mesh")
    chosen = None
    tried = []
    for k, R in enumerate(_ROTATION_CANDIDATES):
        v = m.vertices @ R.T
        att, ok, det = _locate_attachment(v)
        tried.append(det)
        if ok:
            chosen = (k, v, att, det); break
    if chosen is None:
        raise RuntimeError(f"could not orient {path.name}: {tried}")
    k, v, att, det = chosen
    det["rotation_candidate"] = k
    # attachment face 1.5 mm inside the posterior border, mid-thickness at the plate's mid-thickness
    pivot = np.array([lateral_offset, border_y + 1.5 + ap_offset, z_mid])
    v = v - att + pivot
    out = trimesh.Trimesh(v, m.faces, process=True)
    out.fix_normals()
    # The arm of the published cleft-variant file lies in a plane turned 2.6 deg about z from its own
    # axes (planar to 0.03 mm; the no-cleft file is exactly aligned), so placed as-is the baton drifted
    # about 0.9 mm sideways along its length, and the mold's split planes were tilted. Turn it rigidly
    # about the root so its midplane is the patient's midsagittal plane; the shape is not changed.
    if variant:
        R = _midplane_alignment(variant, out, border_y, z_mid, ap_offset, lateral_offset)
        out.vertices = (out.vertices - pivot) @ R.T + pivot
        det["midplane_alignment_deg"] = round(float(np.degrees(np.arccos(np.clip((np.trace(R) - 1) / 2, -1, 1)))), 2)
    # connector: a 10.4 x 3.5 mm bar from the attachment face forward into the plate ("connecting structures")
    reach_in = 4.0 + max(0.0, -ap_offset)
    y_face = border_y + 1.5 + ap_offset
    bar = trimesh.creation.box(extents=[10.4, reach_in + 1.5, 3.5])
    bar.apply_translation([lateral_offset, y_face + 0.5 * (reach_in + 1.5) - 1.5, z_mid])
    out = manifold_boolean("union", [out, bar])
    import hashlib
    info = {"source_file": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "citation": TUEBINGEN_CITATION, "variant": variant or "custom",
            "orientation": det, "tip": out.vertices[np.argmin(out.vertices[:, 2])].round(2).tolist(),
            "posterior_reach_mm": round(float(border_y - out.vertices[:, 1].min()), 2),
            "nominal_length_mm": 28.27,
            "extent_mm": (out.bounds[1] - out.bounds[0]).round(2).tolist()}
    return out, info


# The parametric model fitted to each supplement STL (Nelder-Mead on the symmetric surface distance of
# the arm, 2026-11-16). It is not used as the baton's shape; it is the centreline skeleton along which
# the published mesh is deformed when an endoscopy finding changes it (see deform.py). The residual is
# how far the published shape departs from this family: mean / 95th percentile / max, in mm.
TUEBINGEN_FIT = {
    "cleft": {"params": {"cleft_section_length": 5.662, "bend_radius": 12.956, "tilt_deg": 0.0, "effective_length": 12.675,
                         "tip_round_mm": 1.002},
              "residual_mm": {"mean": 0.34, "p95": 0.89, "max": 1.37}},
    "nocleft": {"params": {"cleft_section_length": 2.853, "bend_radius": 15.344, "tilt_deg": 0.0, "effective_length": 11.521,
                           "tip_round_mm": 4.563},
                "residual_mm": {"mean": 0.20, "p95": 0.63, "max": 0.85}},
}


def tuebingen_params(variant: str, **overrides) -> ExtensionParams:
    """The published extension's own geometry, in this model's terms (width 10.4, thickness 3.5)."""
    return ExtensionParams(**{**TUEBINGEN_FIT[variant]["params"], "width_base": 10.4, "width_tip": 10.4, "thickness": 3.5,
                              "distal_curve_deg": 0.0, **overrides})


def resolve_mode(mode: str, cleft_present: bool) -> str:
    """'auto' picks the published Tübingen extension for the palate: the cleft variant if a cleft was found."""
    if mode == "auto":
        return "tuebingen-cleft" if cleft_present else "tuebingen-nocleft"
    return mode


def make_extension(ep: ExtensionParams, border_y: float, z_mid: float, mode: str = "parametric"):
    """The baton for one design. Returns (mesh, centreline, info).

    parametric        swept from `ep`
    tuebingen-<v>     the published STL; if `ep` differs from that extension's own geometry (an endoscopy
                      change), the mesh is deformed along its fitted skeleton to match `ep`
    path/to/file.stl  any extension in the same convention; only shifts are possible, since it has no skeleton
    """
    if mode == "parametric":
        mesh, path = build_extension(ep, border_y, z_mid)
        return mesh, path, {"source": "parametric"}
    from .deform import skeleton, deform, deltas
    variant = mode.replace("tuebingen-", "") if mode.startswith("tuebingen-") else None
    mesh, info = load_tuebingen_extension(variant or mode, border_y, z_mid, ap_offset=ep.ap_offset, lateral_offset=ep.lateral_offset)
    if variant is None:
        return mesh, None, info
    base = tuebingen_params(variant, ap_offset=ep.ap_offset, lateral_offset=ep.lateral_offset)
    d = deltas(ep, base)
    changed = {k: round(v, 3) for k, v in d.items() if abs(v - (1.0 if k.startswith("k_") else 0.0)) > 1e-9}
    if changed:
        # the drawing gives an R4.5 tip; keep at least that much rigid when the tail is stretched
        sk = skeleton(base, border_y, z_mid, tip_zone=max(base.tip_round_mm, 4.5) + 0.5)
        mesh = deform(mesh, sk, **d)
        info["tip"] = mesh.vertices[np.argmin(mesh.vertices[:, 2])].round(2).tolist()
    info["deformation"] = changed
    info["skeleton_fit_residual_mm"] = TUEBINGEN_FIT[variant]["residual_mm"]
    return mesh, extension_path(ep, border_y, z_mid), info


def prototype_set(base: ExtensionParams, offsets=(-2.0, 0.0, 2.0)) -> list[ExtensionParams]:
    """Tübingen standard: three prototypes 2 mm apart in the sagittal direction."""
    return [replace(base, ap_offset=o) for o in offsets]
