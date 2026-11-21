"""Design quality checks, modelled on the Tübingen pre-delivery rules (Aretxabaleta et al. 2025).

Two kinds of device are checked:
  prototype  printed for the endoscopy try-in: minutes in the mouth, under direct view, no wire
  final      worn for weeks: plate at least 2.5 mm, and a safety wire from about 1 cm after the
             extension root to 1.5-2 mm before the tip (the last 1.5-2 mm is the bedside shortening reserve)
"""
from __future__ import annotations

import numpy as np
import trimesh
from shapely.geometry import Polygon, box
from shapely.ops import unary_union

from .extension import ExtensionParams
from .meshutil import arclength, tip_arclength
from .plate import PlateParams

STANDARD_REVIEW_MM = 4.0    # project heuristic, not a published limit: two prototype steps away from the standard
ROOT_SUPPORT_WARN = 0.90    # project heuristics, not published limits: share of the baton root's width over the plate
ROOT_SUPPORT_FAIL = 0.70
ROOT_STRIPS = (1, 2, 3, 4)  # 1 mm strips, mm in front of the posterior border midline (the attachment line)
FILL_WARN_MM2 = 5.0         # project heuristics, not published limits: plate area over extrapolated surface
FILL_WARN_ROOT = 0.05       # ... and its share of the plate round the baton root
ROOT_WINDOW = (7.4, 5.0)    # |x| and depth in front of the posterior border midline: the root (11.8 mm) plus 1.5 mm each side


def _check(name, ok, value, rule, level="fail"):
    return {"check": name, "status": "pass" if ok else level, "value": value, "rule": rule}


def arm_length(mesh: trimesh.Trimesh, path: np.ndarray, ep: ExtensionParams) -> float:
    """Centreline length from the extension root (where it leaves the plate) to the actual tip."""
    return tip_arclength(mesh, path) - ep.embed_depth


def tip_point(mesh: trimesh.Trimesh, path: np.ndarray) -> np.ndarray:
    s = arclength(path)
    t = path[-1] - path[-4]; t /= np.linalg.norm(t)
    return path[-1] + t * (tip_arclength(mesh, path) - s[-1])


def graded_checks(pp: PlateParams, final: bool = True) -> list[dict]:
    out = []
    if getattr(pp, "border_relief", False) and final:
        out.append(_check("border relief: retention", False, True,
                          "vestibular flange capped, labial frenulum notched, tuberosities relieved (project heuristic, "
                          "Weismann 2026 ulcer sites): confirm the plate stays seated at try-in", level="warn"))
    return out + _graded_checks(pp, final)


def _graded_checks(pp: PlateParams, final: bool = True) -> list[dict]:
    """A graded plate (project heuristic): the thin floor is a hard limit; going below the Tübingen final
    plate thickness at all is flagged on every final device, so nobody casts one without seeing it."""
    if getattr(pp, "profile", "uniform") != "graded":
        return []
    from .graded import THIN_FLOOR_MM, MAX_THICKNESS_RATIO
    out = [_check("graded plate: thin floor", pp.thin_thickness >= THIN_FLOOR_MM - 1e-9, pp.thin_thickness,
                  f">= {THIN_FLOOR_MM} mm anywhere (project heuristic; Choi 2012: PMMA under 2 mm is much weaker)"),
           _check("graded plate: transition ratio", pp.thickness / pp.thin_thickness <= MAX_THICKNESS_RATIO + 1e-9,
                  round(pp.thickness / pp.thin_thickness, 2), f"base/thin <= {MAX_THICKNESS_RATIO} (MoldStudio graded-thickness rule)")]
    if final:
        out.append(_check("graded plate: below Tübingen thickness", False, pp.thin_thickness,
                          "the Tübingen final plate is 2.5-3 mm everywhere; this plate is thinner away from the root, cleft "
                          "and bow wires. A project heuristic: bench-test before any clinical use", level="warn"))
    return out


def overall(checks: list[dict]) -> str:
    return "fail" if any(c["status"] == "fail" for c in checks) else ("warn" if any(c["status"] == "warn" for c in checks) else "pass")


