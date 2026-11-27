"""Direct-print route: print the final plate itself instead of casting it in printed tooling.

Why the safety wire decides everything. The 2025 Tübingen protocol "strongly discourage[s]" any plate
without a safety wire (doi 10.3390/bioengineering12101063). In fracture tests of the full plate shape,
printed splint resins without a wire mostly broke with the spur fully detached (type D), and adding the wire
by hand to a printed groove was "non-reproducible", with bubbles and uneven curing (Aretxabaleta 2021,
doi 10.3390/ma14020344). So every route here keeps the cast plate's wire: same stock, same place, same span
(mold.safety_wire). What changes is how the wire gets inside a printed part.

Routes (ROUTES holds the trade-offs, for the CLI and for a clinician UI):

  pause          one print in one long-term resin. The part is printed lying on its side, so the baton's
                 plane is a layer plane; the printer pauses at the layer that closes a wire-shaped pocket,
                 the pre-bent wire is dropped in, and printing continues over it. Published for a straight
                 wire in a flat specimen (Zhang 2025, doi 10.1186/s40510-025-00559-1); never for a TPP.
  lid            two printed parts in one resin, for printers that cannot pause. The baton has a slot open
                 to one side; the wire goes to the bottom of the slot and a printed key (the lid) fills the
                 rest. The lid is bonded with the same resin before the final cure. This is Tübingen's
                 groove-and-fill with most of the liquid fill replaced by a printed part.
  multimaterial  one print on a colour multi-material jetting printer: the baton and the base as separate
                 colour bodies (Tübingen colour-codes them), with the same pause pocket for the wire.

The software checks geometry only. Whether a printed resin is acceptable for weeks of 24-hour wear on an
infant's mucosa, and whether the wire-resin bond holds, are bench and regulatory questions (docs/DIRECT_PRINT.md).

Frames: everything is built in the design frame, then exported in print orientation with the build
direction as +z and the part's lowest point at z = 0. Supports and rafts are added by the printer software.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import trimesh
from shapely.geometry import Polygon

from . import record as rec_io
from .context import rebuild, chosen_extension
from .extension import extension_path, make_extension
from .meshutil import boolean_union, manifold_boolean, path_frames, _section_polygon
from .mold import baton_plane, safety_wire
from .plate import tissue_clip_solid
from .qc import qc_prototype, root_span

# ---------------------------------------------------------------------------------------------------------
# Evidence tags: [PUB] peer-reviewed, [FDA] 510(k) record, [MFR] manufacturer document, [HEUR] project
# heuristic, [INF] inference by the software's authors. The numbers are copied from the sources named.
RESINS = {
    "dental-lt-clear-v2": {
        "name": "Formlabs Dental LT Clear V2", "process": "SLA (Form 3B/4B)", "colours": ["clear"],
        "contact": "long-term (>30 d) mucosal [FDA K222061]", "indication": "splints, night guards, mouth guards [FDA K222061]",
        "flexural_mpa": "84 [FDA K222061]", "min_wall_mm": None,
        "tpp_evidence": "V1 tested in full TPP shape: 174-190 N without wire, 201 N with (Aretxabaleta 2021) [PUB]; V2 not tested",
        "pause_at_layer": "not offered by PreForm (Formlabs forum request, 2025) [INF from MFR forum]",
        "process_notes": "wash 15 + 5 min IPA, never over 20 min; cure 60 min at 60 °C [MFR]"},
    "dental-lt-comfort": {
        "name": "Formlabs Dental LT Comfort", "process": "SLA (Form 3B/4B)", "colours": ["clear"],
        "contact": "long-term (>30 d) mucosal [FDA K232087]", "indication": "splints, night guards, repositioners [FDA K232087]",
        "flexural_mpa": None, "min_wall_mm": 1.0,
        "tpp_evidence": "none in TPP shape; flexible, so the baton would not meet Tübingen's 'no flexibility' rule [INF]",
        "pause_at_layer": "not offered by PreForm [INF from MFR forum]",
        "process_notes": "walls 1 mm min, occlusal 2 mm min; wash 10 min; cure 20 min at 60 °C [MFR manufacturing guide]"},
    "keysplint-hard": {
        "name": "KeyPrint KeySplint Hard", "process": "DLP, validated printers only", "colours": ["clear"],
        "contact": "mucosal, splint indication [FDA K203000]", "indication": "mouthguards, nightguards, splints, retainers [FDA K203000]",
        "flexural_mpa": "100-110 (quoted in K222061) [FDA]", "min_wall_mm": None,
        "tpp_evidence": "none in TPP shape", "pause_at_layer": "depends on the validated printer [INF]",
        "process_notes": "Keystone's predetermined settings per validated printer and cure unit, 385-405 nm [MFR IFU]"},
    "v-print-splint": {
        "name": "VOCO V-Print splint", "process": "DLP", "colours": ["clear"],
        "contact": "EU MDR IIa; Tübingen prints its cleft plates in it, worn 24 h for months [PUB doi 10.3390/jfb15100301]",
        "indication": "splints [FDA K201091]", "flexural_mpa": None, "min_wall_mm": None,
        "tpp_evidence": "TPP shape: low load without wire, type C/D; with wire type B (Aretxabaleta 2021) [PUB]",
        "pause_at_layer": "depends on the printer [INF]", "process_notes": "2 x 3/2 min ultrasonic IPA; Otoflash 2 x 2000 flashes [PUB]"},
    "truedent": {
        "name": "Stratasys TrueDent (multi-colour)", "process": "PolyJet, J5 DentaJet only", "colours": ["CMY + white + clear blends"],
        "contact": "permanent contact, denture indication [FDA K220771]", "indication": "dentures, denture bases, teeth [FDA K220771]",
        "flexural_mpa": "meets ISO 20795-1 (value not public) [MFR FAQ]", "min_wall_mm": None,
        "tpp_evidence": "none", "pause_at_layer": "not documented; jetting builds face-up, which suits an insert [INF]",
        "process_notes": "Stratasys-trained post-processing [MFR FAQ]"},
}

ROUTES = {
    "pause": {
        "title": "One print, wire dropped in at a pause",
        "parts": ["printed_plate.stl (one body with a closed wire pocket)", "safety_wire.stl"],
        "how": "Print lying on its side. Pause at the layer the manifest names, drop the pre-bent wire into the pocket, resume.",
        "pros": ["No mold, no PMMA, no pressure pot, no hand layering [INF]",
                 "The wire is fully enclosed in one continuous body; no seam, no pin holes to fill [INF]",
                 "Lying on its side puts the baton's bending stress along the layers, not across them [INF]",
                 "The same file can be reprinted for a spare, as Tübingen does for cleft plates [PUB doi 10.3390/jfb15100301]"],
        "cons": ["Needs a printer whose software can pause at a chosen layer; PreForm (Formlabs) cannot [INF from MFR forum]",
                 "Uncured resin can be trapped in the clearance around the wire; post-cure must reach it [INF]",
                 "Printing on its side puts supports on one side face of the plate and the baton [INF]",
                 "Wire-resin bond: an untreated steel wire bonded weakly in a printed resin (5.7 MPa); "
                 "sandblasting, acid etch and silane raised it to 28.8 MPa (Zhang 2025) [PUB]",
                 "Never tested in a TPP shape [INF]"],
        "evidence": "print-pause wire embedding in a flat specimen: Zhang 2025, doi 10.1186/s40510-025-00559-1 [PUB]"},
    "lid": {
        "title": "Two printed parts: plate with a wire slot, and a lid that closes it",
        "parts": ["printed_plate.stl (slot open to one side)", "lid.stl", "safety_wire.stl"],
        "how": "Print both, wash, do not post-cure. Seat the wire at the bottom of the slot, coat the lid with the same resin, "
               "press it in, spot-cure, then post-cure the assembly.",
        "pros": ["Works on any validated printer, including ones that cannot pause [INF]",
                 "The lid takes most of the fill volume, so far less liquid resin than Tübingen's syringe fill, "
                 "which was the source of the bubbles [INF from PUB doi 10.3390/ma14020344]"],
        "cons": ["A bonded seam runs along one side of the baton: a crack starter and a place for biofilm [INF]",
                 "The lid is as thin as the wire (about 0.8 mm), under the 1 mm minimum wall some resin makers give [MFR]",
                 "Tübingen found hand-adding a wire to printed parts non-reproducible (Aretxabaleta 2021) [PUB]",
                 "Never tested in a TPP shape [INF]"],
        "evidence": "groove + wire + resin fill in printed TPPs: Aretxabaleta 2021, doi 10.3390/ma14020344 [PUB]"},
    "multimaterial": {
        "title": "One multi-material print, colour-coded, wire dropped in at a pause",
        "parts": ["plate_base.stl and plate_baton.stl as colour bodies of one print (assembly.3mf)", "safety_wire.stl"],
        "how": "Load assembly.3mf, assign the colours, pause at the named layer, drop the wire in, resume.",
        "pros": ["Restores Tübingen's colour code (blue baton for endoscopy, base colour by plate number) [PUB doi 10.3390/bioengineering12101063]",
                 "Most long-term printable resins are clear only, which makes the spur hard to see on endoscopy [PUB]"],
        "cons": ["The only long-term multi-colour jetting resin found (TrueDent) is cleared for dentures and runs on one printer [FDA K220771]",
                 "Expensive printer, rarely in hospital labs [INF]",
                 "Pausing a jetting printer for an insert is not documented [INF]"],
        "evidence": "TrueDent 510(k) K220771 [FDA]; no TPP data"},
}

NOT_OFFERED = {
    "wireless": "A printed plate without a wire. Tübingen strongly discourages it; printed resins without a wire mostly "
                "broke with the spur detached (type D) [PUB doi 10.3390/ma14020344, 10.3390/bioengineering12101063].",
    "skin-instead-of-wire": "A flexible skin as the only fragment catcher. Available as a bench specimen (--skin --no-wire), never "
                            "registered: a skin can tear from a sharp core fragment, which a steel wire cannot, and it hides a cracked core [INF].",
    "soft-rim": "A soft printed rim bonded to a rigid plate. Parked: delamination and aspiration risk [HEUR, see GRADED_PLATE.md].",
    "jointed-baton": "Baton printed separately and joined at the root. The root carries the largest bending moment; "
                     "a joint there is the weakest place to put one, and the wire does not reach the plate [INF].",
}


# Flexible skins that a multi-material printer can lay over a rigid core in one print, or that can be
# overmolded onto a printed core. None is rated for long-term mucosal contact, so a skinned plate is a bench
# specimen, never a registered device.
SKINS = {
    "med625flx": {"name": "Stratasys MED625FLX", "printers": "PolyJet: J5 MediJet, J5 DentaJet, J750/J850 Digital Anatomy",
                  "pairs_with": "MED610 (rigid clear) in the same print", "contact": "temporary in-mouth placement [MFR dental data sheet 1125a]",
                  "long_term": False, "shore": "A 73-77", "tensile_mpa": (3.0, 5.0), "elongation_pct": "45-55", "tear_kg_per_cm": "8-12",
                  "source": "Stratasys PolyJet dental materials data sheet 1125a [MFR]"},
    "agilus30": {"name": "Stratasys Agilus30 (with Vero rigid)", "printers": "PolyJet: J750/J850, Connex3",
                 "pairs_with": "VeroClear / Vero colours in the same print; digital materials grade between them",
                 "contact": "no biocompatibility claim [MFR]", "long_term": False, "shore": "A 30-35", "tensile_mpa": (2.4, 3.1),
                 "elongation_pct": "220-270", "tear_kg_per_cm": "4-7", "source": "Stratasys Agilus30 data sheet (Black/Translucent) [MFR]"},
    "silicone-overmold": {"name": "Silicone soft liner, overmolded onto a printed core", "printers": "core on any vat printer; skin cast in a printed mold",
                          "pairs_with": "any long-term rigid resin, with the liner's adhesive primer", "contact": "denture soft liners are used for months [PUB]",
                          "long_term": None, "shore": "A 25-50 (typical)", "tensile_mpa": None, "elongation_pct": None, "tear_kg_per_cm": None,
                          "source": "silicone liners colonised by Candida on the surface, not penetrated, over 90 days (Chladek 2024, doi 10.3390/ma17030723) [PUB]"},
}


@dataclass
class PrintParams:
    route: str = "pause"                 # pause | lid | multimaterial
    final_plate_thickness: float = 2.5   # Tübingen final plate 2.5-3 mm [PUB]
    layer_mm: float = 0.1                # 100 µm, the layer Formlabs and Tübingen use for these resins [MFR, PUB]
    wire_w: float = 1.8                  # Tübingen stock, 1.8 x 0.8 mm twisted stainless [PUB]
    wire_t: float = 0.8
    wire_start_mm: float = 10.0          # about 1 cm after the root [PUB]
    wire_end_mm: float = 2.0             # 1.5-2 mm before the tip [PUB]
    clearance: float = 0.05              # pocket and slot clearance per side [HEUR]
    min_cover_mm: float = 0.8            # resin around the wire, every station [HEUR]
    lid_side: str = "+"                  # the slot opens on the +w (patient's right) or "-" side
    resin: str = "dental-lt-clear-v2"
    base_colour: str = "#d64541"         # plate 1 red, 2 yellow, 3 orange; baton dark blue (Tübingen) [PUB]
    baton_colour: str = "#1f3a93"
    proto: int = 2
    device: str | None = None
    # experimental, multimaterial route only: a flexible skin over the rigid baton (bench work only; see SKINS)
    skin_mm: float = 0.0                 # 0 = no skin
    skin_mode: str = "outside"           # outside: skin added over the full-size rigid baton; inside: core shrunk to make room
    skin_material: str = "med625flx"
    wire: bool = True                    # False only for bench comparisons; such a plate is never registered
    skin_colour: str = "#9fd3ff"


def _vol(m) -> float:
    return float(abs(m.volume)) if m is not None and len(m.faces) else 0.0


def _diff(a, b):
    return manifold_boolean("difference", [a, b])


def _inter(a, b):
    return manifold_boolean("intersection", [a, b])


def rect_sweep(path_local: np.ndarray, width: float, thickness: float) -> trimesh.Trimesh:
    """A sharp-cornered rectangular sweep: width along x, thickness along the in-plane normal.

    For a path in a plane x = const its side faces are exactly the planes x = c +- width/2, so the solid is
    a prism along x: a wire can be dropped into it, or a lid slid in, by one straight move along x."""
    _, nv = path_frames(path_local)
    xh = np.array([1.0, 0.0, 0.0])
    corners = [(1, 1), (-1, 1), (-1, -1), (1, -1)]
    rings = np.array([[p + cu * width / 2 * xh + cv * thickness / 2 * n for cu, cv in corners] for p, n in zip(path_local, nv)])
    m = len(rings)
    verts = rings.reshape(-1, 3)
    faces = []
    for i in range(m - 1):
        for j in range(4):
            a, b, c, d = i * 4 + j, i * 4 + (j + 1) % 4, (i + 1) * 4 + j, (i + 1) * 4 + (j + 1) % 4
            faces += [[a, c, b], [b, c, d]]
    faces += [[0, 2, 1], [0, 3, 2], [(m - 1) * 4, (m - 1) * 4 + 1, (m - 1) * 4 + 2], [(m - 1) * 4, (m - 1) * 4 + 2, (m - 1) * 4 + 3]]
    mesh = trimesh.Trimesh(verts, np.array(faces), process=True)
    mesh.fix_normals()
    return mesh


def wire_cover(body: trimesh.Trimesh, wpath: np.ndarray, every: int = 8) -> dict:
    """Resin between the wire pocket and the outside, in cross-sections perpendicular to the wire.

    Each section of the printed body is a polygon with the pocket as a hole; the cover is the distance from
    the hole to the outer boundary."""
    T, _ = path_frames(wpath)
    vals = []
    for i in range(0, len(wpath), every):
        got = _section_polygon(body, wpath[i], T[i])
        if got is None:
            continue
        poly = got[0]
        holes = list(poly.interiors)
        if not holes:
            vals.append({"station": i, "cover_mm": 0.0, "note": "no pocket in this section"})
            continue
        hole = min(holes, key=lambda r: Polygon(r).centroid.distance(poly.centroid))
        vals.append({"station": i, "cover_mm": round(float(poly.exterior.distance(hole)), 3)})
    covers = [v["cover_mm"] for v in vals]
    return {"min_mm": min(covers) if covers else 0.0, "stations": vals}


def _print_frame(w: np.ndarray, sign: float = 1.0) -> np.ndarray:
    """Rotation taking the build direction (sign * w, the baton-plane normal) to +z."""
    R = np.eye(4)
    R[:3, :3] = trimesh.geometry.align_vectors(sign * w, [0.0, 0.0, 1.0])[:3, :3]
    return R


def build(case_dir: Path, pp: PrintParams | None = None) -> Path:
    """Build the direct-print files for one route; returns the output folder. Callable from a UI."""
    pp = pp or PrintParams()
    if pp.skin_mm > 0 and pp.route != "multimaterial":
        raise ValueError("a flexible skin needs the multimaterial route")
    if pp.skin_material not in SKINS:
        raise ValueError(f"skin_material must be one of {sorted(SKINS)}")
    if pp.route not in ROUTES:
        raise ValueError(f"route must be one of {sorted(ROUTES)} (not offered: {', '.join(NOT_OFFERED)})")
    if pp.resin not in RESINS:
        raise ValueError(f"resin must be one of {sorted(RESINS)}")
    case_dir = Path(case_dir)
    ctx = rebuild(case_dir, plate_thickness=pp.final_plate_thickness)
    rec, hfb, outline, lm = ctx["rec"], ctx["hfb"], ctx["outline"], ctx["lm"]
    border_y, z_mid = ctx["border_y"], ctx["z_mid"]
    ep, chosen, device_id = chosen_extension(rec, pp.proto, pp.device)
    mode = rec.get("extension_mode", "parametric")
    notes = [f"design sent to print: {chosen}; extension mode {mode}; final plate thickness {ctx['pp'].thickness} mm; route {pp.route}"]

    ext, path, xinfo = make_extension(ep, border_y, z_mid, mode)
    if path is None:
        path = extension_path(ep, border_y, z_mid)
    plate = ctx["plate"]
    device = boolean_union(plate, ext)            # no draft trim: nothing has to be pulled off a printed part
    if mode != "parametric":
        device = _diff(device, tissue_clip_solid(outline, hfb))
    x_c = float(np.mean(ext.bounds[:, 0]))
    w, c0, planarity = baton_plane(ext, path, border_y, mode, x_c)
    sw = safety_wire(ext, path, ep, border_y, mode, w, pp.wire_start_mm, pp.wire_end_mm, pp.wire_w, pp.wire_t)
    Rw, wloc, wire, wpath = sw["Rw"], sw["wloc"], sw["wire"], sw["wpath"]
    Rinv = np.linalg.inv(Rw)
    wire_x = float(wloc[:, 0].mean())
    wire_planarity = float(np.ptp(wloc[:, 0]))
    if wire_planarity > 0.2:
        notes.append(f"wire centreline leaves the baton plane by {wire_planarity:.2f} mm: a straight drop-in is not possible")

    # print orientation: build direction b = +w (layers are planes parallel to the baton's plane)
    sign = 1.0 if pp.lid_side == "+" else -1.0
    build_sign = sign if pp.route == "lid" else 1.0             # the lid route prints slot-up
    R = _print_frame(w, build_sign)
    dv = device.copy(); dv.apply_transform(R)
    z0 = float(dv.bounds[0, 2])
    def to_print(m):
        m = m.copy(); m.apply_transform(R); m.apply_translation([0, 0, -z0]); return m

    c, L = pp.clearance, pp.layer_mm
    files, bodies, features = {}, {}, {}
    wire_pk = rect_sweep(wloc, pp.wire_w + 2 * c, pp.wire_t + 2 * c)   # the space the wire sits in
    wire_pk.apply_transform(Rinv)

    if pp.route in ("pause", "multimaterial"):
        # snap the pocket top to a layer boundary so the pause falls exactly where the pocket closes
        # Rw maps w onto x, so a local x is the design-frame coordinate along w: the build height.
        top = wire_x + pp.wire_w / 2 + c
        h = top - float(np.min(device.vertices @ w))               # pocket top above the part's lowest point
        n_layer = int(math.ceil(h / L - 1e-6))
        extra = n_layer * L - h
        pocket = rect_sweep(wloc + np.array([extra / 2, 0, 0]), pp.wire_w + 2 * c + extra, pp.wire_t + 2 * c)
        pocket.apply_transform(Rinv)
        if not pp.wire:
            if pp.route != "multimaterial" or pp.skin_mm <= 0:
                raise ValueError("wire=False is only for the bench skin comparison (multimaterial route with skin_mm > 0)")
            pocket = trimesh.Trimesh()
            notes.append("NO SAFETY WIRE: bench specimen only, for comparing a skin against the wire; never registered")
        printed = _diff(device, pocket) if pp.wire else device.copy()
        features["pause"] = {} if not pp.wire else {
            "pause_after_layer": n_layer, "pause_height_mm": round(n_layer * L, 3), "layer_mm": L,
            "pocket_top_gap_mm": round(extra + c, 3),
            "measured_from": "measured from the part's lowest point; add the raft and support lift your printer software shows",
            "steps": ["print to the pause; leave the part on the build platform",
                      "drain and blot the pocket; do not touch the uncured surfaces",
                      "press the pre-bent, sandblasted and silanised wire flat into the pocket (it drops straight in along the build axis)",
                      "check it sits below the last layer, then resume"]}
        bodies["printed_plate"] = printed
    else:
        # lid: a slot from the wire pocket out through one side of the baton (along sign*w)
        far = 60.0
        x_lo = wire_x - pp.wire_w / 2 - c if sign > 0 else wire_x + pp.wire_w / 2 + c
        slot_path = wloc.copy()
        slot_path[:, 0] = x_lo + sign * far / 2
        slot = rect_sweep(slot_path, far, pp.wire_t + 2 * c); slot.apply_transform(Rinv)
        lid_path = wloc.copy()
        x_lid0 = wire_x + sign * (pp.wire_w / 2 + c)
        lid_path[:, 0] = x_lid0 + sign * far / 2
        lid_blank = rect_sweep(lid_path, far, pp.wire_t); lid_blank.apply_transform(Rinv)
        printed = _diff(device, slot)
        lid = _inter(device, lid_blank)
        pocket = _inter(slot, device)
        bodies["printed_plate"], bodies["lid"] = printed, lid
        removed_from_plate = _vol(_inter(_inter(slot, device), plate))
        features["lid"] = {"opens_toward": "patient's right (+x)" if sign > 0 else "patient's left (-x)",
                           "lid_thickness_mm": pp.wire_t, "lid_volume_mm3": round(_vol(lid), 1),
                           "lid_surface_mm2": round(float(lid.area), 1),
                           "slot_cuts_plate_mm3": round(removed_from_plate, 3),
                           "steps": ["print plate and lid; wash; do not post-cure yet",
                                     "seat the pre-bent, sandblasted and silanised wire at the bottom of the slot",
                                     "coat the lid with the same resin, press it in flush, spot-cure 10 s per side",
                                     "post-cure the assembly with the resin's validated settings"]}
    if pp.wire:
        bodies["safety_wire"] = wire

    # --- checks (design frame)
    cover = wire_cover(_diff(device, wire_pk), wpath)
    if pp.route == "lid":
        cover["note"] = "measured with the slot filled; on the lid side the cover is a bonded seam, not one continuous body"
    pocket_exposed = _vol(_diff(pocket, device)) if len(pocket.faces) else 0.0
    wire_in_pocket = _vol(_diff(wire, wire_pk))
    vol_identity = abs(_vol(device) - _vol(pocket) - _vol(bodies["printed_plate"]))
    fqc = qc_prototype(device, path, ep, ctx["pp"], outline, rec["landmarks"], kind="final", reference=rec.get("standard"),
                       wire={"start_mm_after_root": round(float(sw["st"][0] - sw["s_border"]), 2),
                             "end_mm_before_tip": round(float(sw["s_tip"] - sw["st"][-1]), 2)},
                       root=root_span(ext, border_y, z_mid, ep.lateral_offset), support=hfb)
    rz = RESINS[pp.resin]
    min_wall = rz.get("min_wall_mm")
    checks = {
        "printed bodies watertight": all(m.is_watertight for m in bodies.values()),
        "appliance is one body": int(device.body_count) == 1,
        "printed part = appliance minus pocket": vol_identity <= 0.5,
        "wire inside its pocket": wire_in_pocket <= 0.05,
        "wire lies in one layer plane": wire_planarity <= 0.2,
        f"resin cover around wire >= {pp.min_cover_mm} mm": cover["min_mm"] >= pp.min_cover_mm,
        "final-plate rules": fqc["overall"] != "fail",
        "safety wire present (Tübingen rule)": pp.wire,
    }
    if pp.route == "lid":
        checks["slot stays behind the plate"] = features["lid"]["slot_cuts_plate_mm3"] <= 0.05
        checks["lid and plate do not overlap"] = _vol(_inter(lid, printed)) <= 0.05
    else:
        checks["pocket enclosed by resin"] = pocket_exposed <= 0.05
    if pp.route == "lid" and min_wall:
        checks[f"lid thickness >= resin minimum wall {min_wall} mm"] = pp.wire_t >= min_wall
    warnings = []
    if pp.route == "lid" and not min_wall:
        warnings.append(f"lid is {pp.wire_t} mm thick; check the resin's minimum wall (Dental LT Comfort: 1 mm) [MFR]")
    if pp.route in ("pause", "multimaterial") and "not offered" in rz["pause_at_layer"]:
        warnings.append(f"{rz['name']}: its printer software cannot pause at a layer ({rz['pause_at_layer']}); "
                        "use the lid route on that printer, or a resin validated on a printer that can pause")
    if pp.route == "multimaterial" and pp.resin != "truedent":
        warnings.append("multimaterial route with a single-colour resin: colour bodies are exported but will print in one colour")
    if ctx["pp"].profile == "graded":
        thin = ctx["pp"].thin_thickness
        warnings.append(f"graded plate thins to {thin} mm; check it against the resin's minimum wall [MFR]")
        if min_wall and thin < min_wall:
            checks[f"graded floor >= resin minimum wall {min_wall} mm"] = False
    ok = all(checks.values())

    # --- export in print orientation
    sub_dir = pp.route if pp.skin_mm <= 0 else f"{pp.route}-skin-{pp.skin_mode}" + ("" if pp.wire else "-nowire")
    out = case_dir / "print" / sub_dir
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.stl"):
        old.unlink()
    for name, m in bodies.items():
        to_print(m).export(out / f"{name}.stl"); files[name] = f"{name}.stl"
    if pp.route == "multimaterial":
        base_poly = Polygon(outline).buffer(1.5)           # the outward-leaning rim stays base colour
        from .mold import _prism
        bodies_mm = [("plate_base", None, pp.base_colour), ("plate_baton", None, pp.baton_colour)]
        if pp.skin_mm > 0:
            sk = skin_layer(ext, device, pocket, border_y, pp, ep, wpath, cover["min_mm"], outline, hfb, mode)
            features["skin"] = sk["report"]
            checks.update(sk["checks"]); warnings.extend(sk["warnings"])
            bodies["printed_plate"] = sk["outer"]
            bodies_mm.append(("plate_skin", sk["skin"], pp.skin_colour))
            to_print(sk["outer"]).export(out / "printed_plate.stl")
        whole = bodies["printed_plate"]
        rigid = _diff(whole, bodies_mm[2][1]) if pp.skin_mm > 0 else whole
        base = _inter(rigid, _prism(base_poly))
        baton = _diff(rigid, _prism(base_poly))
        bodies_mm[0] = ("plate_base", base, pp.base_colour); bodies_mm[1] = ("plate_baton", baton, pp.baton_colour)
        colour_bodies = []
        for name, m, col in bodies_mm:
            mm = to_print(m)
            mm.export(out / f"{name}.stl"); files[name] = f"{name}.stl"
            colour_bodies.append((name, mm, col))
        write_3mf(out / "assembly.3mf", colour_bodies)
        files["assembly"] = "assembly.3mf"
        checks["colour bodies partition the plate"] = abs(sum(_vol(m) for _, m, _ in bodies_mm) - _vol(whole)) <= 0.5
        ok = all(checks.values())

    manifest = {
        "schema": "pebp-print/0.1", "created": datetime.now(timezone.utc).isoformat(timespec="seconds"), "case": str(case_dir),
        "route": pp.route, "route_info": ROUTES[pp.route], "not_offered": NOT_OFFERED,
        "params": asdict(pp), "device": device_id, "extension_params": asdict(ep), "extension_mode": mode,
        "resin": {"key": pp.resin, **rz},
        "files": files, "features": features,
        "orientation": {"build_direction_design_frame": np.round(build_sign * w, 4).tolist(),
                        "description": "lying on its side: the baton's plane is a layer plane; the patient's "
                                       + ("right" if build_sign > 0 else "left") + " side faces away from the build platform",
                        "part_height_mm": round(float(to_print(device).bounds[1, 2]), 2)},
        "safety_wire": {"length_mm": round(float(sw["w1"] - sw["w0"]), 1),
                        "starts_mm_after_root": round(float(sw["st"][0] - sw["s_border"]), 2),
                        "ends_mm_before_tip": round(float(sw["s_tip"] - sw["st"][-1]), 2),
                        "stock": f"{pp.wire_w} x {pp.wire_t} mm stainless twisted wire, bent flatwise on wire_jig.stl from `pebp mold`",
                        "surface_prep": "sandblast 50 µm Al2O3 at 3 bar, acid etch, silane primer (Zhang 2025) [PUB]; "
                                        "untested with twisted wire [INF]"},
        "volumes_mm3": {k: round(_vol(v), 1) for k, v in bodies.items()},
        "resin_ml": round(_vol(bodies["printed_plate"]) / 1000.0, 2),
        "validation": {"checks": checks, "warnings": warnings, "wire_cover": cover, "pocket_exposed_mm3": round(pocket_exposed, 3),
                       "wire_outside_pocket_mm3": round(wire_in_pocket, 3), "wire_out_of_plane_mm": round(wire_planarity, 3),
                       "baton_plane_planarity_mm_rms": round(planarity, 3), "final_qc": fqc, "ok": ok},
        "notes": notes,
        "status": "geometry only; nothing printed or bench-tested. Research use, not a medical device.",
    }
    if ok:
        same = [d for d in rec["printed_plates"] if d["from"] == device_id and d["params"] == asdict(ep)
                and d["route"] == pp.route and d["final_plate_thickness"] == ctx["pp"].thickness]
        n = same[-1]["n"] if same else max([d["n"] for d in rec["printed_plates"]], default=0) + 1
        manifest["registered_as"] = f"D{n}"
        (out / "print_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        fn = f"printed_{n}.stl"
        device.export(case_dir / fn)
        entry = {"n": n, "id": f"D{n}", "file": fn, "sha256": rec_io.file_sha256(case_dir / fn), "from": device_id,
                 "params": asdict(ep), "final_plate_thickness": ctx["pp"].thickness, "route": pp.route, "resin": pp.resin,
                 "wire": {"start_mm_after_root": manifest["safety_wire"]["starts_mm_after_root"],
                          "end_mm_before_tip": manifest["safety_wire"]["ends_mm_before_tip"],
                          "stock": f"{pp.wire_w} x {pp.wire_t} mm stainless twisted wire"},
                 "files": {"dir": f"print/{sub_dir}", "manifest_sha256": rec_io.file_sha256(out / "print_manifest.json")},
                 "qc": fqc, "time": rec_io.now(),
                 "note": "registered when the print files were generated; it is a physical plate only once printed, wired and cured"}
        rec["printed_plates"] = [d for d in rec["printed_plates"] if d["n"] != n] + [entry]
        rec["printed_plates"].sort(key=lambda d: d["n"])
        from . import airway
        airway.update(rec, case_dir, devices=[entry["id"]])
        rec_io.save(rec, case_dir / "design.json")
    else:
        manifest["registered_as"] = None
        (out / "print_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (out / "PRINT_NOTES.md").write_text(print_notes(manifest), encoding="utf-8")
    return out


def skin_layer(ext, device, pocket, border_y, pp: "PrintParams", ep, wpath, cover_mm, outline, hfb, mode) -> dict:
    """Experimental: a flexible skin over the baton behind the posterior border, for a multi-material print.

    outside: the rigid baton keeps its full Tübingen size and the skin is added over it (the baton grows by
    2 x skin_mm). inside: the rigid core is shrunk by skin_mm to make room, and the outside stays the
    designed shape. Offsets are along vertex normals, as in polyjet.py."""
    from .polyjet import _halfspace, _shrink
    s = pp.skin_mm
    post = _halfspace(border_y, posterior=True)
    t = ep.thickness
    checks, warnings = {}, []
    if pp.skin_mode == "outside":
        grown = _shrink(ext, -s)
        skin = _inter(_diff(grown, device), post)
        if mode != "parametric":
            skin = _diff(skin, tissue_clip_solid(outline, hfb))
        outer = boolean_union(_diff(device, pocket) if len(pocket.faces) else device, skin)
        core_t, outer_t = t, t + 2 * s
        cover_core = cover_mm
    elif pp.skin_mode == "inside":
        core = _shrink(ext, s)
        skin = _inter(_diff(ext, core), post)
        outer = _diff(device, pocket) if len(pocket.faces) else device.copy()
        core_t, outer_t = t - 2 * s, t
        cover_core = cover_mm - s
    else:
        raise ValueError("skin_mode must be 'outside' or 'inside'")
    skin = _inter(skin, outer)                     # never outside the printed part (tissue clip, pocket)
    mat = SKINS[pp.skin_material]
    # tether: if the core snaps, what holds the far piece? the skin's cross-section at mid-wire
    T, _ = path_frames(wpath)
    i = len(wpath) // 2
    sec = skin.section(plane_origin=wpath[i], plane_normal=T[i])
    area = 0.0
    if sec is not None:
        from .meshutil import _filled_polygons
        planar, _ = sec.to_planar()
        area = float(sum(pg.area for pg in _filled_polygons(planar)))
    wire_area = pp.wire_w * pp.wire_t
    tensile = mat.get("tensile_mpa")
    report = {"mode": pp.skin_mode, "skin_mm": s, "material": mat["name"], "material_contact": mat["contact"],
              "rigid_core_thickness_mm": round(core_t, 2), "outer_baton_thickness_mm": round(outer_t, 2),
              "core_bending_stiffness_vs_designed": round((core_t / t) ** 3, 2),
              "core_bending_strength_vs_designed": round((core_t / t) ** 2, 2),
              "skin_volume_mm3": round(_vol(skin), 1),
              "skin_section_at_mid_wire_mm2": round(area, 2), "wire_section_mm2": round(wire_area, 2),
              "skin_tether_newtons_if_core_snaps": [round(area * x, 1) for x in tensile] if tensile else None,
              "tether_note": "plain tension through an intact skin [INF]; a sharp core fragment and the skin's tear strength "
                             "lower it, and the 52.1 N adult tongue-force threshold (Aretxabaleta 2021) is the yardstick [PUB]",
              "evidence": "rigid-flexible interfaces in multi-material prints fail by debonding unless interlocked "
                          "(Qiao 2026, doi 10.3390/ma19112293; Brancewicz-Steinmetz 2024, doi 10.1089/3dp.2023.0210) [PUB]"}
    checks["skin is one watertight body"] = bool(skin.is_watertight) and _vol(skin) > 1.0
    checks["rigid core >= 3 mm (Tübingen)"] = core_t >= 3.0 - 1e-6
    if pp.wire:
        checks[f"wire cover inside the rigid core >= {pp.min_cover_mm} mm"] = cover_core >= pp.min_cover_mm
    checks["skin material rated for long-term mucosal contact"] = bool(mat["long_term"])
    warnings.append("skinned baton is a bench specimen: no flexible printable material is rated for long-term mucosal contact [MFR]")
    if pp.skin_mode == "outside":
        warnings.append(f"the skin makes the baton {outer_t:.1f} mm thick instead of {t:.1f} mm: more bulk in the oropharynx; "
                        "re-run the reach check before any try-in [INF]")
    return {"skin": skin, "outer": outer, "checks": checks, "warnings": warnings, "report": report}


def write_3mf(path: Path, bodies: list[tuple[str, trimesh.Trimesh, str]]) -> None:
    """A minimal 3MF (core spec + basematerials) with one named, coloured object per body, all in place.

    Written by hand so the export needs no XML library; slicers read the names and display colours."""
    import zipfile
    mats = "".join(f'<base name="{n}" displaycolor="{c.upper()}FF"/>' for n, _, c in bodies)
    objs, items = [], []
    for i, (name, m, _) in enumerate(bodies):
        v = "".join(f'<vertex x="{a:.5f}" y="{b:.5f}" z="{c:.5f}"/>' for a, b, c in m.vertices)
        t = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in m.faces)
        objs.append(f'<object id="{i + 2}" name="{name}" type="model" pid="1" pindex="{i}">'
                    f'<mesh><vertices>{v}</vertices><triangles>{t}</triangles></mesh></object>')
        items.append(f'<item objectid="{i + 2}"/>')
    model = ('<?xml version="1.0" encoding="UTF-8"?>'
             '<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
             f'<resources><basematerials id="1">{mats}</basematerials>{"".join(objs)}</resources>'
             f'<build>{"".join(items)}</build></model>')
    types = ('<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
             '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", types)
        z.writestr("_rels/.rels", rels)
        z.writestr("3D/3dmodel.model", model)


def compare_routes() -> list[dict]:
    """The route catalogue, mold route included, as rows a UI can show side by side."""
    rows = [{"route": "mold", "title": "Cast in printed tooling (current default)",
             "pros": ["cold-cure PMMA with a cast-in wire, the material and wire method Tübingen uses [PUB]"],
             "cons": ["needs a pour-type PMMA, a pressure pot, a separator and bench skill [PUB]",
                      "pin holes to fill after demolding [INF]"],
             "evidence": "Tübingen 2025 protocol (hand layering, not a closed mold) [PUB]"}]
    rows += [{"route": k, **v} for k, v in ROUTES.items()]
    return rows


def print_notes(man: dict) -> str:
    r, v = man["route_info"], man["validation"]
    lines = [f"# Direct print: {r['title']}", "", man["status"], "",
             f"Device {man['device']}, resin {man['resin']['name']} ({man['resin']['process']}). "
             f"Registered as {man['registered_as'] or 'nothing (checks failed)'}.", "",
             "## Files", *[f"- `{f}`" for f in man["files"].values()], "",
             "## Orientation", f"{man['orientation']['description']}. Part height {man['orientation']['part_height_mm']} mm. "
             "Supports go on the down-facing side only; keep them off the intaglio where the software allows.", ""]
    if man["features"].get("pause"):
        p = man["features"]["pause"]
        lines += ["## Pause", f"Pause after layer {p['pause_after_layer']} ({p['pause_height_mm']} mm at {p['layer_mm']} mm layers, "
                  f"{p['measured_from']}).", *[f"{i + 1}. {s}" for i, s in enumerate(p["steps"])], ""]
    if "lid" in man["features"]:
        lines += ["## Lid", f"Slot opens toward the {man['features']['lid']['opens_toward']}.",
                  *[f"{i + 1}. {s}" for i, s in enumerate(man["features"]["lid"]["steps"])], ""]
    w = man["safety_wire"]
    lines += ["## Safety wire", f"{w['length_mm']} mm, from {w['starts_mm_after_root']} mm after the root to {w['ends_mm_before_tip']} mm before the tip. "
              f"{w['stock']}. Surface: {w['surface_prep']}.", "",
              "## Post-processing", man["resin"]["process_notes"] + ". Follow the resin maker's validated wash and cure exactly; "
              "biocompatibility depends on it.", "",
              "## Checks", *[f"- {'PASS' if ok else 'FAIL'} {k}" for k, ok in v["checks"].items()],
              f"- minimum resin cover around the wire: {v['wire_cover']['min_mm']} mm", *[f"- warning: {x}" for x in v["warnings"]], "",
              "## Trade-offs", *[f"- + {x}" for x in r["pros"]], *[f"- − {x}" for x in r["cons"]], ""]
    return "\n".join(lines)
