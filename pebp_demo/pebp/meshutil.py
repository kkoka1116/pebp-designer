"""Small mesh helpers shared by the pipeline."""
from __future__ import annotations

import numpy as np
import trimesh
from scipy.spatial import Delaunay
from shapely.geometry import Polygon, Point


def grid_in_polygon(poly: Polygon, step: float) -> np.ndarray:
    """Regular grid of (x, y) points strictly inside a shapely polygon."""
    minx, miny, maxx, maxy = poly.bounds
    xs = np.arange(minx + step, maxx, step)
    ys = np.arange(miny + step, maxy, step)
    gx, gy = np.meshgrid(xs, ys)
    pts = np.column_stack([gx.ravel(), gy.ravel()])
    inner = poly.buffer(-0.35 * step)
    keep = np.array([inner.contains(Point(p)) for p in pts], dtype=bool)
    return pts[keep]


def lower_offset(height_fn, xy: np.ndarray, t: float, bounds: tuple | None = None, spacing: float = 0.25) -> np.ndarray:
    """z of the surface at distance `t` below the height field z = height_fn(x, y): the lower envelope of
    balls of radius t centred on it, z(p) = min over |q - p| <= t of height_fn(q) - sqrt(t^2 - |q - p|^2).
    Unlike a vertex offset along the normal it never folds, and it equals that offset wherever the
    surface is smoother than t. bounds: (x0, x1, y0, y1) to keep q on the field's own grid."""
    xy = np.asarray(xy, float)
    r = np.arange(-t, t + 1e-9, spacing)
    dx, dy = np.meshgrid(r, r, indexing="ij")
    d2 = dx.ravel() ** 2 + dy.ravel() ** 2
    keep = d2 <= t * t
    off = np.column_stack([dx.ravel()[keep], dy.ravel()[keep]])
    drop = np.sqrt(t * t - d2[keep])
    out = np.empty(len(xy))
    for lo in range(0, len(xy), 400):
        q = (xy[lo:lo + 400, None, :] + off[None, :, :]).reshape(-1, 2)
        if bounds is not None:
            q[:, 0] = np.clip(q[:, 0], bounds[0], bounds[1]); q[:, 1] = np.clip(q[:, 1], bounds[2], bounds[3])
        z = height_fn(q).reshape(-1, len(off)) - drop[None, :]
        out[lo:lo + 400] = z.min(axis=1)
    return out


def resample_ring(ring: np.ndarray, spacing: float) -> np.ndarray:
    """Resample a closed 2D polyline to roughly uniform spacing (no repeated end point)."""
    ring = np.asarray(ring, dtype=float)
    closed = np.vstack([ring, ring[:1]])
    seg = np.linalg.norm(np.diff(closed, axis=0), axis=1)
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    total = cum[-1]
    n = max(int(np.round(total / spacing)), 8)
    t = np.linspace(0.0, total, n, endpoint=False)
    x = np.interp(t, cum, closed[:, 0])
    y = np.interp(t, cum, closed[:, 1])
    return np.column_stack([x, y])


def kept_triangles(pts2: np.ndarray, poly: Polygon) -> np.ndarray:
    """Delaunay triangles of pts2 that lie inside `poly`, oriented counter-clockwise in xy, with the
    zero-area ones (from exactly collinear outline points) dropped: they would pass the inside test,
    then be removed later as degenerate and reopen the solid."""
    simp = Delaunay(pts2).simplices
    cent = pts2[simp].mean(axis=1)
    f = simp[np.array([poly.contains(Point(c)) for c in cent], dtype=bool)].copy()
    a = pts2[f[:, 1]] - pts2[f[:, 0]]; b = pts2[f[:, 2]] - pts2[f[:, 0]]
    cr = a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]
    f = f[np.abs(cr) > 1e-9]; cr = cr[np.abs(cr) > 1e-9]
    f[cr < 0] = f[cr < 0][:, [0, 2, 1]]
    return f


