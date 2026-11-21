"""Cleft type: a clinical entry that picks the baton variant and how the plate bridges the palate.

The scan cannot tell a real cleft from missing data, and it cannot see the soft palate at all, so the
cleft type comes from the examination. Three types are offered, because the published rule has three:

  hard_soft   cleft of the hard and soft palate          -> Tübingen cleft baton
  soft_only   cleft of the soft palate or uvula only     -> Tübingen no-cleft baton
  none        no cleft                                   -> Tübingen no-cleft baton

[published] Aretxabaleta et al. 2025, Bioengineering 12:1063, section 5 and 8.1: the no-cleft standard
extension is for "patients without a cleft or with a small soft palate or uvular cleft only". Its flatter
(larger radius) cleft extension leaves room for the intact velum; without that room the plate may increase
the obstruction, interfere with feeding, and risks pressure sores in the uvular region.

Bridging [project heuristic]. Every gap in the scan is bridged whatever the cleft type (a scan cannot tell a
cleft from a hole). What the type changes on top of that:

  hard_soft   the gap the scan analysis reads as a cleft gets the cleft block-out: a flat wax floor at the
              shelf-margin level + block-out, carried straight back past the posterior border, because the
              cleft runs on into the soft palate.
  soft_only,  the hard palate is intact, so a gap read as a cleft can only be missing scan data. It is
  none        bridged like any other scan hole, at its own rim level + block-out, and stops at its own edge:
              nothing is carried behind the border. It is reported for a check on the model.

Plate-to-baton junction [project heuristic]. The posterior border is a crescent through the tuberosity line
that bulges back at the midline, where the baton root leaves the plate. With a cleft of the soft palate the
midline behind the hard palate is the cleft itself, so the bulge sits over the gap (2 mm, as before). With an
intact velum the same bulge would rest on the moving soft palate, the region the paper names for pressure
sores, so for soft_only and none the bulge is 0: the root leaves the plate on the tuberosity line, which the
paper uses to place the extension (Fig. 8A, "posterior maxillary gingival landmarks"). Either can be set by hand
(--posterior-bulge).
"""
from __future__ import annotations

from dataclasses import replace

import numpy as np
from shapely.geometry import Polygon

CLEFT_TYPES = {
    "hard_soft": {"label": "cleft of the hard and soft palate", "baton": "cleft",
                  "bridging": "cleft block-out over the cleft, carried back past the posterior border; other scan gaps bridged at their rim"},
    "soft_only": {"label": "cleft of the soft palate or uvula only", "baton": "nocleft",
                  "bridging": "hard palate intact: every gap, including one read as a cleft, bridged as a scan hole at its own rim level"},
    "none": {"label": "no cleft", "baton": "nocleft",
             "bridging": "hard palate intact: every gap, including one read as a cleft, bridged as a scan hole at its own rim level"},
}
POSTERIOR_BULGE_MM = {"hard_soft": 2.0, "soft_only": 0.0, "none": 0.0, None: 2.0}
ALIASES = {"hard-soft": "hard_soft", "soft-only": "soft_only", "uvula": "soft_only", "no": "none", "yes": "hard_soft"}
RULE = ("Aretxabaleta et al. 2025, Bioengineering 12:1063 (doi 10.3390/bioengineering12101063), sections 5 and 8.1: "
        "the no-cleft standard extension is for no cleft or a small soft palate or uvular cleft only")
LABEL = "clinical entry from the examination; the software never decides the cleft type from the scan"


def normalise(value: str | None) -> str | None:
    if value in (None, "", "unknown", "auto"):
        return None
    v = ALIASES.get(value, value)
    if v not in CLEFT_TYPES:
        raise ValueError(f"unknown cleft type {value!r}: one of {', '.join(CLEFT_TYPES)}")
    return v


def plate_defaults(cleft_type: str | None) -> dict:
    """PlateParams fields whose default depends on the cleft type."""
    return {"posterior_bulge": POSTERIOR_BULGE_MM[normalise(cleft_type)]}


