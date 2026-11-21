"""Synthetic neonatal maxilla with a U-shaped cleft palate, exported as a scan-like surface.

Dimensions are in the range reported for newborns (intertuberosity width ~32 mm,
arch depth ~26 mm, palatal vault ~6 mm). This is NOT patient data; it exists so the
pipeline can be demonstrated and tested end to end.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import trimesh
from shapely.geometry import LineString, Polygon, Point


@dataclass
class MaxillaParams:
    intertuberosity_width: float = 32.0   # mm between posterior ridge ends
    anterior_radius: float = 12.5         # mm radius of the anterior arch curve
    arch_depth: float = 26.0              # mm from anterior crest apex to tuberosity line
    vault_height: float = 6.0             # mm palate vault above the ridge crest
    vault_width: float = 9.0              # mm, distance inside the ridge over which the vault rises
    buccal_slope: float = 1.3             # mm rise per mm outward on the buccal side
    buccal_extent: float = 6.0            # mm of buccal slope captured by the scan
    cleft: bool = True
    cleft_start_y: float = -9.0           # cleft begins this far posterior of the apex (y of apex = 0)
    cleft_half_width_start: float = 2.0   # mm at the anterior tip of the cleft
    cleft_half_width_end: float = 6.5     # mm at the posterior border
    cleft_depth: float = 13.0             # mm the cleft walls rise above the vault (nasal side)
    asymmetry: float = 0.0                # mm lateral shift of the cleft midline (for testing)
    noise_mm: float = 0.03
    # scan-like border (all off by default). A raw intraoral scan rarely stops at the vestibule: it carries lip and
    # cheek tissue that folds back down below the ridge crest, with a torn, irregular edge.
    rim_flap: float = 0.0                 # mm of lip/cheek tissue captured beyond the vestibular sulcus
    rim_drop: float = 1.6                 # mm the flap falls per mm outward past the sulcus
    rim_ragged: float = 0.0               # mm amplitude of the irregular scan edge
    hole_radius: float = 0.0              # mm: a patch of missing scan data in the vault (0 = none)
    hole_center: tuple = (5.0, -12.0)     # (x, y) of that patch, apex at (0, 0)
    step: float = 0.5
    seed: int = 7


def prs_published(seed: int = 7, **overrides) -> MaxillaParams:
    """Preset matching published neonatal Robin-sequence palate morphology.

    Willershausen et al., Orthod Craniofac Res 2024 (DOI 10.1111/ocr.12873), digitised casts of
    20 neonates with Robin sequence: intertuberosity width 33.05 +/- 1.95 mm, palatal length
    21.41 +/- 1.81 mm, posterior cleft width 12.69 +/- 2.31 mm, anterior transverse width ~21.5 mm.
    Pass sd=+1/-1 style overrides to sample the population, e.g. prs_published(intertuberosity_width=35.0).
    """
    base = dict(intertuberosity_width=33.0, arch_depth=23.0, anterior_radius=10.8,
                cleft_half_width_end=6.35, cleft_start_y=-8.0, vault_height=6.0, seed=seed)
    base.update(overrides)
    return MaxillaParams(**base)


def arch_polyline(p: MaxillaParams, n: int = 200) -> np.ndarray:
    """Alveolar ridge crest as an open U, anterior apex at (0, 0), opening posteriorly."""
    R = p.anterior_radius
    cy = -R
    # anterior semicircle from the right side (x<0) over the apex to the left side (x>0)
    th = np.linspace(np.pi, 0.0, n // 2)
    arc = np.column_stack([R * np.cos(th), cy + R * np.sin(th)])  # x from -R to +R at y=cy
    # posterior arms flare out to the tuberosities
    y_end = -p.arch_depth
    half_end = p.intertuberosity_width / 2.0
    ys = np.linspace(cy, y_end, n // 2)
    t = (ys - cy) / (y_end - cy)
    xs = R + (half_end - R) * (1 - (1 - t) ** 1.6)
    left = np.column_stack([xs, ys])          # +x side going posterior
    right = np.column_stack([-xs, ys])[::-1]  # -x side, ordered anterior->posterior reversed
    return np.vstack([right, arc[1:-1], left])


def cleft_half_width(p: MaxillaParams, y: np.ndarray) -> np.ndarray:
    y_end = -p.arch_depth - 4.0
    t = np.clip((p.cleft_start_y - y) / (p.cleft_start_y - y_end), 0.0, 1.0)
    # rounded anterior tip (U shape): quick widening then linear
    return p.cleft_half_width_start + (p.cleft_half_width_end - p.cleft_half_width_start) * np.sqrt(t)


def height_field(p: MaxillaParams, xy: np.ndarray) -> np.ndarray:
    crest = arch_polyline(p)
    line = LineString(crest)
    inside_poly = Polygon(np.vstack([crest, crest[-1] + [0, -40], crest[0] + [0, -40]]))
    d = np.array([line.distance(Point(q)) for q in xy])
    inside = np.array([inside_poly.contains(Point(q)) for q in xy])
    sd = np.where(inside, -d, d)  # negative inside the arch (palatal), positive buccal

    z = np.zeros(len(xy))
    # buccal side: rises outward toward the vestibular sulcus
    buc = sd > 0
    z[buc] = p.buccal_slope * np.minimum(sd[buc], p.buccal_extent)
    # past the sulcus (only scanned when rim_flap > 0): lip / cheek tissue folding back down below the crest
    out = sd > p.buccal_extent
    z[out] -= p.rim_drop * (sd[out] - p.buccal_extent)
    # palatal side: smooth vault
    pal = sd <= 0
    s = np.clip(-sd[pal] / p.vault_width, 0.0, 1.0)
    z[pal] = p.vault_height * (3 * s**2 - 2 * s**3)

    if p.cleft:
        x = xy[:, 0] - p.asymmetry
        y = xy[:, 1]
        hw = cleft_half_width(p, y)
        in_cleft = (y < p.cleft_start_y) & (np.abs(x) < hw)
        # distance to the cleft margin, for a short rounded transition
        margin = hw - np.abs(x)
        rise = np.clip(margin / 1.0, 0.0, 1.0)
        z_cleft = p.cleft_depth * (3 * rise**2 - 2 * rise**3)
        z = np.where(in_cleft & inside, np.maximum(z, z + z_cleft), z)
        # slight midline vomer ridge visible in the depth of the cleft
        vomer = in_cleft & inside & (np.abs(x) < 1.0)
        z[vomer] -= 1.0 * (1 - np.abs(x[vomer]))
    return z, sd


def build_maxilla(p: MaxillaParams | None = None) -> tuple[trimesh.Trimesh, dict]:
    p = p or MaxillaParams()
    rng = np.random.default_rng(p.seed)
    reach = p.buccal_extent + p.rim_flap + p.rim_ragged
    half_w = p.intertuberosity_width / 2.0 + reach + 2
    xs = np.arange(-half_w, half_w + 1e-6, p.step)
    ys = np.arange(-p.arch_depth - 4.0, reach + 1 + 1e-6, p.step)
    gx, gy = np.meshgrid(xs, ys)
    xy = np.column_stack([gx.ravel(), gy.ravel()])
    z, sd = height_field(p, xy)
    z += rng.normal(0.0, p.noise_mm, size=len(z))

    # keep only the region a scan would capture: palate + ridge + buccal slope (+ the scan-like rim, if asked)
    edge = np.full(len(xy), p.buccal_extent + p.rim_flap)
    if p.rim_ragged > 0:
        # irregular edge: a few random harmonics of the angle about the arch centre, plus small tears
        ang = np.arctan2(xy[:, 1] + p.arch_depth / 2.0, xy[:, 0])
        wob = np.zeros(len(xy))
        for k in range(2, 12):
            wob += rng.normal() / k * np.cos(k * ang + rng.uniform(0, 2 * np.pi))
        wob /= np.abs(wob).max() + 1e-9
        edge += p.rim_ragged * (wob + 0.35 * rng.uniform(-1, 1, len(xy)))
    keep = sd < edge
    if p.hole_radius > 0:
        keep &= np.hypot(xy[:, 0] - p.hole_center[0], xy[:, 1] - p.hole_center[1]) > p.hole_radius
    ny, nx = gx.shape
    idx = -np.ones(len(xy), dtype=int)
    idx[keep] = np.arange(keep.sum())
    verts = np.column_stack([xy[keep], z[keep]])
    faces = []
    K = keep.reshape(ny, nx)
    I = idx.reshape(ny, nx)
    for r in range(ny - 1):
        for c in range(nx - 1):
            if K[r, c] and K[r, c + 1] and K[r + 1, c] and K[r + 1, c + 1]:
                a, b, cc, d = I[r, c], I[r, c + 1], I[r + 1, c], I[r + 1, c + 1]
                # wound so that normals face the oral cavity (-z), like a scanner export
                faces.append([a, d, b])
                faces.append([a, cc, d])
    mesh = trimesh.Trimesh(verts, np.array(faces), process=True)
    truth = {
        "params": asdict(p),
        "apex": [0.0, 0.0, 0.0],
        "tuberosities": [[-p.intertuberosity_width / 2, -p.arch_depth, 0.0],
                         [p.intertuberosity_width / 2, -p.arch_depth, 0.0]],
        "occlusal_normal": [0.0, 0.0, 1.0],
    }
    return mesh, truth


def random_rigid(mesh: trimesh.Trimesh, seed: int = 1) -> tuple[trimesh.Trimesh, np.ndarray]:
    """Apply a random rigid transform, like an unoriented scanner export."""
    rng = np.random.default_rng(seed)
    T = trimesh.transformations.random_rotation_matrix(rng.random(3))
    T[:3, 3] = rng.normal(0, 20, 3)
    m = mesh.copy()
    m.apply_transform(T)
    return m, T
