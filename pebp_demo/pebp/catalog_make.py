"""Manufacturing plan for one catalog version: `pebp catalog CASE --make VERSION`.
(Named catalog_make.py so it sits beside make.py, the site-kit manufacturing planner `pebp make`.)

The catalog (catalog.py) holds print files for every try-in version. The worn appliance is cast cold-cure PMMA with
a cast-in safety wire, and its tooling is made for one design at a time. This builds that design for the version
the clinicians chose, in its own folder so the case's own design is untouched:

  catalog/make/<VERSION>/
    design.json, base_plate.stl ...      `pebp design` re-run from the case's scan with the version's cleft type,
                                         plate profile and border relief (other plate settings copied from the case)
    kit/                                 the version's socket base and its 15 kit batons (try-in)
    kit/chairside/                       two-visit route: visit-1 final base + its casting tooling; visit-2 stock
                                         final baton K<n> with its wire, the pick-up resin and the assembled plate S1
    stock_tooling/K<n>/                  mold and wire jig for that stock final baton
    mold/                                one-piece fallback: the cast plate with K<n> and its wire, four-piece mold
    PLAN.md, make.json                   the steps, the files for each, materials, QC, and what is untested

Status: project heuristic, nothing printed, cast or bench-tested. On a real scan it runs on the local machine and
everything it writes is patient data.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from . import record as rec
from .runtime import pebp_command, work_dir

GROUP_CLEFT = {"C": "hard-soft", "N": "none"}


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run([*pebp_command(), *args], cwd=cwd, capture_output=True, text=True)


def make(case_dir: Path, cat: dict, version_id: str, log=print) -> dict:
    from .chairside import ChairsideParams, build as cs_build, build_base
    from .chairside_tooling import export_stock_tooling
    from .kit import KitParams, build as kit_build
    from .mold import MoldParams, build as mold_build
    case_dir = Path(case_dir).resolve()
    v = next((x for x in cat["versions"] if x["id"] == version_id), None)
    if v is None:
        raise ValueError(f"no version {version_id} in this catalog")
    record = rec.load(case_dir / "design.json")
    entered = (record.get("cleft_status") or {}).get("value")
    ct = entered.replace("_", "-") if entered in v["cleft_types"] else GROUP_CLEFT[v["group"]]
    pp = record["plate"]["params"]
    out = case_dir / "catalog" / "make" / version_id
    out.mkdir(parents=True, exist_ok=True)
    pkg_root = work_dir()
    steps_log = []

    def step(name, ok, detail=""):
        steps_log.append({"step": name, "ok": bool(ok), "detail": detail})
        log(f"  {'ok ' if ok else 'FAILED'} {name}" + (f": {detail}" if detail else ""))
        return ok

    # 1. the design for this version
    args = ["design", record["source_scan"]["file"], "--out", str(out), "--cleft", ct,
            "--cleft-entered-by", (record.get("cleft_status") or {}).get("entered_by") or "catalog",
            "--plate-profile", v["plate_profile"], "--flange", str(pp.get("flange", 3.0)), "--block-out", str(pp.get("block_out", 2.5)),
            "--backend", record["landmarks"].get("backend", {}).get("backend", "builtin")]
    if v["border_relief"]:
        args.append("--border-relief")
    r = _run(args, pkg_root)
    if not step("design for the version", r.returncode == 0, (r.stderr or r.stdout).strip().splitlines()[-1] if r.returncode else ct):
        raise RuntimeError("pebp design failed:\n" + r.stderr[-2000:])
    # 2. try-in kit on the version's base
    kp = KitParams(variant=v["baton_variant"])
    kit = kit_build(out, kp, stock=False)
    kn = v["baton"]
    kb = next(b for b in kit["batons"] if b["id"] == kn)
    same = abs(kb["d_length_mm"] - v["d_length_mm"]) < 1e-6 and abs(kb["d_angle_deg"] - v["d_angle_deg"]) < 1e-6
    step("try-in kit", same, f"{kn} = {kb['code']} (catalog {v['code']})")
    if not same:
        raise RuntimeError(f"kit baton {kn} is {kb['code']}, the catalog version is {v['code']}: kit grid differs")
    # 3. two-visit chairside route
    cp = ChairsideParams()
    base = build_base(out, cp=cp)
    step("visit 1: final base and its casting tooling", base["qc"]["overall"] != "fail", f"QC {base['qc']['overall']}")
    s = cs_build(out, kn, cp=cp)
    step(f"visit 2: stock final baton {kn}, wire, pick-up, assembled {s['id']}", s["qc"]["overall"] != "fail", f"QC {s['qc']['overall']}")
    st = export_stock_tooling(out / "stock_tooling", kp, cp, [kn])
    step(f"stock final baton {kn}: mold and wire jig", all(b["ok"] for b in st["batons"]))
    # 4. one-piece fallback
    mdir = mold_build(out, MoldParams(final_plate_thickness=cp.final_thickness, device=kn))
    mman = json.loads((mdir / "mold_manifest.json").read_text())
    mv = mman["validation"]
    step(f"one-piece cast plate with {kn}: four-piece mold", mv.get("ok", False), f"final QC {mv['final_qc']['overall']}")

    ka, ca = kit.get("attachment", {}), base.get("attachment", {})
    joint_ok = s["qc"]["overall"] != "fail" and abs(ca.get("z_mid", 0) - ka.get("z_mid", 0)) < 0.01
    route = "two-visit" if joint_ok else "one-piece"
    route_why = ("the two-visit joint fits with the baton at the try-in height" if joint_ok else
                 "the two-visit joint does not fit this palate cleanly (" + ("visit-2 QC fails" if s["qc"]["overall"] == "fail" else
                 "the worn baton would sit lower than the tried-in one") + "): cast the one-piece plate with the chosen baton")
    rel = lambda p: str(Path(p).resolve().relative_to(case_dir)) if Path(p).is_absolute() else f"catalog/make/{version_id}/{p}"
    plan = {"kind": "pebp-make-plan", "version": version_id, "case": case_dir.name, "cleft_type": ct,
            "plate_profile": v["plate_profile"], "border_relief": v["border_relief"], "baton": kn, "baton_code": v["code"],
            "baton_variant": v["baton_variant"], "steps": steps_log,
            "ok": all(x["ok"] for x in steps_log if route == "two-visit" or not x["step"].startswith("visit 2")),
            "route": route, "route_why": route_why,
            "try_in": {"base": v["base_file"], "baton": v["baton_file"]},
            "visit1": {"final_base": rel(base["file"]), "tooling_dir": rel(base["tooling"]["dir"]),
                       "tooling": base["tooling"]["files"], "pmma_ml": base["tooling"]["pmma_needed_ml"], "qc": base["qc"]},
            "visit2": {"id": s["id"], "files": {k: rel(f) for k, f in s["files"].items()}, "pickup_mm3": s["pickup_volume_mm3"],
                       "wire": s["wire"], "steps": s["steps"], "qc": s["qc"]},
            "stock_tooling": {"dir": rel(out / "stock_tooling"), "batons": st["batons"]},
            "one_piece": {"dir": rel(mdir), "files": mman["files"], "qc": mv["final_qc"]},
            "flag": "project heuristic: nothing printed, cast or bench-tested; try-in under endoscopic supervision only. "
                    "The two-visit joint is unproven until the bench tests in docs/CHAIRSIDE_BENCH.md pass; one-piece casting "
                    "stays the fallback.", "time": rec.now()}
    (out / "make.json").write_text(json.dumps(plan, indent=1, default=str))
    (out / "PLAN.md").write_text(_plan_md(plan))
    record.setdefault("catalog_makes", {})[version_id] = {"dir": f"catalog/make/{version_id}", "ok": plan["ok"], "time": plan["time"]}
    rec.save(record, case_dir / "design.json")
    return plan


def _qc_line(q):
    bad = [f"{c['check']} ({c['status']})" for c in q["checks"] if c["status"] != "pass"]
    return q["overall"] + (": " + "; ".join(bad) if bad else "")


def _plan_md(p: dict) -> str:
    v1, v2, op = p["visit1"], p["visit2"], p["one_piece"]
    L = [f"# Manufacturing plan: {p['version']}", "",
         f"Case {p['case']}. Cleft type {p['cleft_type']}; plate {p['plate_profile']}, border relief {'on' if p['border_relief'] else 'off'}; "
         f"baton {p['baton']} ({p['baton_code']}, Tübingen {p['baton_variant']} extension).", "",
         f"> {p['flag']}", "",
         f"**Route for this patient: {p['route']}**, because {p['route_why']}.", "",
         "## 1. Try-in (printed, short contact, under endoscopy)",
         f"- Print the base `{p['try_in']['base']}` and the baton `{p['try_in']['baton']}` (and its neighbours in the kit) in a biocompatible resin.",
         "- Slide the baton into the socket, tie the floss through the socket and plug holes, keep the tail outside the mouth.",
         "- Confirm the baton at endoscopy; record the version in the Studio.", "",
         "## 2. Visit 1: cast the final base (cold-cure PMMA)",
         f"- Final base: `{v1['final_base']}`; QC {_qc_line(v1['qc'])}.",
         f"- Print the tooling in `{v1['tooling_dir']}/`: " + ", ".join(f"`{Path(f).name}`" for f in v1["tooling"].values()) + ".",
         f"- Separator on every face, seat the tunnel core, close the counter, pour about {v1['pmma_ml']} ml PMMA through the sprue, pressure-cure.",
         "- Demold: tunnel core out backward, counter up, base off the working model.", "",
         f"## 3. Visit 2: fix stock final baton {p['baton']} (device {v2['id']})",
         f"- Cast the stock final baton with its wire in the mold and jig in `{p['stock_tooling']['dir']}/`.",
         f"- Wire: {v2['wire'].get('length_mm')} mm, crossing {v2['wire'].get('crosses_joint_by_mm')} mm into the pocket, ending "
         f"{v2['wire'].get('end_mm_before_tip')} mm before the tip. Pick-up resin about {v2['pickup_mm3']} mm³.",
         *[f"{i + 1}. {t}" for i, t in enumerate(v2["steps"])],
         f"- Assembled plate: `{v2['files'].get('assembled')}`; QC {_qc_line(v2['qc'])}.", "",
         f"## 4. Fallback: one-piece cast plate with {p['baton']}",
         f"- Four-piece mold, cradle and wire jig in `{op['dir']}/`: " + ", ".join(f"`{f}`" for f in op["files"].values()) + ".",
         f"- Final-plate QC {_qc_line(op['qc'])}. Steps: docs/BUILD_WORKFLOW.md.", "",
         "## Build log",
         *[f"- {'ok' if s['ok'] else 'FAILED'}: {s['step']}" + (f" ({s['detail']})" if s["detail"] else "") for s in p["steps"]], ""]
    return "\n".join(L)
