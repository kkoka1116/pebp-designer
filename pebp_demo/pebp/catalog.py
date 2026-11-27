"""Device catalog: every modular version of the try-in device for one patient, narrowed by choices.

The modular kit (kit.py) splits the device into a base made for the patient and a slide-in baton. This
module builds every base and every baton the patient could need, so the clinician narrows a finished set
instead of asking for a rebuild each time a choice changes:

  bases    one socket base per plate group x plate option:
             group C   cleft of the hard and soft palate: cleft bridging, 2 mm posterior midline bulge
             group N   soft palate or uvula only, or no cleft: gaps bridged as scan holes, no bulge
             options   plate profile (uniform | graded) x border relief (off | on)
  batons   the kit grid (5 lengths x 3 angles = 15) of the baton that goes with the group's cleft types:
           the Tübingen cleft extension for C, the no-cleft extension for N, placed on that group's
           attachment (posterior border and plate mid-thickness)

A version is one base with one baton. Each carries its attributes (cleft types it serves, baton variant,
plate profile, border relief, length and angle step) so `narrow()` can filter by any of them. The cleft
type is a clinical entry; length and angle are chosen at the endoscopic try-in. The software only lists.

Status: project heuristic, untested, like the kit itself. Synthetic cases only in the cloud: on a real scan,
run it on the local machine (patient data never leaves it).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

import numpy as np
import trimesh

from . import cleft as cleftmod
from .kit import KitParams, LABEL as KIT_LABEL, baton_piece, drop_check, library, socket_base, socket_drop
from .meshutil import manifold_boolean

LABEL = ("project heuristic: per-patient catalog of modular try-in versions, untested; try-in under direct "
         "endoscopic supervision only, never worn. The cleft type is a clinical entry and the software never picks a version")

GROUPS = {
    "C": {"cleft_types": ["hard_soft"], "baton": "cleft", "rep": "hard_soft",
          "text": "cleft of the hard and soft palate: Tübingen cleft baton, cleft block-out, 2 mm midline bulge"},
    "N": {"cleft_types": ["soft_only", "none"], "baton": "nocleft", "rep": "none",
          "text": "soft palate or uvula only, or no cleft: Tübingen no-cleft baton, gaps bridged as scan holes, no midline bulge"},
}
CHOICES = ("cleft_type", "plate_profile", "border_relief", "d_length_mm", "d_angle_deg")


@dataclass
class CatalogParams:
    profiles: tuple = ("uniform", "graded")
    relief: tuple = (False, True)
    groups: tuple = ("C", "N")
    kit: KitParams = field(default_factory=KitParams)


def option_code(group: str, profile: str, relief: bool) -> str:
    return f"{group}-{'G' if profile == 'graded' else 'U'}{'R' if relief else ''}"


def _attachment(info: dict, hfb, thickness: float) -> tuple[float, float]:
    """Posterior border and plate mid-thickness at the root, as `pebp design` computes them."""
    by = float(info["posterior_border_y_mid"])
    z_top = float(hfb(np.array([[0.0, by + 1.0]]))[0])
    return by, z_top - thickness / 2.0


def _tip(mesh: trimesh.Trimesh) -> list:
    return mesh.vertices[int(np.argmin(mesh.vertices[:, 2]))].round(2).tolist()


def build(case_dir: Path, cp: CatalogParams | None = None, log=print) -> dict:
    """Build every base and baton for the case and write catalog/catalog.json. Returns the catalog."""
    from . import record as rec
    from .landmarks import find_landmarks
    from .plate import PlateParams, design_base_plate
    from .qc import STANDARD_REVIEW_MM, _check, overall
    cp = cp or CatalogParams()
    kp0 = cp.kit
    case_dir = Path(case_dir)
    record = rec.load(case_dir / "design.json")
    if record["landmarks"].get("backend", {}).get("backend") == "moldstudio":
        from .moldstudio_bridge import classify_landmarks
        _, lm0, hf, _, _ = classify_landmarks(trimesh.load(record["source_scan"]["file"], force="mesh"))
    else:
        _, lm0, hf = find_landmarks(trimesh.load(record["source_scan"]["file"], force="mesh"))
    out = case_dir / "catalog"
    (out / "bases").mkdir(parents=True, exist_ok=True)
    (out / "batons").mkdir(parents=True, exist_ok=True)
    base_pp = PlateParams(**record["plate"]["params"])
    bases, batons, versions = [], {}, []
    for g in cp.groups:
        G = GROUPS[g]
        lm, cinfo = cleftmod.apply(lm0, G["rep"])
        kp = replace(kp0, variant=G["baton"])
        lib = library(kp)
        for profile in cp.profiles:
            for relief in cp.relief:
                code = option_code(g, profile, relief)
                pp = replace(base_pp, thickness=kp.prototype_thickness, profile=profile, border_relief=relief,
                             **cleftmod.plate_defaults(G["rep"]))
                plate, hfb, outline, info = design_base_plate(lm, hf, pp)
                by, zm0 = _attachment(info, hfb, pp.thickness)
                drop = socket_drop(hfb, by, zm0, kp)
                zm = zm0 - drop
                base, binfo = socket_base(plate, hfb, outline, by, zm, kp)
                bfile = f"catalog/bases/{code}.stl"
                base.export(case_dir / bfile)
                bchecks = [
                    _check("base watertight", base.is_watertight and base.body_count == 1,
                           f"{bool(base.is_watertight)}, {base.body_count} body", "one closed solid for printing"),
                    _check("plate above the tunnel", binfo["roof_min_mm"] >= kp.min_roof, binfo["roof_min_mm"],
                           f">= {kp.min_roof} mm of plate between the tunnel and the scanned surface (project heuristic)"),
                    drop_check(drop),
                ]
                bentry = {"id": code, "group": g, "cleft_types": G["cleft_types"], "plate_profile": profile,
                          "border_relief": relief, "posterior_bulge_mm": pp.posterior_bulge, "file": bfile,
                          "sha256": rec.file_sha256(case_dir / bfile), "attachment": {"border_y": round(by, 2), "z_mid": round(zm, 2),
                                                                                       "z_mid_plate": round(zm0, 2), "socket_drop_mm": drop},
                          "volume_mm3": round(float(base.volume), 1), "socket": binfo, "bridging": cinfo["bridging"],
                          "bridging_warnings": cinfo["warnings"],
                          "qc": {"checks": bchecks, "overall": overall(bchecks)}}
                bases.append(bentry)
                # batons depend only on the variant and the attachment, so options sharing both share files
                akey = (G["baton"], round(by, 2), round(zm, 2))
                if akey not in batons:
                    sub = f"{G['baton']}_y{by:+.2f}_z{zm:+.2f}".replace("+", "p").replace("-", "m").replace(".", "_")
                    (out / "batons" / sub).mkdir(exist_ok=True)
                    rows = {}
                    for e in lib:
                        piece = baton_piece(e, kp, by, zm)
                        fn = f"catalog/batons/{sub}/{e['id']}.stl"
                        piece.export(case_dir / fn)
                        rows[e["id"]] = {**{k: e[k] for k in ("id", "code", "d_length_mm", "d_angle_deg", "params")},
                                         "file": fn, "sha256": rec.file_sha256(case_dir / fn), "tip": _tip(piece),
                                         "watertight": bool(piece.is_watertight and piece.body_count == 1), "mesh": piece}
                    t0 = np.array(next(r["tip"] for r in rows.values() if r["d_length_mm"] == 0 and r["d_angle_deg"] == 0))
                    for r in rows.values():
                        d = np.array(r["tip"]) - t0
                        r["tip_shift_vs_standard_mm"] = {"y_anterior": round(float(d[1]), 2), "z_superior": round(float(d[2]), 2)}
                    batons[akey] = rows
                for k, b in batons[akey].items():
                    clash = float(manifold_boolean("intersection", [b["mesh"], base]).volume) if b["mesh"].is_volume and base.is_volume else None
                    checks = bchecks + [
                        _check("baton watertight", b["watertight"], b["watertight"], "one closed solid for printing"),
                        _check("baton clear of base", clash is not None and clash < 0.05, None if clash is None else round(clash, 3),
                               "plug and base do not overlap when assembled (mm3)"),
                        _check("extension length vs standard", abs(b["d_length_mm"]) <= STANDARD_REVIEW_MM, b["d_length_mm"],
                               f"within ±{STANDARD_REVIEW_MM:.0f} mm of the standard extension; beyond that, review "
                               "(project heuristic: no published limit)", level="warn")]
                    versions.append({"id": f"{code}-{k}", "base": code, "baton": k, "group": g,
                                     "cleft_types": G["cleft_types"], "baton_variant": G["baton"], "plate_profile": profile,
                                     "border_relief": relief, "d_length_mm": b["d_length_mm"], "d_angle_deg": b["d_angle_deg"],
                                     "code": b["code"], "standard": b["d_length_mm"] == 0 and b["d_angle_deg"] == 0,
                                     "base_file": bfile, "baton_file": b["file"], "tip": b["tip"],
                                     "tip_shift_vs_standard_mm": b["tip_shift_vs_standard_mm"],
                                     "qc": {"checks": checks, "overall": overall(checks)}})
                log(f"{code}: base {base.volume:.0f} mm3, roof {binfo['roof_min_mm']} mm, border y {by:.2f}, "
                    f"{len(lib)} {G['baton']} batons")
    baton_list = [{k: v for k, v in r.items() if k != "mesh"} | {"variant": a[0], "attachment": {"border_y": a[1], "z_mid": a[2]}}
                  for a, rows in batons.items() for r in rows.values()]
    entered = (record.get("cleft_status") or {}).get("value")
    cat = {"kind": "pebp-device-catalog", "flag": LABEL, "kit_flag": KIT_LABEL, "case": str(case_dir),
           "source_scan_sha256": record["source_scan"]["sha256"], "groups": {g: GROUPS[g] for g in cp.groups},
           "choices": {"cleft_type": {t: cleftmod.CLEFT_TYPES[t]["label"] for t in cleftmod.CLEFT_TYPES},
                       "plate_profile": list(cp.profiles), "border_relief": list(cp.relief),
                       "d_length_mm": list(kp0.lengths), "d_angle_deg": list(kp0.angles)},
           "choice_basis": {"cleft_type": "clinical entry from the examination", "plate_profile": "design choice (graded = project heuristic)",
                            "border_relief": "design choice (project heuristic)", "d_length_mm": "endoscopic try-in",
                            "d_angle_deg": "endoscopic try-in"},
           "entered_cleft_type": entered, "kit_params": asdict(kp0), "bases": bases, "batons": baton_list,
           "versions": versions, "counts": {"bases": len(bases), "batons": len(baton_list), "versions": len(versions)},
           "time": rec.now()}
    (out / "catalog.json").write_text(json.dumps(cat, indent=1, default=str), encoding="utf-8")
    record["catalog"] = {"file": "catalog/catalog.json", "sha256": rec.file_sha256(out / "catalog.json"), "flag": LABEL,
                         "counts": cat["counts"], "time": cat["time"]}
    rec.save(record, case_dir / "design.json")
    return cat


def load(case_dir: Path) -> dict | None:
    p = Path(case_dir) / "catalog" / "catalog.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _as_bool(v):
    if isinstance(v, bool) or v is None:
        return v
    s = str(v).lower()
    if s in ("on", "yes", "true", "1"):
        return True
    if s in ("off", "no", "false", "0"):
        return False
    raise ValueError(f"border relief is on or off, not {v!r}")


def narrow(cat: dict, cleft_type=None, plate_profile=None, border_relief=None, d_length_mm=None, d_angle_deg=None,
           passing_only: bool = False) -> dict:
    """The versions left after the given choices (None = not chosen yet), and for each choice the values
    still open with how many versions each would leave."""
    chosen = {"cleft_type": cleftmod.normalise(cleft_type) if cleft_type not in (None, "any") else None,
              "plate_profile": plate_profile or None, "border_relief": _as_bool(border_relief),
              "d_length_mm": None if d_length_mm in (None, "") else float(d_length_mm),
              "d_angle_deg": None if d_angle_deg in (None, "") else float(d_angle_deg)}

    def keep(v, skip=None):
        for k, val in chosen.items():
            if k == skip or val is None:
                continue
            if k == "cleft_type":
                if val not in v["cleft_types"]:
                    return False
            elif v[k] != val:
                return False
        return not passing_only or v["qc"]["overall"] != "fail"
    left = [v for v in cat["versions"] if keep(v)]
    open_ = {}
    for k in CHOICES:
        counts = {}
        for v in cat["versions"]:
            if not keep(v, skip=k):
                continue
            keys = v["cleft_types"] if k == "cleft_type" else [v[k]]
            for key in keys:
                counts[str(key)] = counts.get(str(key), 0) + 1
        open_[k] = counts
    return {"chosen": chosen, "versions": left, "count": len(left), "open": open_,
            "bases": sorted({v["base"] for v in left}), "batons": sorted({(v["baton_variant"], v["baton"]) for v in left})}


def assemble(case_dir: Path, cat: dict, version_id: str) -> Path:
    """One version with its baton in the socket, as one STL for viewing (the parts are printed separately)."""
    v = next((x for x in cat["versions"] if x["id"] == version_id), None)
    if v is None:
        raise ValueError(f"no version {version_id!r} in the catalog")
    case_dir = Path(case_dir)
    parts = [trimesh.load(case_dir / v["base_file"], force="mesh"), trimesh.load(case_dir / v["baton_file"], force="mesh")]
    out = case_dir / "catalog" / "assembled" / f"{version_id}.stl"
    out.parent.mkdir(parents=True, exist_ok=True)
    trimesh.util.concatenate(parts).export(out)
    return out