def landmark_check(lm_json: dict) -> dict:
    """The design frame is only meaningful if the arch was read the right way round: the tuberosities
    side to side on either side of the midline, the apex well in front of them. A detector that reads
    a part-arch or an odd scan along the wrong axis can still give a plate that passes every geometric
    check, so a gross misreading fails the design outright. A tilted tuberosity line is only a warning:
    it can be real (segment displacement in a unilateral cleft) or a frame turned about z, which would
    aim the extension off the midline; either way someone should look."""
    r, l, a = (np.asarray(lm_json[k], float) for k in ("tuberosity_right", "tuberosity_left", "apex"))
    dx, dy = abs(r[0] - l[0]), abs(r[1] - l[1])
    depth = a[1] - 0.5 * (r[1] + l[1])
    gap = min(np.linalg.norm(a[:2] - r[:2]), np.linalg.norm(a[:2] - l[:2]))
    tilt = float(np.degrees(np.arctan2(dy, dx)))
    gross = dx < dy or r[0] * l[0] >= 0 or depth < 10.0 or gap < 8.0
    status = "fail" if gross else ("warn" if tilt > 15.0 else "pass")
    return {"check": "arch landmarks plausible", "status": status,
            "value": {"tuberosity_line_tilt_deg": round(tilt, 1), "arch_depth_mm": round(float(depth), 1),
                      "apex_to_nearest_tuberosity_mm": round(float(gap), 1)},
            "rule": "fail if the tuberosities are not side to side on either side of the midline, the apex is under "
                    "10 mm in front of them or within 8 mm of one (the scan was read along the wrong axis: check the "
                    "landmarks or try the other backend); warn if the tuberosity line is tilted over 15 deg (real "
                    "asymmetry, or a frame turned about z that would aim the extension off the midline: confirm the midline)"}


def root_span(ext: trimesh.Trimesh, border_y: float, z_mid: float, lateral_offset: float = 0.0) -> dict:
    """Where the baton crosses the posterior border: the x range of the extension's plan section at the
    plate's mid-thickness within 1 mm of the border (11.8 mm for the published extension, whose arm is
    widest at mid-thickness; 10.4 mm for the parametric one)."""
    lo, hi = lateral_offset - 5.9, lateral_offset + 5.9
    try:
        sec = ext.section(plane_origin=[0.0, 0.0, z_mid], plane_normal=[0.0, 0.0, 1.0])
        ps = [Polygon(d[:, :2]).buffer(0) for d in (sec.discrete if sec is not None else []) if len(d) >= 3]
        band = unary_union(ps).intersection(box(-50.0, border_y - 1.0, 50.0, border_y + 1.0)) if ps else None
        if band is not None and not band.is_empty:
            lo, hi = band.bounds[0], band.bounds[2]
    except (ValueError, IndexError):
        pass
    return {"border_y": float(border_y), "x_lo": float(lo), "x_hi": float(hi)}


def root_support_check(plate_outline: np.ndarray, root: dict) -> dict:
    """Share of the baton root's width that has plate under it just in front of the posterior border.
    Where the border runs obliquely (a displaced segment in a unilateral cleft) part of the root hangs
    past the plate edge: the mold then has an undercut at the root and the junction is weak."""
    by, lo, hi = root["border_y"], root["x_lo"], root["x_hi"]
    P = Polygon(plate_outline).buffer(0)
    w = max(hi - lo, 1e-6)
    per = [P.intersection(box(lo, by + k, hi, by + k + 1.0)).area / w for k in ROOT_STRIPS]
    share = float(np.mean(per))
    status = "pass" if share >= ROOT_SUPPORT_WARN else ("warn" if share >= ROOT_SUPPORT_FAIL else "fail")
    return {"check": "baton root supported", "status": status,
            "value": {"share_pct": round(100 * share, 1), "strips_pct": [round(100 * v) for v in per], "root_width_mm": round(w, 2)},
            "rule": f"share of the baton root's width (x {lo:+.1f} to {hi:+.1f} mm) over the plate, {ROOT_STRIPS[0]}-{ROOT_STRIPS[-1] + 1} mm "
                    f"in front of the posterior border midline; warn under {ROOT_SUPPORT_WARN:.0%}, fail under {ROOT_SUPPORT_FAIL:.0%} "
                    "(project heuristic, not a published limit: an unsupported root leaves an undercut in the mold and a weak "
                    "junction; extend the plate under it)"}


