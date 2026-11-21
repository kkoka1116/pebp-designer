"""Airway landmarks, the Bioengineering 2022 plate measurement scheme, and a reach plausibility check.

This is experiment 1 of the open-data report ("Open airway CT and endoscopy datasets"). It gives every
design record:

1. `airway`: one landmark vocabulary that every later experiment (CT, MRI, endoscopy) fills in:
   posterior nasal spine, baton root, baton tip, vallecula, epiglottis tip, tongue-base to
   posterior-pharyngeal-wall distance and tongue base-epiglottic angle. Each entry records where its
   value came from. An intraoral scan cannot see the airway, so the anatomical entries start empty.
2. `tpp_metrics`: every device measured as Bioengineering 2022 measures a manufactured plate
   (Aretxabaleta et al., doi:10.3390/bioengineering9120773): main base plane through IP, Q1 and Q2;
   five sections parallel to it with centroids PS1-PS5; effective and sagittal lengths; width,
   thickness and area per section; angles along the centroid skeleton; the narrowest section S0 of
   the cleft extension; effective extension volume.
3. `reach_check`: the standard extension and its 2 mm prototype steps compared on paper with
   published infant norms. It is a plausibility check on geometry only and makes no clinical claim.

Claims are labelled as in the project rules: [published] with a citation, [heuristic] for a choice
this project made, [inference] for reasoning from published numbers.
"""
from __future__ import annotations

import numpy as np
import trimesh

from .meshutil import _section_polygon, arclength
from .record import now

VOCABULARY_VERSION = "airway/0.1"

SOURCES = ("design", "scan_proxy", "ct", "mri", "endoscopy", "manual", "unmeasured")

HU_2026 = "Hu et al. 2026, Front Neurol, doi:10.3389/fneur.2026.1784503"
VORPERIAN_2005 = "Vorperian et al. 2005, J Acoust Soc Am 117:338, doi:10.1121/1.1835958"
PEREZ_2020 = "Perez et al. 2020, Paediatr Anaesth, doi:10.1111/pan.13869"
LEE_2016 = "Lee et al. 2016, JAMA Otolaryngol Head Neck Surg, doi:10.1001/jamaoto.2016.1010"
BIOENG_2022 = "Aretxabaleta et al. 2022, Bioengineering 9:773, doi:10.3390/bioengineering9120773"

# name -> (kind, definition). Points are in the design frame (x right, y anterior, z superior, mm).
LANDMARKS = {
    "pns": ("point",
            "posterior nasal spine: posterior end of the hard palate on the midline. Not visible on an "
            "intraoral scan, and absent as a midline bony point in a cleft palate. The design proxy is the "
            "midline point of the plate's posterior border on the (blocked-out) palatal surface [heuristic]."),
    "baton_root": ("point", "where the baton centreline leaves the plate's posterior border, at plate mid-thickness (per device)"),
    "baton_tip": ("point", "the baton's most inferior point (per device)"),
    "vallecula": ("point", "deepest midline point of the vallecula, between tongue base and epiglottis (imaging only)"),
    "epiglottis_tip": ("point", "free superior tip of the epiglottis on the midline (imaging or endoscopy)"),
    "tb_ppw_distance_mm": ("scalar",
                           "shortest midsagittal distance from the tongue base to the posterior pharyngeal wall "
                           f"(D4 in {HU_2026}) [published definition]"),
    "tb_epiglottic_angle_deg": ("scalar",
                                "angle at the tongue base formed by the stomion-line midpoint, the tongue base and the "
                                f"epiglottic base (A2 in {HU_2026}) [published definition; vertex at the tongue base is "
                                "this project's reading]"),
}

