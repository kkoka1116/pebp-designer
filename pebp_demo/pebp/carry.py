"""Carry an accepted baton onto a new scan, for the next plate.

Tübingen makes later plates (routinely a second one at 3-4 months, as the infant grows) from a new
impression or scan, using the previous plate as the template and with no new digital prototype
[published: Aretxabaleta et al. 2025, Bioengineering 12:1063; Weismann et al. 2026, doi 10.1002/cre2.70291].
This module is the digital version of that step:

  python -m pebp carry OLD/design.json NEW_SCAN.stl --device C1 --out out/case_plate2 [--prototypes]

What is carried [heuristic]: the accepted device's baton, exactly as it left the clinic (every bedside
and lab change already sits in its parameters), placed on the new plate by the same rule as every
other device: relative to the new plate's posterior border, at the new plate's mid-thickness. The
plate itself is rebuilt from the new scan with the old plate settings. Nothing is extrapolated for
growth: whether the baton should be longer on the bigger palate is a clinical decision, and the record
flags it rather than making it.

The carried design gets the device id T. It can be cast directly (`mold --device T`, and `mold` picks it
by default when nothing was changed after it), printed for an optional check, or used as the centre of a
new prototype set (`--prototypes`: P1-P3 at T's AP offset -2, 0, +2 mm).

`lineage(record, device_id)` walks a device back to its prototype and adds up what changed on the way,
including the running total of bedside shortening. PEBP Studio can call `lineage`, `suggest_device`
and `carry_forward` directly.
"""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import numpy as np

from . import record as rec
from .extension import ExtensionParams

LABEL = ("project heuristic: the accepted baton is placed relative to the new plate's posterior border, "
         "following Tübingen's previous-plate-as-template practice; no growth correction is applied")

COMPARED = ("effective_length", "tilt_deg", "distal_curve_deg", "ap_offset", "lateral_offset",
            "width_base", "width_tip", "thickness")


# ---------------------------------------------------------------- lineage

def _parent(record: dict, did: str) -> str | None:
    d = rec.device(record, did)
    return d.get("from")


def _where(record: dict, did: str) -> str:
    if did.startswith("A"):
        e = next(q for q in record["modification_log"] if q.get("id", f"A{q['n']}") == did)
        return e.get("where", "lab")
    if did.startswith("C"):
        return "cast"
    return "design"


def lineage(record: dict, device_id: str) -> dict:
    """The chain from the first design to `device_id`, each step's parameter changes, and the totals.

    bedside_shortening_total_mm: every millimetre of effective length taken off at the bedside along the
    chain (prototype grinding and grinding of wired plates alike). lab_length_change_mm: the net length
    change made in the lab. vs_standard: the device against the published extension it started from."""
    did = rec.normalise_id(device_id)
    chain = [did]
    while True:
        p = _parent(record, chain[-1])
        if p is None or p in chain:
            break
        chain.append(p)
    chain.reverse()
    steps, bedside, lab = [], 0.0, 0.0
    for a, b in zip(chain, chain[1:]):
        pa, pb = rec.device(record, a)["params"], rec.device(record, b)["params"]
        where = _where(record, b)
        diff = {k: round(pb[k] - pa[k], 3) for k in COMPARED if abs(pb[k] - pa[k]) > 1e-9}
        dl = pa["effective_length"] - pb["effective_length"]
        if where == "bedside" and dl > 0:
            bedside += dl
        elif where == "lab":
            lab -= dl
        steps.append({"from": a, "to": b, "where": where, "changes": diff})
    final = rec.device(record, did)["params"]
    std = (record.get("standard") or {}).get("params")
    vs = {k: round(final[k] - std[k], 3) for k in COMPARED if abs(final[k] - std[k]) > 1e-9} if std else None
    out = {"device": did, "chain": chain, "steps": steps, "bedside_shortening_total_mm": round(bedside, 3),
           "lab_length_change_mm": round(lab, 3), "vs_standard": vs,
           "standard_mode": (record.get("standard") or {}).get("mode")}
    cf = record.get("carried_from")
    if cf:
        # the first device of this case was itself carried from an earlier plate
        out["earlier_plates"] = {"generation": cf["generation"] - 1, "from_case": cf["design_file"], "from_device": cf["device"],
                                 "bedside_shortening_total_mm": cf["lineage"].get("bedside_shortening_total_mm")}
        out["bedside_shortening_all_plates_mm"] = round(bedside + cf["lineage"].get("bedside_shortening_all_plates_mm",
                                                                             cf["lineage"]["bedside_shortening_total_mm"]), 3)
    else:
        out["bedside_shortening_all_plates_mm"] = out["bedside_shortening_total_mm"]
    return out


def suggest_device(record: dict) -> tuple[str | None, str]:
    """The device the infant most plausibly wore last, with the reason. A suggestion only: which plate
    is the template is the clinical team's choice, so `carry` still needs it named."""
    ids = set(rec.device_ids(record))
    worn = [s for s in record.get("sleep_studies", []) if s.get("device") in ids]
    if worn:
        s = worn[-1]
        return s["device"], f"last sleep study with the plate in (day {s.get('day_of_life')}, {s.get('index')} {s.get('value_per_h')}/h)"
    acc = [f["decision"].get("accepted_device") for f in record.get("fitting_sessions", []) if (f.get("decision") or {}).get("accepted_device")]
    casts = record.get("cast_plates", [])
    if casts:
        return casts[-1]["id"], "last cast plate registered for this case"
    if acc:
        return acc[-1], "device accepted at the last logged fitting session"
    return None, "no sleep study, cast plate or accepted device on record"


# ---------------------------------------------------------------- carry

