"""Rebuild the geometric context of a case from its design record."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import trimesh

from .extension import ExtensionParams
from .landmarks import find_landmarks
from .plate import PlateParams, design_base_plate


def rebuild(case_dir: Path, plate_thickness: float | None = None, cleft_type: str | None = "record",
            plate_overrides: dict | None = None) -> dict:
    """cleft_type: "record" (default) uses the case's entered cleft type; any other value (or None for the scan
    analysis) rebuilds the plate as if that type had been entered. plate_overrides replace PlateParams fields."""
    from .cleft import apply as apply_cleft
    from .record import load
    case_dir = Path(case_dir)
    rec = load(case_dir / "design.json")
    scan = trimesh.load(rec["source_scan"]["file"], force="mesh")
    if rec["landmarks"].get("backend", {}).get("backend") == "moldstudio":
        from .moldstudio_bridge import classify_landmarks
        om, lm, hf, _, _ = classify_landmarks(scan)
    else:
        om, lm, hf = find_landmarks(scan)
    ct = (rec.get("cleft_status") or {}).get("value") if cleft_type == "record" else cleft_type
    lm, cleft_info = apply_cleft(lm, ct)
    pp = PlateParams(**rec["plate"]["params"])
    if plate_thickness is not None:
        pp = replace(pp, thickness=plate_thickness)
    if plate_overrides:
        pp = replace(pp, **plate_overrides)
    plate, hfb, outline, info = design_base_plate(lm, hf, pp)
    return {"rec": rec, "lm": lm, "hf": hf, "hfb": hfb, "plate": plate, "outline": outline, "pp": pp,
            "border_y": rec["attachment"]["posterior_border_y"], "z_mid": rec["attachment"]["z_mid"], "oriented_scan": om,
            "cleft_info": cleft_info, "plate_info": info}


def chosen_extension(rec: dict, proto: int = 2, device: str | None = None) -> tuple[ExtensionParams, str, str]:
    """The design that goes to manufacture: `device` if given, else the last logged adjustment, else the
    carried design T (a plate made from an earlier plate's baton), else the given prototype. Returns (params, description, device id)."""
    from .record import device as get_device
    if device is None:
        if rec.get("modification_log"):
            device = rec["modification_log"][-1].get("id", f"A{rec['modification_log'][-1]['n']}")
        else:
            device = "T" if rec.get("carried_design") else f"P{proto}"
    d = get_device(rec, device)
    desc = {"prototype": "prototype", "adjusted": "adjusted design", "final_groove": "final design", "cast": "cast plate",
            "printed": "printed plate", "carried": "carried design", "kit": "kit baton", "chairside": "chairside plate"}[d["kind"]]
    return ExtensionParams(**d["params"]), f"{desc} {d['id']}" + (f" (from {d['from']})" if d.get("from") else ""), d["id"]