# Published infant numbers used in the reach check. Values in mm.
NORMS = {
    "hard_palate_mm": (27.9, f"hard palate length at birth, normal infants, MRI fit intercept ({VORPERIAN_2005})"),
    "soft_palate_mm": (23.6, f"soft palate length at birth ({VORPERIAN_2005})"),
    "tongue_length_mm": (56.8, f"tongue length, tip to valleculae, at birth ({VORPERIAN_2005})"),
    "hyoid_below_pns_mm": (25.5, f"hyoid level below the posterior nasal spine at birth ({VORPERIAN_2005})"),
    "larynx_below_pns_mm": (29.7, f"larynx level below the posterior nasal spine at birth ({VORPERIAN_2005})"),
    "robin_tongue_higher_mm": (5.0, f"tongue 0.5 cm higher in Robin sequence vs controls, under 4 years ({PEREZ_2020})"),
    "robin_tongue_posterior_mm": (9.0, f"tongue 0.9 cm more posterior in Robin sequence vs controls, under 4 years ({PEREZ_2020})"),
    "plate_el5_difference_mm": (10.0, f"EL5 difference between two manufactured plates for similar patients ({BIOENG_2022})"),
    "plate_sl5_difference_mm": (8.0, f"SL5 difference between the same two plates ({BIOENG_2022})"),
}
DORSUM_PATH_MM = NORMS["tongue_length_mm"][0] - NORMS["hard_palate_mm"][0]   # 28.9 mm


def blank_airway() -> dict:
    return {"vocabulary": VOCABULARY_VERSION, "frame": "design",
            "landmarks": {k: {"kind": kind, "value": None, "source": "unmeasured", "definition": d}
                          for k, (kind, d) in LANDMARKS.items()},
            "derived": {}}


def upgrade_airway(record: dict) -> None:
    a = record.setdefault("airway", blank_airway())
    for k, (kind, d) in LANDMARKS.items():
        a["landmarks"].setdefault(k, {"kind": kind, "value": None, "source": "unmeasured", "definition": d})
    a.setdefault("derived", {})
    record.setdefault("tpp_metrics", {})


def set_landmark(record: dict, name: str, value, source: str, note: str | None = None) -> None:
    """Store one anatomical value. Points must already be in the design frame (register imaging first)."""
    upgrade_airway(record)
    if name not in LANDMARKS:
        raise KeyError(f"unknown airway landmark {name!r}; known: {', '.join(LANDMARKS)}")
    if name in ("baton_root", "baton_tip"):
        raise ValueError(f"{name} is computed from each device; it cannot be set")
    if source not in SOURCES:
        raise ValueError(f"source {source!r} not in {SOURCES}")
    kind = LANDMARKS[name][0]
    if value is None:
        entry = {"value": None, "source": "unmeasured"}
    elif kind == "point":
        v = [float(t) for t in value]
        if len(v) != 3:
            raise ValueError(f"{name} needs x,y,z")
        entry = {"value": [round(t, 3) for t in v], "source": source}
    else:
        entry = {"value": round(float(value), 3), "source": source}
    entry.update({"set": now(), "note": note})
    record["airway"]["landmarks"][name].update(entry)


# ------------------------------------------------------------------ geometry helpers

def _plane(ip, q1, q2):
    n = np.cross(np.asarray(q1) - ip, np.asarray(q2) - ip)
    n /= np.linalg.norm(n)
    return n if n[2] > 0 else -n                    # superior side positive


def _chord(poly, to3d, c2, dir3d, reach: float = 30.0):
    """Length of the chord of `poly` through c2 along a 3D in-plane direction."""
    from shapely.geometry import LineString
    d2 = np.linalg.inv(to3d)[:3, :3] @ np.asarray(dir3d, float)
    d2 = d2[:2] / (np.linalg.norm(d2[:2]) + 1e-12)
    seg = LineString([c2 - d2 * reach, c2 + d2 * reach]).intersection(poly)
    parts = [seg] if seg.geom_type == "LineString" else list(getattr(seg, "geoms", []))
    from shapely.geometry import Point
    hit = [g for g in parts if g.geom_type == "LineString" and g.distance(Point(c2)) < 1e-6]
    return round(float(hit[0].length if hit else sum(g.length for g in parts)), 2)


def _section(mesh, origin, normal, width_dir):
    got = _section_polygon(mesh, np.asarray(origin, float), np.asarray(normal, float))
    if got is None:
        return None
    poly, to3d, _ = got
    c2 = np.array(poly.centroid.coords[0])
    c3 = trimesh.transform_points([[c2[0], c2[1], 0.0]], to3d)[0]
    w = np.asarray(width_dir, float) - np.dot(width_dir, normal) * np.asarray(normal)
    w /= np.linalg.norm(w)
    t = np.cross(normal, w)
    return {"centroid": c3, "area_mm2": round(float(poly.area), 2),
            "width_mm": _chord(poly, to3d, c2, w), "thickness_mm": _chord(poly, to3d, c2, t)}