def boundary_walls(faces_top: np.ndarray, n: int) -> np.ndarray:
    """Side-wall faces joining the top surface (vertices 0..n-1) to the bottom (n..2n-1) along the
    boundary of the kept top triangles. Built from the triangles themselves rather than the outline
    order: the centroid test can discard a thin triangle along the outline, and walls that follow the
    outline would then no longer meet the top and bottom faces (an open solid)."""
    e = np.vstack([faces_top[:, [0, 1]], faces_top[:, [1, 2]], faces_top[:, [2, 0]]])
    key = e[:, 0].astype(np.int64) * (2 * n + 1) + e[:, 1]
    rev = e[:, 1].astype(np.int64) * (2 * n + 1) + e[:, 0]
    b = e[~np.isin(key, rev)]                     # directed edges with no twin: the boundary
    i, j = b[:, 0], b[:, 1]
    return np.vstack([np.column_stack([j, i, i + n]), np.column_stack([j, i + n, j + n])])


def slab_from_heightfields(
    outline: np.ndarray,
    step: float,
    top_fn,
    normal_fn,
    thickness: float,
    bottom_fn=None,
) -> trimesh.Trimesh:
    """Build a watertight slab bounded by a 2D outline.

    top_fn(xy) -> z of the upper (intaglio) surface.
    normal_fn(xy) -> unit normal of the upper surface pointing +z-ish.
    bottom_fn(xy) -> z of the lower surface; if None, the lower surface is the upper surface offset by
    `thickness` along -normal (which folds over itself where the upper surface bends more tightly than
    `thickness`; see lower_offset).
    """
    outline = resample_ring(outline, step)
    poly = Polygon(outline).buffer(0)
    if poly.geom_type == "MultiPolygon":
        poly = max(poly.geoms, key=lambda g: g.area)
    outline = np.array(poly.exterior.coords)[:-1]
    outline = resample_ring(outline, step)
    poly = Polygon(outline)
    inner = grid_in_polygon(poly, step)
    pts2 = np.vstack([outline, inner])

    faces_top = kept_triangles(pts2, poly)

    z_top = top_fn(pts2)
    top = np.column_stack([pts2, z_top])
    if bottom_fn is None:
        bottom = top - thickness * normal_fn(pts2)
    else:
        bottom = np.column_stack([pts2, np.minimum(bottom_fn(pts2), z_top - 1e-3)])

    n = len(pts2)
    faces_bottom = faces_top[:, [0, 2, 1]] + n

    sides = boundary_walls(faces_top, n)

    verts = np.vstack([top, bottom])
    faces = np.vstack([faces_top, faces_bottom, sides])
    mesh = trimesh.Trimesh(verts, faces, process=True)
    mesh.merge_vertices()
    mesh.update_faces(mesh.nondegenerate_faces())
    mesh.update_faces(mesh.unique_faces())
    mesh.fix_normals()
    if not mesh.is_watertight:
        trimesh.repair.fill_holes(mesh)
        mesh.fix_normals()
    return mesh


def path_frames(path: np.ndarray):
    """Unit tangents and in-sagittal-plane normals (cross(tangent, x)) along a path."""
    path = np.asarray(path, dtype=float)
    tang = np.gradient(path, axis=0)
    tang /= np.linalg.norm(tang, axis=1)[:, None]
    xhat = np.array([1.0, 0.0, 0.0])
    nv = np.cross(tang, xhat)
    nv /= np.linalg.norm(nv, axis=1)[:, None]
    return tang, nv