def scan_support_check(plate_outline: np.ndarray, hfb, border_y: float) -> dict:
    """Plate area whose intaglio is extrapolated: a grid cell with no scan vertex within DATA_TOL_MM in plan and
    outside the cleft block-out. The height field fills holes in the scan by nearest neighbour (linear inside the
    scan's hull), so the fit there is invented and may press or gap; rescan, or check the fit on the model."""
    from .plate import DATA_TOL_MM, _cells
    P = Polygon(plate_outline).buffer(0)
    cells = _cells(P, 0.2)
    kind = hfb.support(cells, DATA_TOL_MM)
    fill = kind == "fill"
    hw, depth = ROOT_WINDOW
    win = (np.abs(cells[:, 0]) <= hw) & (cells[:, 1] >= border_y) & (cells[:, 1] <= border_y + depth)
    root_share = float(fill[win].mean()) if win.any() else 0.0
    area = float(fill.sum()) * 0.04
    far = 0.0
    if fill.any() and getattr(hfb, "data_dist", None) is not None:
        i = np.clip(np.rint((cells[fill, 0] - hfb.xs[0]) / hfb.step).astype(int), 0, len(hfb.xs) - 1)
        j = np.clip(np.rint((cells[fill, 1] - hfb.ys[0]) / hfb.step).astype(int), 0, len(hfb.ys) - 1)
        far = float(hfb.data_dist[i, j].max())
    ok = area <= FILL_WARN_MM2 and root_share <= FILL_WARN_ROOT
    return {"check": "plate on scanned surface", "status": "pass" if ok else "warn",
            "value": {"fill_mm2": round(area, 1), "fill_pct": round(100 * float(fill.mean()), 1) if len(fill) else 0.0,
                      "root_window_fill_pct": round(100 * root_share, 1), "farthest_from_scan_mm": round(far, 1)},
            "rule": f"plate area with no scan vertex within {DATA_TOL_MM} mm in plan and outside the cleft block-out (the intaglio "
                    f"there is extrapolated); warn over {FILL_WARN_MM2:.0f} mm2, or over {FILL_WARN_ROOT:.0%} of the plate within "
                    f"{hw} mm of the midline and {depth:.0f} mm in front of the posterior border (project heuristic: rescan, or "
                    "check the fit there on the model)"}