def _angle(a, b, c) -> float:
    u, v = np.asarray(a) - b, np.asarray(c) - b
    return round(float(np.degrees(np.arccos(np.clip(u @ v / (np.linalg.norm(u) * np.linalg.norm(v)), -1, 1)))), 1)


def _point_on_path_at_depth(path, depth_of, d):
    dep = np.array([depth_of(p) for p in path])
    i = np.where(dep >= d)[0]
    if len(i) == 0 or i[0] == 0:
        return path[-1]
    j = i[0]
    f = (d - dep[j - 1]) / max(dep[j] - dep[j - 1], 1e-9)
    return path[j - 1] + f * (path[j] - path[j - 1])


def device_tip(mesh: trimesh.Trimesh, border_y: float) -> np.ndarray:
    v = mesh.vertices[mesh.vertices[:, 1] < border_y]
    return v[np.argmin(v[:, 2])]


def tpp_metrics(mesh: trimesh.Trimesh, ip, q1, q2, border_y: float, path: np.ndarray, root_s: float,
                n_sections: int = 5) -> dict:
    """One device in the Bioengineering 2022 scheme.

    [published] MBP through IP, Q1, Q2; sections parallel to it along the effective extension with
    centroids PS1-PS5; EL = distance from a centroid to its projection on the MBP; SL = distance from IP
    to that projection; width and thickness from two perpendicular planes through each centroid; angles
    between successive skeleton lines, the first from IP to PS1; S0 = smallest of the sections
    perpendicular to the cleft extension at the points dividing it into five equal segments; V = volume
    of the effective extension.
    [heuristic] The paper does not state the section spacing in its text: here the sections sit at
    i/(n+1) of the tip's depth below the MBP, i = 1..n, so none falls on the rounded tip. IP, Q1, Q2 are
    the scan landmarks (incisive papilla, tuberosities), which lie on the plate's intaglio; the paper
    picks them on the plate by hand. Angles are interior angles in 3D (180 = straight)."""
    ip, q1, q2 = (np.asarray(p, float) for p in (ip, q1, q2))
    n = _plane(ip, q1, q2)
    depth = lambda p: float((ip - np.asarray(p)) @ n)          # mm below the MBP
    proj = lambda p: np.asarray(p) + depth(p) * n
    end = device_tip(mesh, border_y)
    D = depth(end)
    xdir = np.array([1.0, 0.0, 0.0])
    secs = []
    for i in range(1, n_sections + 1):
        d = D * i / (n_sections + 1)
        o = _point_on_path_at_depth(path, depth, d)
        o = o + (depth(o) - d) * n                              # exactly on the plane
        s = _section(mesh, o, n, xdir)
        if s is None:
            secs.append({"name": f"S{i}", "depth_mm": round(d, 2), "missing": True}); continue
        c = s["centroid"]
        secs.append({"name": f"S{i}", "PS": c.round(2).tolist(), "EL_mm": round(depth(c), 2),
                     "SL_mm": round(float(np.linalg.norm(proj(c) - ip)), 2),
                     "width_mm": s["width_mm"], "thickness_mm": s["thickness_mm"], "area_mm2": s["area_mm2"]})
    # angles along IP -> PS1 -> ... -> PS5
    pts = [ip] + [np.array(s["PS"]) for s in secs if "PS" in s]
    angles = {f"alpha{k}": _angle(pts[k - 1], pts[k], pts[k + 1]) for k in range(1, len(pts) - 1)}
    # S0: narrowest perpendicular section of the cleft extension (root to where the centreline meets the MBP)
    s_all = arclength(path)
    # behind the plate's posterior border only: a prototype moved anterior starts under the plate
    behind = [k for k in range(len(path)) if path[k][1] < border_y - 0.5]
    root_s = max(root_s, s_all[behind[0]]) if behind else root_s
    above = [k for k in behind if s_all[k] >= root_s and depth(path[k]) <= 0.0]
    s0 = None
    if len(above) >= 3:
        sa, sb = s_all[above[0]], s_all[above[-1]]
        cands = []
        for k in range(1, 5):
            sv = sa + (sb - sa) * k / 5
            j = int(np.argmin(np.abs(s_all - sv)))
            tng = path[min(j + 1, len(path) - 1)] - path[max(j - 1, 0)]
            tng /= np.linalg.norm(tng)
            sec = _section(mesh, path[j], tng, xdir)
            if sec is not None:
                cands.append({"s_from_root_mm": round(float(sv - root_s), 2), "PS0": sec["centroid"].round(2).tolist(),
                              "width_mm": sec["width_mm"], "thickness_mm": sec["thickness_mm"], "area_mm2": sec["area_mm2"]})
        s0 = min(cands, key=lambda c: c["area_mm2"]) if cands else None
    # effective extension volume: below the MBP and behind the posterior border (Manifold intersection;
    # trimesh's capped slice needs rtree, which this project does not use)
    vol, vol_method = None, None
    try:
        from .meshutil import manifold_boolean
        e1 = np.array([1.0, 0.0, 0.0]) - n[0] * n
        e1 /= np.linalg.norm(e1)
        T = np.eye(4)
        T[:3, 0], T[:3, 1], T[:3, 2] = e1, np.cross(n, e1), n
        T[:3, 3] = ip - n * 100.0                                   # box spans 200 mm below the MBP up to it
        half = trimesh.creation.box(extents=[400.0, 400.0, 200.0], transform=T)
        back = trimesh.creation.box(extents=[400.0, 400.0, 400.0])
        back.apply_translation([0.0, border_y - 200.0, 0.0])
        eff = manifold_boolean("intersection", [mesh, half, back])
        if len(eff.faces):
            vol, vol_method = round(float(abs(eff.volume)), 1), "boolean"
    except ValueError:
        pass                                                    # not a closed volume as loaded: integrate sections
    if vol is None:
        dz, acc = 0.25, 0.0
        for d in np.arange(dz / 2, D, dz):
            o = _point_on_path_at_depth(path, depth, d)
            s = _section(mesh, o + (depth(o) - d) * n, n, xdir)
            acc += (s["area_mm2"] if s else 0.0) * dz
        vol, vol_method = round(float(acc), 1), "section integral, 0.25 mm steps"
    return {"scheme": BIOENG_2022, "mbp": {"IP": ip.round(2).tolist(), "Q1": q1.round(2).tolist(), "Q2": q2.round(2).tolist(),
                                           "normal": n.round(4).tolist()},
            "end": end.round(2).tolist(), "EL_end_mm": round(D, 2), "SL_end_mm": round(float(np.linalg.norm(proj(end) - ip)), 2),
            "sections": secs, "angles_deg": angles, "S0": s0, "effective_volume_mm3": vol, "volume_method": vol_method,
            "section_spacing": f"i/{n_sections + 1} of the tip depth below the MBP [heuristic]"}