def sweep_tube(path: np.ndarray, widths: np.ndarray, thicknesses: np.ndarray,
               n_round: int = 5, round_tip_mm: float = 0.0) -> trimesh.Trimesh:
    """Sweep a rounded-rectangle cross-section along a 3D path.

    Width is along global x (transverse); thickness is along the path's sagittal normal.
    Both may vary per ring. With round_tip_mm > 0 the last rings shrink to a rounded tip.
    Returns a watertight tube.
    """
    path = np.asarray(path, dtype=float)
    widths = np.asarray(widths, dtype=float).copy()
    thicknesses = np.asarray(thicknesses, dtype=float).copy()
    m = len(path)
    _, nv = path_frames(path)
    xhat = np.array([1.0, 0.0, 0.0])
    if round_tip_mm > 0:
        seg = np.linalg.norm(np.diff(path, axis=0), axis=1)
        s_from_end = np.concatenate([np.cumsum(seg[::-1])[::-1], [0.0]])
        u = np.clip(s_from_end / round_tip_mm, 0.0, 1.0)
        scale = np.sqrt(np.clip(1.0 - (1.0 - u) ** 2, 0.0, 1.0))
        scale[-1] = 0.12
        widths *= scale
        thicknesses *= scale
    k = n_round
    prof = []
    for ci, (cu, cv) in enumerate([(1, 1), (-1, 1), (-1, -1), (1, -1)]):
        start = ci * np.pi / 2
        for t in np.linspace(start, start + np.pi / 2, k, endpoint=False):
            prof.append((cu, cv, np.cos(t), np.sin(t)))
    rings = []
    for i in range(m):
        r = thicknesses[i] / 2.0
        hw = max(widths[i] / 2.0 - r, 0.0)
        ring = [path[i] + (cu * hw + r * c) * xhat + (r * s) * nv[i] for cu, cv, c, s in prof]
        rings.append(np.array(ring))
    rings = np.array(rings)
    P = rings.shape[1]
    verts = rings.reshape(-1, 3)
    faces = []
    for i in range(m - 1):
        for j in range(P):
            a = i * P + j
            b = i * P + (j + 1) % P
            c = (i + 1) * P + j
            d = (i + 1) * P + (j + 1) % P
            faces.append([a, c, b])
            faces.append([b, c, d])
    c0 = len(verts)
    verts = np.vstack([verts, rings[0].mean(axis=0), rings[-1].mean(axis=0)])
    c1 = c0 + 1
    for j in range(P):
        faces.append([c0, (j + 1) % P, j])
        faces.append([c1, (m - 1) * P + j, (m - 1) * P + (j + 1) % P])
    mesh = trimesh.Trimesh(verts, np.array(faces), process=True)
    mesh.fix_normals()
    return mesh


RING_TOL_MM = 1e-5


def clean_ring(xy: np.ndarray, tol: float = RING_TOL_MM) -> np.ndarray:
    """A closed 2-D ring (open form, last point != first) without consecutive points closer than `tol`.
    Shapely unions and differences can leave two vertices a few 1e-15 mm apart where pieces meet. Shapely
    calls that polygon valid, but trimesh welds the pair when it extrudes or meshes it, so the prism or
    slab comes out non-manifold and Manifold refuses it ("Not all meshes are volumes")."""
    xy = np.asarray(xy, float)
    if len(xy) > 1 and np.linalg.norm(xy[0] - xy[-1]) < tol:
        xy = xy[:-1]
    keep = [0]
    for i in range(1, len(xy)):
        if np.linalg.norm(xy[i] - xy[keep[-1]]) >= tol:
            keep.append(i)
    while len(keep) > 3 and np.linalg.norm(xy[keep[-1]] - xy[keep[0]]) < tol:
        keep.pop()
    return xy[keep]


def clean_polygon(poly: Polygon, tol: float = RING_TOL_MM) -> Polygon:
    """`poly` with every ring passed through `clean_ring`."""
    return Polygon(clean_ring(np.array(poly.exterior.coords), tol),
                   [clean_ring(np.array(r.coords), tol) for r in poly.interiors])


def extrude_clean(poly: Polygon, height: float) -> trimesh.Trimesh:
    """trimesh.creation.extrude_polygon on a cleaned polygon, so the prism is always a closed volume."""
    return trimesh.creation.extrude_polygon(clean_polygon(poly), height)


# A cutter laid exactly along the surface it cuts puts some of its vertices on, or a float32 hair off,
# that surface. The Manifold boolean then returns a pinched mesh: watertight in memory through its vertex
# indices, with pairs of vertices at the same point. STL keeps no indices, so on reload those vertices weld
# and edges end up shared by four faces. It showed on the flat faces of the no-cleft Tübingen extension
# (number dimples, wire groove) and where the tissue clip runs along the plate's fitting surface. Such
# cutters are moved off the surface they would sit on (see reloads_watertight): the dimples and the groove
# by CUTTER_CLEARANCE_MM and grown by as much, so their depth is kept; the tissue clip by
# TISSUE_CLIP_CLEARANCE_MM, small enough that the plate's fitting surface does not change in practice.
# Either is many times the float32 spacing at these coordinates (about 2e-6 mm at 20 mm).
CUTTER_CLEARANCE_MM = 0.05
TISSUE_CLIP_CLEARANCE_MM = 0.005