def qc_prototype(mesh: trimesh.Trimesh, path, ep: ExtensionParams, pp: PlateParams,
                 plate_outline: np.ndarray, lm_json: dict, kind: str = "prototype",
                 reference: dict | None = None, wire: dict | None = None, root: dict | None = None,
                 support=None) -> dict:
    """reference: {'params': ExtensionParams dict of the standard design, 'arm_length_mm': float}
    wire: measured wire or groove {'start_mm_after_root', 'end_mm_before_tip'} for a final device.
    root: where the baton crosses the border, from root_span(); adds the 'baton root supported' check.
    support: the blocked height field the plate was built on; adds the 'plate on scanned surface' check."""
    checks = []
    checks.append(_check("mesh watertight", mesh.is_watertight, bool(mesh.is_watertight), "single closed solid for printing"))
    checks.append(_check("single body", mesh.body_count == 1, int(mesh.body_count), "plate and extension fused"))
    checks.append(_check("extension thickness", ep.thickness >= 3.0, round(ep.thickness, 2), ">= 3.0 mm (Tübingen: never below 3 mm)"))
    ref_w = reference["params"]["width_base"] if reference else ep.width_base
    w_min = min(ep.width_base, ep.width_tip)
    checks.append(_check("extension width", w_min >= 0.5 * ref_w, round(w_min / ref_w, 2),
                         f"never narrowed by more than half of the original {ref_w:.1f} mm"))
    if path is not None:
        L = arm_length(mesh, path, ep)
        if reference and reference.get("arm_length_mm"):
            dL = L - reference["arm_length_mm"]
            checks.append(_check("extension length vs standard", abs(dL) <= STANDARD_REVIEW_MM, round(dL, 1),
                                 f"within ±{STANDARD_REVIEW_MM:.0f} mm of the standard extension ({reference['arm_length_mm']:.1f} mm); "
                                 "beyond that, review (project heuristic: no published limit)", level="warn"))
        else:
            checks.append(_check("extension length", True, round(L, 1), "centreline length from root to tip, mm", level="warn"))
        tip = tip_point(mesh, path)
        checks.append(_check("tip below occlusal plane", tip[2] < -2.0, round(float(tip[2]), 1),
                             "tip below the occlusal plane (a geometric proxy: the target relative to the epiglottis is set "
                             "by the clinical team and checked under endoscopy)", level="warn"))
    checks.append(_check("effective section angle", abs(ep.tilt_deg) <= 15.0, round(ep.tilt_deg, 1),
                         "tail within 15 deg of perpendicular to the occlusal plane", level="warn"))
    checks.append(_check("extension centred", abs(ep.lateral_offset) <= 3.0, round(ep.lateral_offset, 2), "lateral offset within 3 mm", level="warn"))
    if kind == "final":
        checks.append(_check("plate thickness", pp.thickness >= 2.5, round(pp.thickness, 2),
                             ">= 2.5 mm final plate (Tübingen: below 2.5-3 mm the acrylic and wire become unstable)"))
        checks.append(_check("plate thickness target", pp.thickness <= 3.0, round(pp.thickness, 2), "aim for 2.5-3 mm", level="warn"))
        checks += graded_checks(pp)
        checks.append(_check("safety wire present", wire is not None, wire is not None,
                             "every plate worn outside direct supervision has a safety wire (Tübingen 2025)"))
        if wire is not None and wire.get("check_span", True):
            # skipped for a wired plate ground at the bedside: its reserve is tracked by lineage_checks
            a, b = wire["start_mm_after_root"], wire["end_mm_before_tip"]
            checks.append(_check("safety wire start", 8.0 <= a <= 12.0, a, "about 1 cm after the extension root", level="warn"))
            checks.append(_check("safety wire end", 1.5 - 1e-6 <= b <= 2.0 + 0.05, b,
                                 "1.5-2 mm before the tip: the reserve for shortening at the bedside"))
    else:
        checks.append(_check("plate thickness", pp.thickness >= 2.0, round(pp.thickness, 2), ">= 2.0 mm prototype"))
        checks += graded_checks(pp, final=False)
    checks.append(landmark_check(lm_json))
    tub_y = 0.5 * (lm_json["tuberosity_left"][1] + lm_json["tuberosity_right"][1])
    reach = float(plate_outline[:, 1].min())
    checks.append(_check("plate reaches tuberosities", reach <= tub_y + 1.0, round(reach - tub_y, 2),
                         "posterior border at or behind the tuberosity line (needed for retention; do not cut it short)"))
    if root is not None:
        checks.append(root_support_check(plate_outline, root))
    if support is not None:
        checks.append(scan_support_check(plate_outline, support, root["border_y"] if root is not None else
                                         float(plate_outline[:, 1].min())))
    if lm_json["cleft_present"]:
        checks.append(_check("cleft blocked out", True, True, "flat floor over the cleft, 2-3 mm above the shelf margins"))
    vol = float(mesh.volume)
    checks.append(_check("volume plausible", 1500 < vol < 8000, round(vol, 0), "1.5-8 cm3 for an infant plate", level="warn"))
    return {"kind": kind, "overall": overall(checks), "checks": checks}


def lineage_checks(entry: dict) -> list[dict]:
    """Checks that depend on a device's history rather than its geometry: what has been ground off a
    wired plate at the bedside, measured against the wire's tip reserve."""
    out = []
    if not entry.get("wired"):
        return out
    reserve = entry.get("reserve_mm")
    used = entry.get("bedside_shortening_mm", 0.0)
    if reserve is not None:
        left = reserve - used
        st = "pass" if left >= 1.0 - 1e-6 else ("warn" if left >= 0.5 - 1e-6 else "fail")
        out.append({"check": "tip reserve", "status": st, "value": round(left, 2),
                    "rule": f"bedside shortening of a wired plate ({used:.1f} mm so far) uses up the {reserve:.1f} mm between "
                            "the wire end and the tip; keep at least 0.5 mm of acrylic over the wire end (project heuristic), "
                            "and plan a new plate when under 1 mm is left"})
    if entry.get("bedside_thinning"):
        out.append({"check": "thickness change on a wired plate", "status": "fail", "value": True,
                    "rule": "once the wire is in, extension thickness can only be changed in the lab (Tübingen 2025)"})
    return out
