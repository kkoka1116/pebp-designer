"""PolyJet multi-material export: split a design into material regions and write a multi-body
3MF (plus per-body STL), a manifest, and print notes for GrabCAD Print.

Regions (parametric extension):
  plate_core            rigid, transparent            the palatal plate body
  plate_rim_skin        flexible                      outer band of the plate (vestibular rim / lip corners)
  plate_posterior_skin  flexible                      band along the posterior border (a named ulcer site)
  ext_core              rigid, tough, opaque          load-bearing core of the velar extension
  ext_skin              flexible                      1 mm skin around the extension behind the plate border
  ext_tip               flexible (softer in graded)   last ~7 mm around the tip
  markers               opaque                        prototype-number dimples, filled for endoscopic visibility

Profiles:
  prototype        biocompatible set for endoscopy fitting trials (mucosal contact < 24 h)
  graded-research  Vero/Agilus digital materials for bench work only (NOT for patient contact)
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import trimesh
from shapely.geometry import Polygon

from .extension import ExtensionParams, build_extension, extension_path, make_extension, marker_centres
from .meshutil import sweep_tube, boolean_union, boolean_difference, manifold_boolean, extrude_clean
from .plate import PlateParams, design_base_plate, tissue_clip_solid

MIN_WALL_MM = 1.0          # Stratasys DfAM guide: 0.6 mm general, >= 1 mm load-bearing

PROFILES = {
    "prototype": {
        "intended_use": "Endoscopy fitting prototypes and bench/teaching models. Biocompatible PolyJet materials are rated "
                        "for limited (< 24 h) mucosal-membrane contact; the months-long final appliance stays conventional PMMA.",
        "printers": ["Stratasys J5 MediJet", "Stratasys J750/J850 Digital Anatomy Printer"],
        "support": "WSS150 water-soluble (J5 MediJet) or SUP706B; avoid high-pressure water-jet on 1 mm skins",
        "finish": "glossy where possible on mucosal-contact faces; matte acceptable",
        "sterilization": "steam or gamma for all bodies (MED625FLX and MED DABS+ are not EtO-approved)",
        "materials": {
            "plate_core":           {"material": "MED610", "role": "rigid transparent core; shows mucosal blanching through the plate", "shore": "D 83-86", "color": "clear"},
            "plate_rim_skin":       {"material": "MED625FLX", "role": "soft vestibular rim", "shore": "A 73-77", "color": "clear"},
            "plate_posterior_skin": {"material": "MED625FLX", "role": "soft posterior border (ulcer site)", "shore": "A 73-77", "color": "clear"},
            "ext_core":             {"material": "MED DABS+", "role": "tough opaque core (takes the safety-wire role in a printed prototype)", "shore": "D 85-87", "color": "ivory",
                                     "fallback": "MED610"},
            "ext_skin":             {"material": "MED625FLX", "role": "soft contact skin on the tongue base", "shore": "A 73-77", "color": "clear"},
            "ext_tip":              {"material": "MED625FLX", "role": "soft tip cushion above the epiglottis", "shore": "A 73-77", "color": "clear"},
            "markers":              {"material": "MED620", "role": "opaque A2-shade number markers for endoscopic identification", "shore": "D 83-86", "color": "A2 tooth shade",
                                     "fallback": "MED615RGD"},
        },
    },
    "graded-research": {
        "intended_use": "Bench research only (pressure mapping, phantoms, design iteration). Vero/Agilus digital materials are NOT biocompatible.",
        "printers": ["Stratasys J750/J850", "Digital Anatomy Printer"],
        "support": "SUP706B",
        "finish": "glossy",
        "sterilization": "none (not for patient contact)",
        "materials": {
            "plate_core":           {"material": "VeroClear", "role": "rigid transparent core", "shore": "D 83-86", "color": "clear"},
            "plate_rim_skin":       {"material": "DM VeroClear/Agilus30 Shore A 85", "role": "semi-soft rim", "shore": "A 85", "color": "clear"},
            "plate_posterior_skin": {"material": "DM VeroClear/Agilus30 Shore A 85", "role": "semi-soft posterior border", "shore": "A 85", "color": "clear"},
            "ext_core":             {"material": "Digital ABS Plus (RGD515/RGD535)", "role": "tough core", "shore": "D 85-87", "color": "ivory"},
            "ext_skin":             {"material": "DM VeroClear/Agilus30 Shore A 70", "role": "graded contact skin", "shore": "A 70", "color": "clear"},
            "ext_tip":              {"material": "DM VeroClear/Agilus30 Shore A 50", "role": "soft tip", "shore": "A 50", "color": "clear"},
            "markers":              {"material": "VeroMagentaV", "role": "high-contrast markers", "shore": "D 83-86", "color": "magenta"},
        },
    },
}

COLORS = {"plate_core": "#e0b84a", "plate_rim_skin": "#f2d98b", "plate_posterior_skin": "#f5c45e",
          "ext_core": "#2f6fed", "ext_skin": "#7fb0ff", "ext_tip": "#ff9f43", "markers": "#d33"}


@dataclass
class PolyJetParams:
    skin_mm: float = 1.0          # extension skin thickness
    tip_mm: float = 7.0           # radius of the tip cushion region
    rim_mm: float = 2.0           # width of the plate rim band
    posterior_mm: float = 3.0     # depth of the posterior border band
    marker_r: float = 0.8


def _halfspace(y_cut: float, posterior: bool, size: float = 200.0) -> trimesh.Trimesh:
    """A big box covering y < y_cut (posterior=True) or y >= y_cut."""
    b = trimesh.creation.box(extents=[size, size, size])
    b.apply_translation([0.0, y_cut - size / 2.0 if posterior else y_cut + size / 2.0, 0.0])
    return b


def _prism(poly_xy: np.ndarray, z0: float = -60.0, z1: float = 60.0) -> trimesh.Trimesh:
    p = Polygon(poly_xy).buffer(0)
    if p.geom_type == "MultiPolygon":
        p = max(p.geoms, key=lambda g: g.area)
    m = extrude_clean(p, z1 - z0)
    m.apply_translation([0, 0, z0])
    return m


def _inter(a, b):
    return manifold_boolean("intersection", [a, b])


def _diff(a, b):
    return manifold_boolean("difference", [a, b])


def _shrink(mesh: trimesh.Trimesh, d: float) -> trimesh.Trimesh:
    """Inward vertex-normal offset (for the real-STL extension, where no path is known)."""
    m = mesh.copy()
    m.vertices = m.vertices - d * m.vertex_normals
    m.process(validate=True); m.fix_normals()
    return m


def split_regions(case_dir: Path, pp_params: PolyJetParams, proto_number: int = 2, device_id: str | None = None):
    """Rebuild a device from the design record and split it into bodies. Prototypes P1-P3 carry their
    number as dots; an adjusted design (A<n>) has none."""
    from .record import load, device
    rec = load(case_dir / "design.json")
    proto = device(rec, device_id or f"P{proto_number}")
    n_dots = int(proto["id"][1:]) if proto["kind"] == "prototype" else 0
    ep = ExtensionParams(**proto["params"])
    border_y = rec["attachment"]["posterior_border_y"]; z_mid = rec["attachment"]["z_mid"]
    plate = trimesh.load(case_dir / "base_plate.stl", force="mesh")
    outline = np.load(case_dir / "plate_outline.npy")
    mode = rec.get("extension_mode", "parametric")
    s = pp_params.skin_mm
    notes = []

    if mode == "parametric":
        ext_full, path = build_extension(ep, border_y, z_mid)
        core_params = ExtensionParams(**{**proto["params"], "width_base": ep.width_base - 2 * s, "width_tip": ep.width_tip - 2 * s,
                                         "thickness": ep.thickness - 2 * s, "tip_round_mm": max(ep.tip_round_mm - s, 1.0)})
        if core_params.thickness < MIN_WALL_MM:
            raise ValueError(f"extension core would be {core_params.thickness:.2f} mm thick; reduce skin_mm")
        ext_core_full, _ = build_extension(core_params, border_y, z_mid)
        tip = path[-1]
    else:
        ext_full, path, info = make_extension(ep, border_y, z_mid, mode)
        ext_core_full = _shrink(ext_full, s)
        if not ext_core_full.is_watertight or ext_core_full.volume <= 0 or ext_core_full.volume > ext_full.volume:
            raise ValueError("inward offset of the real extension failed; use the parametric extension for a multi-material split")
        tip = np.array(info["tip"])
        notes.append("published Tübingen extension" + (f" deformed by {info['deformation']}" if info.get("deformation") else "")
                     + f"; skin made by a {s} mm inward vertex offset")
    markers_src = []
    if path is not None:
        for c in marker_centres(ext_full, path, n_dots, ep, border_y):
            sp = trimesh.creation.icosphere(subdivisions=2, radius=pp_params.marker_r); sp.apply_translation(c); markers_src.append(sp)
    elif n_dots:
        notes.append("custom extension STL: no centreline, so no number markers")

    post = _halfspace(border_y, posterior=True)
    ext_post = _inter(ext_full, post)
    core_post = _inter(ext_core_full, post)
    tip_ball = trimesh.creation.icosphere(subdivisions=3, radius=pp_params.tip_mm); tip_ball.apply_translation(tip)
    ext_skin_all = _diff(ext_post, core_post)
    ext_tip = _inter(ext_skin_all, tip_ball)
    ext_skin = _diff(ext_skin_all, tip_ball)
    ext_core = core_post
    markers = None
    if markers_src:
        mk = markers_src[0]
        for sp in markers_src[1:]:
            mk = boolean_union(mk, sp)
        markers = _inter(mk, ext_post)
        ext_skin = _diff(ext_skin, mk); ext_core = _diff(ext_core, mk); ext_tip = _diff(ext_tip, mk)

    # plate side: the union minus the posterior part of the extension, split into core / rim / posterior band
    union = boolean_union(plate, ext_full)
    if mode != "parametric":
        from .landmarks import find_landmarks  # noqa  (clip uses the recorded floor through the plate outline)
    plate_full = _diff(union, ext_post)
    outer = Polygon(outline).buffer(0)
    rim_poly = outer.difference(outer.buffer(-pp_params.rim_mm))
    rim_prism = extrude_clean(rim_poly, 120.0); rim_prism.apply_translation([0, 0, -60])
    plate_rim = _inter(plate_full, rim_prism)
    rest = _diff(plate_full, rim_prism)
    post_band = _inter(rest, _halfspace(border_y + pp_params.posterior_mm, posterior=True))
    plate_core = _diff(rest, _halfspace(border_y + pp_params.posterior_mm, posterior=True))

    bodies = {"plate_core": plate_core, "plate_rim_skin": plate_rim, "plate_posterior_skin": post_band,
              "ext_core": ext_core, "ext_skin": ext_skin, "ext_tip": ext_tip}
    if markers is not None and markers.volume > 0.05:
        bodies["markers"] = markers
    bodies = {k: v for k, v in bodies.items() if v is not None and len(v.faces) > 0 and v.volume > 0.05}
    return bodies, union, rec, proto, notes


def validate(bodies: dict, union: trimesh.Trimesh) -> dict:
    report = {"bodies": {}, "pairwise_overlap_mm3": {}, "volume_sum_mm3": 0.0, "union_volume_mm3": round(float(union.volume), 1)}
    names = list(bodies)
    for n in names:
        b = bodies[n]
        report["bodies"][n] = {"watertight": bool(b.is_watertight), "volume_mm3": round(float(b.volume), 1), "faces": int(len(b.faces)),
                               "body_count": int(b.body_count)}
        report["volume_sum_mm3"] += float(b.volume)
    report["volume_sum_mm3"] = round(report["volume_sum_mm3"], 1)
    for i, a in enumerate(names):
        for bname in names[i + 1:]:
            try:
                ov = manifold_boolean("intersection", [bodies[a], bodies[bname]]).volume
            except Exception:
                ov = float("nan")
            if ov > 0.01:
                report["pairwise_overlap_mm3"][f"{a}~{bname}"] = round(float(ov), 3)
    report["volume_mismatch_mm3"] = round(report["volume_sum_mm3"] - report["union_volume_mm3"], 1)
    report["ok"] = all(v["watertight"] for v in report["bodies"].values()) and not report["pairwise_overlap_mm3"] \
        and abs(report["volume_mismatch_mm3"]) < 0.02 * report["union_volume_mm3"]
    return report


def export(case_dir: Path, profile: str = "prototype", params: PolyJetParams | None = None, proto_number: int = 2,
           orientation: str = "intaglio-up", device_id: str | None = None) -> Path:
    params = params or PolyJetParams()
    bodies, union, rec, proto, notes = split_regions(case_dir, params, proto_number, device_id)
    out = case_dir / "polyjet" / (profile if device_id is None else f"{profile}_{proto['id']}")
    out.mkdir(parents=True, exist_ok=True)
    # print orientation: 'intaglio-up' keeps the design frame (plate intaglio glossy, extension in support);
    # 'extension-up' flips 180 deg about x so the extension prints upward (glossy) and the vault is in support.
    T = np.eye(4)
    if orientation == "extension-up":
        T = trimesh.transformations.rotation_matrix(np.pi, [1, 0, 0])
    scene = trimesh.Scene()
    mats = PROFILES[profile]["materials"]
    manifest = {"schema": "pebp-polyjet/0.1", "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "case": str(case_dir), "device": proto["id"], "prototype": proto_number if proto["kind"] == "prototype" else None, "extension_mode": rec.get("extension_mode", "parametric"),
                "profile": profile, "profile_info": {k: v for k, v in PROFILES[profile].items() if k != "materials"},
                "orientation": orientation, "params": asdict(params), "bodies": {}, "notes": notes}
    for name, mesh in bodies.items():
        m = mesh.copy(); m.apply_transform(T)
        m.export(out / f"{name}.stl")
        scene.add_geometry(m, node_name=name, geom_name=name)
        manifest["bodies"][name] = {**mats.get(name, {"material": "UNASSIGNED"}), "file": f"{name}.stl",
                                    "volume_mm3": round(float(m.volume), 1), "color_hex": COLORS.get(name, "#999999")}
    (out / "assembly.3mf").write_bytes(scene.export(file_type="3mf"))
    manifest["validation"] = validate(bodies, union)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    (out / "PRINT_NOTES.md").write_text(print_notes(manifest))
    return out


def print_notes(man: dict) -> str:
    p = man["profile_info"]
    rows = "\n".join(f"| {n} | {b['material']} | {b.get('shore','')} | {b.get('color','')} | {b['volume_mm3']} | {b['role']}{' (fallback: ' + b['fallback'] + ')' if b.get('fallback') else ''} |"
                     for n, b in man["bodies"].items())
    v = man["validation"]
    return f"""# PolyJet print notes — profile `{man['profile']}`