def reloads_watertight(mesh: trimesh.Trimesh) -> bool:
    """True if the mesh is still one closed solid after a round trip through binary STL.

    STL stores loose triangles in float32, so a reader welds vertices by position. A boolean can return
    a mesh that is watertight only through its vertex indices (two vertices at the same point, kept
    apart); it passes `is_watertight` in memory and fails once written and read back."""
    import io
    if not mesh.is_watertight:
        return False
    back = trimesh.load(io.BytesIO(mesh.export(file_type="stl")), file_type="stl")
    return bool(back.is_watertight)


SLIVER_MM3 = 0.01


def drop_slivers(mesh: trimesh.Trimesh, min_volume: float = SLIVER_MM3) -> trimesh.Trimesh:
    """Remove zero-volume shells a boolean can leave where two surfaces nearly touch. They hold no
    material, but they make a one-piece part count as two bodies. Anything larger is kept, so a
    genuinely separate piece still fails the single-body check."""
    if mesh.body_count <= 1:
        return mesh
    parts = mesh.split(only_watertight=False)
    keep = [q for q in parts if abs(q.volume) >= min_volume]
    if len(keep) == len(parts) or not keep:
        return mesh
    return trimesh.util.concatenate(keep)


def manifold_boolean(op: str, meshes) -> trimesh.Trimesh:
    """Boolean on watertight meshes with the Manifold engine, exactly as trimesh's own bridge does it
    (float32 in, pairwise operations, the result taken as Manifold returns it) but without the
    fix_normals the pipeline used to call afterwards. Manifold output is already oriented, and
    fix_normals can turn an enclosed cavity shell outward, so that block minus device came back as block
    plus device. Nearly coplanar faces between pieces (tilted split planes) are not handled reliably at
    float32 or float64, so the mold keeps its planes axis-aligned instead."""
    import manifold3d as m3
    from functools import reduce
    meshes = list(meshes)
    if not all(m.is_volume for m in meshes):
        raise ValueError("Not all meshes are volumes!")
    ms = [m3.Manifold(mesh=m3.Mesh(vert_properties=np.array(m.vertices, dtype=np.float32, order="C"),
                                   tri_verts=np.array(m.faces, dtype=np.uint32, order="C"))) for m in meshes]
    if op == "difference":
        r = ms[0] - (reduce(lambda a, b: a + b, ms[1:]) if len(ms) > 2 else ms[1])
    elif op == "union":
        r = reduce(lambda a, b: a + b, ms)
    elif op == "intersection":
        r = reduce(lambda a, b: a ^ b, ms)
    else:
        raise ValueError(op)
    o = r.to_mesh()
    return trimesh.Trimesh(vertices=o.vert_properties, faces=o.tri_verts, process=False)


def boolean_union(a: trimesh.Trimesh, b: trimesh.Trimesh) -> trimesh.Trimesh:
    return drop_slivers(manifold_boolean("union", [a, b]))


def boolean_difference(a: trimesh.Trimesh, b: trimesh.Trimesh) -> trimesh.Trimesh:
    return drop_slivers(manifold_boolean("difference", [a, b]))


def solid_between(outline: np.ndarray, step: float, z_lo_fn, z_hi_fn) -> trimesh.Trimesh:
    """Watertight solid over a 2D outline between a lower and an upper height function.
    Two solids built with the same outline and step share identical vertices where their
    height functions agree, so they mate exactly."""
    outline = resample_ring(np.asarray(outline, float), step)
    poly = Polygon(outline).buffer(0)
    if poly.geom_type == "MultiPolygon":
        poly = max(poly.geoms, key=lambda g: g.area)
    outline = resample_ring(np.array(poly.exterior.coords)[:-1], step)
    poly = Polygon(outline)
    inner = grid_in_polygon(poly, step)
    pts2 = np.vstack([outline, inner])
    ft = kept_triangles(pts2, poly)
    top = np.column_stack([pts2, z_hi_fn(pts2)])
    bot = np.column_stack([pts2, z_lo_fn(pts2)])
    n = len(pts2)
    fb = ft[:, [0, 2, 1]] + n
    mesh = trimesh.Trimesh(np.vstack([top, bot]), np.vstack([ft, fb, boundary_walls(ft, n)]), process=True)
    mesh.merge_vertices(); mesh.update_faces(mesh.nondegenerate_faces()); mesh.update_faces(mesh.unique_faces())
    mesh.fix_normals()
    if not mesh.is_watertight:
        trimesh.repair.fill_holes(mesh); mesh.fix_normals()
    return mesh


