"""Command line for the PEBP Designer demo.

  python -m pebp synth   --out out/case_synth [--unoriented] [--asym 1.5] [--no-cleft] [--preset prs]
  python -m pebp design  SCAN.stl --out out/case1 [--final] [--extension auto|parametric|tuebingen-cleft|...]
  python -m pebp carry   out/case1/design.json NEW_SCAN.stl --device C1 --out out/case1_plate2 [--prototypes]
  python -m pebp lineage out/case1/design.json --device C1     a device's history and bedside-shortening total
  python -m pebp adjust  out/case1/design.json --from P2 --mod shorten_mm=3 --mod curve_deg=15 [--bedside] --note "..."
  python -m pebp fitting template|form|log|measure ...   structured endoscopy try-in
  python -m pebp sleep   out/case1/design.json --device C1 --day 21 --index MOAI --value 2.1 ...
  python -m pebp measure DESIGN.stl SCAN.stl              bench: scanned part against its design
  python -m pebp mold    out/case1 [--device A1]          printed tooling for the cast final plate (registers C<n>)
  python -m pebp kit     out/case1 [--lengths -2,0,2,4,6 --angles -8,0,8]   modular try-in kit (registers K<n>)
  python -m pebp kit     --stock out/kit_stock [--variant cleft]             the patient-independent stock batons
  python -m pebp chairside out/case1 --base                                research, visit 1: final base + its casting tooling
  python -m pebp chairside out/case1 --kit K8                              research, visit 2: stock wired baton picked up into the base (S<n>)
  python -m pebp chairside --stock-tooling out/cs_stock [--only K5,K8]     molds and wire jigs for the stock final batons
  python -m pebp bench   out/bench [--baton K5]                            synthetic bench specimens for the 8 chairside tests
  python -m pebp airway  out/case1/design.json [--set vallecula=x,y,z --source ct]   landmarks, plate metrics, reach check
  python -m pebp viewer  out/case1
  python -m pebp studio  [--workspace out]             step-by-step clinician UI on http://127.0.0.1:8780
  python -m pebp app     [--install]                   the same studio as a desktop app (--install: PEBP Studio.app + starter folder)
  python -m pebp frames  fetch|synth|train|eval|classify ...   endoscopy frame-quality filter
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
import trimesh

from . import record as rec
from . import fitting as fit
from .extension import (ExtensionParams, engrave_number, prototype_set, cut_wire_groove, make_extension,
                        tuebingen_params, resolve_mode)
from .landmarks import find_landmarks
from .meshutil import boolean_union, boolean_difference, arclength
from .plate import PlateParams, design_base_plate, tissue_clip_solid
from .qc import qc_prototype, lineage_checks, arm_length, tip_point, overall, root_span
from .synthetic import MaxillaParams, build_maxilla, random_rigid, prs_published

FRAME_NOTE = ("x: patient's right + (unmirrored export assumed), y: anterior +, z: superior +; occlusal plane z=0 through the ridge crest; "
              "origin at the midpoint between the tuberosities; mm")


def _fail(msg: str, code: int = 2):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def cmd_synth(a):
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    if a.preset == "prs":
        p = prs_published(seed=a.seed, asymmetry=a.asym, cleft=not a.no_cleft,
                          intertuberosity_width=33.05 + 1.95 * a.sd, arch_depth=23.0 + 1.81 * a.sd,
                          cleft_half_width_end=(12.69 + 2.31 * a.sd) / 2.0)
    else:
        p = MaxillaParams(asymmetry=a.asym, cleft=not a.no_cleft, seed=a.seed)
    mesh, truth = build_maxilla(p)
    if a.unoriented:
        mesh, T = random_rigid(mesh, seed=a.seed)
        truth["applied_transform"] = T.tolist()
    path = out / "scan.stl"
    mesh.export(path)
    (out / "scan_truth.json").write_text(json.dumps(truth, indent=2))
    print(f"wrote {path}  ({len(mesh.vertices)} vertices, cleft={'yes' if p.cleft else 'no'}, unoriented={a.unoriented})")


# ---------------------------------------------------------------- building one device

def _build_one(g: dict, ep: ExtensionParams, number: int, mode: str, kind: str, reference: dict | None,
               groove: bool = False, wire: dict | None = None) -> dict:
    """Plate + baton for one device. g: plate, hfb, outline, pp, lm_json, border_y, z_mid.
    `wire`: the wire of an existing wired plate (bedside change); otherwise a groove is measured if cut."""
    ext, path, info = make_extension(ep, g["border_y"], g["z_mid"], mode)
    mesh = boolean_union(g["plate"], ext)
    if mode != "parametric":
        # the published anchor block is generic: cut away whatever lies above the intaglio (tissue / cleft space)
        mesh = boolean_difference(mesh, tissue_clip_solid(g["outline"], g["hfb"]))
    if number and path is not None:
        mesh = engrave_number(mesh, path, number, ep, g["border_y"])
    if groove and path is not None:
        mesh, wire = cut_wire_groove(mesh, path, ep, g["border_y"])
    qc = qc_prototype(mesh, path, ep, g["pp"], g["outline"], g["lm_json"], kind=kind, reference=reference, wire=wire,
                      root=root_span(ext, g["border_y"], g["z_mid"], ep.lateral_offset), support=g["hfb"], as_written=True)
    tip = tip_point(mesh, path) if path is not None else np.array(info["tip"])
    return {"mesh": mesh, "path": path, "qc": qc, "info": info, "wire": wire, "tip": tip}


def _geometry(case_dir: Path, record: dict, thickness: float | None, cache: dict) -> dict:
    """Plate and attachment for a case at a plate thickness (rebuilt from the scan; cached per thickness)."""
    key = thickness
    if key not in cache:
        from .context import rebuild
        ctx = rebuild(case_dir, plate_thickness=thickness)
        cache[key] = {"plate": ctx["plate"], "hfb": ctx["hfb"], "outline": ctx["outline"], "pp": ctx["pp"],
                      "lm_json": record["landmarks"], "border_y": ctx["border_y"], "z_mid": ctx["z_mid"]}
    return cache[key]


def _print_qc(qc: dict, indent: str = "  "):
    for c in qc["checks"]:
        if c["status"] != "pass":
            print(f"{indent}{c['status'].upper()}: {c['check']} = {c['value']} ({c['rule']})")


def build_variant(case_dir: Path, record: dict, source_id: str, ep_new: ExtensionParams, mods: list[dict], note: str,
                  where: str, finding: str | None = None, final: bool = False, session: int | None = None,
                  cache: dict | None = None) -> dict:
    """A changed device A<n>, derived from `source_id`, appended to the modification log.

    where='bedside': the same physical device, ground or bent at the bedside. If it carries a wire, the
                     wire stays: shortening uses up the tip reserve and thickness must not change.
    where='lab':     a new device made from the changed design (printed, or cast later with `mold`).
    final=True:      a lab final with a wire groove (the older groove-and-cover route)."""
    cache = {} if cache is None else cache
    src = rec.device(record, source_id)
    base = ExtensionParams(**src["params"])
    bedside_wired = where == "bedside" and src["wired"]
    kind = "final" if (final or bedside_wired) else "prototype"
    thickness = None
    if kind == "final":
        if src["kind"] in ("cast", "printed"):
            key = "cast_plates" if src["kind"] == "cast" else "printed_plates"
            thickness = next(c["final_plate_thickness"] for c in record[key] if c["id"] == src["id"])
        elif src["kind"] == "adjusted" and src["wired"]:
            thickness = next(e["final_plate_thickness"] for e in record["modification_log"] if e["id"] == src["id"])
        else:
            thickness = record.get("final_plate_thickness", 2.5)
    g = _geometry(case_dir, record, thickness, cache)
    wire, used, thinning = None, 0.0, False
    if bedside_wired:
        used = round(src["bedside_shortening_mm"] + (base.effective_length - ep_new.effective_length), 3)
        thinning = ep_new.thickness < base.thickness - 1e-9
        wire = {**src["wire"], "check_span": False, "end_mm_before_tip": round(src["reserve_mm"] - used, 2)}
    res = _build_one(g, ep_new, 0, record.get("extension_mode", "parametric"), kind, record.get("standard"),
                     groove=final and not bedside_wired, wire=wire)
    n = max([e["n"] for e in record["modification_log"]], default=0) + 1
    fn = f"adjusted_{n}.stl"
    res["mesh"].export(case_dir / fn)
    entry = {"n": n, "id": f"A{n}", "from": src["id"], "from_prototype": int(src["id"][1:]) if src["kind"] == "prototype" else None,
             "changes": {k: round(v, 3) for k, v in asdict(ep_new).items() if v != asdict(base)[k]},
             "modifications": mods, "where": where, "finding": finding, "session": session, "note": note,
             "kind": kind, "file": fn, "sha256": rec.file_sha256(case_dir / fn), "params": asdict(ep_new),
             "tip": res["tip"].round(2).tolist(), "final_plate_thickness": thickness,
             "wired": bedside_wired, "reserve_mm": src["reserve_mm"] if bedside_wired else None,
             "bedside_shortening_mm": used, "bedside_thinning": thinning, "wire": res["wire"], "time": rec.now()}
    qc = res["qc"]
    qc["checks"] += lineage_checks(entry)
    qc["overall"] = overall(qc["checks"])
    entry["qc"] = qc
    record["modification_log"].append(entry)
    from . import airway
    airway.update(record, case_dir, devices=[entry["id"]])
    return entry


def _report_variant(e: dict):
    print(f"{e['id']} from {e['from']} ({e['where']}{', wired' if e['wired'] else ''}): {e['changes']} -> {e['file']}, "
          f"tip {np.round(e['tip'], 1).tolist()}, QC {e['qc']['overall']}")
    _print_qc(e["qc"])


# ---------------------------------------------------------------- design

def _standard(mode: str) -> ExtensionParams:
    return tuebingen_params(mode.replace("tuebingen-", "")) if mode.startswith("tuebingen-") else ExtensionParams()


def cmd_design(a, carry: dict | None = None):
    """carry: a spec from carry.carry_spec(); the accepted baton of an earlier plate goes onto this scan as T."""
    t0 = time.time()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    scan = trimesh.load(a.scan, force="mesh")
    extra = {"backend": "builtin"}
    if a.backend == "moldstudio":
        from .moldstudio_bridge import classify_landmarks
        om, lm, hf, extra, trimmed = classify_landmarks(scan, cleft_rise_threshold=a.cleft_threshold)
        trimmed.export(out / "scan_trimmed.stl")
    else:
        om, lm, hf = find_landmarks(scan, cleft_rise_threshold=a.cleft_threshold)
    from . import cleft as cleftmod
    try:
        lm, cleft_info = cleftmod.apply(lm, a.cleft)
    except ValueError as e:
        _fail(str(e))
    lm_json = lm.to_json()
    lm_json["backend"] = extra
    om.export(out / "scan_oriented.stl")
    bulge = a.posterior_bulge if a.posterior_bulge is not None else cleftmod.plate_defaults(a.cleft)["posterior_bulge"]
    pp = PlateParams(thickness=a.plate_thickness, flange=a.flange, block_out=a.block_out,
                     profile=a.plate_profile, thin_thickness=a.thin_thickness, border_relief=a.border_relief,
                     posterior_bulge=bulge)
    try:
        plate, hfb, outline, info = design_base_plate(lm, hf, pp)
        if a.plate_profile == "graded" and a.final:
            from .graded import validate
            validate(a.final_thickness, a.thin_thickness)
    except ValueError as e:
        _fail(str(e))
    plate.export(out / "base_plate.stl")
    border_y = info["posterior_border_y_mid"]
    z_top = float(hfb(np.array([[0.0, border_y + 1.0]]))[0])
    z_mid = z_top - pp.thickness / 2.0

    mode = cleftmod.resolve_extension(a.extension, a.cleft, cleft_info["scan_found_cleft"])
    if cleft_info["value"] and mode.startswith("tuebingen-") and a.extension != "auto" \
            and mode != f"tuebingen-{cleftmod.baton_variant(a.cleft, False)}":
        cleft_info["warnings"].append(f"--extension {mode} overrides the baton the cleft type indicates "
                                      f"(tuebingen-{cleftmod.baton_variant(a.cleft, False)})")
    if carry:
        carry["mode_auto"] = mode
        mode = carry["mode"]
    std = _standard(mode)
    over = {k: v for k, v in {"cleft_section_length": a.cleft_section, "effective_length": a.effective_length,
                              "bend_radius": a.bend_radius, "thickness": a.ext_thickness, "width_base": a.width_base,
                              "width_tip": a.width_tip, "tilt_deg": a.tilt, "distal_curve_deg": a.curve,
                              "lateral_offset": a.lateral}.items() if v is not None}
    if mode.startswith("tuebingen-"):
        # the published extension has a constant width; one width option sets both ends
        if "width_base" in over and "width_tip" not in over:
            over["width_tip"] = over["width_base"]
        if "width_tip" in over and "width_base" not in over:
            over["width_base"] = over["width_tip"]
    ep = carry["params"] if carry else replace(std, **over)

    g = {"plate": plate, "hfb": hfb, "outline": outline, "pp": pp, "lm_json": lm_json, "border_y": border_y, "z_mid": z_mid}
    try:
        std_mesh, std_path, _ = make_extension(std, border_y, z_mid, mode)
        make_extension(ep, border_y, z_mid, mode)          # fail early on a change the mode cannot make
    except (ValueError, FileNotFoundError) as e:
        _fail(e.args[0] if e.args else str(e))
    standard = {"mode": mode, "params": asdict(std),
                "arm_length_mm": round(arm_length(std_mesh, std_path, std), 2) if std_path is not None else None,
                "note": "the extension this design is compared against (QC 'length vs standard', 'width')"}
    record = rec.new_record(a.scan, lm_json, info, ep, FRAME_NOTE)
    record["attachment"] = {"posterior_border_y": round(border_y, 2), "z_mid": round(z_mid, 2)}
    record["extension_mode_requested"] = a.extension
    record["extension_mode"] = mode
    record["cleft_status"] = cleftmod.record_entry(a.cleft, a.cleft_entered_by, cleft_info, mode, rec.now(), bulge)
    record["standard"] = standard
    record["final_plate_thickness"] = a.final_thickness
    if a.border_relief:
        from .relief import LABEL as RLABEL
        br = info["border_relief"]
        record.setdefault("design_choices", {})["border_relief"] = {
            "value": True, "flange_rise_mm": pp.flange_rise_mm, "frenum_notch_mm": pp.frenum_notch_mm,
            "tuberosity_relief_mm": pp.tuberosity_relief_mm, "tuberosity_relief_radius_mm": pp.tuberosity_relief_radius,
            "trimmed_mm2": br["vestibular_cap"]["trimmed_mm2"], "notch_removed_mm2": br["labial_frenulum_notch"]["removed_mm2"],
            "chosen_by": "designer (--border-relief)", "flag": RLABEL, "time": rec.now()}
    if a.plate_profile == "graded":
        from .graded import LABEL, THIN_FLOOR_MM
        # a per-case design choice, flagged: the software offers it, a person chose it
        record.setdefault("design_choices", {})["plate_profile"] = {
            "value": "graded", "thin_mm": a.thin_thickness, "base_mm": {"prototype": a.plate_thickness, "final": a.final_thickness},
            "floor_mm": THIN_FLOOR_MM, "chosen_by": "designer (--plate-profile graded)", "flag": LABEL, "time": rec.now()}
    np.save(out / "plate_outline.npy", outline)
    cs = record["cleft_status"]
    print(f"cleft type: {cs['label']}" + (f" (entered by {cs['entered_by']})" if cs["entered_by"] else "") + f"; bridging: {cs['bridging']}; "
          f"posterior midline bulge {bulge:g} mm")
    for w in cs["warnings"]:
        print("WARN:", w)
    if carry:
        print(f"extension: {mode}, carried from {carry['from']['device']} of {carry['from']['design_file']}")
    else:
        print(f"extension: {mode}" + ((f" (from the cleft type)" if cs["value"] else f" (auto: cleft {'found' if cleft_info['scan_found_cleft'] else 'not found'} by the scan analysis)") if a.extension == "auto" else "")
              + (f"; changed from the standard: {over}" if over else "; the standard, unchanged")
              + (f"; standard arm {standard['arm_length_mm']} mm" if standard["arm_length_mm"] else ""))
    if carry:
        from .carry import add_carried
        t = add_carried(record, g, ep, mode, standard, out, carry, _build_one)
        print(f"T (carried {carry['from']['device']}): tip {np.round(t['tip'], 1).tolist()}, QC {t['qc']['overall']}")
        _print_qc(t["qc"])
    offsets = (ep.ap_offset - 2.0, ep.ap_offset, ep.ap_offset + 2.0) if carry else (-2.0, 0.0, 2.0)
    for k, p in enumerate(prototype_set(ep, offsets) if (not carry or carry.get("prototypes")) else [], start=1):
        r = _build_one(g, p, k, mode, "prototype", standard)
        fn = f"prototype_{k}.stl"
        r["mesh"].export(out / fn)
        record["prototypes"].append({"number": k, "id": f"P{k}", "file": fn, "sha256": rec.file_sha256(out / fn),
                                     "ap_offset": p.ap_offset, "params": asdict(p), "tip": r["tip"].round(2).tolist(),
                                     "qc": r["qc"], "extension_info": r["info"]})
        print(f"P{k}: ap {p.ap_offset:+.1f} mm, tip {r['tip'].round(1).tolist()}, QC {r['qc']['overall']}")
        _print_qc(r["qc"])
    if a.final:
        pf = replace(pp, thickness=a.final_thickness)
        plate_f, hfb_f, outline_f, _ = design_base_plate(lm, hf, pf)
        gf = {**g, "plate": plate_f, "hfb": hfb_f, "outline": outline_f, "pp": pf}
        r = _build_one(gf, ep, 0, mode, "final", standard, groove=True)
        r["mesh"].export(out / "final_design.stl")
        record["final_design"] = {"id": "F", "file": "final_design.stl", "sha256": rec.file_sha256(out / "final_design.stl"),
                                  "params": asdict(ep), "plate_thickness": a.final_thickness, "extension_info": r["info"],
                                  "wire_groove": r["wire"], "qc": r["qc"]}
        print(f"F (final with wire groove {r['wire']}): QC {r['qc']['overall']}" if r["wire"] else f"F: QC {r['qc']['overall']}")
        _print_qc(r["qc"])
    from . import airway
    airway.update(record, out)
    (out / "airway_report.md").write_text(airway.report_md(record))
    if carry:
        from .carry import growth_summary
        record["carried_from"]["growth"] = growth_summary(record)
    rec.save(record, out / "design.json")
    _print_reach(record)
    if carry:
        gs = record["carried_from"]["growth"]
        print("since the previous plate: " + ", ".join(f"{k.replace('_mm', '')} {v['previous']} -> {v['new']} mm" for k, v in gs.items()
                                                      if isinstance(v, dict)) + (f"; tip moved {gs['tip_shift_mm']} mm" if gs.get("tip_shift_mm") else ""))
        for f in record["carried_from"]["flags"]:
            print("flag:", f)
    for n in lm_json["notes"]:
        print("note:", n)
    print(f"landmarks: arch depth {lm_json['arch_depth_mm']} mm, intertuberosity {lm_json['intertuberosity_width_mm']} mm, "
          f"cleft read by the scan analysis: {'yes' if cleft_info['scan_found_cleft'] else 'no'}")
    rp = info.get("root_pad") or {}
    if rp.get("area_mm2"):
        print(f"plate: root pad {rp['area_mm2']} mm2 added behind the old edge"
              + (" (" + ", ".join(f"{k} {v:.0%}" for k, v in rp["support"].items() if v) + ")" if rp.get("support") else ""))
    if rp.get("cut_back"):
        cb = rp["cut_back"]
        print(f"note: root pad cut back ({cb['full_pad_mm2']} mm2 at full size; {cb['note']})")
    print(f"done in {time.time() - t0:.1f} s -> {out}/design.json")


# ---------------------------------------------------------------- carry (next plate from an accepted baton)

def cmd_carry(a):
    """The next plate: a new scan, with the accepted baton of an earlier plate as the template (Tübingen)."""
    from argparse import Namespace
    from . import carry as cr
    prev = rec.load(a.design)
    if not a.device:
        sid, why = cr.suggest_device(prev)
        _fail("name the plate to carry with --device (which plate is the template is the clinical team's choice)"
              + (f"; the record suggests {sid}: {why}" if sid else f"; {why}"))
    try:
        spec = cr.carry_spec(a.design, a.device)
    except KeyError as e:
        _fail(e.args[0] if e.args else str(e))
    spec["prototypes"] = a.prototypes
    pp = spec["plate_params"]
    da = Namespace(scan=a.scan, out=a.out, final=a.final, plate_thickness=pp["thickness"], flange=pp["flange"],
                   block_out=pp["block_out"], plate_profile=pp.get("profile", "uniform"), thin_thickness=pp.get("thin_thickness", 1.5),
                   border_relief=pp.get("border_relief", False), final_thickness=spec["final_plate_thickness"],
                   cleft_threshold=a.cleft_threshold, extension=spec["mode_requested"], backend=spec["backend"],
                   cleft_section=None, effective_length=None, bend_radius=None, ext_thickness=None, width_base=None,
                   width_tip=None, tilt=None, curve=None, lateral=None,
                   # the cleft type is a clinical entry: the next plate keeps the one recorded on the earlier plate
                   cleft=(prev.get("cleft_status") or {}).get("value"),
                   cleft_entered_by=(prev.get("cleft_status") or {}).get("entered_by"),
                   posterior_bulge=pp.get("posterior_bulge"))
    lin = spec["from"]["lineage"]
    print(f"carrying {spec['from']['device']} ({' -> '.join(lin['chain'])}); bedside shortening so far "
          f"{lin['bedside_shortening_all_plates_mm']} mm; plate {spec['from']['generation']}")
    cmd_design(da, carry=spec)


def cmd_lineage(a):
    from . import carry as cr
    record = rec.load(a.design)
    did = a.device
    if not did:
        did, why = cr.suggest_device(record)
        if not did:
            _fail(f"name a device with --device ({why})")
        print(f"(no --device: showing {did}, {why})")
    try:
        lin = cr.lineage(record, did)
    except KeyError as e:
        _fail(e.args[0] if e.args else str(e))
    if a.json:
        print(json.dumps(lin, indent=2)); return
    print(f"{lin['device']}: {' -> '.join(lin['chain'])}")
    for st in lin["steps"]:
        print(f"  {st['from']} -> {st['to']} ({st['where']}): {st['changes'] or 'no geometry change'}")
    print(f"bedside shortening on this plate: {lin['bedside_shortening_total_mm']} mm; lab length change {lin['lab_length_change_mm']:+} mm")
    if lin.get("earlier_plates"):
        e = lin["earlier_plates"]
        print(f"carried from {e['from_device']} of {e['from_case']}; bedside shortening over all plates {lin['bedside_shortening_all_plates_mm']} mm")
    if lin["vs_standard"] is not None:
        print(f"against the standard ({lin['standard_mode']}): {lin['vs_standard'] or 'unchanged'}")


# ---------------------------------------------------------------- adjust (one change, by hand)

def _parse_mods(items: list[str], device: str) -> list[dict]:
    mods = []
    for it in items or []:
        code, _, amount = it.partition("=")
        code = code.strip()
        if code not in fit.MODIFICATIONS:
            _fail(f"unknown modification {code!r}; allowed: {', '.join(fit.MODIFICATIONS)}")
        if fit.MODIFICATIONS[code][0] == "flag":
            mods.append({"device": device, "code": code, "amount": None})
            continue
        try:
            mods.append({"device": device, "code": code, "amount": float(amount)})
        except ValueError:
            _fail(f"--mod {it!r}: expected code=number, e.g. shorten_mm=2")
    return mods


def cmd_adjust(a):
    """Apply an endoscopy finding as numbers: the digital version of the modification form."""
    case_dir = Path(a.design).parent
    record = rec.load(a.design)
    try:
        src = rec.device(record, a.from_id)
    except KeyError as e:
        _fail(e.args[0] if e.args else str(e))
    base = ExtensionParams(**src["params"])
    changes = {}
    # older absolute options, kept so earlier commands still work
    if a.shorten: changes["effective_length"] = base.effective_length - a.shorten
    if a.lengthen: changes["effective_length"] = base.effective_length + a.lengthen
    if a.tilt is not None: changes["tilt_deg"] = a.tilt
    if a.curve is not None: changes["distal_curve_deg"] = a.curve
    if a.ap is not None: changes["ap_offset"] = base.ap_offset + a.ap
    if a.lateral is not None: changes["lateral_offset"] = a.lateral
    if a.thickness is not None: changes["thickness"] = a.thickness
    if a.width_scale is not None:
        changes["width_base"] = base.width_base * a.width_scale
        changes["width_tip"] = base.width_tip * a.width_scale
    mods = _parse_mods(a.mod, src["id"])
    ep, _, flags = fit.apply_modifications(replace(base, **changes), mods)
    for f in flags:
        print("flag:", f)
    if ep == base:
        _fail("no change to make")
    where = "bedside" if a.bedside else a.where
    for m in mods:
        m["where"] = where
    try:
        e = build_variant(case_dir, record, src["id"], ep, mods, a.note, where, final=a.final)
    except ValueError as err:
        _fail(str(err))
    rec.save(record, a.design)
    _report_variant(e)


# ---------------------------------------------------------------- fitting (structured endoscopy try-in)

def cmd_fitting(a):
    if a.action == "form":
        txt = fit.printable_form()
        if a.out:
            Path(a.out).write_text(txt); print(f"wrote {a.out}")
        else:
            print(txt)
        return
    if a.design is None:
        _fail(f"fitting {a.action} needs DESIGN.json")
    case_dir = Path(a.design).parent
    record = rec.load(a.design)
    devices = rec.device_ids(record)
    if a.action == "template":
        txt = json.dumps(fit.template(record, devices), indent=2, ensure_ascii=False)
        if a.out:
            Path(a.out).write_text(txt); print(f"wrote {a.out}")
        else:
            print(txt)
        return
    if a.action == "log":
        if not a.form:
            _fail("fitting log needs --form FORM.json")
        form = json.loads(Path(a.form).read_text())
        form.pop("_help", None)
        errors = fit.validate(form, devices)
        if errors:
            for e in errors:
                print("invalid:", e, file=sys.stderr)
            sys.exit(2)
        for tr in form.get("trials") or []:
            tr["device"] = rec.normalise_id(tr["device"])
        dec = form.get("decision") or {}
        if dec.get("accepted_device"):
            dec["accepted_device"] = rec.normalise_id(dec["accepted_device"])
        mods = fit.real_modifications(form)
        dec["modifications"] = mods
        flags = fit.flags(form, record["clinical_settings"])
        n = len(record["fitting_sessions"]) + 1
        by_dev: dict[str, list] = {}
        for m in mods:
            by_dev.setdefault(m["device"], []).append(m)
        built, cache = [], {}
        for dev, ms in by_dev.items():
            base = ExtensionParams(**rec.device(record, dev)["params"])
            ep, _, fl = fit.apply_modifications(base, ms)
            flags += [f"{dev}: {x}" for x in fl]
            if any(m["code"] == "lengthen_mm" for m in ms):
                flags.append(f"{dev}: lengthening needs a new prototype and a second endoscopy (Tübingen)")
            if ep == base:
                continue
            where = "lab" if any(m["where"] == "lab" for m in ms) else "bedside"
            finding = ", ".join(sorted({m["finding"] for m in ms if m.get("finding")})) or None
            try:
                e = build_variant(case_dir, record, dev, ep, ms, f"fitting session {n}", where, finding=finding, session=n, cache=cache)
            except ValueError as err:
                _fail(f"{dev}: {err}")
            built.append(e["id"])
            _report_variant(e)
        record["fitting_sessions"].append({"n": n, "logged": rec.now(), "form_file": str(a.form), "form_sha256": rec.file_sha256(a.form),
                                           "session": form.get("session"), "baseline": form.get("baseline"),
                                           "trials": form.get("trials") or [], "decision": dec, "flags": flags,
                                           "variants_built": built})
        rec.save(record, a.design)
        s = form["session"]
        print(f"fitting session {n} logged: day {s['day_of_life']}, {s['sedation']}, {len(form.get('trials') or [])} device(s) tried, "
              f"accepted {dec.get('accepted_device') or '-'}, built {', '.join(built) or 'nothing'}")
        for f in flags:
            print("flag:", f)
        return
    if a.action == "measure":
        from .measure import compare
        if not (a.device and a.scan):
            _fail("fitting measure needs --device and --scan")
        d = rec.device(record, a.device)
        ref = trimesh.load(case_dir / d["file"], force="mesh")
        scan = trimesh.load(a.scan, force="mesh")
        ep = ExtensionParams(**d["params"])
        border_y, z_mid = record["attachment"]["posterior_border_y"], record["attachment"]["z_mid"]
        from .extension import extension_path
        path = extension_path(ep, border_y, z_mid)
        s = arclength(path)
        root_s = float(s[int(np.argmin(np.abs(path[:, 1] - (border_y + ep.ap_offset))))])
        res, aligned = compare(ref, scan, align=not a.no_align, path=path, root_s=root_s, border_y=border_y)
        res.update({"device": d["id"], "scan_file": str(a.scan), "scan_sha256": rec.file_sha256(a.scan), "time": rec.now()})
        r, dv, tip = res["registration"], res["deviation"]["scan_to_design"], res["tip"]
        print(f"{d['id']} vs scan: registration {r.get('start')} (residual to samples {r.get('residual_to_samples_mm', '-')} mm); "
              f"tip shortened {tip['shortened_mm']:+.2f} mm")
        print(f"  scan to design: mean {dv['mean_abs_mm']} mm, p95 {dv['p95_abs_mm']} mm, within 0.25 mm {100 * dv['within_0.25_mm']:.0f}%")
        _print_regions(res["deviation"]["scan_to_design"])
        for sec in res.get("sections", []):
            print(f"  {sec['s_after_root_mm']:5.1f} mm after root: width {sec['design']['width_mm']} -> {sec['scan']['width_mm']} mm, "
                  f"thickness {sec['design']['thickness_mm']} -> {sec['scan']['thickness_mm']} mm")
        if a.out:
            Path(a.out).write_text(json.dumps(res, indent=2)); print(f"wrote {a.out}")
        if a.apply:
            amt = round(tip["shortened_mm"], 1)
            if amt < 0.2:
                _fail(f"measured shortening {tip['shortened_mm']:+.2f} mm is below 0.2 mm; nothing applied")
            mods = [{"device": d["id"], "code": "shorten_mm", "amount": amt, "where": "bedside", "finding": None,
                     "measured_from_scan": res["scan_sha256"]}]
            ep_new, _, _ = fit.apply_modifications(ep, mods)
            e = build_variant(case_dir, record, d["id"], ep_new, mods, a.note or f"bedside shortening measured from scan {Path(a.scan).name}",
                              "bedside")
            e["measurement"] = {k: res[k] for k in ("tip", "sections", "scan_sha256") if k in res}
            rec.save(record, a.design)
            _report_variant(e)
        return
    _fail(f"unknown fitting action {a.action}")


# ---------------------------------------------------------------- sleep study

def cmd_sleep(a):
    case_dir = Path(a.design).parent
    record = rec.load(a.design)
    plate_in = a.plate == "in"
    did, sha = None, None
    if plate_in:
        if not a.device:
            _fail("--device is required when the plate was in")
        try:
            d = rec.device(record, a.device)
        except KeyError as e:
            _fail(e.args[0] if e.args else str(e))
        did = d["id"]
        f = case_dir / d["file"]
        sha = rec.file_sha256(f) if f.exists() else None
    try:
        e = fit.sleep_entry(record, did, a.day, a.kind, a.index, a.value, plate_in, a.days_since_removal, a.scoring, a.threshold, sha)
    except ValueError as err:
        _fail(str(err))
    record["sleep_studies"].append(e)
    rec.save(record, a.design)
    tgt = ("no target set for this index" if e["target_below_per_h"] is None else
           f"{'below' if e['meets_target'] else 'not below'} the team's target of {e['target_below_per_h']}/h")
    print(f"sleep study {e['n']}: day {e['day_of_life']}, {e['kind']}, {e['index']} {e['value_per_h']}/h, "
          f"plate {'in (' + did + ')' if plate_in else 'out'}: {tgt}")


# ---------------------------------------------------------------- bench measurement

def _print_regions(dv: dict, indent: str = "    "):
    for k, v in (dv.get("regions") or {}).items():
        print(f"{indent}{k:<12} mean {v['mean_abs_mm']} mm, p95 {v['p95_abs_mm']} mm, signed mean {v['mean_signed_mm']:+.3f} mm, "
              f"within 0.25 mm {100 * v['within_0.25_mm']:.0f}%  (n={v['n_points']})")


def cmd_measure(a):
    from .measure import compare
    ref = trimesh.load(a.design, force="mesh")
    scan = trimesh.load(a.scan, force="mesh")
    border_y = rec.load(a.record)["attachment"]["posterior_border_y"] if a.record else None
    res, aligned = compare(ref, scan, align=not a.no_align, border_y=border_y)
    res.update({"design_file": str(a.design), "design_sha256": rec.file_sha256(a.design),
                "scan_file": str(a.scan), "scan_sha256": rec.file_sha256(a.scan), "time": rec.now()})
    r = res["registration"]
    print(f"registration: {r.get('start')} (residual to samples {r.get('residual_to_samples_mm', '-')} mm)")
    for k, v in res["deviation"].items():
        print(f"  {k.replace('_', ' ')}: mean {v['mean_abs_mm']} mm, p95 {v['p95_abs_mm']} mm, max {v['max_abs_mm']} mm, "
              f"signed mean {v['mean_signed_mm']:+.3f} mm, within 0.1/0.25/0.5 mm "
              f"{100 * v['within_0.1_mm']:.0f}/{100 * v['within_0.25_mm']:.0f}/{100 * v['within_0.5_mm']:.0f}%")
        _print_regions(v)
    if a.out:
        Path(a.out).write_text(json.dumps(res, indent=2)); print(f"wrote {a.out}")
    if a.aligned:
        aligned.export(a.aligned); print(f"wrote {a.aligned}")


# ---------------------------------------------------------------- manufacturing and viewing

def cmd_polyjet(a):
    from .polyjet import export, PolyJetParams
    out = export(Path(a.dir), profile=a.profile, params=PolyJetParams(skin_mm=a.skin, tip_mm=a.tip, rim_mm=a.rim, posterior_mm=a.posterior),
                 proto_number=a.proto, orientation=a.orientation, device_id=a.device)
    man = json.loads((out / "manifest.json").read_text())
    v = man["validation"]
    print(f"wrote {out}: {len(man['bodies'])} bodies -> assembly.3mf, manifest.json, PRINT_NOTES.md")
    for n, b in man["bodies"].items():
        print(f"  {n:22s} {b['material']:36s} {b['volume_mm3']:8.1f} mm3  watertight={v['bodies'][n]['watertight']}")
    print(f"  validation ok={v['ok']} overlaps={v['pairwise_overlap_mm3'] or 'none'} volume mismatch={v['volume_mismatch_mm3']} mm3")


def cmd_mold(a):
    from .mold import build, MoldParams
    out = build(Path(a.dir), MoldParams(final_plate_thickness=a.final_thickness, wall=a.wall, proto=a.proto, device=a.device))
    man = json.loads((out / "mold_manifest.json").read_text()); v = man["validation"]
    print(f"wrote {out}: " + ", ".join(man["files"].values()))
    print(f"  device {man['device']}; block {v['block_mm']} mm, cavity {v['cavity_volume_mm3']} mm3 (appliance {v['device_volume_mm3']}), "
          f"mix about {man['pmma_needed_ml']} ml PMMA")
    print(f"  watertight {v['watertight']}")
    print(f"  bodies {v['body_counts']}")
    print(f"  piece overlaps {v['piece_overlaps_mm3']}  appliance inside pieces {v['device_inside_piece_mm3']}  (tolerance {v['tolerance_mm3']} mm3)")
    print(f"  cavity pieces {v['cavity_pieces_mm3']}, appliance outside cavity {v['device_outside_cavity_mm3']}, pins vs wire {v['pins_clear_of_wire_mm3']}, wire outside appliance {v['wire_inside_device_mm3']}")
    for d in v["demold"]:
        print(f"  demold {d['piece']:<15} pull {d['pull_direction_pour_frame']}: overlap {d['overlap_mm3_at_mm']} free={d['free']}")
    w = man["features"]["safety_wire"]
    print(f"  safety wire {w['length_mm']} mm: {w['starts_mm_after_border']} mm after the root to {w['ends_mm_before_tip']} mm before the tip")
    print(f"  final-plate QC {v['final_qc']['overall']}")
    _print_qc(v["final_qc"], "    ")
    print(f"  ok={v['ok']}" + (f"; registered as {man['registered_as']}" if man.get("registered_as") else "; not registered"))
    from .mold_viewer import write_mold_viewer
    print(f"  viewer: {write_mold_viewer(out)}")
    for n in man["notes"]:
        print("  note:", n)


def cmd_print(a):
    from .directprint import build, PrintParams, compare_routes, RESINS
    if a.routes:
        for r in compare_routes():
            print(f"{r['route']}: {r['title']}")
            for x in r["pros"]:
                print(f"  + {x}")
            for x in r["cons"]:
                print(f"  - {x}")
        print("resins: " + ", ".join(RESINS))
        return
    if not a.dir:
        _fail("give a case folder, or --routes")
    routes = ["pause", "lid", "multimaterial"] if a.route == "all" else [a.route]
    for route in routes:
        resin = a.resin or ("truedent" if route == "multimaterial" else "dental-lt-clear-v2")
        out = build(Path(a.dir), PrintParams(route=route, final_plate_thickness=a.final_thickness, layer_mm=a.layer,
                                             lid_side=a.lid_side, resin=resin, proto=a.proto, device=a.device,
                                             skin_mm=a.skin, skin_mode=a.skin_mode, skin_material=a.skin_material,
                                             wire=not a.no_wire))
        man = json.loads((out / "print_manifest.json").read_text()); v = man["validation"]
        print(f"wrote {out}: " + ", ".join(man["files"].values()))
        print(f"  route {route}: {man['route_info']['title']}; device {man['device']}; resin {man['resin']['name']}; about {man['resin_ml']} ml")
        if man["features"].get("pause"):
            p = man["features"]["pause"]
            print(f"  pause after layer {p['pause_after_layer']} ({p['pause_height_mm']} mm above the part's lowest point, {p['layer_mm']} mm layers)")
        if "lid" in man["features"]:
            print(f"  lid {man['features']['lid']['lid_volume_mm3']} mm3, slot opens toward the {man['features']['lid']['opens_toward']}")
        if "skin" in man["features"]:
            k = man["features"]["skin"]
            print(f"  skin ({k['mode']}) {k['skin_mm']} mm of {k['material']}: rigid core {k['rigid_core_thickness_mm']} mm, outer {k['outer_baton_thickness_mm']} mm, "
                  f"core stiffness x{k['core_bending_stiffness_vs_designed']}, skin tether if the core snaps about {k['skin_tether_newtons_if_core_snaps']} N")
        w = man["safety_wire"]
        print(f"  safety wire {w['length_mm']} mm: {w['starts_mm_after_root']} mm after the root to {w['ends_mm_before_tip']} mm before the tip; "
              f"min resin cover {v['wire_cover']['min_mm']} mm")
        for k, ok in v["checks"].items():
            print(f"  {'PASS' if ok else 'FAIL'} {k}")
        for x in v["warnings"]:
            print(f"  warning: {x}")
        print(f"  final-plate QC {v['final_qc']['overall']}; ok={v['ok']}" + (f"; registered as {man['registered_as']}" if man["registered_as"] else "; not registered"))


def cmd_make(a):
    from .make import Kit, KITS, plan, run, write
    if a.kits:
        for k, v in KITS.items():
            print(f"{k:12s} {v['title']}")
        return
    if not a.dir:
        _fail("give a case folder, or --kits")
    kit = Kit(printer=a.kit, pressure_pot=not a.no_pressure_pot, direct_print_validated_by=a.direct_print_validated_by,
              direct_print_validated_on=a.direct_print_validated_on)
    try:
        p = plan(Path(a.dir), kit, a.stage, a.device)
    except KeyError as err:
        _fail(str(err))
    if a.build:
        p = run(p, with_fallback=a.with_fallback)
    out = write(p)
    print(f"{a.stage} stage on {kit.printer}: route {p['route']} (fallback {p['fallback'] or 'none'}), device {p['device']} ({p['device_why']})")
    for w in p["why"]:
        print(f"  because {w}")
    for b in p["blockers"]:
        print(f"  needed: {b}")
    for f in p["flags"]:
        print(f"  flag: {f}")
    for i, s in enumerate(p["steps"], 1):
        print(f"  {i}. {s['title']} [{s['who']}, {s['time']}]" + (f": {s['command']}" if s["command"] else ""))
    for b in p["built"] or []:
        print(f"  built {b['route']}: ok={b['ok']}" + (f", registered as {b['registered_as']}" if b.get("registered_as") else ""))
    print(f"wrote {out / 'MAKE_PLAN.md'} and make_plan.json ({len(p['gates'])} QC gates)")
def cmd_catalog(a):
    from . import catalog as catm
    case = Path(a.case)
    cat = catm.load(case)
    if a.build or cat is None:
        if not (case / "design.json").exists():
            _fail(f"no design record in {case}: run `pebp design` first")
        print("building every base and baton for this patient (synthetic cases only in the cloud; real scans stay on this computer)")
        cat = catm.build(case)
        print(f"catalog: {cat['counts']['bases']} bases, {cat['counts']['batons']} batons, {cat['counts']['versions']} versions "
              f"-> {case / 'catalog' / 'catalog.json'}")
    ct = a.cleft
    if ct is None:
        ct = cat.get("entered_cleft_type")
        if ct:
            print(f"cleft type from the case: {ct} (pass --cleft any to see every group)")
    try:
        res = catm.narrow(cat, cleft_type=ct, plate_profile=a.profile, border_relief=a.relief,
                          d_length_mm=a.length, d_angle_deg=a.angle, passing_only=a.passing)
    except ValueError as e:
        _fail(str(e))
    print(f"{res['count']} of {len(cat['versions'])} versions left")
    for k, counts in res["open"].items():
        if res["chosen"][k] is None:
            print(f"  {k}: " + ", ".join(f"{v} ({n})" for v, n in counts.items()))
    if res["count"] <= a.show:
        for v in res["versions"]:
            print(f"  {v['id']:<14} {v['baton_variant']:<8} {v['plate_profile']:<8} relief {'on ' if v['border_relief'] else 'off'} "
                  f"{v['code']:<10} tip shift y {v['tip_shift_vs_standard_mm']['y_anterior']:+.1f} z {v['tip_shift_vs_standard_mm']['z_superior']:+.1f}  QC {v['qc']['overall']}")
    if a.assemble:
        print("assembled for viewing:", catm.assemble(case, cat, a.assemble))
    if a.json:
        Path(a.json).write_text(json.dumps({k: v for k, v in res.items() if k != "batons"}, indent=1, default=str))
    if a.make:
        from .catalog_make import make
        try:
            plan = make(case, cat, a.make)
        except (ValueError, RuntimeError) as e:
            _fail(str(e))
        print(f"plan {'ok' if plan['ok'] else 'with failures'}: {case / 'catalog' / 'make' / a.make / 'PLAN.md'}")
        if not plan["ok"]:
            sys.exit(1)
    if a.viewer:
        from . import catalog_viewer
        out = catalog_viewer.write(case, cat)
        print(f"viewer ({out.stat().st_size / 1e6:.1f} MB, opens offline in any browser): {out}")


def cmd_kit(a):
    from .kit import KitParams, build, export_stock, plug_strength
    from .extension import tuebingen_params
    nums = lambda t: tuple(float(v) for v in t.split(",") if v.strip())
    kp = KitParams(variant=a.variant, lengths=nums(a.lengths), angles=nums(a.angles), width=a.width)
    if a.width < 5.2:
        _fail("kit width below half the published 10.4 mm (Tübingen: never narrowed by more than half)")
    if a.stock:
        man = export_stock(Path(a.stock), kp)
        print(f"wrote {len(man['batons'])} stock batons ({kp.variant}) to {a.stock}: "
              + ", ".join(f"{b['id']} {b['code']}" for b in man["batons"]))
        bad = [b["id"] for b in man["batons"] if not b["watertight"] or b["bodies"] != 1]
        print("  all watertight, one body each" if not bad else f"  NOT printable: {bad}")
        print("  flag:", man["flag"])
        return
    if not a.dir:
        _fail("give a case folder, or --stock DIR")
    try:
        kit = build(Path(a.dir), kp, stock=not a.no_stock)
    except ValueError as e:
        _fail(str(e))
    s = kit["socket"]
    print(f"base: {a.dir}/kit/base.stl; socket block {s['block_mm']['width']} mm wide, {s['block_mm']['below_plate_tongue_surface']} mm "
          f"below the plate; tunnel {s['tunnel_mm']}; plate above the tunnel at least {s['roof_min_mm']} mm")
    ps = kit["plug_strength_vs_baton"]
    print(f"plug bending section {ps['plug_mm3']} mm3 vs the baton's {ps['baton_mm3']} mm3 ({ps['ratio']:.0%})")
    for e in kit["batons"]:
        sh = e.get("tip_shift_vs_standard_mm", {})
        print(f"{e['id']:>4} {e['code']:<10} tip {np.round(e['tip'], 1).tolist()}  shift y {sh.get('y_anterior', 0):+.1f} z {sh.get('z_superior', 0):+.1f}  "
              f"arm {e['arm_length_mm']} mm  QC {e['qc']['overall']}")
        _print_qc(e["qc"], "       ")
    env = kit["tip_envelope"]
    print(f"kit tip envelope: {env['tip_y_range_mm']} mm front to back (P1-P3: {env['prototype_set_tip_y_range_mm']}), {env['tip_z_range_mm']} mm up and down")
    print("flag:", kit["flag"])
    print(f"the chosen baton goes to the cast plate with: python -m pebp mold {a.dir} --device K<n>")


def _kit_params_from(a):
    from .kit import KitParams
    return KitParams(variant=a.variant)


def cmd_chairside(a):
    from .chairside import build, build_base, ChairsideParams
    cp = ChairsideParams(final_thickness=a.final_thickness)
    if not 2.5 <= a.final_thickness <= 3.0:
        _fail("final plate thickness must be 2.5-3 mm (Tübingen 2025)")
    modes = sum(bool(x) for x in (a.base, a.kit, a.stock_tooling))
    if modes != 1:
        _fail("give exactly one of --base, --kit K<n> or --stock-tooling DIR")
    if a.stock_tooling:
        from .chairside_tooling import export_stock_tooling
        ids = [rec.normalise_id(v) for v in a.only.split(",")] if a.only else None
        man = export_stock_tooling(Path(a.stock_tooling), _kit_params_from(a), cp, ids)
        for b in man["batons"]:
            print(f"{b['id']:>4} {b['code']:<10} mold {'ok' if b['ok'] else 'FAILED'}  PMMA {b['cast_volume_mm3']} mm3  wire {b['wire_length_mm']} mm")
        print("  flag:", man["flag"])
        if not all(b["ok"] for b in man["batons"]):
            _fail("some stock tooling failed its checks (see manifest.json in each folder)", 1)
        return
    if not a.dir:
        _fail("give a case folder")
    try:
        if a.base:
            e = build_base(Path(a.dir), cp=cp)
            print(f"final base: {a.dir}/{e['file']} ({e['volume_mm3']} mm3, {e['final_plate_thickness']} mm plate); "
                  f"plate over tunnel {e['socket']['roof_min_mm']} mm, over pocket {e['pocket_roof_mm']} mm; QC {e['qc']['overall']}")
            _print_qc(e["qc"])
            print(f"  casting tooling: {', '.join(e['tooling']['files'].values())}; about {e['tooling']['pmma_needed_ml']} ml PMMA")
            print("  flag:", e["flag"])
            if e["qc"]["overall"] == "fail":
                sys.exit(1)
            return
        e = build(Path(a.dir), a.kit, cp=cp)
    except ValueError as err:
        _fail(str(err))
    w = e["wire"]
    print(f"{e['id']} from {e['from']} ({e['code']}): wire {w['length_mm']} mm, {w['crosses_joint_by_mm']} mm across the joint into the pocket, "
          f"ends {w['end_mm_before_tip']} mm before the tip; pick-up resin {e['pickup_volume_mm3']} mm3; QC {e['qc']['overall']}")
    _print_qc(e["qc"])
    print("  files: " + ", ".join(e["files"].values()))
    print("  flag:", e["flag"])
    if e["qc"]["overall"] == "fail":
        sys.exit(1)


def cmd_bench(a):
    from .bench import build
    from .chairside import ChairsideParams
    try:
        man = build(Path(a.out), _kit_params_from(a), ChairsideParams(), baton=rec.normalise_id(a.baton))
    except StopIteration:
        _fail(f"no kit baton {a.baton}")
    for k, v in man["validation"].items():
        print(f"  {'PASS' if v else 'FAIL'}  {k}")
    for t in man["tests"]:
        print(f"  test {t['n']}: {t['test']}: {t['specimens']}")
    print(f"wrote {len(man['files'])} files to {a.out}; protocol: docs/CHAIRSIDE_BENCH.md")
    print("  flag:", man["flag"])
    if not man["ok"]:
        sys.exit(1)


def _print_reach(record: dict) -> None:
    flagged = [c for c in record["reach_check"]["checks"] if not c["within"]]
    print(f"airway: {len(record['tpp_metrics'])} device(s) in the Bioengineering 2022 scheme; reach check "
          f"{len(record['reach_check']['checks']) - len(flagged)}/{len(record['reach_check']['checks'])} within the published envelope "
          "(plausibility only, no clinical claim)")
    for c in flagged:
        print(f"  outside: {c['check']}: {c['value']} vs {c['reference']} ({c['margin_mm']:+.2f} mm)")


def cmd_airway(a):
    """Airway landmark vocabulary, plate metrics and reach check (experiment 1)."""
    from . import airway
    case_dir = Path(a.design).parent
    record = rec.load(a.design)
    for item in a.set or []:
        name, _, val = item.partition("=")
        name = name.strip()
        try:
            if val.strip().lower() in ("", "none", "null"):
                value = None
            elif airway.LANDMARKS.get(name, ("scalar",))[0] == "point":
                value = [float(v) for v in val.split(",")]
            else:
                value = float(val)
            airway.set_landmark(record, name, value, a.source, a.note)
        except (KeyError, ValueError) as e:
            _fail(e.args[0] if e.args else str(e))
    airway.update(record, case_dir)
    (case_dir / "airway_report.md").write_text(airway.report_md(record))
    rec.save(record, a.design)
    _print_reach(record)
    for k, v in record["airway"]["derived"].items():
        print(f"derived {k}: {v}")
    print(f"wrote {case_dir / 'airway_report.md'}")


def cmd_viewer(a):
    from .viewer import write_viewer
    path = write_viewer(Path(a.dir))
    print(f"wrote {path}")


def cmd_studio(a):
    from .studio.server import serve
    serve(Path(a.workspace), port=a.port, open_browser=not a.no_browser)


def cmd_app(a):
    from .studio import desktop
    if a.install:
        if sys.platform != "darwin":
            _fail("--install makes a macOS .app; on other systems run `python -m pebp app` or `python -m pebp studio`")
        home = Path(a.home).expanduser() if a.home else desktop.DEFAULT_HOME
        if desktop.icloud_desktop_synced() and not a.home:
            home = Path.home() / desktop.APP_NAME
            print(f"Desktop syncs to iCloud on this Mac, so the starter folder goes in {home} instead "
                  "(real scans must not leave this computer).")
        app = desktop.install_app(Path(a.dest), home / "Cases")
        desktop.make_starter(home, app)
        print(f"installed {app}\nstarter folder {home}\nopen it with: open {desktop._q(app)}")
        return
    ws = Path(a.workspace).expanduser() if a.workspace else desktop.DEFAULT_HOME / "Cases"
    raise SystemExit(desktop.run_app(ws, port=a.port, debug=a.debug))


def cmd_selftest(a):
    from .deform import selftest
    rows = selftest(verbose=False)
    for r in rows:
        print(f"  {r['case']:<30} mean {r['mean_mm']:.3f}  p95 {r['p95_mm']:.3f}  max {r['max_mm']:.3f}  "
              f"tip-region max {r['tip_region_max_mm']:.3f} mm  (change at tip {r['change_at_tip_max_mm']:.2f} mm)  watertight {r['watertight']}")
    worst = max(r["max_mm"] for r in rows)
    print(f"deformer vs exact parametric answer: worst max {worst:.3f} mm -> {'pass' if worst < 0.25 and all(r['watertight'] for r in rows) else 'FAIL'}")


def cmd_frames(a):
    from . import framequality as fq
    if a.action == "fetch":
        dest = Path(a.out or "data/ibsu1432")
        try:
            src = fq.fetch_zenodo(dest, a.record, api_url=a.api_url)
        except ValueError as e:
            _fail(f"download rejected: {e}")
        except OSError as e:
            _fail(f"download failed ({e}). check the network connection to zenodo.org.")
        print(f"{src['title']} ({src['doi']}), licence {src['license']}: {len(src['files'])} file(s) in {dest}/raw, frames in {dest}/frames")
        if not src["commercial_use_ok"]:
            print(f"warning: licence '{src['license']}' is not plain CC BY; check it before building anything on it", file=sys.stderr)
        return
    if a.action == "synth":
        if not a.out:
            _fail("synth needs --out")
        c = fq.write_synthetic_set(Path(a.out), a.n, a.seed, short_names=a.short_names)
        print(f"wrote {sum(c.values())} synthetic frames to {a.out}")
        return
    if a.action == "classify":
        if not (a.model and a.paths):
            _fail("classify needs --model and image files or folders")
        paths = []
        for q in map(Path, a.paths):
            paths += sorted(p for p in q.rglob("*") if p.suffix.lower() in fq.IMAGE_EXT) if q.is_dir() else [q]
        out = Path(a.out or "frame_quality.csv")
        r = fq.classify_paths(fq.load_model(a.model), paths, out, a.threshold)
        print(f"{r['n']} frames, {r['kept']} kept (p_informative >= {r['threshold']}); {r['by_label']} -> {out}")
        return
    if not a.paths or len(a.paths) != 1:
        _fail(f"{a.action} needs one labelled data folder")
    data = Path(a.paths[0])
    ds = fq.scan_dataset(data, a.group_regex)
    if len(ds["y"]) == 0:
        _fail(f"no labelled frames under {data} (class comes from folder names like informative/ or I/)")
    if ds["skipped"]:
        print(f"note: {len(ds['skipped'])} image(s) without a class label skipped, e.g. {ds['skipped'][0]}")
    if ds["groups"] is not None:
        unmatched = sum(g == "?" for g in ds["groups"])
        if unmatched:
            _fail(f"--group-regex matched none of {unmatched} frame path(s), e.g. {ds['paths'][ds['groups'].index('?')].relative_to(data)}")
        if len(set(ds["groups"])) < a.folds:
            _fail(f"only {len(set(ds['groups']))} groups for {a.folds} folds; lower --folds or fix --group-regex")
    synthetic = any(data.rglob("SYNTHETIC.txt"))
    srcf = next((p for p in [data / "SOURCE.json", data.parent / "SOURCE.json", data.parent.parent / "SOURCE.json"] if p.exists()), None)
    src = json.loads(srcf.read_text()) if srcf else {}
    desc = ("synthetic frames (code test)" if synthetic else
            f"{src.get('title') or data.name} ({src.get('doi', 'no DOI')}, licence {src.get('license', 'unknown')})")
    prov = {"data": str(data), "description": desc, "synthetic": synthetic, "doi": src.get("doi"), "license": src.get("license"),
            "creators": src.get("creators"), "n_frames": int(len(ds["y"]))}
    out = Path(a.out or ("out/frames_" + a.action))
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    X = fq.feature_matrix(ds["paths"])
    if a.action == "train":
        m = fq.cross_validate(X, ds["y"], a.folds, a.seed, ds["groups"], a.l2, a.threshold or 0.5)
        model = fq.train(X, ds["y"], a.l2, a.threshold or 0.5, prov)
        fq.save_model(model, out / "frame_quality_model.json")
        title = "Frame-quality filter: cross-validated baseline"
    else:
        if not a.model:
            _fail("eval needs --model")
        model = fq.load_model(a.model)
        m = fq.metrics(ds["y"], fq.predict_proba(model, X), a.threshold or model.get("keep_threshold", 0.5))
        m["model"] = str(a.model); m["model_provenance"] = model.get("provenance")
        title = "Frame-quality filter: held-out evaluation"
    m["source"] = prov; m["seconds"] = round(time.time() - t0, 1)
    (out / "metrics.json").write_text(json.dumps(m, indent=1))
    (out / "report.md").write_text(fq.report_md(m, title, prov))
    fq.plot_confusion(m, out / "confusion.png", title + (" (synthetic)" if synthetic else ""))
    print(f"{a.action}: {m['n']} frames, accuracy {m['accuracy']:.3f}, macro F1 {m['macro_f1']:.3f} -> {out}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pebp", description="PEBP Designer demo")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("synth", help="make a synthetic neonatal cleft maxilla scan")
    s.add_argument("--out", required=True); s.add_argument("--unoriented", action="store_true")
    s.add_argument("--asym", type=float, default=0.0); s.add_argument("--no-cleft", action="store_true")
    s.add_argument("--seed", type=int, default=7)
    s.add_argument("--preset", choices=["default", "prs"], default="default",
                   help="prs: published neonatal Robin-sequence palate morphology (Willershausen 2024)")
    s.add_argument("--sd", type=float, default=0.0, help="with --preset prs: shift the published means by this many SDs")
    s.set_defaults(fn=cmd_synth)

    d = sub.add_parser("design", help="scan -> base plate + 3 prototypes (+ final with wire groove)")
    d.add_argument("scan"); d.add_argument("--out", required=True); d.add_argument("--final", action="store_true")
    d.add_argument("--plate-thickness", type=float, default=2.0, help="prototype plate, mm")
    d.add_argument("--final-thickness", type=float, default=2.5, help="final plate, mm (Tübingen: 2.5-3)")
    d.add_argument("--flange", type=float, default=3.0, help="rim beyond the ridge crest, mm (over scanned tissue only)")
    d.add_argument("--plate-profile", choices=["uniform", "graded"], default="uniform",
                   help="uniform (default, Tübingen: one thickness everywhere) or graded (project heuristic: full thickness "
                        "only at the baton root, over the cleft and at the bow-wire tags; thinner elsewhere)")
    d.add_argument("--thin-thickness", type=float, default=1.5, help="graded: thinnest wall, mm (floor 1.5)")
    d.add_argument("--border-relief", action="store_true",
                   help="project heuristic: cap the vestibular flange, notch the labial frenulum, relieve the tuberosities")
    d.add_argument("--block-out", type=float, default=2.5); d.add_argument("--cleft-threshold", type=float, default=2.0)
    d.add_argument("--cleft", default=None, choices=["hard-soft", "soft-only", "none", "hard_soft", "soft_only"],
                   help="cleft type from the examination (clinical entry): hard-soft = cleft of the hard and soft palate "
                        "(Tübingen cleft baton, cleft block-out); soft-only = soft palate or uvula only, or none "
                        "(Tübingen no-cleft baton; any gap bridged as a scan hole). Not given: the scan analysis decides, as before")
    d.add_argument("--cleft-entered-by", default=None, help="name or initials of whoever entered the cleft type")
    d.add_argument("--posterior-bulge", type=float, default=None,
                   help="mm the posterior border bulges back at the midline, where the baton root leaves the plate "
                        "(project heuristic; default 2 with a cleft of the soft palate or no entry, 0 with an intact velum)")
    d.add_argument("--extension", default="auto",
                   help="auto (default: the published Tübingen extension, cleft or no-cleft variant to match the palate), "
                        "tuebingen-cleft, tuebingen-nocleft, parametric (swept model calibrated to the Tübingen drawing), "
                        "or a path to an STL")
    g = d.add_argument_group("extension geometry (default: the chosen extension's own; a value changes it)")
    g.add_argument("--effective-length", type=float); g.add_argument("--ext-thickness", type=float)
    g.add_argument("--width-base", type=float); g.add_argument("--width-tip", type=float)
    g.add_argument("--tilt", type=float, help="deg; + swings the tail toward the tongue base")
    g.add_argument("--curve", type=float, help="deg of distal curve ('C-shape')")
    g.add_argument("--lateral", type=float, help="mm, + toward the patient's right")
    g.add_argument("--cleft-section", type=float, help="parametric only"); g.add_argument("--bend-radius", type=float, help="parametric only")
    d.add_argument("--backend", choices=["builtin", "moldstudio"], default="builtin",
                   help="landmark backend: builtin single-arch detector, or MoldStudio's segment-aware classifier")
    d.set_defaults(fn=cmd_design)

    c = sub.add_parser("carry", help="next plate: a new scan with an earlier plate's accepted baton as template (registers T)")
    c.add_argument("design", help="design.json of the previous plate's case"); c.add_argument("scan", help="the new intraoral scan")
    c.add_argument("--out", required=True); c.add_argument("--device", help="the accepted device to carry (e.g. C1, A2, P2)")
    c.add_argument("--prototypes", action="store_true", help="also make P1-P3 centred on the carried baton (-2, 0, +2 mm)")
    c.add_argument("--final", action="store_true", help="also make the groove final F")
    c.add_argument("--cleft-threshold", type=float, default=2.0)
    c.set_defaults(fn=cmd_carry)

    ln = sub.add_parser("lineage", help="a device's history: every change since its prototype and the bedside-shortening total")
    ln.add_argument("design"); ln.add_argument("--device"); ln.add_argument("--json", action="store_true")
    ln.set_defaults(fn=cmd_lineage)

    j = sub.add_parser("adjust", help="apply one change to a device and log it as A<n>")
    j.add_argument("design")
    j.add_argument("--from", dest="from_id", required=True, help="device id (P1-P3, A<n>, F, C<n>; a bare number means P<n>)")
    j.add_argument("--mod", action="append", metavar="CODE=AMOUNT",
                   help="a change in the fitting-form vocabulary, e.g. shorten_mm=2, angle_deg=5, curve_deg=10 (repeatable): "
                        + ", ".join(fit.MODIFICATIONS))
    j.add_argument("--bedside", action="store_true", help="the change was made to the physical device at the bedside")
    j.add_argument("--where", choices=["lab", "bedside"], default="lab")
    j.add_argument("--note", default=""); j.add_argument("--final", action="store_true", help="lab final with a wire groove")
    old = j.add_argument_group("older options (absolute values except --ap, --shorten, --lengthen)")
    old.add_argument("--shorten", type=float, default=0.0); old.add_argument("--lengthen", type=float, default=0.0)
    old.add_argument("--tilt", type=float); old.add_argument("--curve", type=float); old.add_argument("--ap", type=float)
    old.add_argument("--lateral", type=float); old.add_argument("--thickness", type=float); old.add_argument("--width-scale", type=float)
    j.set_defaults(fn=cmd_adjust)

    f = sub.add_parser("fitting", help="structured endoscopy try-in: blank template, printable form, log a session, measure a ground device")
    f.add_argument("action", choices=["template", "form", "log", "measure"])
    f.add_argument("design", nargs="?"); f.add_argument("--form", help="filled-in form JSON (log)")
    f.add_argument("--out"); f.add_argument("--device", help="device id (measure)"); f.add_argument("--scan", help="scan of the device (measure)")
    f.add_argument("--apply", action="store_true", help="measure: log the measured shortening as a bedside change")
    f.add_argument("--no-align", action="store_true", help="measure: the scan is already in the design frame")
    f.add_argument("--note", default="")
    f.set_defaults(fn=cmd_fitting)

    sl = sub.add_parser("sleep", help="log a sleep study against the device that was in the mouth")
    sl.add_argument("design"); sl.add_argument("--device"); sl.add_argument("--day", type=int, required=True, help="day of life")
    sl.add_argument("--kind", choices=fit.SLEEP_KINDS, default="polygraphy")
    sl.add_argument("--index", choices=fit.SLEEP_INDICES, default="MOAI"); sl.add_argument("--value", type=float, required=True, help="events per hour")
    sl.add_argument("--plate", choices=["in", "out"], default="in")
    sl.add_argument("--days-since-removal", type=int, help="plate out: days since it was last worn")
    sl.add_argument("--scoring", default="unspecified", help="scoring rules, e.g. 'AASM 3'")
    sl.add_argument("--threshold", type=float, help="target, events/h (default: the case's clinical_settings)")
    sl.set_defaults(fn=cmd_sleep)

    me = sub.add_parser("measure", help="bench: register a scanned part to its design and report surface deviation")
    me.add_argument("design", help="design STL (e.g. cast_1.stl, prototype_2.stl, mold/working_model.stl)")
    me.add_argument("scan", help="scan of the physical part")
    me.add_argument("--no-align", action="store_true"); me.add_argument("--out"); me.add_argument("--aligned", help="write the aligned scan here")
    me.add_argument("--record", help="design.json of the case, when DESIGN is in its design frame (cast_N.stl, prototype_N.stl): "
                                     "splits the result into intaglio, rest of plate and baton")
    me.set_defaults(fn=cmd_measure)

    pj = sub.add_parser("polyjet", help="split a prototype into PolyJet material bodies and export 3MF + manifest")
    pj.add_argument("dir"); pj.add_argument("--profile", choices=["prototype", "graded-research"], default="prototype")
    pj.add_argument("--proto", type=int, default=2); pj.add_argument("--device", help="any device id instead of --proto (e.g. A1)")
    pj.add_argument("--skin", type=float, default=1.0)
    pj.add_argument("--tip", type=float, default=7.0); pj.add_argument("--rim", type=float, default=2.0); pj.add_argument("--posterior", type=float, default=3.0)
    pj.add_argument("--orientation", choices=["intaglio-up", "extension-up"], default="intaglio-up")
    pj.set_defaults(fn=cmd_polyjet)

    mo = sub.add_parser("mold", help="printable tooling (model, counter-mold, cradle, wire jig) for casting the final plate; registers it as C<n>")
    mo.add_argument("dir"); mo.add_argument("--final-thickness", type=float, default=2.5); mo.add_argument("--wall", type=float, default=6.0)
    mo.add_argument("--proto", type=int, default=2); mo.add_argument("--device", help="device id to cast (default: last adjusted design, else P<proto>)")
    mo.set_defaults(fn=cmd_mold)

    pr = sub.add_parser("print", help="direct-print the final plate with an embedded safety wire (pause, lid or multi-material); registers it as D<n>")
    pr.add_argument("dir", nargs="?"); pr.add_argument("--route", choices=["pause", "lid", "multimaterial", "all"], default="pause")
    pr.add_argument("--resin", help="resin key (see --routes); default dental-lt-clear-v2, or truedent for multimaterial")
    pr.add_argument("--final-thickness", type=float, default=2.5); pr.add_argument("--layer", type=float, default=0.1)
    pr.add_argument("--lid-side", choices=["+", "-"], default="+"); pr.add_argument("--proto", type=int, default=2)
    pr.add_argument("--device", help="device id to print (default: last adjusted design, else P<proto>)")
    pr.add_argument("--skin", type=float, default=0.0, help="experimental, multimaterial only: flexible skin over the baton, mm (bench specimens only)")
    pr.add_argument("--skin-mode", choices=["outside", "inside"], default="outside")
    pr.add_argument("--skin-material", choices=["med625flx", "agilus30", "silicone-overmold"], default="med625flx")
    pr.add_argument("--no-wire", action="store_true", help="bench comparison only (needs --skin); never registered")
    pr.add_argument("--routes", action="store_true", help="list the routes and their trade-offs, then exit")
    pr.set_defaults(fn=cmd_print)

    mk = sub.add_parser("make", help="manufacturing planner: the route, steps, equipment and QC gates for the next device on this site's kit; --build makes the files")
    mk.add_argument("dir", nargs="?")
    mk.add_argument("--stage", choices=["tryin", "final", "next"], default="final")
    mk.add_argument("--kit", choices=["sla-nopause", "resin-pause", "polyjet", "none"], default="sla-nopause")
    mk.add_argument("--no-pressure-pot", action="store_true")
    mk.add_argument("--direct-print-validated-by", help="named person who recorded that this site passed the direct-print bench tests")
    mk.add_argument("--direct-print-validated-on", help="date of that record")
    mk.add_argument("--device", help="device to make (default: the last change or accepted device from fitting sessions)")
    mk.add_argument("--build", action="store_true", help="also build the files for the route")
    mk.add_argument("--with-fallback", action="store_true", help="with --build, build the fallback route too")
    mk.add_argument("--kits", action="store_true", help="list the kit profiles")
    mk.set_defaults(fn=cmd_make)
    ca = sub.add_parser("catalog", help="every modular try-in version for this patient (bases x batons), narrowed by choices")
    ca.add_argument("case"); ca.add_argument("--build", action="store_true", help="(re)build the bases and batons")
    ca.add_argument("--cleft", help="hard-soft | soft-only | none | any (default: the case's entered cleft type)")
    ca.add_argument("--profile", choices=["uniform", "graded"]); ca.add_argument("--relief", choices=["on", "off"])
    ca.add_argument("--length", type=float, help="baton length step, mm"); ca.add_argument("--angle", type=float, help="baton angle step, deg")
    ca.add_argument("--passing", action="store_true", help="only versions whose QC does not fail")
    ca.add_argument("--show", type=int, default=20, help="list the versions when at most this many are left")
    ca.add_argument("--assemble", metavar="VERSION", help="write base + baton as one STL for viewing")
    ca.add_argument("--json", help="write the narrowed result here")
    ca.add_argument("--make", metavar="VERSION", help="manufacturing plan for one version: its design, try-in kit, two-visit "
                    "cast base + stock baton tooling, one-piece fallback mold, PLAN.md (in catalog/make/VERSION/)")
    ca.add_argument("--viewer", action="store_true", help="write catalog/catalog_viewer.html: every version, offline, for clinicians to narrow")
    ca.set_defaults(fn=cmd_catalog)

    kt = sub.add_parser("kit", help="modular try-in kit: socket base for the case plus stock slide-in batons (registers K<n>)")
    kt.add_argument("dir", nargs="?"); kt.add_argument("--stock", metavar="OUT", help="write only the patient-independent stock batons")
    kt.add_argument("--variant", choices=["cleft", "nocleft"], default="cleft", help="with --stock (a case uses its own)")
    kt.add_argument("--lengths", default="-2,0,2,4,6", help="mm changes to the straight tail")
    kt.add_argument("--angles", default="-8,0,8", help="deg; + swings the tail toward the tongue base")
    kt.add_argument("--width", type=float, default=10.4)
    kt.add_argument("--no-stock", action="store_true", help="do not write kit/stock/")
    kt.set_defaults(fn=cmd_kit)

    cs = sub.add_parser("chairside", help="research route: fix a stock wired baton matching kit baton K<n> into the worn plate chairside (registers S<n>)")
    cs.add_argument("dir", nargs="?")
    cs.add_argument("--base", action="store_true", help="visit 1: the final base (worn plate with socket and pocket) and its casting tooling")
    cs.add_argument("--kit", help="visit 2: the kit baton chosen at the try-in, e.g. K8 (needs --base first)")
    cs.add_argument("--stock-tooling", metavar="DIR", help="molds and wire jigs for the stock final batons (no case)")
    cs.add_argument("--only", help="with --stock-tooling: comma-separated ids, e.g. K5,K8")
    cs.add_argument("--variant", default="cleft", choices=["cleft", "nocleft"])
    cs.add_argument("--final-thickness", type=float, default=2.5)
    cs.set_defaults(fn=cmd_chairside)

    bn = sub.add_parser("bench", help="research route: synthetic bench specimens and tooling for the 8 chairside tests")
    bn.add_argument("out"); bn.add_argument("--baton", default="K5"); bn.add_argument("--variant", default="cleft", choices=["cleft", "nocleft"])
    bn.set_defaults(fn=cmd_bench)

    aw = sub.add_parser("airway", help="airway landmarks, Bioengineering 2022 plate metrics and the reach check for every device")
    aw.add_argument("design", help="design.json of the case")
    aw.add_argument("--set", action="append", metavar="NAME=VALUE",
                    help="store an anatomical value in the design frame: vallecula=x,y,z, epiglottis_tip=x,y,z, pns=x,y,z, "
                         "tb_ppw_distance_mm=4.2, tb_epiglottic_angle_deg=95 (empty value clears it)")
    aw.add_argument("--source", default="manual", help="where the value came from: ct, mri, endoscopy, manual")
    aw.add_argument("--note")
    aw.set_defaults(fn=cmd_airway)

    v = sub.add_parser("viewer", help="write viewer.html for a case folder")
    v.add_argument("dir"); v.set_defaults(fn=cmd_viewer)

    so = sub.add_parser("studio", help="step-by-step clinician UI in the browser (this computer only, 127.0.0.1)")
    so.add_argument("--workspace", default="out", help="folder that holds the case folders (default: out)")
    so.add_argument("--port", type=int, default=8780); so.add_argument("--no-browser", action="store_true")
    so.set_defaults(fn=cmd_studio)

    sa = sub.add_parser("app", help="PEBP Studio in its own window (desktop app); --install writes PEBP Studio.app (macOS)")
    sa.add_argument("--workspace", help="folder of case folders (default: ~/Desktop/PEBP Studio/Cases)")
    sa.add_argument("--port", type=int, default=8781); sa.add_argument("--debug", action="store_true")
    sa.add_argument("--install", action="store_true", help="write PEBP Studio.app and the starter folder (macOS)")
    sa.add_argument("--dest", default="~/Applications", help="where --install puts the .app (default ~/Applications)")
    sa.add_argument("--home", help="the starter folder for --install (default ~/Desktop/PEBP Studio)")
    sa.set_defaults(fn=cmd_app)

    st = sub.add_parser("selftest", help="check the mesh deformer against the parametric model's exact answers")
    st.set_defaults(fn=cmd_selftest)
    fr = sub.add_parser("frames", help="endoscopy frame-quality filter: fetch IBSU-1432, make synthetic frames, train, eval, classify")
    fr.add_argument("action", choices=["fetch", "synth", "train", "eval", "classify"])
    fr.add_argument("paths", nargs="*", help="train/eval: one labelled folder; classify: image files or folders")
    fr.add_argument("--out"); fr.add_argument("--model", help="frame_quality_model.json (eval, classify)")
    fr.add_argument("--record", default="7929648", help="fetch: Zenodo record id"); fr.add_argument("--api-url", help=argparse.SUPPRESS)
    fr.add_argument("--n", type=int, default=60, help="synth: frames per class"); fr.add_argument("--short-names", action="store_true")
    fr.add_argument("--seed", type=int, default=0); fr.add_argument("--folds", type=int, default=5)
    fr.add_argument("--group-regex", help="train: regex whose first group names the video or patient, so CV never splits one")
    fr.add_argument("--l2", type=float, default=1e-2); fr.add_argument("--threshold", type=float, help="keep when p(informative) >= this (default 0.5)")
    fr.set_defaults(fn=cmd_frames)

    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
