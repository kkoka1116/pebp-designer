"""End-to-end check of the Phase 0 software on synthetic data only.

    .venv/bin/python scripts/phase0_check.py            (about 2 minutes)

Builds out/phase0_check from a synthetic Robin-sequence palate and asserts the numbers that matter:
the deformer against exact answers, QC on prototypes and the final design, that a requested change
moves the real geometry by that amount, the fitting-form round trip, tooling and cast-plate
registration, the wire tip reserve, measurement of a ground device from a noisy scan, sleep records,
and the parametric mode. Never touches real patient scans.
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
sys.path.insert(0, str(ROOT))
OUT = ROOT / "out" / "phase0_check"
D = str(OUT / "design.json")
results = []


def run(*args) -> str:
    r = subprocess.run([sys.executable, "-m", "pebp", *map(str, args)], cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"pebp {' '.join(map(str, args))} failed:\n{r.stdout}\n{r.stderr}")
    return r.stdout


def check(name: str, ok: bool, detail: str):
    results.append((ok, name, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")


def rec() -> dict:
    return json.loads(Path(D).read_text(encoding="utf-8"))


def main():
    from pebp.deform import selftest
    from pebp.extension import ExtensionParams, extension_path
    from pebp.measure import tip_reach

    rows = selftest(verbose=False)
    worst = max(r["max_mm"] for r in rows)
    check("deformer vs exact parametric answer", worst < 0.25 and all(r["watertight"] for r in rows), f"worst max {worst:.3f} mm over {len(rows)} cases")

    shutil.rmtree(OUT, ignore_errors=True)
    run("synth", "--preset", "prs", "--out", OUT, "--unoriented", "--asym", "1.0")
    run("design", OUT / "scan.stl", "--out", OUT, "--final")
    r = r0 = rec()
    check("auto mode picks the published extension", r["extension_mode"] == "tuebingen-cleft", r["extension_mode"])
    check("prototypes pass QC", all(p["qc"]["overall"] == "pass" for p in r["prototypes"]), str([p["qc"]["overall"] for p in r["prototypes"]]))
    g = r["final_design"]["wire_groove"]
    check("final groove span", 9.9 <= g["start_mm_after_root"] <= 10.1 and 1.5 <= g["end_mm_before_tip"] <= 2.05,
          f"{g['start_mm_after_root']} mm after root to {g['end_mm_before_tip']} mm before tip")

    tm = r["tpp_metrics"]
    el = [s["EL_mm"] for s in tm["P2"]["sections"]]
    sl5 = [tm[k]["sections"][-1]["SL_mm"] for k in ("P1", "P2", "P3")]
    s0t = [tm[k]["S0"]["thickness_mm"] for k in ("P1", "P2", "P3")]
    check("plate metrics for every device (Bioengineering 2022 scheme)",
          set(tm) == {"P1", "P2", "P3", "F"} and len(el) == 5 and all(abs(b - a - el[0]) < 0.05 for a, b in zip(el, el[1:]))
          and abs((sl5[0] - sl5[2]) - 4.0) < 0.2 and min(s0t) >= 3.0 and all(tm[k]["effective_volume_mm3"] for k in tm),
          f"EL {el}, SL5 P1-P3 spread {sl5[0] - sl5[2]:.2f} mm (2 mm steps), S0 thickness min {min(s0t)} mm")
    run("airway", D, "--set", "vallecula=0,-24,-24", "--set", "tb_ppw_distance_mm=3.5", "--source", "manual",
        "--note", "made-up test value")
    a = rec()["airway"]
    tv = a["derived"]["per_device"]["P2"]["tip_to_vallecula_mm"]
    want = float(np.linalg.norm(np.array([0, -24, -24]) - np.array(rec()["tpp_metrics"]["P2"]["baton_tip"])))
    check("airway landmarks set and derived", a["landmarks"]["vallecula"]["source"] == "manual" and abs(tv - want) < 0.01
          and a["landmarks"]["pns"]["source"] == "scan_proxy" and a["landmarks"]["epiglottis_tip"]["value"] is None,
          f"P2 tip to vallecula {tv} mm; reach check {sum(c['within'] for c in rec()['reach_check']['checks'])}/"
          f"{len(rec()['reach_check']['checks'])} within")

    run("adjust", D, "--from", "2", "--mod", "shorten_mm=2.4")
    r = rec()
    by, zm = r["attachment"]["posterior_border_y"], r["attachment"]["z_mid"]
    path = extension_path(ExtensionParams(**r["prototypes"][1]["params"]), by, zm)
    got = tip_reach(trimesh.load(OUT / "prototype_2.stl"), path) - tip_reach(trimesh.load(OUT / r["modification_log"][-1]["file"]), path)
    check("shorten 2.4 mm moves the real tip", abs(got - 2.4) < 0.1, f"{got:.2f} mm")

    t = json.loads(run("fitting", "template", D))
    form = {k: v for k, v in t.items() if k != "_help"}
    form["session"].update({"day_of_life": 9, "sedation": "awake", "purpose": "try_in", "minutes_since_feed": 90})
    form["trials"][0].update({"gag_on_insertion": True})
    form["decision"] = {"accepted_device": "P2", "decided_by_role": "ENT",
                        "modifications": [{"device": "P2", "code": "shorten_mm", "amount": 1.5, "where": "bedside", "finding": "too_long"},
                                          {"device": "P2", "code": "curve_deg", "amount": 10, "where": "bedside"}]}
    (OUT / "form.json").write_text(json.dumps(form), encoding="utf-8")
    out = run("fitting", "log", D, "--form", OUT / "form.json")
    r = rec()
    s = r["fitting_sessions"][-1]
    check("fitting form round trip", s["variants_built"] == ["A2"] and any("gag" in f for f in s["flags"]) and any("120" in f or "2 h" in f for f in s["flags"]),
          f"built {s['variants_built']}, {len(s['flags'])} flags")
    bad = json.loads(json.dumps(form)); bad["session"]["sedation"] = "asleep"
    (OUT / "bad.json").write_text(json.dumps(bad), encoding="utf-8")
    rb = subprocess.run([sys.executable, "-m", "pebp", "fitting", "log", D, "--form", str(OUT / "bad.json")], cwd=ROOT, capture_output=True, text=True)
    check("invalid form rejected", rb.returncode == 2 and "sedation" in rb.stderr and len(rec()["fitting_sessions"]) == 1, rb.stderr.strip().splitlines()[0])

    run("mold", OUT)
    man = json.loads((OUT / "mold" / "mold_manifest.json").read_text(encoding="utf-8"))
    v = man["validation"]
    w = man["features"]["safety_wire"]
    check("tooling for A2 valid and registered", v["ok"] and man["registered_as"] == "C1" and max(v["device_inside_piece_mm3"].values()) < 0.5,
          f"ok={v['ok']} registered {man['registered_as']}, device inside pieces max {max(v['device_inside_piece_mm3'].values())} mm3")
    check("cast wire span", 9.9 <= w["starts_mm_after_border"] <= 10.1 and 1.5 <= w["ends_mm_before_tip"] <= 2.05,
          f"{w['starts_mm_after_border']} to {w['ends_mm_before_tip']} mm before tip")

    # direct print: the same wire, embedded in a printed plate instead of a cast one (pebp/directprint.py)
    run("print", OUT, "--route", "all")
    pm = {r: json.loads((OUT / "print" / r / "print_manifest.json").read_text(encoding="utf-8")) for r in ("pause", "lid", "multimaterial")}
    pz, pw = pm["pause"]["features"]["pause"], pm["pause"]["safety_wire"]
    check("direct print: pause route valid and registered", pm["pause"]["validation"]["ok"] and pm["pause"]["registered_as"] == "D1"
          and pm["pause"]["validation"]["wire_cover"]["min_mm"] >= 0.8,
          f"ok={pm['pause']['validation']['ok']} {pm['pause']['registered_as']}, cover {pm['pause']['validation']['wire_cover']['min_mm']} mm")
    check("direct print: same wire as the cast plate", abs(pw["length_mm"] - w["length_mm"]) <= 0.1
          and abs(pw["starts_mm_after_root"] - w["starts_mm_after_border"]) <= 0.1 and abs(pw["ends_mm_before_tip"] - w["ends_mm_before_tip"]) <= 0.1,
          f"print {pw['length_mm']} mm ({pw['starts_mm_after_root']} to {pw['ends_mm_before_tip']}), cast {w['length_mm']} mm")
    check("direct print: pause lands on the pocket top", abs(pz["pause_height_mm"] - pz["pause_after_layer"] * pz["layer_mm"]) < 1e-6
          and 0.05 <= pz["pocket_top_gap_mm"] <= pz["layer_mm"] + 0.05 + 1e-6,
          f"layer {pz['pause_after_layer']} at {pz['pause_height_mm']} mm, gap over the wire {pz['pocket_top_gap_mm']} mm")
    pl = pm["lid"]["validation"]["checks"]
    check("direct print: lid route valid", pm["lid"]["validation"]["ok"] and pl["lid and plate do not overlap"] and pl["slot stays behind the plate"]
          and pm["multimaterial"]["validation"]["checks"]["colour bodies partition the plate"],
          f"lid {pm['lid']['features']['lid']['lid_volume_mm3']} mm3; registered {pm['lid']['registered_as']}, {pm['multimaterial']['registered_as']}")
    from pebp import record as rec_io
    from pebp.directprint import build as print_build, PrintParams
    # experimental core + flexible skin (experimental): built for bench specimens, never registered
    n_printed = len(rec()["printed_plates"])
    so = json.loads((print_build(OUT, PrintParams(route="multimaterial", device="A2", skin_mm=0.5)) / "print_manifest.json").read_text(encoding="utf-8"))
    si = json.loads((print_build(OUT, PrintParams(route="multimaterial", device="A2", skin_mm=0.5, skin_mode="inside")) / "print_manifest.json").read_text(encoding="utf-8"))
    ko, ki, t = so["features"]["skin"], si["features"]["skin"], so["extension_params"]["thickness"]
    check("skinned baton: bench only, geometry and budget right",
          not so["validation"]["ok"] and not si["validation"]["ok"] and len(rec()["printed_plates"]) == n_printed
          and so["validation"]["checks"]["skin is one watertight body"] and so["validation"]["checks"]["colour bodies partition the plate"]
          and abs(ko["outer_baton_thickness_mm"] - (t + 1.0)) < 1e-6 and ko["rigid_core_thickness_mm"] == round(t, 2)
          and abs(ki["core_bending_stiffness_vs_designed"] - round(((t - 1.0) / t) ** 3, 2)) < 1e-6
          and not si["validation"]["checks"]["rigid core >= 3 mm (Tübingen)"],
          f"outside: core {ko['rigid_core_thickness_mm']} / outer {ko['outer_baton_thickness_mm']} mm; inside: core stiffness x{ki['core_bending_stiffness_vs_designed']}; "
          f"skin tether {ko['skin_tether_newtons_if_core_snaps']} N; not registered")
    dv = rec_io.device(rec(), "D1")
    try:
        print_build(OUT, PrintParams(route="wireless")); refused = False
    except ValueError:
        refused = True
    check("direct print: D<n> devices are wired and a wireless route is refused", dv["kind"] == "printed" and dv["wired"] and refused
          and "D1" in rec()["tpp_metrics"], f"D1 kind {dv['kind']}, wired {dv['wired']}, wireless refused {refused}")

    tm = rec()["tpp_metrics"]
    check("new devices measured as they are made", {"A1", "A2", "C1"} <= set(tm) and tm["A1"]["EL_end_mm"] < tm["P2"]["EL_end_mm"],
          f"{sorted(tm)}; A1 EL end {tm['A1']['EL_end_mm']} mm vs P2 {tm['P2']['EL_end_mm']} mm")

    run("adjust", D, "--from", "C1", "--mod", "shorten_mm=1.2", "--bedside")
    run("adjust", D, "--from", "A3", "--mod", "shorten_mm=0.5", "--bedside")
    run("adjust", D, "--from", "C1", "--mod", "thin_mm=0.3", "--bedside")
    log = {e["id"]: e for e in rec()["modification_log"]}
    st = lambda e, c: next(x["status"] for x in e["qc"]["checks"] if x["check"] == c)
    check("tip reserve: warn, then fail", st(log["A3"], "tip reserve") == "warn" and st(log["A4"], "tip reserve") == "fail",
          f"A3 {st(log['A3'], 'tip reserve')}, A4 {st(log['A4'], 'tip reserve')}")
    check("thinning a wired plate fails", st(log["A5"], "thickness change on a wired plate") == "fail", "A5")

    rng = np.random.default_rng(11)
    m = trimesh.load(OUT / log["A3"]["file"]).subdivide_to_size(0.4)
    m.vertices += rng.normal(0, 0.03, m.vertices.shape)
    T = trimesh.transformations.random_rotation_matrix(rng.random(3)); T[:3, 3] = rng.uniform(-40, 40, 3)
    m.apply_transform(T); m.export(OUT / "ground_scan.stl")
    run("fitting", "measure", D, "--device", "C1", "--scan", OUT / "ground_scan.stl", "--out", OUT / "measure.json")
    meas = json.loads((OUT / "measure.json").read_text(encoding="utf-8"))
    check("ground plate measured from a noisy, moved scan", abs(meas["tip"]["shortened_mm"] - 1.2) < 0.1
          and meas["deviation"]["scan_to_design"]["within_0.25_mm"] > 0.9,
          f"{meas['tip']['shortened_mm']:+.2f} mm (true 1.2)")

    c = trimesh.load(OUT / "cast_1.stl").subdivide_to_size(0.4)
    nv, v = c.vertex_normals.copy(), c.vertices.copy()
    up = (nv[:, 2] > 0.3) & (v[:, 1] > by + 0.5)
    v[up, 2] -= 0.15 * np.clip((v[up, 1] - by - 0.5) / 10, 0, 1)        # palatal surface sinks 0.15 mm (shrinkage)
    c.vertices = v + rng.normal(0, 0.02, v.shape)
    c.apply_transform(T); c.export(OUT / "warped_cast_scan.stl")
    run("measure", OUT / "cast_1.stl", OUT / "warped_cast_scan.stl", "--record", D, "--out", OUT / "cast_measure.json")
    reg = json.loads((OUT / "cast_measure.json").read_text(encoding="utf-8"))["deviation"]["scan_to_design"]["regions"]
    check("region split localises a palatal warp", reg["intaglio"]["mean_signed_mm"] < -0.03 and abs(reg["baton"]["mean_signed_mm"]) < 0.01,
          f"intaglio {reg['intaglio']['mean_signed_mm']:+.3f} mm, baton {reg['baton']['mean_signed_mm']:+.3f} mm")

    run("sleep", D, "--device", "C1", "--day", 21, "--value", 2.1)
    sl = rec()["sleep_studies"][-1]
    check("sleep study tied to the device", sl["device"] == "C1" and sl["meets_target"] is True and sl["device_sha256"], f"{sl['index']} {sl['value_per_h']}/h")

    run("design", OUT / "scan.stl", "--out", OUT / "parametric", "--extension", "parametric")
    rp = json.loads((OUT / "parametric" / "design.json").read_text(encoding="utf-8"))
    check("parametric mode still works", all(p["qc"]["overall"] == "pass" for p in rp["prototypes"]), rp["extension_mode"])

    # graded-thickness plate (project heuristic): the default stays uniform; the graded option keeps full
    # thickness at the root, cleft and bow wires, never goes under 1.5 mm, and still makes valid tooling
    check("default plate stays uniform", "thickness_profile" not in r0["plate"] and "design_choices" not in r0
          and r0["plate"]["params"]["profile"] == "uniform", f"profile {r0['plate']['params']['profile']}")
    G = OUT / "graded"
    run("design", OUT / "scan.stl", "--out", G, "--final", "--plate-profile", "graded")
    rg = json.loads((G / "design.json").read_text(encoding="utf-8"))
    tp = rg["plate"]["thickness_profile"]
    check("graded field: floor, gradient, keep-outs", tp["field_min_mm"] >= 1.5 and tp["measured_gradient_max"] <= 1 / 3 + 1e-3
          and {"baton_root", "cleft_block_out", "bow_wire_left", "bow_wire_right"} <= set(tp["rigid_zones_mm2"])
          and rg["plate"]["watertight"],
          f"min {tp['field_min_mm']} mm, |grad T| {tp['measured_gradient_max']}, zones {sorted(tp['rigid_zones_mm2'])}")
    check("graded plate measured wall", tp["measured_wall"]["min_mm"] >= 1.45 and tp["measured_wall"]["max_mm"] <= 2.0 + 0.05,
          f"{tp['measured_wall']['min_mm']}-{tp['measured_wall']['max_mm']} mm (prototype base 2.0)")
    fq = {c["check"]: c["status"] for c in rg["final_design"]["qc"]["checks"]}
    check("graded choice flagged on the case", rg["design_choices"]["plate_profile"]["value"] == "graded"
          and "project heuristic" in rg["design_choices"]["plate_profile"]["flag"]
          and fq.get("graded plate: below Tübingen thickness") == "warn" and fq.get("graded plate: thin floor") == "pass",
          f"final QC {rg['final_design']['qc']['overall']}")
    run("mold", G)
    vg = json.loads((G / "mold" / "mold_manifest.json").read_text(encoding="utf-8"))["validation"]
    check("tooling for a graded plate valid", vg["ok"], f"ok={vg['ok']}, final QC {vg['final_qc']['overall']}")
    rb = subprocess.run([sys.executable, "-m", "pebp", "design", str(OUT / "scan.stl"), "--out", str(OUT / "graded_bad"),
                         "--plate-profile", "graded", "--thin-thickness", "1.2"], cwd=ROOT, capture_output=True, text=True)
    check("graded plate under 1.5 mm rejected", rb.returncode != 0 and "floor" in rb.stderr, rb.stderr.strip().splitlines()[-1] if rb.stderr else "")

    # border relief (project heuristic): flange capped, frenulum notched, tuberosities relieved; the ridge stays
    # covered, the posterior border does not move, and tooling still builds
    B = OUT / "relief"
    run("design", OUT / "scan.stl", "--out", B, "--final", "--plate-profile", "graded", "--border-relief")
    rr = json.loads((B / "design.json").read_text(encoding="utf-8"))
    br = rr["plate"]["border_relief"]
    from shapely.geometry import Point, Polygon
    o_g, o_r = np.load(G / "plate_outline.npy"), np.load(B / "plate_outline.npy")
    from pebp.context import rebuild
    lm = rebuild(B)["lm"]
    covered = Polygon(o_r).buffer(0.3)
    check("border relief keeps ridge and posterior border", all(covered.contains(Point(q)) for q in lm.crest_ring[:, :2])
          and abs(o_r[:, 1].min() - o_g[:, 1].min()) < 0.05 and Polygon(o_r).area < Polygon(o_g).area and rr["plate"]["watertight"],
          f"flange trimmed {br['vestibular_cap']['trimmed_mm2']} mm2, posterior border {o_g[:, 1].min():.2f} -> {o_r[:, 1].min():.2f} mm")
    pg, pr = trimesh.load(G / "base_plate.stl"), trimesh.load(B / "base_plate.stl")
    def top_z(m, c):
        v = m.vertices[np.linalg.norm(m.vertices[:, :2] - c, axis=1) < 0.8]
        return v[:, 2].max()
    dz = [top_z(pg, np.array(c)) - top_z(pr, np.array(c)) for c in br["tuberosity_relief"]["centres"]]
    check("tuberosity relief depth", all(0.2 <= d <= 0.4 for d in dz), f"{[round(float(d), 2) for d in dz]} mm (set 0.3)")
    fq = {c["check"]: c["status"] for c in rr["final_design"]["qc"]["checks"]}
    check("border relief flagged on the case", rr["design_choices"]["border_relief"]["value"] is True
          and "project heuristic" in rr["design_choices"]["border_relief"]["flag"] and fq.get("border relief: retention") == "warn",
          f"final QC {rr['final_design']['qc']['overall']}")
    run("print", G, "--route", "pause")
    gw = json.loads((G / "print" / "pause" / "print_manifest.json").read_text(encoding="utf-8"))["validation"]
    check("direct print of a graded plate flags the thin floor", gw["ok"] and any("graded" in x for x in gw["warnings"]),
          f"ok={gw['ok']}, warnings {len(gw['warnings'])}")
    run("mold", B)
    vb = json.loads((B / "mold" / "mold_manifest.json").read_text(encoding="utf-8"))["validation"]
    check("tooling for a relieved plate valid", vb["ok"], f"ok={vb['ok']}, final QC {vb['final_qc']['overall']}")

    # manufacturing planner: one command picks the route for the site's kit, names the device, builds it
    mk = subprocess.run([sys.executable, "-m", "pebp", "make", str(OUT), "--build"], cwd=ROOT, capture_output=True, text=True)
    mp = json.loads((OUT / "make" / "final" / "make_plan.json").read_text(encoding="utf-8")) if mk.returncode == 0 else {}
    fs = [s for s in rec()["fitting_sessions"] if s.get("variants_built")]
    check("make: default kit casts PMMA with the wire, on the device from the last fitting session",
          mk.returncode == 0 and mp["route"] == "mold" and mp["fallback"] == "lab" and mp["device"] == fs[-1]["variants_built"][-1]
          and mp["built"][0]["ok"] and str(mp["built"][0]["registered_as"]).startswith("C") and len(mp["gates"]) == 7
          and rec()["manufacturing_plans"][-1]["route"] == "mold",
          f"exit {mk.returncode}, route {mp.get('route')}, device {mp.get('device')}, built {[(b['route'], b['registered_as']) for b in mp.get('built') or []]}")
    from pebp.make import Kit, choose
    ch = {(k, v): choose(Kit(printer=k, pressure_pot=pot, direct_print_validated_by=v), "final")
          for k in ("sla-nopause", "resin-pause", "polyjet", "none") for v in (None, "X") for pot in (True,)}
    nopot = choose(Kit(printer="sla-nopause", pressure_pot=False), "final")
    check("make: direct print only after a named bench-parity record, never on jetting, lab with no printer",
          all(ch[(k, None)]["route"] in ("mold", "lab") for k in ("sla-nopause", "resin-pause", "polyjet", "none"))
          and ch[("resin-pause", "X")]["route"] == "print-pause" and ch[("sla-nopause", "X")]["route"] == "print-lid"
          and ch[("polyjet", "X")]["route"] == "mold" and ch[("none", "X")]["route"] == "lab"
          and ch[("resin-pause", "X")]["fallback"] == "mold" and nopot["blockers"],
          "; ".join(f"{k}{'+validated' if v else ''} -> {c['route']}" for (k, v), c in ch.items()))

    # The README's quick-start, word for word: default synth (no --asym), design without --final, then mold.
    # On main before this check, the root pad's polygon union left two outline vertices ~4e-15 mm apart,
    # trimesh welded them when extruding the rim-draft prism, and mold stopped at "Not all meshes are volumes".
    from pebp.meshutil import clean_ring, extrude_clean
    sq = np.array([[0, 0], [10, 0], [10, 10], [10, 10 + 4e-15], [0, 10]], float)
    raw_ok, clean_ok = trimesh.creation.extrude_polygon(Polygon(sq), 5.0).is_volume, extrude_clean(Polygon(sq), 5.0).is_volume
    check("near-duplicate outline vertices removed before extruding", len(clean_ring(sq)) == 4 and clean_ok,
          f"{len(sq)} -> {len(clean_ring(sq))} vertices; prism a closed volume: raw {raw_ok}, cleaned {clean_ok}")
    R = ROOT / "out" / "phase0_readme"
    shutil.rmtree(R, ignore_errors=True)
    run("synth", "--out", R, "--preset", "prs", "--unoriented")
    run("design", R / "scan.stl", "--out", R)
    o = np.load(R / "plate_outline.npy")
    seg = float(np.linalg.norm(np.diff(np.vstack([o, o[:1]]), axis=0), axis=1).min())
    m = subprocess.run([sys.executable, "-m", "pebp", "mold", str(R)], cwd=ROOT, capture_output=True, text=True)
    vr = json.loads((R / "mold" / "mold_manifest.json").read_text(encoding="utf-8"))["validation"] if m.returncode == 0 else {"ok": False}
    check("README quick-start path: synth, design, mold", m.returncode == 0 and vr["ok"],
          f"exit {m.returncode}, ok={vr['ok']}, shortest outline edge {seg:.1e} mm"
          + ("" if m.returncode == 0 else f": {(m.stderr.strip().splitlines() or ['?'])[-1]}"))

    # carry: the next plate from a new (grown) scan with the accepted baton as template (Tübingen practice)
    from pebp.context import chosen_extension
    K = ROOT / "out" / "phase0_carry"
    shutil.rmtree(K, ignore_errors=True)
    run("synth", "--preset", "prs", "--sd", "-1", "--out", K / "p1")
    run("design", K / "p1" / "scan.stl", "--out", K / "p1")
    run("adjust", K / "p1" / "design.json", "--from", "2", "--mod", "shorten_mm=1.5", "--bedside")
    run("adjust", K / "p1" / "design.json", "--from", "A1", "--mod", "curve_deg=10")
    run("synth", "--preset", "prs", "--sd", "1", "--seed", "3", "--unoriented", "--out", K / "p2")
    nodev = subprocess.run([sys.executable, "-m", "pebp", "carry", str(K / "p1" / "design.json"), str(K / "p2" / "scan.stl"),
                            "--out", str(K / "p2")], cwd=ROOT, capture_output=True, text=True)
    run("carry", K / "p1" / "design.json", K / "p2" / "scan.stl", "--device", "A2", "--out", K / "p2")
    r1, r2 = (json.loads((K / q / "design.json").read_text(encoding="utf-8")) for q in ("p1", "p2"))
    o, n = r1["tpp_metrics"]["A2"], r2["tpp_metrics"]["T"]
    v_old, v_new = np.subtract(o["baton_tip"], o["baton_root"]), np.subtract(n["baton_tip"], n["baton_root"])
    check("carry: baton pose kept on the new plate",
          r2["carried_design"]["params"] == r1["modification_log"][-1]["params"] and abs(o["arm_length_mm"] - n["arm_length_mm"]) < 0.05
          and float(np.abs(v_old - v_new).max()) < 0.1 and r2["carried_design"]["qc"]["overall"] == "pass",
          f"arm {o['arm_length_mm']} -> {n['arm_length_mm']} mm, root-to-tip differs by {float(np.abs(v_old - v_new).max()):.3f} mm, "
          f"arch depth {r1['landmarks']['arch_depth_mm']} -> {r2['landmarks']['arch_depth_mm']} mm")
    cf = r2["carried_from"]
    check("carry: lineage and bedside-shortening total recorded",
          cf["lineage"]["chain"] == ["P2", "A1", "A2"] and cf["lineage"]["bedside_shortening_total_mm"] == 1.5
          and cf["generation"] == 2 and any("growth" in f for f in cf["flags"]) and nodev.returncode == 2 and "--device" in nodev.stderr,
          f"{' -> '.join(cf['lineage']['chain'])}, bedside {cf['lineage']['bedside_shortening_total_mm']} mm, plate {cf['generation']}, "
          f"{len(cf['flags'])} flags; no --device refused")
    check("carry: T is the device for tooling, no prototypes by default",
          chosen_extension(r2)[2] == "T" and r2["prototypes"] == [] and set(r2["tpp_metrics"]) == {"T"},
          f"tooling default {chosen_extension(r2)[2]}, devices {sorted(r2['tpp_metrics'])}")
    run("adjust", K / "p2" / "design.json", "--from", "T", "--mod", "shorten_mm=0.5", "--bedside")
    run("synth", "--preset", "prs", "--sd", "1.5", "--seed", "4", "--out", K / "p3")
    run("carry", K / "p2" / "design.json", K / "p3" / "scan.stl", "--device", "A1", "--prototypes", "--out", K / "p3")
    r3 = json.loads((K / "p3" / "design.json").read_text(encoding="utf-8"))
    lin3 = json.loads(run("lineage", K / "p3" / "design.json", "--device", "T", "--json"))
    pr = {p["id"]: p["params"] for p in r3["prototypes"]}
    check("carry: third plate, prototypes centred on the carried baton",
          lin3["bedside_shortening_all_plates_mm"] == 2.0 and r3["carried_from"]["generation"] == 3
          and pr["P2"] == r3["carried_design"]["params"] and pr["P1"]["ap_offset"] == pr["P2"]["ap_offset"] - 2.0
          and all(p["qc"]["overall"] == "pass" for p in r3["prototypes"]),
          f"bedside over all plates {lin3['bedside_shortening_all_plates_mm']} mm (1.5 + 0.5), plate {r3['carried_from']['generation']}, "
          f"P1-P3 ap {[pr[k]['ap_offset'] for k in ('P1', 'P2', 'P3')]}")
    # cleft type (clinical entry, pebp/cleft.py): it picks the baton and the bridging; no entry keeps the scan analysis
    run("design", OUT / "scan.stl", "--out", OUT / "cleft_none", "--cleft", "none", "--cleft-entered-by", "T")
    rn = json.loads((OUT / "cleft_none" / "design.json").read_text(encoding="utf-8"))
    cs = rn["cleft_status"]
    check("cleft type 'none' on a cleft scan: no-cleft baton, the gap bridged as a scan hole, no midline bulge",
          rn["extension_mode"] == "tuebingen-nocleft" and cs["value"] == "none" and cs["scan_found_cleft"] is True
          and not rn["landmarks"]["cleft_present"] and rn["plate"].get("scan_holes_blocked", {}).get("count", 0) >= 1
          and rn["plate"]["params"]["posterior_bulge"] == 0.0 and cs["warnings"]
          and all(p["qc"]["overall"] == "pass" for p in rn["prototypes"]),
          f"{rn['extension_mode']}, holes {rn['plate'].get('scan_holes_blocked', {}).get('count')}, bulge {rn['plate']['params']['posterior_bulge']}, "
          f"border y {rn['attachment']['posterior_border_y']} vs {r0['attachment']['posterior_border_y']} with no entry")
    run("design", OUT / "scan.stl", "--out", OUT / "cleft_hs", "--cleft", "hard-soft", "--cleft-entered-by", "T")
    rh = json.loads((OUT / "cleft_hs" / "design.json").read_text(encoding="utf-8"))
    check("cleft type 'hard and soft': cleft baton and cleft block-out, as with no entry",
          rh["extension_mode"] == "tuebingen-cleft" and rh["landmarks"]["cleft_present"] and rh["plate"]["cleft_floor_z"] is not None
          and rh["plate"]["params"]["posterior_bulge"] == 2.0 and r0["cleft_status"]["value"] is None
          and r0["plate"]["params"]["posterior_bulge"] == 2.0,
          f"{rh['extension_mode']}, cleft floor z {rh['plate']['cleft_floor_z']}")

    # no-cleft devices reload watertight: the flat no-cleft extension used to pinch under the number dimples
    # and the wire groove, so the files were broken while the in-memory QC said pass. Seed 0 hit both.
    from pebp.meshutil import reloads_watertight
    nc = OUT / "nocleft"
    run("synth", "--out", nc, "--no-cleft", "--seed", 0)
    run("design", nc / "scan.stl", "--out", nc / "d", "--final")
    rnc = json.loads((nc / "d" / "design.json").read_text(encoding="utf-8"))
    files = ["base_plate.stl", "final_design.stl"] + [p["file"] for p in rnc["prototypes"]]
    back = {f: trimesh.load(nc / "d" / f) for f in files}
    bad = [f for f, m in back.items() if not (m.is_watertight and m.body_count == 1)]
    check("no-cleft devices reload from STL as one closed solid",
          rnc["extension_mode"] == "tuebingen-nocleft" and not bad and len(files) == 5
          and all(q["qc"]["overall"] == "pass" for q in rnc["prototypes"] + [rnc["final_design"]]),
          f"{rnc['extension_mode']}, {len(files) - len(bad)}/{len(files)} files watertight after reload" + (f", broken {bad}" if bad else ""))
    # and QC can see the defect: two cubes sharing an edge through duplicated vertices are watertight in
    # memory and not after an STL round trip
    a = trimesh.creation.box(extents=(1, 1, 1)); b = a.copy(); b.apply_translation((1, 1, 0))
    pinched = trimesh.Trimesh(np.vstack([a.vertices, b.vertices]), np.vstack([a.faces, b.faces + len(a.vertices)]), process=False)
    check("QC watertight check reads the mesh as written to STL",
          pinched.is_watertight and not reloads_watertight(pinched) and reloads_watertight(a),
          f"in memory {pinched.is_watertight}, after reload {reloads_watertight(pinched)}")

    # orientation on scan-like palates (ragged rim, hole, wider than deep): scripts/orient_check.py
    sys.path.insert(0, str(ROOT / "scripts"))
    from orient_check import checks as orient_checks
    for ok, name, detail in orient_checks():
        check(name, ok, detail)

    n_fail = sum(not ok for ok, _, _ in results)
    print(f"\n{len(results) - n_fail}/{len(results)} checks passed")
    sys.exit(1 if n_fail else 0)


if __name__ == "__main__":
    main()