# ------------------------------------------------------------------ reach plausibility

def reach_check(record: dict) -> dict:
    """The standard extension and its prototype steps against published infant norms. Plausibility only."""
    lm = record["landmarks"]
    pns = np.array(record["airway"]["landmarks"]["pns"]["value"])
    rows = {}
    for did, m in record["tpp_metrics"].items():
        tip = np.array(m["baton_tip"])
        rows[did] = {"arm_length_mm": m["arm_length_mm"],
                     "tip_posterior_of_pns_mm": round(float(pns[1] - tip[1]), 2),
                     "tip_below_pns_mm": round(float(pns[2] - tip[2]), 2),
                     "pns_to_tip_mm": round(float(np.linalg.norm(pns - tip)), 2)}
    hp = round(float(lm["apex"][1] - pns[1]), 2)
    checks = []

    def add(name, value, ref, ok, label, why):
        margin = round(float(value) - float(ref), 2) if value is not None else None
        checks.append({"check": name, "value": value, "reference": ref, "margin_mm": margin, "within": bool(ok),
                       "label": label, "why": why})

    add("scan palate length (alveolar apex to PNS proxy) vs normal hard palate", hp, NORMS["hard_palate_mm"][0],
        abs(hp / NORMS["hard_palate_mm"][0] - 1) <= 0.25, "inference",
        "within 25% of the at-birth fit; the apex sits in front of ANS and the proxy is the plate border, so only gross scale errors show")
    protos = [k for k in rows if k.startswith("P")]
    for did in protos:
        r = rows[did]
        add(f"{did} arm length vs tongue-dorsum path, PNS level to vallecula", r["arm_length_mm"], round(DORSUM_PATH_MM, 1),
            r["arm_length_mm"] < DORSUM_PATH_MM, "inference",
            "tongue length minus hard palate, 56.8 - 27.9 mm, is a rough curvilinear path to the vallecula; a baton meant to stop "
            "above the epiglottis should be shorter")
        add(f"{did} straight PNS proxy to tip vs tongue-dorsum path", r["pns_to_tip_mm"], round(DORSUM_PATH_MM, 1),
            r["pns_to_tip_mm"] < DORSUM_PATH_MM, "inference",
            "a straight chord is shorter than any curved path between the same levels, so a chord longer than the "
            "dorsum path is the stronger sign of over-reach")
        add(f"{did} tip depth below PNS proxy vs hyoid level", r["tip_below_pns_mm"], NORMS["hyoid_below_pns_mm"][0],
            r["tip_below_pns_mm"] < NORMS["hyoid_below_pns_mm"][0], "inference",
            "the vallecula and epiglottis tip lie above the hyoid, so a tip below hyoid level would be implausible")
    if len(protos) >= 2:
        ys = [rows[k]["tip_posterior_of_pns_mm"] for k in protos]
        spread = round(max(ys) - min(ys), 2)
        sl5 = [record["tpp_metrics"][k]["sections"][-1].get("SL_mm") for k in protos]
        sl_spread = round(max(sl5) - min(sl5), 2) if all(v is not None for v in sl5) else None
        add("prototype set: tip spread front to back vs Robin posterior tongue offset", spread,
            NORMS["robin_tongue_posterior_mm"][0], spread >= NORMS["robin_tongue_posterior_mm"][0], "inference",
            "how much of the published Robin vs control tongue displacement the three-prototype set spans")
        add("prototype set: SL5 spread vs SL5 difference between two real plates", sl_spread,
            NORMS["plate_sl5_difference_mm"][0], sl_spread is not None and sl_spread >= NORMS["plate_sl5_difference_mm"][0],
            "inference", "how much of the plate-to-plate variation seen in practice the prototype set spans")
    return {"purpose": "plausibility check of geometry against population norms; no clinical claim", "norms": NORMS,
            "derived_norms": {"tongue_dorsum_path_mm": (round(DORSUM_PATH_MM, 1), "56.8 - 27.9 mm [inference]")},
            "per_device": rows, "checks": checks,
            "caveats": [
                "Vorperian values are fitted means for normal infants imaged supine by MRI; Robin infants differ in the "
                "direction that matters (tongue higher and more posterior, Perez 2020), so the normal path is an upper "
                "bound on retroglossal space, not a target [inference].",
                "Perez 2020 offsets are for children under 4 years, not newborns, and were read from the abstract record.",
                f"{LEE_2016} reports its Robin vs control differences in SD units after size normalisation, so no mm offset is taken from it.",
                f"{HU_2026} gives the D4 and A2 definitions used in the vocabulary; its tables report odds ratios, not group means.",
                "No tabulated infant PNS to epiglottis-tip distance was found; that is the number this check most lacks.",
                "The PNS is a proxy (plate border on the midline); vertical in the design frame (occlusal plane) need not "
                "match vertical in Vorperian's head frame.",
            ]}


