"""Checks for the modular try-in kit (`pebp kit`), on synthetic data only.

    .venv/bin/python scripts/kit_check.py            (about 2 minutes)

Builds out/kit_check from a synthetic Robin-sequence palate and asserts: the stock batons are printable
and identical to the ones placed on the case (so a stock set can be printed ahead of time), every kit
baton passes QC with no hard failure, the plug clears the socket, the plate above the tunnel is thick
enough, kit batons are ordinary devices (bedside change, airway metrics, cast plate with its wire),
the no-cleft variant builds, the chairside worn plate (S<n>) is wired across its joint, and an over-narrow
kit is refused. Never touches real patient scans.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import trimesh

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "out" / "kit_check"
sys.path.insert(0, str(ROOT))
results = []


def run(*args, ok=True) -> subprocess.CompletedProcess:
    r = subprocess.run([sys.executable, "-m", "pebp", *map(str, args)], cwd=ROOT, capture_output=True, text=True)
    if ok and r.returncode != 0:
        raise SystemExit(f"pebp {' '.join(map(str, args))} failed:\n{r.stdout}\n{r.stderr}")
    return r


def check(name: str, ok: bool, detail: str):
    results.append((ok, name, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")


def main():
    shutil.rmtree(OUT, ignore_errors=True)
    case = OUT / "case"
    run("synth", "--preset", "prs", "--out", case, "--unoriented", "--asym", "1.0")
    run("design", case / "scan.stl", "--out", case)
    run("kit", case)
    r = json.loads((case / "design.json").read_text(encoding="utf-8"))
    kit = r["kit"]
    B = kit["batons"]
    check("15 kit batons registered", len(B) == 15 and [b["id"] for b in B] == [f"K{i}" for i in range(1, 16)], str(len(B)))
    fails = {b["id"]: [c["check"] for c in b["qc"]["checks"] if c["status"] == "fail"] for b in B}
    check("no kit baton fails QC", not any(fails.values()), str({k: v for k, v in fails.items() if v}) or "all pass or warn")
    warns = sorted({c["check"] for b in B for c in b["qc"]["checks"] if c["status"] == "warn"})
    check("only the long batons warn, on length", set(warns) <= {"extension length vs standard"}, str(warns))
    check("socket roof", kit["socket"]["roof_min_mm"] >= 1.0, f"{kit['socket']['roof_min_mm']} mm of plate above the tunnel")
    check("plug clear of base", all(next(c for c in b["qc"]["checks"] if c["check"] == "baton clear of base")["value"] < 0.05 for b in B),
          "assembled overlap < 0.05 mm3 for every baton")
    k5 = next(b for b in B if b["code"] == "L+0/T+0")
    std = r["prototypes"][1]["tip"]
    check("K5 is the published standard", np.allclose(k5["tip"], std, atol=0.3) and k5["params"] == r["prototypes"][1]["params"],
          f"K5 tip {k5['tip']} vs P2 {std}")
    sh = {b["code"]: b["tip_shift_vs_standard_mm"] for b in B}
    check("length steps move the tip by the step", abs(sh["L+4/T+0"]["z_superior"] + 4.0) < 0.3 and abs(sh["L+4/T+0"]["y_anterior"]) < 0.3,
          f"L+4 shift {sh['L+4/T+0']}")
    check("+angle moves the tip toward the tongue base", sh["L+0/T+8"]["y_anterior"] > 1.5 and sh["L+0/T-8"]["y_anterior"] < -1.5,
          f"T+8 {sh['L+0/T+8']['y_anterior']}, T-8 {sh['L+0/T-8']['y_anterior']} mm")
    env = kit["tip_envelope"]
    check("kit spans more than P1-P3 front to back", env["tip_y_range_mm"] > 4.0, f"{env['tip_y_range_mm']} mm vs 4.0")

    # stock set: printable, and the same parts the case uses
    stock = case / "kit" / "stock"
    man = json.loads((stock / "kit_stock_manifest.json").read_text(encoding="utf-8"))
    check("stock batons printable", all(b["watertight"] and b["bodies"] == 1 for b in man["batons"]), f"{len(man['batons'])} parts")
    by, zm = r["attachment"]["posterior_border_y"], r["attachment"]["z_mid"]
    worst = 0.0
    for b in man["batons"]:
        s = trimesh.load(stock / b["file"]); s.apply_translation([0.0, by, zm])
        c = trimesh.load(case / "kit" / "batons" / f"{b['id']}.stl")
        worst = max(worst, float(np.abs(s.bounds - c.bounds).max()), abs(s.volume - c.volume))
    check("stock batons are patient-independent", worst < 1e-3, f"worst bounds/volume difference {worst:.1e}")
    base = trimesh.load(case / kit["base_file"])
    check("base printable", base.is_watertight and base.body_count == 1, f"watertight {base.is_watertight}, {base.body_count} body")

    # a second synthetic palate gets the same stock parts
    case2 = OUT / "case2"
    run("synth", "--no-cleft", "--out", case2, "--seed", "11")
    run("design", case2 / "scan.stl", "--out", case2)
    run("kit", case2, "--no-stock")
    r2 = json.loads((case2 / "design.json").read_text(encoding="utf-8"))
    f2 = [b["id"] for b in r2["kit"]["batons"] if b["qc"]["overall"] == "fail"]
    check("kit fits a second, no-cleft palate", not f2 and r2["kit"]["variant"] == "nocleft", f"variant {r2['kit']['variant']}, roof {r2['kit']['socket']['roof_min_mm']} mm; fails {f2}")

    # kit batons are ordinary devices
    check("kit batons in the airway metrics", all(b["id"] in r["tpp_metrics"] for b in B), "tpp_metrics has K1-K15")
    out = run("adjust", case / "design.json", "--from", "K8", "--bedside", "--mod", "shorten_mm=1", "--note", "kit check").stdout
    r = json.loads((case / "design.json").read_text(encoding="utf-8"))
    a1 = r["modification_log"][-1]
    check("bedside change of a kit baton", a1["from"] == "K8" and abs(a1["params"]["effective_length"] - (B[7]["params"]["effective_length"] - 1)) < 1e-6,
          out.strip().splitlines()[-1])
    out = run("mold", case, "--device", "K8").stdout
    r = json.loads((case / "design.json").read_text(encoding="utf-8"))
    c1 = r["cast_plates"][-1]
    check("chosen kit baton goes straight to the cast plate", c1["from"] == "K8" and c1["params"] == B[7]["params"] and "ok=True" in out,
          f"C{c1['n']} from {c1['from']}, wire {c1['wire']['start_mm_after_root']}-{c1['wire']['end_mm_before_tip']} mm")

    # chairside route, visit 2 before visit 1 is refused; visit 1: the final base and its casting tooling
    early = run("chairside", case, "--kit", "K8", ok=False)
    check("chairside needs the base first", early.returncode == 2 and "--base" in early.stderr, early.stderr.strip()[:90])
    out = run("chairside", case, "--base").stdout
    r = json.loads((case / "design.json").read_text(encoding="utf-8"))
    cb = r["chairside_base"]
    tman = json.loads((case / cb["tooling"]["dir"] / "manifest.json").read_text(encoding="utf-8"))
    check("final base passes QC", cb["qc"]["overall"] == "pass", out.strip().splitlines()[0])
    check("final base tooling demolds", tman["validation"]["ok"] and [d["piece"] for d in tman["validation"]["demold"]]
          == ["tunnel_core", "counter_oral", "cast final base"], ", ".join(f"{d['piece']} {'free' if d['free'] else 'LOCKED'}" for d in tman["validation"]["demold"]))
    # visit 2: the chosen baton fixed into the worn plate, wire across the joint
    out = run("chairside", case, "--kit", "K8").stdout
    r = json.loads((case / "design.json").read_text(encoding="utf-8"))
    s1 = r["chairside_finals"][-1]
    q = {c["check"]: c for c in s1["qc"]["checks"]}
    check("chairside plate passes QC", s1["qc"]["overall"] == "pass", out.strip().splitlines()[0])
    check("chairside wire enclosed and across the joint", q["wire enclosed"]["status"] == "pass" and s1["wire"]["crosses_joint_by_mm"] >= 8.0
          and q["wire hooked in the pocket"]["status"] == "pass",
          f"{s1['wire']['crosses_joint_by_mm']} mm across, hook {q['wire hooked in the pocket']['value']} mm above the tunnel roof")
    check("chairside plate is 2.5 mm and wired", q["plate thickness"]["value"] >= 2.5 and q["safety wire present"]["status"] == "pass",
          f"plate {q['plate thickness']['value']} mm")
    check("chairside plate in the registry", s1["id"] in r["tpp_metrics"] and s1["params"] == B[7]["params"], f"{s1['id']} from {s1['from']}")
    run("chairside", case2, "--base")
    out = run("chairside", case2, "--kit", "K5").stdout
    r2 = json.loads((case2 / "design.json").read_text(encoding="utf-8"))
    check("chairside on the no-cleft palate", r2["chairside_finals"][-1]["qc"]["overall"] == "pass", out.strip().splitlines()[0])

    # no-cleft stock and the width rule
    run("kit", "--stock", OUT / "stock_nocleft", "--variant", "nocleft")
    mn = json.loads((OUT / "stock_nocleft" / "kit_stock_manifest.json").read_text(encoding="utf-8"))
    check("no-cleft stock set", all(b["watertight"] and b["bodies"] == 1 for b in mn["batons"]), f"{len(mn['batons'])} parts")
    bad = run("kit", case, "--width", "5.0", ok=False)
    st = run("chairside", "--stock-tooling", OUT / "cs_stock", "--only", "K1,K5,K15").stdout
    sm = json.loads((OUT / "cs_stock" / "stock_tooling_manifest.json").read_text(encoding="utf-8"))
    check("stock final-baton molds demold", len(sm["batons"]) == 3 and all(b["ok"] for b in sm["batons"]),
          ", ".join(f"{b['id']} {'ok' if b['ok'] else 'FAILED'}" for b in sm["batons"]))
    k15 = json.loads((OUT / "cs_stock" / "K15" / "manifest.json").read_text(encoding="utf-8"))["validation"]
    check("mold pins clear of the wire", k15["pins_clear_of_wire_mm3"] <= 0.05, f"{k15['pins_clear_of_wire_mm3']} mm3 (K15)")
    bench = run("bench", OUT / "bench", ok=False)
    bm = json.loads((OUT / "bench" / "bench_manifest.json").read_text(encoding="utf-8")) if (OUT / "bench" / "bench_manifest.json").exists() else {}
    check("bench specimens and their tooling", bench.returncode == 0 and bm.get("ok") and len(bm["tests"]) == 8,
          ", ".join(k for k, v in bm.get("validation", {}).items() if not v) or f"{len(bm.get('files', {}))} files, 8 tests")
    # every modular version for this patient (catalog.py), narrowed by choices
    cr = run("catalog", case, "--build", "--cleft", "any", ok=False)
    from pebp.catalog import load as load_cat, narrow
    cat = load_cat(case) or {"versions": [], "counts": {}}
    check("catalog: 8 bases, 30 batons, 120 versions, none failing; the +6 mm batons flagged for review",
          cr.returncode == 0 and cat["counts"] == {"bases": 8, "batons": 30, "versions": 120}
          and all(v["qc"]["overall"] == ("warn" if v["d_length_mm"] > 4 else "pass") for v in cat["versions"]), f"{cat['counts']}")
    gN = [b for b in cat.get("bases", []) if b["group"] == "N"]; gC = [b for b in cat.get("bases", []) if b["group"] == "C"]
    check("catalog groups differ at the junction: no-cleft bases end 2 mm further forward",
          gN and gC and abs((gN[0]["attachment"]["border_y"] - gC[0]["attachment"]["border_y"]) - 2.0) < 0.05,
          f"border y C {gC[0]['attachment']['border_y'] if gC else '?'} N {gN[0]['attachment']['border_y'] if gN else '?'}")
    n1 = narrow(cat, cleft_type="none", plate_profile="graded", border_relief="on", d_length_mm=0, d_angle_deg=8)
    check("narrowing to one version", n1["count"] == 1 and n1["versions"][0]["id"] == "N-GR-K6", str([v["id"] for v in n1["versions"]]))
    vw = run("catalog", case, "--viewer", ok=False)
    vf = case / "catalog" / "catalog_viewer.html"
    vt = vf.read_text(encoding="utf-8") if vf.exists() else ""
    vd = json.loads(vt.split("const D = ", 1)[1].split(";\n", 1)[0]) if "const D = " in vt else {}
    check("offline catalog viewer: every version and mesh embedded, no network loads",
          vw.returncode == 0 and len(vd.get("versions", [])) == 120 and "scan" in vd.get("meshes", {})
          and all(v["base_file"] in vd["meshes"] and v["baton_file"] in vd["meshes"] for v in vd.get("versions", []))
          and "<script src=" not in vt and "THREE.WebGLRenderer" in vt, f"{len(vt) / 1e6:.1f} MB, {len(vd.get('meshes', {}))} meshes")
    # a palate that falls away over the tunnel: forced here by asking for more plate above it than the synthetic gives
    from pebp.context import rebuild as _rb
    from pebp.kit import KitParams as _KP, socket_drop, socket_base as _sb
    cx = _rb(case, plate_thickness=2.0)
    kp4 = _KP(min_roof=4.0)
    d4 = socket_drop(cx["hfb"], cx["border_y"], cx["z_mid"], kp4)
    b4, i4 = _sb(cx["plate"], cx["hfb"], cx["outline"], cx["border_y"], cx["z_mid"] - d4, kp4)
    check("socket lowered until the plate over the tunnel is thick enough",
          d4 > 0 and i4["roof_min_mm"] >= 4.0 and b4.is_watertight and b4.body_count == 1
          and socket_drop(cx["hfb"], cx["border_y"], cx["z_mid"], _KP()) == 0.0,
          f"drop {d4} mm, roof {i4['roof_min_mm']} mm (no drop needed on the synthetic palate at 1 mm)")
    mk = run("catalog", case, "--make", "N-GR-K8", ok=False)
    mj = case / "catalog" / "make" / "N-GR-K8" / "make.json"
    mp = json.loads(mj.read_text(encoding="utf-8")) if mj.exists() else {}
    check("manufacturing plan for a chosen version: design, kit, visit-1 base + tooling, visit-2 baton, stock tooling, one-piece mold",
          mk.returncode == 0 and mp.get("ok") and len(mp.get("steps", [])) == 6 and (mj.parent / "PLAN.md").exists()
          and mp.get("cleft_type") == "none" and mp.get("plate_profile") == "graded" and mp.get("border_relief") is True,
          "; ".join(f"{x['step']}: {'ok' if x['ok'] else 'FAILED'}" for x in mp.get("steps", [])) or (mk.stderr or "")[-300:])
    check("over-narrow kit refused", bad.returncode != 0 and "half" in (bad.stdout + bad.stderr), "width 5.0 mm refused")

    n_ok = sum(ok for ok, _, _ in results)
    print(f"\n{n_ok}/{len(results)} checks passed")
    sys.exit(0 if n_ok == len(results) else 1)


if __name__ == "__main__":
    main()
