"""Structured records for the endoscopy try-in and the sleep study.

No centre publishes a fitting form or data dictionary for the PEBP. The fields below are each a
published criterion (Aretxabaleta 2025; Schmidt 2021; Nandan 2025; Müller-Hagedorn 2017; Leister 2026),
so a form filled in this vocabulary is comparable across sessions, observers and centres.

One vocabulary drives everything: the blank JSON template, the printable bedside form, validation,
and the translation of each modification into a design change. Grades are stored, never acted on by
the software: inter-rater agreement on awake airway endoscopy is only fair (Basart 2016, kappa 0.23 in
Robin infants), so every session keeps a reference to its video for re-scoring.

No names or dates: observers are role plus a study code, time is day of life.
"""
from __future__ import annotations

import json
from dataclasses import replace

from .extension import ExtensionParams
from .record import normalise_id

YN = [True, False, None]

SESSION = {
    "day_of_life": ("int", None, "age in days at the session (no calendar dates)"),
    "purpose": ("enum", ["baseline", "try_in", "delivery_check", "follow_up"], "why the endoscopy was done"),
    "sedation": ("enum", ["awake", "sedated"], "Tübingen fits awake so muscle tone is normal; Cologne fits under sedation"),
    "sedation_agents": ("list", None, "if sedated, the agents used"),
    "head_position": ("enum", ["neutral", "sniffing", "other"], "Tübingen reports perceived length changes with position"),
    "minutes_since_feed": ("int", None, "Tübingen: at least 2 h fasting before endoscopy"),
    "endoscopist_role": ("enum", ["ENT", "neonatology", "pulmonology", "anaesthesia", "other"], "who held the scope"),
    "observer_code": ("str", None, "study code of the person filling in the form, not a name"),
    "video_ref": ("str", None, "reference to the recording in the hospital system, so findings can be re-scored"),
}

BASELINE = {
    "sher_type": ("enum", [1, 2, 3, 4, None], "1 glossoptosis; 2 tongue presses the velum to the back wall; 3 lateral; 4 circular"),
    "berlin_category": ("enum", ["I", "II", "III", "IV", None], "Schmidt 2021 categories, if the team uses them"),
    "yellon_epiglottis": ("enum", [0, 1, 2, 3, None], "Yellon grade for the epiglottis"),
    "yellon_tongue_base": ("enum", [0, 1, 2, 3, None], "Yellon grade for tongue-base prolapse"),
    "laryngomalacia": ("enum", YN, "floppy epiglottis lying backwards"),
}

TRIAL = {
    "device": ("device", None, "P1-P3, A<n>, F or C<n> from the design record"),
    "gag_on_insertion": ("enum", YN, "gagging means too long: Tübingen removes it at once and shortens"),
    "tongue_base_anterior": ("enum", ["yes", "partial", "no", None], "is the tongue base held forward"),
    "airway_opening": ("enum", ["open", "partial", "obstructed", None], "pharyngeal airway behind the tongue"),
    "epiglottis_free_breathing": ("enum", YN, "epiglottis moves freely while breathing"),
    "epiglottis_free_crying": ("enum", YN, "... while crying"),
    "epiglottis_free_swallowing": ("enum", YN, "... while swallowing"),
    "epiglottis_free_coughing": ("enum", YN, "... while coughing"),
    "tip_position": ("enum", ["above_epiglottis_rim", "at_rim", "in_vallecula", "touching_epiglottis", "not_seen", None],
                     "where the tip sits"),
    "lateral_tongue_bulging": ("enum", YN, "tongue bulges past the sides: baton too narrow"),
    "wall_irritation": ("enum", YN, "side walls of the throat touched or irritated: baton too wide"),
    "wall_collapse": ("enum", ["none", "lateral", "circumferential", None], "persisting collapse calls for a tube or ring"),
    "glottis_fully_visible": ("enum", YN, "Berlin target"),
    "notes": ("str", None, "free text"),
}