def _derived(record: dict) -> dict:
    L = record["airway"]["landmarks"]
    pt = lambda k: np.array(L[k]["value"]) if L[k]["value"] is not None and L[k]["kind"] == "point" else None
    out = {}
    for k in ("vallecula", "epiglottis_tip"):
        p = pt(k)
        if p is None:
            continue
        if pt("pns") is not None:
            out[f"pns_to_{k}_mm"] = round(float(np.linalg.norm(p - pt("pns"))), 2)
        for did, m in record["tpp_metrics"].items():
            out.setdefault("per_device", {}).setdefault(did, {})[f"tip_to_{k}_mm"] = round(float(np.linalg.norm(p - np.array(m["baton_tip"]))), 2)
    return out


def update(record: dict, case_dir, devices: list[str] | None = None) -> dict:
    """Fill the airway block, measure every device in the Bioengineering 2022 scheme, run the reach check."""
    from pathlib import Path
    from .extension import ExtensionParams, extension_path
    from .qc import arm_length
    from .record import device, device_ids
    upgrade_airway(record)
    case_dir = Path(case_dir)
    lm = record["landmarks"]
    by, zm = record["attachment"]["posterior_border_y"], record["attachment"]["z_mid"]
    z_top = zm + record["plate"]["params"]["thickness"] / 2.0
    A = record["airway"]["landmarks"]
    if A["pns"]["source"] in ("unmeasured", "scan_proxy"):
        A["pns"].update({"value": [0.0, round(by, 3), round(z_top, 3)], "source": "scan_proxy",
                         "note": "midline of the plate's posterior border on the palatal surface [heuristic]"})
    roots, tips = {}, {}
    for did in devices or device_ids(record):
        d = device(record, did)
        f = case_dir / d["file"]
        if not f.exists():
            continue
        mesh = trimesh.load(f, force="mesh")
        ep = ExtensionParams(**d["params"])
        path = extension_path(ep, by, zm)
        m = tpp_metrics(mesh, lm["incisive_papilla"], lm["tuberosity_right"], lm["tuberosity_left"], by, path, ep.embed_depth)
        root = np.array([ep.lateral_offset, by, zm])
        m["baton_root"] = root.round(2).tolist()
        m["baton_tip"] = m["end"]
        m["arm_length_mm"] = round(arm_length(mesh, path, ep), 2)
        record["tpp_metrics"][did] = m
        roots[did], tips[did] = m["baton_root"], m["baton_tip"]
    A["baton_root"].update({"source": "design", "value": None, "per_device": roots})
    A["baton_tip"].update({"source": "design", "value": None, "per_device": tips})
    record["airway"]["derived"] = _derived(record)
    record["reach_check"] = reach_check(record)
    return record