def arclength(path: np.ndarray) -> np.ndarray:
    seg = np.linalg.norm(np.diff(np.asarray(path, float), axis=0), axis=1)
    return np.concatenate([[0.0], np.cumsum(seg)])


def _filled_polygons(planar) -> list:
    """Filled regions of a planar section, holes included, without trimesh's polygons_full (which needs
    rtree once a section has more than one loop). Loops are combined by even-odd fill, largest first."""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    loops = []
    for d in planar.discrete:
        if len(d) >= 4 and np.allclose(d[0], d[-1], atol=1e-6):
            pg = Polygon(d).buffer(0)
            if not pg.is_empty and pg.area > 1e-9:
                loops.append(pg)
    if not loops:
        return []
    region = None
    for pg in sorted(loops, key=lambda g: -g.area):
        region = pg if region is None else region.symmetric_difference(pg)
    region = unary_union(region)
    return [g for g in getattr(region, "geoms", [region]) if g.geom_type == "Polygon" and g.area > 1e-9]


def _section_polygon(mesh: trimesh.Trimesh, origin, normal):
    """Cross-section of `mesh` by the plane (origin, normal) nearest to `origin`, as (shapely polygon, to3d, origin2d)."""
    sec = mesh.section(plane_origin=origin, plane_normal=normal)
    if sec is None:
        return None
    planar, to3d = sec.to_planar()
    polys = _filled_polygons(planar)
    if not polys:
        return None
    o2 = trimesh.transform_points([origin], np.linalg.inv(to3d))[0][:2]
    best = min(polys, key=lambda g: g.distance(Point(o2)))
    return best, to3d, o2


def centreline_through(mesh: trimesh.Trimesh, path: np.ndarray) -> np.ndarray:
    """Move each path point to the centroid of the mesh's cross-section perpendicular to the path there."""
    T, _ = path_frames(path)
    out = np.array(path, dtype=float, copy=True)
    for i, (p, t) in enumerate(zip(path, T)):
        got = _section_polygon(mesh, p, t)
        if got is None:
            continue
        poly, to3d, _ = got
        c = np.array(poly.centroid.coords[0])
        out[i] = trimesh.transform_points([[c[0], c[1], 0.0]], to3d)[0]
    sm = out.copy()
    sm[1:-1] = 0.25 * out[:-2] + 0.5 * out[1:-1] + 0.25 * out[2:]
    return sm


def surface_point(mesh: trimesh.Trimesh, origin, tangent, direction, reach: float = 15.0):
    """Where a ray from `origin` along `direction` (in the plane perpendicular to `tangent`) leaves the
    mesh's cross-section. Returns a 3D point, or None if the section is missing."""
    from shapely.geometry import LineString
    got = _section_polygon(mesh, np.asarray(origin, float), np.asarray(tangent, float))
    if got is None:
        return None
    poly, to3d, o2 = got
    inv = np.linalg.inv(to3d)
    d2 = trimesh.transform_points([np.asarray(origin, float) + np.asarray(direction, float)], inv)[0][:2] - o2
    nd = np.linalg.norm(d2)
    if nd < 1e-9:
        return None
    ray = LineString([o2, o2 + d2 / nd * reach])
    hit = ray.intersection(poly.exterior)
    if hit.is_empty:
        return None
    pts = [hit] if hit.geom_type == "Point" else [g for g in getattr(hit, "geoms", []) if g.geom_type == "Point"]
    if not pts:
        return None
    far = max(pts, key=lambda q: Point(o2).distance(q))
    return trimesh.transform_points([[far.x, far.y, 0.0]], to3d)[0]


def tip_arclength(mesh: trimesh.Trimesh, path: np.ndarray, radius: float = 9.0) -> float:
    """Arclength of the actual tip: the path's end plus however far the mesh reaches beyond it along the
    final tangent (a rounded cap may sit slightly past the last centreline point)."""
    s = arclength(path)
    end = np.asarray(path[-1], float)
    t = end - np.asarray(path[-4], float); t /= np.linalg.norm(t)
    v = mesh.vertices
    near = v[np.linalg.norm(v - end, axis=1) < radius]
    over = float(((near - end) @ t).max()) if len(near) else 0.0
    return float(s[-1] + over)