# code -> (unit, where it is usually done, what it means, how it changes ExtensionParams)
MODIFICATIONS = {
    "shorten_mm":   ("mm", "bedside", "grind the tip shorter", lambda e, x: {"effective_length": e.effective_length - x}),
    "lengthen_mm":  ("mm", "lab", "a longer baton: new prototype and a second endoscopy", lambda e, x: {"effective_length": e.effective_length + x}),
    "anterior_mm":  ("mm", "lab", "move the whole baton forward (beyond prototype 3: wax at the bedside, then redesign)",
                     lambda e, x: {"ap_offset": e.ap_offset + x}),
    "posterior_mm": ("mm", "lab", "move the whole baton back", lambda e, x: {"ap_offset": e.ap_offset - x}),
    "lateral_mm":   ("mm", "lab", "move sideways, + toward the patient's right", lambda e, x: {"lateral_offset": e.lateral_offset + x}),
    "angle_deg":    ("deg", "lab", "swing the tail, + toward the tongue base (see clinical_settings.angle_sign)",
                     lambda e, x: {"tilt_deg": e.tilt_deg + x}),
    "curve_deg":    ("deg", "bedside", "bend the distal third ('C-shape'), + toward the tongue base",
                     lambda e, x: {"distal_curve_deg": e.distal_curve_deg + x}),
    "widen_mm":     ("mm", "lab", "wider baton (tongue bulging past the sides)",
                     lambda e, x: {"width_base": e.width_base + x, "width_tip": e.width_tip + x}),
    "narrow_mm":    ("mm", "bedside", "narrower baton (side walls irritated)",
                     lambda e, x: {"width_base": e.width_base - x, "width_tip": e.width_tip - x}),
    "thin_mm":      ("mm", "bedside", "grind the posterior surface (modelled as an even thickness change)",
                     lambda e, x: {"thickness": e.thickness - x}),
    "tube_or_ring": ("flag", "lab", "side or circular collapse persists: not supported, design by hand", None),
}

FINDINGS = ["gag_on_insertion", "too_long", "too_short", "tongue_not_advanced", "too_little_pressure", "not_steep_enough",
            "too_steep", "tip_between_tongue_root_and_epiglottis", "lateral_tongue_bulging", "wall_irritation",
            "posterior_pressure", "lateral_collapse", "circumferential_collapse", "other"]


def apply_modifications(base: ExtensionParams, mods: list[dict]) -> tuple[ExtensionParams, dict, list[str]]:
    """Turn a list of {'code', 'amount'} into new ExtensionParams. Returns (params, changed fields, flags)."""
    ep, flags = base, []
    for m in mods:
        code = m["code"]
        if code not in MODIFICATIONS:
            raise ValueError(f"unknown modification {code!r}; allowed: {', '.join(MODIFICATIONS)}")
        fn = MODIFICATIONS[code][3]
        if fn is None:
            flags.append(f"{code}: not supported by the software, needs a hand design")
            continue
        ep = replace(ep, **fn(ep, float(m["amount"])))
    changed = {k: round(v, 3) for k, v in ep.__dict__.items() if v != getattr(base, k)}
    return ep, changed, flags


def _blank(spec: dict) -> dict:
    return {k: ([] if t == "list" else None) for k, (t, _, _) in spec.items()}


def template(record: dict, devices: list[str]) -> dict:
    """A blank form for one session, listing the devices that exist for this case."""
    return {
        "_help": {
            "devices_available": devices,
            "session": {k: {"type": t, "allowed": a, "meaning": d} for k, (t, a, d) in SESSION.items()},
            "baseline": {k: {"allowed": a, "meaning": d} for k, (t, a, d) in BASELINE.items()},
            "trial": {k: {"allowed": a, "meaning": d} for k, (t, a, d) in TRIAL.items()},
            "modification_codes": {k: {"unit": u, "usually": w, "meaning": d} for k, (u, w, d, _) in MODIFICATIONS.items()},
            "findings": FINDINGS,
            "clinical_settings": record.get("clinical_settings", {}),
        },
        "session": _blank(SESSION),
        "baseline": _blank(BASELINE),
        "trials": [{**_blank(TRIAL), "device": d} for d in devices if d.startswith("P")],
        "decision": {"accepted_device": None, "decided_by_role": None,
                     "modifications": [{"device": None, "code": None, "amount": None, "where": None, "finding": None}]},
    }