def report_md(record: dict) -> str:
    """A readable summary of the airway block, the plate metrics and the reach check."""
    L = record["airway"]["landmarks"]
    out = ["# Airway landmarks and plate metrics", "",
           "Plausibility check of geometry only. No clinical claim.", "",
           "## Landmarks (design frame, mm)", "", "| landmark | value | source |", "|---|---|---|"]
    for k, e in L.items():
        v = e.get("per_device") if e.get("per_device") else e["value"]
        if isinstance(v, dict):
            v = "; ".join(f"{d} {p}" for d, p in v.items())
        out.append(f"| {k} | {v if v is not None else '—'} | {e['source']} |")
    out += ["", f"## Bioengineering 2022 scheme ({BIOENG_2022})", "",
            "| device | EL1–EL5 / EL end | SL1–SL5 / SL end | width S1–S5 | thickness S1–S5 | α1–α4 (°) | S0 w×t, area | V eff (mm³) | arm |",
            "|---|---|---|---|---|---|---|---|---|"]
    for did, m in record["tpp_metrics"].items():
        S = [s for s in m["sections"] if "PS" in s]
        j = lambda key: " ".join(f"{s[key]:.1f}" for s in S)
        s0 = m["S0"]
        out.append(f"| {did} | {j('EL_mm')} / {m['EL_end_mm']:.1f} | {j('SL_mm')} / {m['SL_end_mm']:.1f} | {j('width_mm')} | "
                   f"{j('thickness_mm')} | {' '.join(str(a) for a in m['angles_deg'].values())} | "
                   + (f"{s0['width_mm']}×{s0['thickness_mm']}, {s0['area_mm2']}" if s0 else "—")
                   + f" | {m['effective_volume_mm3']} | {m['arm_length_mm']} |")
    rc = record["reach_check"]
    out += ["", "## Reach check against infant norms", "", "| check | value | reference | value − reference | within | label |", "|---|---|---|---|---|---|"]
    for c in rc["checks"]:
        out.append(f"| {c['check']} | {c['value']} | {c['reference']} | {c['margin_mm']:+.2f} | {'yes' if c['within'] else 'no'} | {c['label']} |")
    out += ["", "Norms used:", ""] + [f"- {k}: {v} mm, {src} [published]" for k, (v, src) in rc["norms"].items()]
    out += ["", "Caveats:", ""] + [f"- {c}" for c in rc["caveats"]]
    return "\n".join(out) + "\n"