Generated {man['created']} from `{man['case']}`, device {man['device']}, extension mode `{man['extension_mode']}`.

**Intended use.** {p['intended_use']}

## Load into GrabCAD Print
1. Insert `assembly.3mf` (all bodies, named) or the individual STLs as one assembly; keep them in place, do not re-centre bodies separately.
2. Assign the material per body from the table below. Where a material is not in the printer's cabinet, use the listed fallback.
3. Printers: {', '.join(p['printers'])}. Support: {p['support']}. Finish: {p['finish']}.
4. Orientation exported: `{man['orientation']}`. `intaglio-up` prints the plate intaglio glossy (the main mucosal surface) with the extension in support; `extension-up` does the opposite.

| body | material | Shore | colour | volume mm³ | role |
|---|---|---|---|---|---|
{rows}

## Design rules applied
- Skin thickness {man['params']['skin_mm']} mm and extension core ≥ {MIN_WALL_MM} mm (Stratasys DfAM: 0.6 mm minimum wall, ≥ 1 mm load-bearing).
- Rim band {man['params']['rim_mm']} mm, posterior band {man['params']['posterior_mm']} mm, tip cushion radius {man['params']['tip_mm']} mm.
- Bodies share faces exactly (boolean split of one solid), so GrabCAD Print sees a single multi-material part.

## Validation
- all bodies watertight: {all(b['watertight'] for b in v['bodies'].values())}; pairwise overlaps: {v['pairwise_overlap_mm3'] or 'none'}; volume sum {v['volume_sum_mm3']} vs source {v['union_volume_mm3']} mm³ (mismatch {v['volume_mismatch_mm3']} mm³); overall ok = {v['ok']}

## Post-processing and use
- Remove support per material instructions; rinse; inspect the 1 mm skins for tears before use.
- Sterilization: {p['sterilization']}.
- Biocompatibility ratings for every PolyJet medical material are limited (< 24 h) for mucosal-membrane contact (Stratasys biocompatible PolyJet spec sheet, 2023). These prints are for fitting trials, teaching and bench work; the worn appliance remains conventional PMMA with a steel safety wire per the Tübingen protocol.
- Markers (prototype numbers) are opaque bodies so they read under the endoscope; clear MED610/MED625FLX bodies have poor endoscopic contrast on their own.
{chr(10).join('- ' + n for n in man['notes'])}
"""