def _check_value(where: str, key: str, value, spec: tuple, devices: list[str], errors: list[str]) -> None:
    t, allowed, _ = spec
    if value is None or value == []:
        return
    if t == "enum" and value not in allowed:
        errors.append(f"{where}.{key}: {value!r} not in {allowed}")
    elif t == "int" and not (isinstance(value, int) and not isinstance(value, bool)):
        errors.append(f"{where}.{key}: expected a whole number, got {value!r}")
    elif t == "list" and not isinstance(value, list):
        errors.append(f"{where}.{key}: expected a list")
    elif t == "device" and normalise_id(value) not in devices:
        errors.append(f"{where}.{key}: no device {value!r} in this record ({', '.join(devices)})")


def validate(form: dict, devices: list[str]) -> list[str]:
    errors = []
    for block, spec in (("session", SESSION), ("baseline", BASELINE)):
        got = form.get(block) or {}
        for k in got:
            if k not in spec:
                errors.append(f"{block}.{k}: unknown field")
        for k, s in spec.items():
            _check_value(block, k, got.get(k), s, devices, errors)
    if (form.get("session") or {}).get("day_of_life") is None:
        errors.append("session.day_of_life is required")
    if (form.get("session") or {}).get("sedation") is None:
        errors.append("session.sedation is required (awake or sedated)")
    for i, tr in enumerate(form.get("trials") or []):
        for k in tr:
            if k not in TRIAL:
                errors.append(f"trials[{i}].{k}: unknown field")
        for k, s in TRIAL.items():
            _check_value(f"trials[{i}]", k, tr.get(k), s, devices, errors)
        if not tr.get("device"):
            errors.append(f"trials[{i}].device is required")
    dec = form.get("decision") or {}
    acc = dec.get("accepted_device")
    if acc is not None and normalise_id(acc) not in devices:
        errors.append(f"decision.accepted_device: no device {acc!r}")
    for i, m in enumerate(dec.get("modifications") or []):
        if not any(m.get(k) is not None for k in ("device", "code", "amount")):
            continue                                   # untouched template row
        if m.get("device") is None or normalise_id(m["device"]) not in devices:
            errors.append(f"decision.modifications[{i}].device: no device {m.get('device')!r}")
        if m.get("code") not in MODIFICATIONS:
            errors.append(f"decision.modifications[{i}].code: {m.get('code')!r} not one of {', '.join(MODIFICATIONS)}")
        elif MODIFICATIONS[m["code"]][0] != "flag" and not isinstance(m.get("amount"), (int, float)):
            errors.append(f"decision.modifications[{i}].amount: a number in {MODIFICATIONS[m['code']][0]} is required")
        if m.get("where") not in ("bedside", "lab", None):
            errors.append(f"decision.modifications[{i}].where: 'bedside' or 'lab'")
        if m.get("finding") not in FINDINGS + [None]:
            errors.append(f"decision.modifications[{i}].finding: {m.get('finding')!r} not in the findings list")
    return errors


def real_modifications(form: dict) -> list[dict]:
    rows = [m for m in (form.get("decision") or {}).get("modifications") or []
            if any(m.get(k) is not None for k in ("device", "code", "amount"))]
    for m in rows:
        m["device"] = normalise_id(m["device"])
        m.setdefault("where", None)
        if m["where"] is None:
            m["where"] = MODIFICATIONS[m["code"]][1]
    return rows


