"""Manufacturing planner: one command that says how to make the next device with the equipment a site has,
and builds it.

The routes already exist (prototypes from `design`, `polyjet`, `mold`, `print`). What a new centre lacks is
the order: which route fits its printer, which device goes to manufacture, what to check at each stage and
when a step must stop. `plan()` answers that from the design record and a kit profile; `run()` builds the
files with the existing modules. The planner adds no geometry.

Stages
  tryin   short-contact endoscopy prototypes P1-P3 (or the chosen device), printed on whatever the site has.
  final   the worn plate. Cold-cure PMMA cast with the Tübingen safety wire in printed tooling (`mold`) is the
          default. A direct-printed plate (`print`) is chosen only when the site has recorded, under a named
          person, that it completed the bench-parity tests in docs/DIRECT_PRINT.md (a site choice, stored
          and flagged; the software never makes it).
  next    a later plate for a growing infant: the accepted baton is carried onto a new scan (`pebp carry`,
          where installed), then the final stage runs again with no new try-in unless the clinician asks.

Evidence tags as in directprint.py: [PUB] [FDA] [MFR] [HEUR] project heuristic, [INF] inference. Times are
[INF] estimates for planning, not measurements; nothing here has been timed on a bench yet.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path

from . import record as rec_io

SCHEMA = "pebp-make/0.1"

# What the site has. `pauses` = the printer's own software can stop at a chosen layer and resume.
KITS = {
    "sla-nopause": {"title": "desktop SLA printer whose software cannot pause at a layer (e.g. Formlabs Form 3B/4B with PreForm)",
                    "pauses": False, "jetting": False, "prints": True},
    "resin-pause": {"title": "desktop resin printer (SLA, DLP or LCD) whose software can pause at a chosen layer",
                    "pauses": True, "jetting": False, "prints": True},
    "polyjet": {"title": "multi-material jetting printer (Stratasys J-series / DentaJet)",
                "pauses": False, "jetting": True, "prints": True},
    "none": {"title": "no printer on site: files go to a partner dental lab or 3D-printing hub",
             "pauses": False, "jetting": False, "prints": False},
}


@dataclass
class Kit:
    printer: str = "sla-nopause"
    pressure_pot: bool = True
    direct_print_validated_by: str | None = None   # a named person: the site finished the DIRECT_PRINT bench tests
    direct_print_validated_on: str | None = None   # date of that record


# ------------------------------------------------------------------ the gates every route passes through
GATES = [
    ("G1 scan", "Whole palate to the tuberosities, cleft margins and vestibule captured, no holes; the design "
     "QC's scan checks pass.", "design", "[HEUR]"),
    ("G2 design", "Every device's QC passes or each warning is read and signed; extension at least 3 mm thick and "
     "never narrowed by more than half its width.", "qc", "[PUB Tübingen 2025] + [HEUR]"),
    ("G3 prototype in hand", "Printed in a resin cleared for mucosal contact, washed and post-cured to the resin's "
     "instructions, seated on a printed working model, no sharp edges or flash.", "bench", "[MFR] + [HEUR]"),
    ("G4 try-in", "Awake endoscopy: the accepted device and every change are logged with `pebp fitting log`. "
     "The choice is the clinician's, never the software's.", "clinical", "[PUB Tübingen practice] + project decision"),
    ("G5 final build", "The software checks on the chosen route all pass (safety wire present and in place, "
     "plate 2.5-3 mm, wire cover, mold demold or print pocket) and the device is registered as C<n> or D<n>.",
     "software", "[PUB Tübingen 2025 rules] + [HEUR]"),
    ("G6 physical part", "Wire visible end to end through the clear resin, no porosity around it, thickness checked "
     "with calipers at the baton root and tip, scan of the part checked with `pebp measure`.", "bench", "[HEUR]"),
    ("G7 delivery and follow-up", "Seat, check borders for blanching, review at the clinic's interval for pressure "
     "sores (most Tübingen ulcers were at overextended borders, Weismann 2026); log sleep studies with "
     "`pebp sleep`.", "clinical", "[PUB doi 10.1002/cre2.70291] + project decision"),
]


def _final_device(record: dict, device: str | None) -> tuple[str, str]:
    """The device that goes to the worn plate, and why."""
    if device:
        return rec_io.normalise_id(device), "given on the command line"
    for s in reversed(record.get("fitting_sessions", [])):
        built = s.get("variants_built") or []
        if built:
            return built[-1], f"last change built at fitting session {s['n']}"
        acc = (s.get("decision") or {}).get("accepted_device")
        if acc:
            return rec_io.normalise_id(acc), f"accepted at fitting session {s['n']}"
    if record.get("modification_log"):
        e = record["modification_log"][-1]
        return e.get("id", f"A{e['n']}"), "last logged change (no fitting session logged)"
    return "P2", "no try-in logged: P2 is a placeholder, and G4 is open"


def choose(kit: Kit, stage: str) -> dict:
    """Route and fallback for a stage, with the reasons. Pure function, for a clinician UI."""
    k = KITS[kit.printer]
    why, blockers = [], []
    if stage == "tryin":
        if k["jetting"]:
            route, fallback = "polyjet", "prototypes"
            why.append("multi-material prototypes with the number in a contrasting colour (`pebp polyjet`)")
        elif k["prints"]:
            route, fallback = "prototypes", "hub"
            why.append("P1-P3 printed as single-resin parts; any desktop printer with a mucosal-contact resin will do [INF]")
        else:
            route, fallback = "hub", None
            why.append("no printer: send the prototype STLs to a partner lab")
        return {"stage": stage, "route": route, "fallback": fallback, "why": why, "blockers": blockers}

    validated = bool(kit.direct_print_validated_by)
    if validated and k["prints"] and not k["jetting"]:
        route = "print-pause" if k["pauses"] else "print-lid"
        fallback = "mold" if kit.pressure_pot else "lab"
        why.append(f"site recorded direct-print bench parity ({kit.direct_print_validated_by}"
                   + (f", {kit.direct_print_validated_on}" if kit.direct_print_validated_on else "") + "): a site choice, flagged")
        why.append("one print in a long-term resin, the wire dropped in at a pause" if k["pauses"]
                   else "the printer cannot pause, so the wire goes in a slot under a printed lid")
    elif k["prints"]:
        route, fallback = "mold", "lab"
        why.append("cold-cure PMMA with the cast-in Tübingen wire is the project's worn-appliance decision; "
                   "the printer makes the mold, cradle and wire jig")
        if validated and k["jetting"]:
            why.append("direct print on a jetting printer needs a long-term multi-colour resin (TrueDent on the J5 "
                       "DentaJet only, cleared for dentures not plates [FDA K220771]): staying with the mold")
        elif not validated:
            why.append("direct print stays a bench route until the site records bench parity (docs/DIRECT_PRINT.md)")
        if not kit.pressure_pot:
            blockers.append("a pressure pot for curing the PMMA (the mold route needs one) [MFR cold-cure IFUs]")
    else:
        route, fallback = "lab", None
        why.append("no printer: a partner dental lab casts the plate on a printed or milled model by the Tübingen "
                   "method, from this case's STLs")
    return {"stage": stage, "route": route, "fallback": fallback, "why": why, "blockers": blockers}


# ------------------------------------------------------------------ steps per route
def _steps(route: str, case: str, dev: str, kit: Kit) -> list[dict]:
    S = lambda title, who, time, cmd=None, tag="[INF]": {"title": title, "who": who, "time": time, "command": cmd, "tag": tag}
    if route in ("prototypes", "polyjet"):
        out = [S("Print P1-P3 (2 mm apart)", "printer operator", "1-3 h unattended",
                 f"pebp polyjet {case} --proto N" if route == "polyjet" else f"prototype_1..3.stl in {case}")]
        out += [S("Wash, dry and post-cure to the resin's instructions", "printer operator", "30-90 min", None, "[MFR]"),
                S("Remove supports, smooth, seat on a printed working model (G3)", "printer operator", "15 min per part"),
                S("Clean and disinfect for a short awake try-in", "clinic", "per the clinic's protocol", None, "project decision"),
                S("Awake endoscopy try-in, log the result (G4)", "clinician", "one session",
                  f"pebp fitting log {case}/design.json FORM.json", "[PUB Tübingen practice]")]
        return out
    if route == "hub":
        return [S("Send the prototype STLs to the partner lab", "coordinator", "lab turnaround"),
                S("Check the parts on arrival against the working model (G3)", "clinic", "15 min")]
    if route == "mold":
        return [S("Build the tooling for the chosen device (G5)", "software", "2-5 min", f"pebp mold {case} --device {dev}"),
                S("Print the four mold pieces, cradle and wire jig", "printer operator", "3-6 h unattended"),
                S("Wash, cure, coat the cavity with separator", "printer operator", "60-90 min", None, "[MFR]"),
                S("Bend the 1.8 x 0.8 mm twisted stainless wire on the printed jig", "technician", "10 min", None, "[PUB Tübingen 2025]"),
                S("Close the mold on the wire, fill with cold-cure PMMA, cure in the pressure pot", "technician", "30-45 min",
                  None, "[MFR cold-cure IFU]"),
                S("Demold in the order the mold viewer shows, trim sprue, polish", "technician", "45-60 min"),
                S("Inspect and measure the part (G6)", "technician", "15 min", f"pebp measure {case}/cast_N.stl SCAN_OF_PART.stl")]
    if route in ("print-pause", "print-lid"):
        r = route.split("-")[1]
        out = [S("Build the print files for the chosen device (G5)", "software", "2-5 min", f"pebp print {case} --route {r} --device {dev}"),
               S("Sandblast, etch and silane the bent wire", "technician", "20 min", None,
                 "[PUB Zhang 2025, doi 10.1186/s40510-025-00559-1]")]
        if r == "pause":
            out += [S("Print on its side; at the pause, drop the wire into its pocket and resume", "printer operator",
                      "2-4 h, attended at the pause")]
        else:
            out += [S("Print the plate and the lid", "printer operator", "2-4 h unattended"),
                    S("Seat the wire, bond the lid with the same resin, spot-cure", "technician", "15 min")]
        out += [S("Wash, dry and post-cure to the resin's instructions (under-cure raises cytotoxicity)", "printer operator",
                  "60-90 min", None, "[MFR] + [PUB doi 10.1016/j.jdent.2022.104097]"),
                S("Remove supports, polish, inspect and measure (G6)", "technician", "30-45 min",
                  f"pebp measure {case}/printed_N.stl SCAN_OF_PART.stl")]
        return out
    if route == "lab":
        return [S("Send the working model and final design STLs to the partner lab", "coordinator", "lab turnaround"),
                S("Lab casts PMMA with the safety wire by the Tübingen method", "partner lab", "same day at Tübingen", None,
                  "[PUB Tübingen 2025]"),
                S("Inspect and measure on arrival (G6)", "clinic", "15 min")]
    raise ValueError(route)


EQUIPMENT = {
    "scan": ["intraoral scanner, or an impression poured in stone and scanned"],
    "prototypes": ["desktop resin printer", "resin cleared for mucosal contact (e.g. Dental LT Clear V2 [FDA K222061])",
                   "wash and post-cure units"],
    "polyjet": ["Stratasys J-series with MED610 / MED625FLX [MFR]"],
    "mold": ["desktop resin printer and a model resin", "wash and post-cure units", "pressure pot",
             "cold-cure PMMA (powder/liquid)", "separator", "1.8 x 0.8 mm twisted stainless wire [PUB Tübingen 2025]",
             "polishing kit"],
    "print-pause": ["desktop resin printer that pauses at a layer", "long-term resin (see `pebp print --routes`)",
                    "wash and post-cure units", "wire", "sandblaster, etchant, silane", "polishing kit"],
    "print-lid": ["desktop resin printer", "long-term resin (see `pebp print --routes`)", "wash and post-cure units",
                  "wire", "sandblaster, etchant, silane", "polishing kit"],
    "hub": ["a partner lab or hub that prints dental resins"],
    "lab": ["a partner orthodontic lab that makes cast PMMA plates"],
}


def plan(case_dir: Path, kit: Kit, stage: str = "final", device: str | None = None) -> dict:
    case_dir = Path(case_dir)
    record = rec_io.upgrade(rec_io.load(case_dir / "design.json"))
    c = choose(kit, stage if stage != "next" else "final")
    if stage == "tryin":
        dev, dev_why = (rec_io.normalise_id(device), "given on the command line") if device else ("P1-P3", "the three prototypes")
    else:
        dev, dev_why = _final_device(record, device)
        rec_io.device(record, dev)                       # raises on an unknown id
    steps = []
    blockers = list(c["blockers"])
    if stage == "next":
        import importlib.util
        if importlib.util.find_spec("pebp.carry") is None:
            blockers.append("`pebp carry` is not in this install yet (baton carry-forward, in review); until then, "
                            "design the new scan with `pebp design` and re-enter the accepted changes with `pebp adjust`")
        steps.append({"title": "Scan the infant again and carry the accepted baton onto the new scan", "who": "clinician + software",
                      "time": "10 min + 2 min", "command": f"pebp carry {case_dir}/design.json NEW_SCAN.stl --device {dev}",
                      "tag": "[PUB Tübingen previous-plate-as-template practice] + [HEUR]"})
        steps.append({"title": "Run this plan again on the new case folder; try in again only if the clinician asks",
                      "who": "coordinator", "time": "-", "command": "pebp make NEW_CASE --stage final", "tag": "project decision"})
    else:
        steps = _steps(c["route"], str(case_dir), dev, kit)
    flags = []
    if c["route"].startswith("print-"):
        flags.append(f"direct-printed worn plate chosen because {kit.direct_print_validated_by} recorded bench parity for this site; "
                     "site choice, not checked by the software")
    if stage != "tryin" and dev_why.startswith("no try-in"):
        flags.append("no fitting session logged: the device is a placeholder until G4 is closed")
    return {"schema": SCHEMA, "case": str(case_dir), "stage": stage, "kit": asdict(kit), "kit_title": KITS[kit.printer]["title"],
            "route": c["route"], "fallback": c["fallback"], "why": c["why"], "blockers": blockers,
            "device": dev, "device_why": dev_why, "equipment": EQUIPMENT["scan"] + EQUIPMENT.get(c["route"], []),
            "fallback_equipment": EQUIPMENT.get(c["fallback"] or "", []), "steps": steps,
            "gates": [{"gate": g, "check": t, "kind": k, "tag": tag} for g, t, k, tag in GATES], "flags": flags,
            "built": None}


def run(p: dict, with_fallback: bool = False) -> dict:
    """Build the files the plan names, with the existing modules. Returns the plan with `built` filled."""
    case_dir = Path(p["case"])
    built = []

    def do(route: str):
        if route == "mold":
            from .mold import build, MoldParams
            out = build(case_dir, MoldParams(device=p["device"]))
            m = json.loads((out / "mold_manifest.json").read_text())
            built.append({"route": route, "dir": str(out), "ok": m["validation"]["ok"], "registered_as": m.get("registered_as")})
        elif route.startswith("print-"):
            from .directprint import build, PrintParams
            out = build(case_dir, PrintParams(route=route.split("-")[1], device=p["device"]))
            m = json.loads((out / "print_manifest.json").read_text())
            built.append({"route": route, "dir": str(out), "ok": m["validation"]["ok"], "registered_as": m.get("registered_as")})
        elif route == "polyjet":
            from .polyjet import export
            for n in (1, 2, 3):
                out = export(case_dir, proto_number=n)
                m = json.loads((out / "manifest.json").read_text())
                built.append({"route": route, "dir": str(out), "ok": m["validation"]["ok"], "registered_as": None})
        elif route == "prototypes":
            files = [f for f in (f"prototype_{n}.stl" for n in (1, 2, 3)) if (case_dir / f).exists()]
            built.append({"route": route, "dir": str(case_dir), "ok": len(files) == 3, "files": files, "registered_as": None})
        else:
            built.append({"route": route, "dir": None, "ok": True, "registered_as": None,
                          "note": "nothing to build here: the steps list what to send"})

    if p["stage"] == "next":
        built.append({"route": "carry", "dir": None, "ok": True, "registered_as": None,
                      "note": "run `pebp carry` with the new scan, then `pebp make` on the new case"})
    else:
        do(p["route"])
        if with_fallback and p["fallback"]:
            do(p["fallback"])
    p = dict(p, built=built)
    return p


def write(p: dict) -> Path:
    """make/<stage>/make_plan.json + MAKE_PLAN.md, and a log line in the design record."""
    case_dir = Path(p["case"])
    out = case_dir / "make" / p["stage"]
    out.mkdir(parents=True, exist_ok=True)
    (out / "make_plan.json").write_text(json.dumps(p, indent=2))
    (out / "MAKE_PLAN.md").write_text(markdown(p))
    path = case_dir / "design.json"
    record = rec_io.upgrade(rec_io.load(path))
    record.setdefault("manufacturing_plans", []).append(
        {"time": rec_io.now(), "stage": p["stage"], "route": p["route"], "fallback": p["fallback"], "device": p["device"],
         "kit": p["kit"], "flags": p["flags"], "built": [{k: b.get(k) for k in ("route", "ok", "registered_as")} for b in p["built"] or []]})
    rec_io.save(record, path)
    return out


def markdown(p: dict) -> str:
    L = [f"# Manufacturing plan: {p['stage']} stage", "",
         f"Case `{Path(p['case']).name}`. Kit: {p['kit_title']}" + ("" if p["kit"]["pressure_pot"] else ", no pressure pot") + ".", "",
         f"**Route: {p['route']}.** Fallback: {p['fallback'] or 'none'}. Device: {p['device']} ({p['device_why']}).", ""]
    L += [f"- {w}" for w in p["why"]]
    if p["blockers"]:
        L += ["", "**Needed before this route can run:**"] + [f"- {b}" for b in p["blockers"]]
    if p["flags"]:
        L += ["", "**Flags:**"] + [f"- {f}" for f in p["flags"]]
    L += ["", "## Equipment", ""] + [f"- {e}" for e in p["equipment"]]
    if p["fallback_equipment"]:
        L += ["", f"Fallback ({p['fallback']}): " + "; ".join(p["fallback_equipment"])]
    L += ["", "## Steps", "", "| # | Step | Who | Time | Basis |", "|---|---|---|---|---|"]
    for i, s in enumerate(p["steps"], 1):
        cmd = f" `{s['command']}`" if s["command"] else ""
        L.append(f"| {i} | {s['title']}{cmd} | {s['who']} | {s['time']} | {s['tag']} |")
    L += ["", "Times are planning estimates [INF], not bench measurements.", "", "## QC gates", ""]
    L += [f"- [ ] **{g['gate']}** ({g['kind']}): {g['check']} {g['tag']}" for g in p["gates"]]
    if p["built"]:
        L += ["", "## Built", ""]
        for b in p["built"]:
            L.append(f"- {b['route']}: ok={b['ok']}" + (f", registered as {b['registered_as']}" if b.get("registered_as") else "")
                     + (f", `{b['dir']}`" if b.get("dir") else "") + (f" ({b['note']})" if b.get("note") else ""))
    L += ["", "Not for clinical use until the bench tests in docs/PHASE0_BENCH_PROTOCOL.md and docs/DIRECT_PRINT.md are done."]
    return "\n".join(L) + "\n"