def carry_spec(prev_design: str | Path, device_id: str) -> dict:
    """What `design` needs to rebuild the next plate from a new scan: the accepted baton, the old plate
    settings and the provenance."""
    prev_path = Path(prev_design)
    prev = rec.load(prev_path)
    d = rec.device(prev, device_id)
    lin = lineage(prev, d["id"])
    pg = (prev.get("carried_from") or {}).get("generation", 1)
    tpp = (prev.get("tpp_metrics") or {}).get(d["id"]) or {}
    measured = [k for k, v in (prev.get("airway") or {}).get("landmarks", {}).items()
                if v.get("source") not in (None, "unmeasured", "scan_proxy", "design")]
    return {"params": ExtensionParams(**d["params"]), "mode": prev.get("extension_mode", "parametric"),
            "mode_requested": prev.get("extension_mode_requested", "auto"),
            "plate_params": prev["plate"]["params"], "final_plate_thickness": prev.get("final_plate_thickness", 2.5),
            "backend": (prev["landmarks"].get("backend") or {}).get("backend", "builtin"),
            "clinical_settings": prev.get("clinical_settings"),
            "from": {"design_file": str(prev_path), "design_sha256": rec.file_sha256(prev_path), "device": d["id"],
                     "device_kind": d["kind"], "device_file": d["file"], "generation": pg + 1,
                     "previous_scan_sha256": prev["source_scan"]["sha256"],
                     "previous_landmarks": {k: prev["landmarks"].get(k) for k in ("arch_depth_mm", "intertuberosity_width_mm", "cleft_present")},
                     "previous_attachment": prev.get("attachment"),
                     "previous_tip": tpp.get("baton_tip"), "previous_root": tpp.get("baton_root"),
                     "previous_arm_length_mm": tpp.get("arm_length_mm"),
                     "previous_airway_measured": measured, "lineage": lin, "label": LABEL}}


def add_carried(record: dict, g: dict, ep: ExtensionParams, mode: str, standard: dict, out: Path, spec: dict, build_one) -> dict:
    """Build T on the new plate, register it, and write the provenance and flags into the record."""
    r = build_one(g, ep, 0, mode, "prototype", standard)
    fn = "carried_design.stl"
    r["mesh"].export(out / fn)
    record["carried_design"] = {"id": "T", "file": fn, "sha256": rec.file_sha256(out / fn), "params": asdict(ep),
                                "tip": r["tip"].round(2).tolist(), "qc": r["qc"], "extension_info": r["info"],
                                "plate_thickness": g["pp"].thickness,
                                "note": "the accepted baton carried onto the new scan; cast it with `mold --device T`"}
    cf = dict(spec["from"])
    cf["time"] = rec.now()
    if spec.get("clinical_settings"):
        record["clinical_settings"] = dict(spec["clinical_settings"])
    flags = []
    if spec.get("mode_auto") and spec["mode_auto"] != mode:
        flags.append(f"the new scan would get the {spec['mode_auto']} extension on its own; the template's {mode} was kept. Review.")
    if cf["previous_scan_sha256"] == record["source_scan"]["sha256"]:
        flags.append("the new scan is the same file as the previous plate's scan")
    lin = cf["lineage"]
    if lin["bedside_shortening_all_plates_mm"] > 0:
        flags.append(f"{lin['bedside_shortening_all_plates_mm']:.1f} mm of bedside shortening is carried into this plate "
                     "(it is already in the baton's length)")
    flags.append("no growth correction applied: whether the baton should change for the larger palate is a clinical decision")
    if cf["previous_airway_measured"]:
        flags.append("airway landmarks measured for the previous plate (" + ", ".join(cf["previous_airway_measured"])
                     + ") were not carried: they describe a younger infant; measure again")
    if spec.get("clinical_settings"):
        flags.append("clinical settings copied from the previous plate's record (same infant); confirm they still apply")
    cf["flags"] = flags
    record["carried_from"] = cf
    return record["carried_design"]


def growth_summary(record: dict) -> dict:
    """Old against new, in each plate's own design frame (origin midway between the tuberosities)."""
    cf = record["carried_from"]
    tpp = (record.get("tpp_metrics") or {}).get("T") or {}
    old_lm, new_lm = cf["previous_landmarks"], record["landmarks"]
    out = {k: {"previous": old_lm.get(k), "new": new_lm.get(k),
               "change": round(new_lm[k] - old_lm[k], 2) if isinstance(old_lm.get(k), (int, float)) else None}
           for k in ("arch_depth_mm", "intertuberosity_width_mm")}
    pa, na = cf.get("previous_attachment") or {}, record.get("attachment") or {}
    if pa and na:
        out["posterior_border_y_change_mm"] = round(na["posterior_border_y"] - pa["posterior_border_y"], 2)
    if cf.get("previous_tip") and tpp.get("baton_tip"):
        out["tip_shift_mm"] = np.round(np.subtract(tpp["baton_tip"], cf["previous_tip"]), 2).tolist()
    if cf.get("previous_arm_length_mm") and tpp.get("arm_length_mm"):
        out["arm_length_change_mm"] = round(tpp["arm_length_mm"] - cf["previous_arm_length_mm"], 2)
    return out


def carry_forward(prev_design, new_scan, out_dir, device_id: str, prototypes: bool = False, final: bool = False) -> dict:
    """Library entry point (PEBP Studio): rebuild the next plate on `new_scan` and return its record."""
    from .cli import main
    args = ["carry", str(prev_design), str(new_scan), "--out", str(out_dir), "--device", device_id]
    args += ["--prototypes"] if prototypes else []
    args += ["--final"] if final else []
    main(args)
    return rec.load(Path(out_dir) / "design.json")