def baton_variant(cleft_type: str | None, scan_found_cleft: bool) -> str:
    """'cleft' or 'nocleft'. Without an entry the scan analysis decides, as before."""
    t = normalise(cleft_type)
    if t is None:
        return "cleft" if scan_found_cleft else "nocleft"
    return CLEFT_TYPES[t]["baton"]


def resolve_extension(mode: str, cleft_type: str | None, scan_found_cleft: bool) -> str:
    if mode == "auto":
        return f"tuebingen-{baton_variant(cleft_type, scan_found_cleft)}"
    return mode


def apply(lm, cleft_type: str | None):
    """Landmarks as the plate should see them for this cleft type. Returns (landmarks, info).
    The detector's own call is kept in info for the record."""
    t = normalise(cleft_type)
    info = {"value": t, "scan_found_cleft": bool(lm.cleft_present), "scan_holes": len(lm.holes), "warnings": []}
    if t is None:
        info["source"] = "scan analysis (no cleft type entered)"
        info["bridging"] = ("cleft block-out where the scan analysis found a cleft, scan holes at their rim"
                            if lm.cleft_present else "scan holes bridged at their rim level")
        return lm, info
    info["source"] = "clinical entry"
    info["bridging"] = CLEFT_TYPES[t]["bridging"]
    if t == "hard_soft":
        if not lm.cleft_present:
            info["warnings"].append(
                "a cleft of the hard palate was entered but the scan analysis found none, so no cleft block-out was "
                "made and nothing is carried behind the border: check on the model that the plate does not reach up "
                "into the cleft" + (f" ({len(lm.holes)} scan gap(s) are bridged at their rim)" if lm.holes else ""))
        return lm, info
    # soft_only / none: the hard palate is intact; a gap read as a cleft is missing data
    if not lm.cleft_present:
        return lm, info
    polys = lm.cleft_polygon if isinstance(lm.cleft_polygon, list) else [lm.cleft_polygon]
    polys = [np.asarray(q) for q in polys if q is not None and len(q) >= 3]
    new_holes = []
    for q in polys:
        a = float(Polygon(q).buffer(0).area)
        new_holes.append({"polygon": q, "rim_z": float(lm.cleft_margin_z), "area_mm2": a, "from": "gap read as a cleft"})
    area = sum(h["area_mm2"] for h in new_holes)
    widest = max((w for _, w in lm.cleft_width_profile), default=0.0)
    info["reclassified_gap"] = {"regions": len(new_holes), "area_mm2": round(area, 1), "widest_mm": round(float(widest), 2),
                                "rim_z_mm": round(float(lm.cleft_margin_z), 2)}
    info["warnings"].append(
        f"'{CLEFT_TYPES[t]['label']}' was entered but the scan analysis read a {area:.0f} mm2 gap (widest {widest:.1f} mm) "
        "in the hard palate as a cleft. It is bridged as missing scan data at its own rim level and not carried behind "
        "the border: check the scan and the plate there, and re-check the examination if the gap is real")
    notes = list(lm.notes) + [f"cleft type entered as '{t}': the gap read as a cleft is bridged as a scan hole"]
    lm2 = replace(lm, cleft_present=False, cleft_polygon=None, cleft_width_profile=[], holes=list(lm.holes) + new_holes,
                  notes=notes)
    return lm2, info


def record_entry(cleft_type: str | None, entered_by: str | None, info: dict, extension_mode: str, now: str,
                 posterior_bulge: float | None = None) -> dict:
    t = normalise(cleft_type)
    return {"posterior_bulge_mm": posterior_bulge, "posterior_bulge_default_mm": POSTERIOR_BULGE_MM[t],"value": t, "label": CLEFT_TYPES[t]["label"] if t else "not entered",
            "entered_by": entered_by if t else None, "source": info["source"],
            "baton_variant": extension_mode.replace("tuebingen-", "") if extension_mode.startswith("tuebingen-") else extension_mode,
            "bridging": info["bridging"], "scan_found_cleft": info["scan_found_cleft"],
            "reclassified_gap": info.get("reclassified_gap"), "warnings": info["warnings"],
            "rule": RULE, "flag": LABEL if t else "no cleft type entered: the scan analysis chose (enter it with --cleft)",
            "time": now}
