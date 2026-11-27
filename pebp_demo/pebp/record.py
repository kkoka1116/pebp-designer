"""The design record: everything needed to reproduce, audit, or adjust a design.

Every physical or digital device has an id:
  P1-P3  printed prototypes for the endoscopy try-in (no wire)
  A<n>   a changed design or a changed physical device (n-th entry in modification_log)
  T      the accepted baton of an earlier plate carried onto a new scan (`carry`, for the next plate)
  F      the digital final design with a wire groove (older groove-and-cover route)
  C<n>   a final plate cast with a wire in printed tooling (`mold`)
  D<n>   a final plate printed directly, with the wire embedded (`print`)
  K<n>   a baton from the modular try-in kit (`kit`), slid into the kit's socket base (no wire)
  S<n>   a worn plate made chairside: a stock wired baton picked up into the patient's final base (`chairside`)
Endoscopy sessions (`fitting_sessions`) and sleep studies (`sleep_studies`) refer to these ids, so
each result is tied to the exact device that was in the mouth.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "pebp-design/0.3"   # 0.3: airway landmark vocabulary, Bioengineering 2022 plate metrics, reach check

# Clinical choices the software must not make. They are stored per case and used only for flagging.
DEFAULT_SETTINGS = {
    "tip_target": "unset",            # "above_epiglottis_rim" (Tübingen today) or "in_vallecula" (Berlin, earlier Tübingen)
    "sleep_index": "MOAI",            # mixed-obstructive apnoea index
    "sleep_target_below_per_h": 3.0,  # Tübingen: MOAI below 3 per hour
    "angle_sign": "+ swings the tip toward the tongue base, - toward the back wall of the throat",
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def new_record(scan_path: str, lm_json: dict, plate_info: dict, ext_params, frame_note: str) -> dict:
    return upgrade({
        "schema": SCHEMA,
        "created": now(),
        "source_scan": {"file": str(scan_path), "sha256": file_sha256(scan_path)},
        "frame": frame_note,
        "landmarks": lm_json,
        "plate": plate_info,
        "extension_base": asdict(ext_params),
        "prototypes": [],
        "final_design": None,
        "modification_log": [],
    })


def upgrade(record: dict) -> dict:
    """Bring an older record up to the current schema without changing what it already holds."""
    record.setdefault("clinical_settings", dict(DEFAULT_SETTINGS))
    for k, v in DEFAULT_SETTINGS.items():
        record["clinical_settings"].setdefault(k, v)
    for k in ("cast_plates", "printed_plates", "fitting_sessions", "sleep_studies"):
        record.setdefault(k, [])
    for e in record.get("modification_log", []):
        e.setdefault("id", f"A{e['n']}")
        e.setdefault("from", f"P{e['from_prototype']}" if isinstance(e.get("from_prototype"), int) else e.get("from_prototype"))
        e.setdefault("where", "lab")
        e.setdefault("wired", False)
    from .airway import upgrade_airway
    upgrade_airway(record)
    record["schema"] = SCHEMA
    return record


def save(record: dict, path: str | Path) -> None:
    Path(path).write_text(json.dumps(record, indent=2), encoding="utf-8")


def load(path: str | Path) -> dict:
    return upgrade(json.loads(Path(path).read_text(encoding="utf-8")))


def normalise_id(did) -> str:
    s = str(did).strip().upper()
    return f"P{s}" if s.isdigit() else s


def device(record: dict, did) -> dict:
    """A uniform view of any device: id, kind, file, params, wired, reserve_mm, bedside_shortening_mm."""
    did = normalise_id(did)
    if did.startswith("P"):
        p = next((q for q in record["prototypes"] if q["number"] == int(did[1:])), None)
        if p is None:
            raise KeyError(f"no prototype {did}")
        return {"id": did, "kind": "prototype", "file": p["file"], "params": p["params"], "wired": False,
                "reserve_mm": None, "bedside_shortening_mm": 0.0, "wire": None}
    if did == "T":
        t = record.get("carried_design")
        if not t:
            raise KeyError("no carried design (make one with `carry`)")
        return {"id": "T", "kind": "carried", "file": t["file"], "params": t["params"], "wired": False,
                "reserve_mm": None, "bedside_shortening_mm": 0.0, "wire": None}
    if did == "F":
        f = record.get("final_design")
        if not f:
            raise KeyError("no final design (run design with --final)")
        g = f.get("wire_groove") or {}
        return {"id": "F", "kind": "final_groove", "file": f["file"], "params": f["params"], "wired": bool(g),
                "reserve_mm": g.get("end_mm_before_tip"), "bedside_shortening_mm": 0.0, "wire": g or None}
    if did.startswith("A"):
        n = int(did[1:])
        e = next((q for q in record["modification_log"] if q["n"] == n), None)
        if e is None:
            raise KeyError(f"no adjusted design {did}")
        return {"id": did, "kind": "adjusted", "file": e["file"], "params": e["params"], "wired": e.get("wired", False),
                "reserve_mm": e.get("reserve_mm"), "bedside_shortening_mm": e.get("bedside_shortening_mm", 0.0),
                "wire": e.get("wire"), "from": e.get("from")}
    if did.startswith("C"):
        n = int(did[1:])
        c = next((q for q in record["cast_plates"] if q["n"] == n), None)
        if c is None:
            raise KeyError(f"no cast plate {did}")
        return {"id": did, "kind": "cast", "file": c["file"], "params": c["params"], "wired": True,
                "reserve_mm": c["wire"]["end_mm_before_tip"], "bedside_shortening_mm": 0.0, "wire": c["wire"], "from": c.get("from")}
    if did.startswith("D"):
        n = int(did[1:])
        c = next((q for q in record.get("printed_plates", []) if q["n"] == n), None)
        if c is None:
            raise KeyError(f"no printed plate {did}")
        return {"id": did, "kind": "printed", "file": c["file"], "params": c["params"], "wired": True,
                "reserve_mm": c["wire"]["end_mm_before_tip"], "bedside_shortening_mm": 0.0, "wire": c["wire"], "from": c.get("from")}
    if did.startswith("K"):
        kit = record.get("kit") or {}
        k = next((q for q in kit.get("batons", []) if q["id"] == did), None)
        if k is None:
            raise KeyError(f"no kit baton {did} (run `pebp kit` for this case)")
        return {"id": did, "kind": "kit", "file": k["file"], "params": k["params"], "wired": False,
                "reserve_mm": None, "bedside_shortening_mm": 0.0, "wire": None, "code": k["code"]}
    if did.startswith("S"):
        n = int(did[1:])
        c = next((q for q in record.get("chairside_finals", []) if q["n"] == n), None)
        if c is None:
            raise KeyError(f"no chairside plate {did}")
        return {"id": did, "kind": "chairside", "file": c["file"], "params": c["params"], "wired": True,
                "reserve_mm": c["wire"]["end_mm_before_tip"], "bedside_shortening_mm": 0.0, "wire": c["wire"], "from": c.get("from")}
    raise KeyError(f"unknown device id {did!r} (use P1-P3, T, A<n>, F, C<n>, D<n>, K<n> or S<n>)")


def device_ids(record: dict) -> list[str]:
    ids = [f"P{p['number']}" for p in record["prototypes"]]
    if record.get("carried_design"):
        ids.append("T")
    ids += [e.get("id", f"A{e['n']}") for e in record["modification_log"]]
    if record.get("final_design"):
        ids.append("F")
    ids += [f"C{c['n']}" for c in record.get("cast_plates", [])]
    ids += [f"D{c['n']}" for c in record.get("printed_plates", [])]
    ids += [k["id"] for k in (record.get("kit") or {}).get("batons", [])]
    ids += [f"S{c['n']}" for c in record.get("chairside_finals", [])]
    return ids