def flags(form: dict, settings: dict) -> list[str]:
    """Observations worth a second look. Advisory only; the clinical team decides."""
    out = []
    target = settings.get("tip_target", "unset")
    for tr in form.get("trials") or []:
        d = tr.get("device")
        if tr.get("gag_on_insertion"):
            out.append(f"{d}: gag on insertion; Tübingen reads this as too long")
        if tr.get("wall_collapse") in ("lateral", "circumferential"):
            out.append(f"{d}: {tr['wall_collapse']} wall collapse persists; a steeper baton, tube or ring may be needed (hand design)")
        pos = tr.get("tip_position")
        if target != "unset" and pos in ("above_epiglottis_rim", "at_rim", "in_vallecula", "touching_epiglottis") and pos != target:
            out.append(f"{d}: tip seen {pos.replace('_', ' ')}, team target is {target.replace('_', ' ')}")
        if pos == "touching_epiglottis":
            out.append(f"{d}: tip touching the epiglottis")
        if any(tr.get(f"epiglottis_free_{k}") is False for k in ("breathing", "crying", "swallowing", "coughing")):
            out.append(f"{d}: epiglottis not free in at least one manoeuvre")
    s = form.get("session") or {}
    if s.get("minutes_since_feed") is not None and s["minutes_since_feed"] < 120:
        out.append(f"session: {s['minutes_since_feed']} min since feed (Tübingen asks for 2 h)")
    if s.get("sedation") == "sedated":
        out.append("session: sedated fitting; muscle tone differs from awake, record it as a covariate")
    return out


def printable_form() -> str:
    """Markdown bedside form in the same vocabulary as the JSON template."""
    def opts(a):
        return " / ".join("☐ " + ("n/a" if v is None else ("yes" if v is True else ("no" if v is False else str(v)))) for v in a)
    L = ["# PEBP endoscopy try-in form", "",
         "Fill in one form per session. No names or dates: day of life and study codes only. "
         "Enter it with `pebp fitting log DESIGN.json form.json`; the JSON template (`pebp fitting template`) has the same fields.", "",
         "## Session", "", "| Field | Entry | Meaning |", "|---|---|---|"]
    for k, (t, a, d) in SESSION.items():
        L.append(f"| {k} | {opts(a) if t == 'enum' else '______'} | {d} |")
    L += ["", "## Baseline (first endoscopy only)", "", "| Field | Entry | Meaning |", "|---|---|---|"]
    for k, (t, a, d) in BASELINE.items():
        L.append(f"| {k} | {opts(a)} | {d} |")
    L += ["", "## Each device tried", "", "| Field | P1 | P2 | P3 | Meaning |", "|---|---|---|---|---|"]
    for k, (t, a, d) in TRIAL.items():
        if k == "device":
            continue
        choice = " / ".join("n/a" if v is None else ("yes" if v is True else ("no" if v is False else str(v))) for v in a) if a else "______"
        L.append(f"| {k} | | | | {choice}. {d} |")
    L += ["", "## Decision", "", "Accepted device: ______  Decided by (role): ______", "",
          "| Device | Code | Amount | Bedside or lab | Finding |", "|---|---|---|---|---|", "| | | | | |", "| | | | | |", "",
          "Modification codes: " + "; ".join(f"`{k}` ({u}, usually {w}): {d}" for k, (u, w, d, _) in MODIFICATIONS.items()) + ".", "",
          "Findings: " + ", ".join(f"`{f}`" for f in FINDINGS) + ".", ""]
    return "\n".join(L)


SLEEP_KINDS = ["polygraphy", "polysomnography"]
SLEEP_INDICES = ["MOAI", "OAI", "OAHI", "AHI"]


def sleep_entry(record: dict, device_id: str, day: int, kind: str, index: str, value: float, plate_in: bool,
                days_since_removal: int | None, scoring: str, threshold: float | None, device_sha: str | None) -> dict:
    if kind not in SLEEP_KINDS:
        raise ValueError(f"kind must be one of {SLEEP_KINDS}")
    if index not in SLEEP_INDICES:
        raise ValueError(f"index must be one of {SLEEP_INDICES}")
    s = record.get("clinical_settings", {})
    thr = threshold if threshold is not None else (s.get("sleep_target_below_per_h") if index == s.get("sleep_index") else None)
    return {"n": len(record["sleep_studies"]) + 1, "device": device_id if plate_in else None, "device_sha256": device_sha if plate_in else None,
            "plate_in": plate_in, "days_since_plate_removed": days_since_removal, "day_of_life": day, "kind": kind,
            "index": index, "value_per_h": value, "scoring_rules": scoring,
            "target_below_per_h": thr, "meets_target": (value < thr) if thr is not None else None}
