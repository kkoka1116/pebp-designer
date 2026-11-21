"""Local web server for PEBP Studio.

Design rules this file keeps:

- It listens on 127.0.0.1 only and answers only requests whose Host header names this machine, so a
  web page elsewhere cannot reach it through DNS rebinding. Every POST must carry the header
  `X-PEBP-Studio: 1`, which a cross-site form cannot set. Scans and everything made from them stay
  in the workspace folder on this computer.
- Geometry is never computed here. Each build runs `python -m pebp <command>` in a subprocess, the
  same command line the README documents, and its output is shown to the user and appended to
  `studio_log.jsonl` in the case folder.
- Approvals live in `review.json` beside `design.json`. Each approval stores a fingerprint of what was
  approved; when the design changes underneath it, the step reads "changed since approval" and every
  later step is held until it is approved again. A finding marked `stop` holds the approval unless the
  reviewer ticks "approve anyway", which is recorded with the findings it overrode.
- Clinical choices (tip target) are made by a person in the UI and stored in the design record with who
  set them; the software never fills them in.
"""
from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

PKG_ROOT = Path(__file__).resolve().parents[2]          # pebp_demo/
STATIC = Path(__file__).resolve().parent / "static"
STEPS = ["case", "landmarks", "plate", "changes", "checks", "mold"]          # one-piece cast plate (default)
KIT_STEPS = ["case", "landmarks", "plate", "kit", "visit2"]                    # two-visit chairside route
ALL_STEPS = STEPS + ["kit", "visit2"]
STEP_TITLES = {"case": "Case", "landmarks": "Landmarks", "plate": "Plate and prototypes",
               "changes": "Baton changes", "checks": "Checks", "mold": "Mold",
               "kit": "Visit 1: try-in kit and final base", "visit2": "Visit 2: worn plate"}
ROUTES = {"cast": "one-piece cast plate (Tübingen, default)", "kit": "two-visit chairside kit (untested research route)"}
CASE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
SERVE_EXT = {".stl", ".json", ".md", ".html", ".3mf", ".png", ".txt", ".csv"}
TIP_TARGETS = {
    "unset": "not set",
    "above_epiglottis_rim": "above the epiglottis rim (Tübingen today)",
    "in_vallecula": "in the vallecula (Berlin, earlier Tübingen)",
}
MAX_UPLOAD = 300 * 1024 * 1024
CDN_THREE = "https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"

CAST_ROUTE_KINDS = {"prototype", "adjusted", "final_groove"}
# QC checks grouped by the step a person reviews them in
PLATE_CHECKS = {"plate thickness", "plate on scanned surface", "baton root supported", "graded plate", "border relief"}
LANDMARK_CHECKS = {"arch landmarks plausible"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _file_sha(p: Path) -> str | None:
    if not p.exists():
        return None
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ------------------------------------------------------------------ case state

class Workspace:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.jobs: dict[str, dict] = {}
        self.locks: dict[str, threading.Lock] = {}
        self.mu = threading.Lock()

    # paths
    def case_dir(self, name: str) -> Path:
        if not CASE_RE.match(name or ""):
            raise ValueError("case names use letters, digits, '-', '_' and '.', up to 64 characters")
        d = (self.root / name).resolve()
        if d.parent != self.root:
            raise ValueError("bad case name")
        return d

    def lock(self, name: str) -> threading.Lock:
        with self.mu:
            return self.locks.setdefault(name, threading.Lock())

    def list_cases(self) -> list[dict]:
        out = []
        for d in sorted(p for p in self.root.iterdir() if p.is_dir() and CASE_RE.match(p.name)):
            meta = _read_json(d / "studio_case.json") or {}
            if not meta and not (d / "design.json").exists():
                continue
            out.append({"name": d.name, "synthetic": meta.get("synthetic", (d / "scan_truth.json").exists()),
                        "has_design": (d / "design.json").exists(), "created": meta.get("created")})
        return out


def _read_json(p: Path):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def _write_json(p: Path, obj) -> None:
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2))
    tmp.replace(p)


def _log(case_dir: Path, entry: dict) -> None:
    with open(case_dir / "studio_log.jsonl", "a") as f:
        f.write(json.dumps(entry) + "\n")


def kit_available() -> bool:
    """The chairside route needs `pebp kit` and `pebp chairside` (docs/KIT_CLI.md); older checkouts lack them."""
    import importlib.util
    return all(importlib.util.find_spec(m) is not None for m in ("pebp.kit", "pebp.chairside"))


def _mod_text(m: dict) -> str:
    code, amt = m.get("code", ""), m.get("amount")
    unit = "°" if code.endswith("_deg") else " mm" if code.endswith("_mm") else ""
    word = re.sub(r"_(mm|deg)$", "", code).replace("_", " ")
    return word if amt is None else f"{word} {amt:g}{unit}"


def devices(record: dict) -> list[dict]:
    """Every device in the record with the fields the UI shows."""
    out = []
    for p in record.get("prototypes", []):
        out.append({"id": p["id"] if "id" in p else f"P{p['number']}", "kind": "prototype", "file": p["file"],
                    "label": {1: "2 mm posterior", 2: "centred", 3: "2 mm anterior"}.get(p["number"], ""),
                    "qc": p.get("qc"), "tip": p.get("tip"), "params": p.get("params")})
    for e in record.get("modification_log", []):
        out.append({"id": e.get("id", f"A{e['n']}"), "kind": "adjusted", "file": e["file"], "from": e.get("from"),
                    "label": f"from {e.get('from')} ({e.get('where')}): " + (", ".join(_mod_text(m) for m in e.get("modifications") or [])
                                                                       or json.dumps(e.get("changes"))),
                    "qc": e.get("qc"), "tip": e.get("tip"), "params": e.get("params"), "where": e.get("where"),
                    "note": e.get("note")})
    f = record.get("final_design")
    if f:
        out.append({"id": "F", "kind": "final_groove", "file": f["file"], "label": "final design with wire groove",
                    "qc": f.get("qc"), "params": f.get("params")})
    for b in (record.get("kit") or {}).get("batons", []):
        out.append({"id": b["id"], "kind": "kit", "file": b["file"], "piece_file": b.get("piece_file"),
                    "label": f"kit baton {b.get('code')}", "qc": b.get("qc"), "tip": b.get("tip"), "params": b.get("params"),
                    "code": b.get("code"), "tip_shift": b.get("tip_shift_vs_standard_mm")})
    for e in record.get("chairside_finals", []):
        out.append({"id": e["id"], "kind": "chairside", "file": e["file"], "from": e.get("from"),
                    "label": f"worn plate from {e.get('from')} ({e.get('code')})", "qc": e.get("qc"), "params": e.get("params"),
                    "files": e.get("files"), "wire": e.get("wire"), "steps": e.get("steps"), "flag": e.get("flag")})
    for c in record.get("cast_plates", []):
        out.append({"id": f"C{c['n']}", "kind": "cast", "file": c.get("file"), "from": c.get("from"),
                    "label": f"cast plate from {c.get('from')}", "qc": c.get("qc"), "params": c.get("params")})
    return out


def _qc_findings(devs: list[dict], names: set[str] | None, exclude: set[str] | None = None) -> list[dict]:
    """QC checks that are not a pass, as findings. fail -> stop, warn -> warn."""
    out = []
    for d in devs:
        for c in (d.get("qc") or {}).get("checks", []):
            if c["status"] == "pass":
                continue
            if names is not None and c["check"] not in names:
                continue
            if exclude and c["check"] in exclude:
                continue
            out.append({"level": "stop" if c["status"] == "fail" else "warn", "device": d["id"], "check": c["check"],
                        "text": f"{d['id']}: {c['check']} = {json.dumps(c['value'])} ({c['rule']})",
                        "basis": _basis(c["rule"])})
    return out


def _basis(rule: str) -> str:
    r = rule.lower()
    if "heuristic" in r:
        return "project heuristic"
    if "tübingen" in r or "tuebingen" in r:
        return "published (Tübingen 2025)"
    return "geometry check"


class Case:
    """Everything the UI needs about one case folder, computed fresh on every request."""

    def __init__(self, ws: Workspace, name: str):
        self.ws, self.name = ws, name
        self.dir = ws.case_dir(name)
        self.record = _read_json(self.dir / "design.json") if (self.dir / "design.json").exists() else None
        self.review = _read_json(self.dir / "review.json") or {"steps": {}, "log": []}
        self.meta = _read_json(self.dir / "studio_case.json") or {}
        self.mold = _read_json(self.dir / "mold" / "mold_manifest.json")

    def fingerprint(self, step: str):
        """What an approval is an approval *of*, in the form it is stored (JSON), so the two compare equal."""
        fp = self._fingerprint(step)
        return None if fp is None else json.loads(json.dumps(fp, default=str))

    def _fingerprint(self, step: str):
        r = self.record or {}
        if step == "case":
            return {"scan": _file_sha(self.dir / "scan.stl"), "cleft": (self.meta.get("cleft") or {}).get("value")}
        if not r:
            return None
        if step == "landmarks":
            lm = {k: v for k, v in r.get("landmarks", {}).items()}
            fp = {"scan": r.get("source_scan", {}).get("sha256"), "landmarks": _sha(lm)}
            if (r.get("cleft_status") or {}).get("value"):
                fp["cleft"] = r["cleft_status"]["value"]       # a changed cleft type expires the approval
            return fp
        if step == "plate":
            # the kit route adds design_choices.try_in_kit in Visit 1; that is not part of the plate's approval
            choices = {k: v for k, v in (r.get("design_choices") or {}).items() if k != "try_in_kit"}
            return {"plate": _sha(r.get("plate", {}).get("params")), "choices": _sha(choices or None),
                    "extension": r.get("extension_mode"), "protos": [p.get("sha256") for p in r.get("prototypes", [])],
                    "final": (r.get("final_design") or {}).get("sha256")}
        if step == "changes":
            return {"devices": [(d["id"], d["file"]) for d in devices(r) if d["kind"] in CAST_ROUTE_KINDS],
                    "chosen": self.review.get("chosen_device"), "settings": _sha(r.get("clinical_settings"))}
        if step == "checks":
            return {"qc": _sha([(d["id"], d.get("qc")) for d in devices(r) if d["kind"] in CAST_ROUTE_KINDS]),
                    # a cast plate made later in the Mold step adds its own reach rows; they are not part of this approval
                    "reach": _sha([c for c in (r.get("reach_check") or {}).get("checks", []) if not re.match(r"^C\d+ ", c.get("check", ""))])}
        if step == "mold":
            return {"mold": _sha(self.mold) if self.mold else None}
        if step == "kit":
            k, b = r.get("kit"), r.get("chairside_base")
            if not k:
                return None
            return {"kit": k.get("base_sha256"), "batons": [(x["id"], x.get("sha256"), (x.get("qc") or {}).get("overall")) for x in k.get("batons", [])],
                    "base": (b or {}).get("sha256"), "base_qc": ((b or {}).get("qc") or {}).get("overall")}
        if step == "visit2":
            s = self.worn()
            return {"worn": [s["id"], s.get("sha256"), s.get("base_sha256"), (s.get("qc") or {}).get("overall")]} if s else None
        return None

    @property
    def route(self) -> str:
        return self.review.get("route", "cast") if kit_available() else "cast"

    @property
    def steps(self) -> list[str]:
        return KIT_STEPS if self.route == "kit" else STEPS

    def worn(self) -> dict | None:
        """The worn plate S<n> under review: the one the reviewer picked, else the newest."""
        fin = (self.record or {}).get("chairside_finals") or []
        want = self.review.get("chosen_worn")
        return next((e for e in fin if e["id"] == want), fin[-1] if fin else None)

    def findings(self, step: str) -> list[dict]:
        r = self.record
        f: list[dict] = []
        if step == "case":
            if not (self.dir / "scan.stl").exists():
                f.append({"level": "stop", "text": "no scan yet: add a scan or make a synthetic demo case"})
            if not self.meta.get("synthetic", True):
                f.append({"level": "info", "text": "this is a real scan: it and everything made from it stay on this computer"})
            cl = _cleft_entry(self.meta)
            if not cl:
                f.append({"level": "stop", "text": "cleft type not entered: choose hard and soft palate, soft palate or uvula only, "
                                                   "or no cleft. It picks the baton (Tübingen cleft or no-cleft extension) and how the "
                                                   "plate bridges the palate; the software does not decide it",
                          "basis": "clinical choice"})
            elif cl.get("legacy"):
                f.append({"level": "warn", "text": f"the cleft was entered as '{cl['legacy']}' before the question had three answers; "
                                                   f"it is read as '{cl['value']}'. Enter the cleft type again: a cleft of the soft "
                                                   "palate or uvula only takes the no-cleft baton", "basis": "clinical choice"})
            if self.meta.get("scan_repair"):
                f.append({"level": "warn", "text": "scan file header corrected on upload: " + self.meta["scan_repair"],
                          "basis": "geometry check"})
            return f
        if not r:
            return [{"level": "stop", "text": "not built yet"}]
        devs = devices(r)
        protos = [d for d in devs if d["kind"] == "prototype"]
        if step == "landmarks":
            lm = r["landmarks"]
            f += _qc_findings(protos[1:2] or protos[:1], LANDMARK_CHECKS)
            f.append({"level": "info", "text": "left and right assume an unmirrored scan export (x = patient's right). "
                                               "Confirm the cleft side on screen matches the patient."})
            cs = r.get("cleft_status") or {}
            entered = _cleft_entry(self.meta)
            if cs.get("value"):
                f.append({"level": "info", "text": f"cleft type '{cs.get('label')}' entered by {cs.get('entered_by') or '?'}; the baton "
                                                   f"follows it ({r.get('extension_mode')}); bridging: {cs.get('bridging')}; posterior "
                                                   f"midline bulge {cs.get('posterior_bulge_mm')} mm."})
                for w in cs.get("warnings", []):
                    f.append({"level": "warn", "text": w, "basis": "geometry check"})
                if entered and entered["value"] != cs["value"]:
                    f.append({"level": "stop", "text": f"the cleft type was changed to '{entered['value']}' after this design was built "
                                                       f"('{cs['value']}'): detect again", "basis": "clinical choice"})
            else:
                found = bool(lm.get("cleft_present"))
                f.append({"level": "info", "text": f"no cleft type in this design: cleft {'found' if found else 'not found'} by the scan "
                                                   f"analysis; the extension follows it ({r.get('extension_mode')})."})
                if entered:
                    f.append({"level": "stop", "text": "the cleft type was entered after this design was built: detect again",
                              "basis": "clinical choice"})
            for n in lm.get("notes", []):
                f.append({"level": "info", "text": n})
            return f
        if step == "plate":
            # plate checks are the same on every prototype: report P2 (or the first)
            f += _qc_findings(protos[1:2] or protos[:1], PLATE_CHECKS)
            for k, v in (r.get("design_choices") or {}).items():
                f.append({"level": "warn", "text": f"design choice {k}: {v.get('flag', '')}", "basis": "project heuristic"})
            rp = (r.get("plate") or {}).get("root_pad") or {}
            if rp.get("cut_back"):
                f.append({"level": "warn", "text": f"root pad cut back: {rp['cut_back'].get('note')}"})
            return f
        if step == "changes":
            cs = r.get("clinical_settings", {})
            if cs.get("tip_target", "unset") == "unset":
                f.append({"level": "info", "text": "tip target is not set. It is the team's choice; until it is set, endoscopy "
                                                   "findings about the tip position cannot be flagged against it."})
            chosen = self.review.get("chosen_device")
            if not chosen:
                f.append({"level": "stop", "text": "choose the device to cast (the one accepted at the try-in)"})
            elif chosen not in [d["id"] for d in devs]:
                f.append({"level": "stop", "text": f"the chosen device {chosen} is not in the design record any more"})
            else:
                d = next(d for d in devs if d["id"] == chosen)
                if (d.get("qc") or {}).get("overall") == "fail":
                    f.append({"level": "stop", "text": f"{chosen} fails QC; see Checks"})
            return f
        if step == "checks":
            chosen = self.review.get("chosen_device")
            relevant = [d for d in devs if d["kind"] in CAST_ROUTE_KINDS and (d["kind"] == "prototype" or d["id"] == chosen)]
            f += _qc_findings(relevant, None, exclude=LANDMARK_CHECKS | PLATE_CHECKS)
            out = [c for c in (r.get("reach_check") or {}).get("checks", []) if not c.get("within")]
            if out:
                f.append({"level": "info", "basis": "plausibility against published infant norms, no clinical claim",
                          "text": f"{len(out)} reach measure(s) fall outside the published infant envelope (table above). "
                                  "This is a plausibility check, not a fitting rule."})
            return f
        if step == "kit":
            k, b = r.get("kit"), r.get("chairside_base")
            if not k:
                return [{"level": "stop", "text": "the try-in kit is not built yet"}]
            f.append({"level": "warn", "basis": "untested research route", "text": f"try-in kit: {k.get('flag', '')}"})
            for x in k.get("batons", []):
                for c in (x.get("qc") or {}).get("checks", []):
                    if c["status"] != "pass":
                        f.append({"level": "stop" if c["status"] == "fail" else "warn", "device": x["id"], "basis": _basis(c["rule"]),
                                  "text": f"{x['id']} ({x.get('code')}): {c['check']} = {json.dumps(c['value'])} ({c['rule']})"})
            if not b:
                f.append({"level": "stop", "text": "the final base is not built yet; it is cast between the visits"})
            else:
                f.append({"level": "warn", "basis": "untested research route", "text": f"final base: {b.get('flag', '')}"})
                for c in (b.get("qc") or {}).get("checks", []):
                    if c["status"] != "pass":
                        f.append({"level": "stop" if c["status"] == "fail" else "warn", "basis": _basis(c["rule"]),
                                  "text": f"final base: {c['check']} = {json.dumps(c['value'])} ({c['rule']})"})
            return f
        if step == "visit2":
            s = self.worn()
            if not s:
                return [{"level": "stop", "text": "no worn plate yet: after the try-in, choose the kit baton that fitted and build it"}]
            f.append({"level": "warn", "basis": "untested research route", "text": f"{s['id']}: {s.get('flag', '')}"})
            b = r.get("chairside_base") or {}
            if b.get("sha256") and s.get("base_sha256") and s["base_sha256"] != b["sha256"]:
                f.append({"level": "stop", "text": f"{s['id']} was built on an earlier final base; build it again on the current one"})
            for c in (s.get("qc") or {}).get("checks", []):
                if c["status"] != "pass":
                    f.append({"level": "stop" if c["status"] == "fail" else "warn", "basis": _basis(c["rule"]),
                              "text": f"{s['id']}: {c['check']} = {json.dumps(c['value'])} ({c['rule']})"})
            return f
        if step == "mold":
            if not self.mold:
                return [{"level": "stop", "text": "no mold built yet"}]
            v = self.mold.get("validation", {})
            if not v.get("ok"):
                f.append({"level": "stop", "text": "the mold failed its own validation (see the table)"})
            for c in (v.get("final_qc") or {}).get("checks", []):
                if c["status"] != "pass":
                    f.append({"level": "stop" if c["status"] == "fail" else "warn", "basis": _basis(c["rule"]),
                              "text": f"cast plate: {c['check']} = {json.dumps(c['value'])} ({c['rule']})"})
            for n in self.mold.get("notes", []):
                f.append({"level": "info", "text": n})
            return f
        return f

    def step_states(self) -> list[dict]:
        out, prev_ok = [], True
        for s in self.steps:
            fp = self.fingerprint(s)
            ap = self.review["steps"].get(s)
            fnd = self.findings(s)
            built = fp is not None and not (s == "case" and fp.get("scan") is None) and not (s == "mold" and not self.mold)
            if ap and ap.get("fingerprint") == fp and prev_ok:
                status = "approved"
            elif ap and ap.get("fingerprint") != fp:
                status = "changed"
            elif not prev_ok:
                status = "locked"
            elif built:
                status = "review"
            else:
                status = "todo"
            out.append({"step": s, "title": STEP_TITLES[s], "status": status, "findings": fnd,
                        "approval": ap, "stops": sum(1 for x in fnd if x["level"] == "stop")})
            prev_ok = status == "approved"
        return out

    def catalog(self) -> dict | None:
        """The per-patient catalog of modular versions (catalog/catalog.json), if built, with a flag when the
        design has changed since it was built."""
        cat = _read_json(self.dir / "catalog" / "catalog.json")
        if not cat:
            return None
        r = self.record or {}
        cat["stale"] = cat.get("source_scan_sha256") != (r.get("source_scan") or {}).get("sha256") or \
            (r.get("catalog") or {}).get("time") != cat.get("time")
        return cat

    def summary(self) -> dict:
        r = self.record
        files = sorted(str(p.relative_to(self.dir)) for p in self.dir.rglob("*")
                       if p.is_file() and p.suffix.lower() in SERVE_EXT and not p.name.startswith("studio_")) if self.dir.exists() else []
        jobs = [j for j in self.ws.jobs.values() if j["case"] == self.name]
        return {"name": self.name, "meta": self.meta, "has_scan": (self.dir / "scan.stl").exists(),
                "record": r, "devices": devices(r) if r else [], "review": self.review, "steps": self.step_states(),
                "mold": self.mold, "files": files, "tip_targets": TIP_TARGETS,
                "route": self.route, "routes": ROUTES, "kit_available": kit_available(), "worn": self.worn(),
                "catalog": self.catalog(), "catalog_choice": self.review.get("catalog_choice"),
                "running": next((j for j in jobs if j["status"] == "running"), None),
                "last_job": max(jobs, key=lambda j: j["started"]) if jobs else None}


# ------------------------------------------------------------------ jobs

def _run_pebp(args: list[str]) -> tuple[int, str, str]:
    p = subprocess.run([sys.executable, "-m", "pebp", *map(str, args)], cwd=PKG_ROOT, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def start_job(ws: Workspace, name: str, kind: str, steps: list[list[str]], after=None, before=None,
              ok_codes: tuple[int, ...] = (0,)) -> dict:
    """Run one or more pebp commands for a case in a thread. Only one job per case at a time.
    `ok_codes`: exit codes that still count as a build (the chairside commands exit 1 when they wrote
    their files but QC failed; the failure then shows as a STOP finding, not a crashed build)."""
    lk = ws.lock(name)
    if not lk.acquire(blocking=False):
        raise RuntimeError("another build is running for this case; wait for it to finish")
    job = {"id": uuid.uuid4().hex[:10], "case": name, "kind": kind, "status": "running", "started": now(),
           "commands": [" ".join(["pebp", *map(str, s)]) for s in steps], "stdout": "", "stderr": "", "rc": None}
    ws.jobs[job["id"]] = job

    def work():
        case_dir = ws.case_dir(name)
        try:
            if before:
                before()
            rc = 0
            for s in steps:
                rc, out, err = _run_pebp(s)
                job["stdout"] += out
                job["stderr"] += err
                _log(case_dir, {"time": now(), "kind": kind, "command": "pebp " + " ".join(map(str, s)), "rc": rc})
                if rc not in ok_codes:
                    break
            if rc in ok_codes and after:
                after(job)
            job["rc"] = rc
            job["status"] = "done" if rc in ok_codes else "failed"
        except Exception as e:  # report, never swallow
            job["stderr"] += f"\n{type(e).__name__}: {e}"
            job["status"] = "failed"
        finally:
            job["ended"] = now()
            lk.release()

    threading.Thread(target=work, daemon=True).start()
    return job


# ------------------------------------------------------------------ actions

def _num(body: dict, key: str, lo: float, hi: float, default=None):
    v = body.get(key, default)
    if v is None or v == "":
        return None
    v = float(v)
    if not lo <= v <= hi:
        raise ValueError(f"{key} must be between {lo} and {hi}")
    return v


def act_new_case(ws: Workspace, body: dict) -> dict:
    name = body.get("name", "").strip()
    d = ws.case_dir(name)
    if d.exists():
        raise ValueError(f"a case called {name!r} already exists")
    d.mkdir()
    _write_json(d / "studio_case.json", {"created": now(), "synthetic": None})
    return {"name": name}


def act_synthetic(ws: Workspace, name: str, body: dict) -> dict:
    d = ws.case_dir(name)
    if (d / "scan.stl").exists():
        raise ValueError("this case already has a scan; make a new case")
    args = ["synth", "--out", d, "--preset", body.get("preset", "prs"), "--unoriented"]
    if body.get("no_cleft"):
        args.append("--no-cleft")
    asym = _num(body, "asym", 0, 5, 1.0)
    if asym:
        args += ["--asym", asym]
    args += ["--seed", int(body.get("seed", 7))]

    def after(job):
        meta = _read_json(d / "studio_case.json") or {}
        meta.update({"synthetic": True, "scan_source": "pebp synth " + json.dumps(body)})
        _write_json(d / "studio_case.json", meta)
    return start_job(ws, name, "synthetic scan", [args], after=after)


def _cleft_entry(meta: dict) -> dict | None:
    """The entered cleft type, with entries from before the three-way question read as: yes = hard and soft
    palate, no = none (flagged as legacy so the finding asks for it again)."""
    cl = meta.get("cleft") or {}
    v = cl.get("value")
    legacy = {"yes": "hard_soft", "no": "none"}
    if v in legacy:
        return {**cl, "value": legacy[v], "legacy": v}
    return cl if v in ("hard_soft", "soft_only", "none") else None


def act_cleft(ws: Workspace, name: str, body: dict) -> dict:
    """The clinician enters the cleft type from the examination: hard_soft, soft_only or none. It picks the
    Tübingen baton variant and how the plate bridges the palate (see pebp/cleft.py)."""
    from ..cleft import CLEFT_TYPES
    d = ws.case_dir(name)
    if not d.exists():
        raise ValueError("no such case")
    value, by = body.get("value"), str(body.get("by", "")).strip()
    if value not in CLEFT_TYPES:
        raise ValueError("cleft type is one of: " + ", ".join(CLEFT_TYPES))
    if not by:
        raise ValueError("enter your name or initials first")
    meta = _read_json(d / "studio_case.json") or {}
    meta["cleft"] = {"value": value, "by": by, "time": now()}
    _write_json(d / "studio_case.json", meta)
    _log(d, {"time": now(), "kind": "cleft entered", "value": value, "by": by})
    return {"ok": True}


def act_reveal(ws: Workspace, name: str, body: dict) -> dict:
    """Show the case folder (or one file in it) in Finder. Local only, like every other action."""
    d = ws.case_dir(name)
    if not d.exists():
        raise ValueError("no such case")
    target = d
    if body.get("path"):
        p = (d / str(body["path"])).resolve()
        if d not in p.parents or not p.exists():
            raise ValueError("no such file in this case")
        target = p
    if sys.platform == "darwin":
        cmd = ["open", "-R", str(target)] if target.is_file() else ["open", str(target)]
    elif sys.platform.startswith("win"):
        cmd = ["explorer", str(target if target.is_dir() else target.parent)]
    else:
        cmd = ["xdg-open", str(target if target.is_dir() else target.parent)]
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        return {"ok": False, "path": str(target), "message": f"no file browser here; the folder is {target}"}
    return {"ok": True, "path": str(target)}


def repair_binary_stl_count(data: bytes) -> tuple[bytes, str | None]:
    """Some scanner exporters write a binary STL whose triangle-count field is 0 (or wrong).

    trimesh trusts the field and reads an empty mesh. When the file is binary (not "solid ... facet"
    text) and its size is exactly 84 + 50 n bytes, write n into the count field. The triangles
    themselves are untouched. Returns the bytes and a note, or None when nothing was changed.
    """
    if len(data) < 84 + 50:
        return data, None
    head = data[:1024].lstrip().lower()
    if head.startswith(b"solid") and b"facet" in head:
        return data, None                                   # ASCII STL
    declared = int.from_bytes(data[80:84], "little")
    body = len(data) - 84
    if body % 50 or declared == body // 50:
        return data, None
    n = body // 50
    return data[:80] + n.to_bytes(4, "little") + data[84:], (
        f"the file's header gave {declared} triangles but it holds {n}; the count was corrected "
        "(scanner exporters sometimes leave it at 0). The triangles are unchanged.")


def act_upload(ws: Workspace, name: str, data: bytes, filename: str) -> dict:
    d = ws.case_dir(name)
    if not d.exists():
        raise ValueError("no such case")
    if (d / "scan.stl").exists():
        raise ValueError("this case already has a scan; make a new case")
    if not filename.lower().endswith(".stl"):
        raise ValueError("the scan must be an STL file")
    original_sha = hashlib.sha256(data).hexdigest()
    data, repair = repair_binary_stl_count(data)
    tmp = d / "scan.upload.stl"
    tmp.write_bytes(data)
    import trimesh
    try:
        m = trimesh.load(tmp, force="mesh")
        if len(m.faces) < 100:
            raise ValueError("the file holds fewer than 100 triangles")
        import numpy as np
        distinct = len(np.unique(np.round(np.asarray(m.vertices), 4), axis=0))
        extent = float(np.ptp(np.asarray(m.vertices), axis=0).max()) if len(m.vertices) else 0.0
        if distinct < 100 or extent < 5.0:
            raise ValueError(f"the file holds no scan shape: its {len(m.faces)} triangles have only {distinct} distinct "
                             f"corner points and span {extent:.1f} mm. The file may be blank or an incomplete download; "
                             "download or export it again and check it opens in a mesh viewer")
    except Exception as e:
        tmp.unlink(missing_ok=True)
        raise ValueError(f"could not read the STL: {e}")
    tmp.replace(d / "scan.stl")
    meta = _read_json(d / "studio_case.json") or {}
    meta.update({"synthetic": False, "scan_source": f"uploaded {filename}", "scan_sha256": _file_sha(d / "scan.stl"),
                 "triangles": int(len(m.faces))})
    if repair:
        meta.update({"scan_repair": repair, "scan_original_sha256": original_sha})
    _write_json(d / "studio_case.json", meta)
    _log(d, {"time": now(), "kind": "scan added", "file": filename, "sha256": meta["scan_sha256"],
             **({"repair": repair, "original_sha256": original_sha} if repair else {})})
    return {"ok": True, "triangles": int(len(m.faces)), "repair": repair}


DESIGN_FLAGS = {"border_relief": "--border-relief", "final": "--final"}


def act_design(ws: Workspace, name: str, body: dict) -> dict:
    """(Re)build landmarks, plate and P1-P3 with `pebp design`. A rebuild starts a new design record, so the
    old one is archived first, and the person's clinical settings and imaging values are carried over."""
    d = ws.case_dir(name)
    if not (d / "scan.stl").exists():
        raise ValueError("add a scan first")
    old = _read_json(d / "design.json")
    if old and (old.get("modification_log") or old.get("cast_plates") or old.get("fitting_sessions") or old.get("kit")) \
            and not body.get("confirm_reset"):
        raise PermissionError("rebuilding starts a new design record: the changed devices, cast plates and try-ins in the "
                              "current one are archived, not carried over. Tick 'I understand' to rebuild.")
    args = ["design", d / "scan.stl", "--out", d]
    profile = body.get("plate_profile", "uniform")
    if profile not in ("uniform", "graded"):
        raise ValueError("plate profile is uniform or graded")
    args += ["--plate-profile", profile]
    for key, flag, lo, hi in [("plate_thickness", "--plate-thickness", 1.5, 4), ("final_thickness", "--final-thickness", 1.5, 4),
                              ("thin_thickness", "--thin-thickness", 1.0, 3), ("flange", "--flange", 0, 4),
                              ("block_out", "--block-out", 0, 6)]:
        v = _num(body, key, lo, hi)
        if v is not None and (key != "thin_thickness" or profile == "graded"):
            args += [flag, v]
    for key, flag in DESIGN_FLAGS.items():
        if body.get(key):
            args.append(flag)
    ext = body.get("extension", "auto")
    if ext not in ("auto", "tuebingen-cleft", "tuebingen-nocleft", "parametric"):
        raise ValueError("unknown extension mode")
    entered = _cleft_entry(_read_json(d / "studio_case.json") or {})
    if entered:
        # the clinician's entry, not the detector: it picks the baton (with --extension auto) and the bridging
        args += ["--cleft", entered["value"], "--cleft-entered-by", entered.get("by") or "?"]
    args += ["--extension", ext]
    backend = body.get("backend", "builtin")
    if backend not in ("builtin", "moldstudio"):
        raise ValueError("unknown landmark backend")
    args += ["--backend", backend]
    keep_settings = (old or {}).get("clinical_settings")
    keep_airway = {k: v for k, v in ((old or {}).get("airway") or {}).get("landmarks", {}).items()
                   if v.get("value") is not None and v.get("source") not in ("scan_proxy", "design")}

    def before():
        if old:
            hist = d / "history"
            hist.mkdir(exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            shutil.copy2(d / "design.json", hist / f"design_{stamp}.json")
            if (d / "review.json").exists():
                shutil.copy2(d / "review.json", hist / f"review_{stamp}.json")
            # devices from the old record no longer belong to the new one
            for p in list(d.glob("adjusted_*.stl")) + list(d.glob("cast_*.stl")):
                p.unlink()
            if (d / "mold").exists():
                shutil.move(str(d / "mold"), str(hist / f"mold_{stamp}"))
            if (d / "kit").exists():   # docs/KIT_CLI.md: the kit and chairside files belong to the old record
                shutil.move(str(d / "kit"), str(hist / f"kit_{stamp}"))
            if (d / "catalog").exists():   # the catalog was built from the old plate settings and cleft type
                shutil.move(str(d / "catalog"), str(hist / f"catalog_{stamp}"))
            rv = _read_json(d / "review.json") or {"steps": {}, "log": []}
            rv.pop("chosen_device", None)
            rv.pop("chosen_worn", None)
            _write_json(d / "review.json", rv)
        (d / "final_design.stl").unlink(missing_ok=True)

    def after(job):
        from .. import record as rec
        if keep_settings:
            r = rec.load(d / "design.json")
            r["clinical_settings"].update(keep_settings)
            rec.save(r, d / "design.json")
        for k, v in keep_airway.items():
            val = ",".join(str(x) for x in v["value"]) if isinstance(v["value"], list) else str(v["value"])
            rc, out, err = _run_pebp(["airway", d / "design.json", "--set", f"{k}={val}", "--source", v.get("source", "manual")]
                                     + (["--note", v["note"]] if v.get("note") else []))
            job["stdout"] += out
            job["stderr"] += err
        _remember(d, {"design_options": body})
    return start_job(ws, name, "design", [args], after=after, before=before)


def _remember(d: Path, patch: dict) -> None:
    rv = _read_json(d / "review.json") or {"steps": {}, "log": []}
    rv.update(patch)
    _write_json(d / "review.json", rv)


def act_adjust(ws: Workspace, name: str, body: dict) -> dict:
    from ..fitting import MODIFICATIONS
    d = ws.case_dir(name)
    src = str(body.get("from", "")).strip().upper()
    if not re.match(r"^(P[1-3]|A\d+|F|C\d+)$", src):
        raise ValueError("choose the device to change")
    mods = body.get("mods") or []
    if not mods:
        raise ValueError("add at least one change")
    args = ["adjust", d / "design.json", "--from", src]
    for m in mods:
        code = m.get("code")
        if code not in MODIFICATIONS:
            raise ValueError(f"unknown change {code!r}")
        if MODIFICATIONS[code][0] == "flag":
            args += ["--mod", code]
        else:
            amt = float(m.get("amount"))
            if not 0 < amt <= 30:
                raise ValueError(f"{code}: amount must be above 0 and at most 30")
            args += ["--mod", f"{code}={amt:g}"]
    where = body.get("where", "lab")
    args += ["--bedside"] if where == "bedside" else ["--where", "lab"]
    if body.get("note"):
        args += ["--note", str(body["note"])[:500]]
    return start_job(ws, name, "change", [args])


def act_airway(ws: Workspace, name: str, body: dict) -> dict:
    from ..airway import LANDMARKS
    d = ws.case_dir(name)
    key = body.get("name")
    if key not in LANDMARKS or key in ("pns", "baton_root", "baton_tip"):
        raise ValueError("this value can't be set here")
    val = body.get("value")
    if val in (None, ""):
        sval = ""
    elif LANDMARKS[key][0] == "point":
        pts = [float(x) for x in (val if isinstance(val, list) else str(val).split(","))]
        if len(pts) != 3:
            raise ValueError("a point needs x, y and z in the design frame (mm)")
        sval = ",".join(f"{x:g}" for x in pts)
    else:
        sval = f"{float(val):g}"
    source = body.get("source", "manual")
    if source not in ("ct", "mri", "endoscopy", "manual"):
        raise ValueError("source is ct, mri, endoscopy or manual")
    args = ["airway", d / "design.json", "--set", f"{key}={sval}", "--source", source]
    if body.get("note"):
        args += ["--note", str(body["note"])[:300]]
    return start_job(ws, name, "airway value", [args])


def act_mold(ws: Workspace, name: str, body: dict) -> dict:
    c = Case(ws, name)
    if c.route != "cast":
        raise ValueError("this case is on the two-visit chairside route; its tooling is built in Visit 1")
    _require_approved(c, "checks", "approve the Checks step before building the mold")
    dev = c.review.get("chosen_device")
    if not dev:
        raise ValueError("choose the device to cast in the Baton changes step")
    args = ["mold", c.dir, "--device", dev]
    for key, flag, lo, hi in [("final_thickness", "--final-thickness", 2.0, 4.0), ("wall", "--wall", 3.0, 15.0)]:
        v = _num(body, key, lo, hi)
        if v is not None:
            args += [flag, v]

    def before():
        if (c.dir / "mold").exists():
            hist = c.dir / "history"
            hist.mkdir(exist_ok=True)
            shutil.move(str(c.dir / "mold"), str(hist / f"mold_{datetime.now().strftime('%Y%m%d-%H%M%S')}"))
    return start_job(ws, name, "mold", [args], before=before)


def act_route(ws: Workspace, name: str, body: dict) -> dict:
    """Choose how the worn plate is made. Changing it withdraws every approval after the Plate step."""
    route = body.get("route")
    if route not in ROUTES:
        raise ValueError("unknown route")
    if route == "kit" and not kit_available():
        raise ValueError("this checkout has no `pebp kit` / `pebp chairside` (docs/KIT_CLI.md)")
    c = Case(ws, name)
    with ws.lock(name):
        rv = c.review
        if rv.get("route", "cast") != route:
            for s in set(ALL_STEPS) - {"case", "landmarks", "plate"}:
                rv["steps"].pop(s, None)
            rv["route"] = route
            rv["log"].append({"time": now(), "by": body.get("by", ""), "action": "chose route", "route": route})
            _write_json(c.dir / "review.json", rv)
            _log(c.dir, {"time": now(), "kind": "route", "route": route, "by": body.get("by", "")})
    return {"ok": True}


def _require_approved(c: "Case", step: str, why: str) -> None:
    st = {s["step"]: s for s in c.step_states()}
    if st.get(step, {}).get("status") != "approved":
        raise PermissionError(why)


def act_kit(ws: Workspace, name: str, body: dict) -> dict:
    """Visit 1: the try-in kit (socket base, K1-K15) and the worn plate's final base with its casting tooling."""
    c = Case(ws, name)
    if c.route != "kit":
        raise ValueError("choose the two-visit chairside route on the Plate step first")
    _require_approved(c, "plate", "approve the Plate step before building the kit")
    t = _num(body, "final_thickness", 2.5, 3.0, 2.5)
    return start_job(ws, name, "try-in kit and final base",
                     [["kit", c.dir], ["chairside", c.dir, "--base", "--final-thickness", t]], ok_codes=(0, 1))


def act_worn(ws: Workspace, name: str, body: dict) -> dict:
    """Visit 2: the worn plate S<n> from the kit baton chosen at the try-in."""
    c = Case(ws, name)
    if c.route != "kit":
        raise ValueError("this case is on the cast-plate route")
    _require_approved(c, "kit", "approve Visit 1 before building the worn plate")
    k = str(body.get("kit", "")).upper()
    if k not in [b["id"] for b in ((c.record or {}).get("kit") or {}).get("batons", [])]:
        raise ValueError("choose a kit baton from this case's kit")
    t = ((c.record or {}).get("chairside_base") or {}).get("final_plate_thickness", 2.5)

    def after(job):
        r = _read_json(c.dir / "design.json") or {}
        fin = r.get("chairside_finals") or []
        if fin:
            _remember(c.dir, {"chosen_worn": fin[-1]["id"]})
    return start_job(ws, name, f"worn plate from {k}", [["chairside", c.dir, "--kit", k, "--final-thickness", t]],
                     after=after, ok_codes=(0, 1))


def act_catalog(ws: Workspace, name: str, body: dict) -> dict:
    """Build every modular version for this patient: bases for both cleft groups and every plate option,
    and the 15 kit batons of each baton variant (`pebp catalog --build`)."""
    c = Case(ws, name)
    if c.route != "kit":
        raise ValueError("choose the two-visit chairside route on the Plate step first")
    _require_approved(c, "plate", "approve the Plate step before building the catalog")
    return start_job(ws, name, "device catalog", [["catalog", c.dir, "--build"]])


def act_catalog_choice(ws: Workspace, name: str, body: dict) -> dict:
    """Record the clinician's narrowing and, once one version is left or picked, that version."""
    c = Case(ws, name)
    cat = c.catalog()
    if not cat:
        raise ValueError("build the catalog first")
    who = str(body.get("by", "")).strip()[:80]
    if not who:
        raise ValueError("enter your name or initials on the Case step first")
    vid = body.get("version")
    if vid is not None and vid not in {v["id"] for v in cat["versions"]}:
        raise ValueError("no such version in the catalog")
    filters = {k: body.get("filters", {}).get(k) for k in ("cleft_type", "plate_profile", "border_relief", "d_length_mm", "d_angle_deg")}
    with ws.lock(name):
        rv = c.review
        rv["catalog_choice"] = {"filters": filters, "version": vid, "by": who, "time": now(), "catalog_time": cat.get("time")}
        rv["log"].append({"time": now(), "by": who, "action": "catalog narrowed", "filters": filters, "version": vid})
        _write_json(c.dir / "review.json", rv)
    return {"ok": True}


def act_choose_worn(ws: Workspace, name: str, body: dict) -> dict:
    c = Case(ws, name)
    sid = str(body.get("worn", "")).upper()
    if sid not in [e["id"] for e in (c.record or {}).get("chairside_finals", [])]:
        raise ValueError("no such worn plate")
    with ws.lock(name):
        rv = c.review
        rv["chosen_worn"] = sid
        rv["log"].append({"time": now(), "by": body.get("by", ""), "action": "chose worn plate", "device": sid})
        _write_json(c.dir / "review.json", rv)
    return {"ok": True}


def act_settings(ws: Workspace, name: str, body: dict) -> dict:
    from .. import record as rec
    d = ws.case_dir(name)
    tt = body.get("tip_target")
    if tt not in TIP_TARGETS:
        raise ValueError("unknown tip target")
    who = (body.get("by") or "").strip()[:80]
    if tt != "unset" and not who:
        raise ValueError("enter your name or initials on the Case step first: a clinical choice records who made it")
    with ws.lock(name):
        r = rec.load(d / "design.json")
        old = r["clinical_settings"].get("tip_target")
        r["clinical_settings"]["tip_target"] = tt
        rec.save(r, d / "design.json")
        rv = _read_json(d / "review.json") or {"steps": {}, "log": []}
        rv["log"].append({"time": now(), "by": who, "action": "clinical setting", "tip_target": tt, "was": old})
        _write_json(d / "review.json", rv)
        _log(d, {"time": now(), "kind": "clinical setting", "tip_target": tt, "by": who})
    return {"ok": True}


def act_choose(ws: Workspace, name: str, body: dict) -> dict:
    c = Case(ws, name)
    dev = str(body.get("device", "")).upper()
    if dev not in [x["id"] for x in devices(c.record or {})]:
        raise ValueError("no such device")
    with ws.lock(name):
        rv = c.review
        rv["chosen_device"] = dev
        rv["log"].append({"time": now(), "by": body.get("by", ""), "action": "chose device to cast", "device": dev})
        _write_json(c.dir / "review.json", rv)
    return {"ok": True}


def act_review(ws: Workspace, name: str, body: dict) -> dict:
    c = Case(ws, name)
    step = body.get("step")
    if step not in c.steps:
        raise ValueError("unknown step for this route")
    who = (body.get("by") or "").strip()[:80]
    states = {s["step"]: s for s in c.step_states()}
    with ws.lock(name):
        rv = c.review
        if body.get("action") == "withdraw":
            # withdrawing a step withdraws every step after it
            for s in c.steps[c.steps.index(step):]:
                rv["steps"].pop(s, None)
            rv["log"].append({"time": now(), "by": who, "action": "withdrew", "step": step})
        else:
            if not who:
                raise ValueError("enter your name or initials on the Case step before approving")
            i = c.steps.index(step)
            if i and states[c.steps[i - 1]]["status"] != "approved":
                raise PermissionError(f"approve {STEP_TITLES[c.steps[i - 1]]} first")
            st = states[step]
            if st["status"] in ("todo",):
                raise PermissionError("nothing to approve yet: build this step first")
            stops = [f for f in st["findings"] if f["level"] == "stop"]
            if stops and not body.get("override"):
                raise PermissionError("a finding marked STOP holds this approval. Read it, and tick 'approve anyway' if you "
                                      "have decided to go on.")
            entry = {"by": who, "time": now(), "fingerprint": c.fingerprint(step),
                     "override": [f["text"] for f in stops] if stops else None,
                     "warnings_seen": [f["text"] for f in st["findings"] if f["level"] == "warn"]}
            rv["steps"][step] = entry
            rv["log"].append({"time": now(), "by": who, "action": "approved" + (" anyway" if stops else ""), "step": step,
                              "overrode": entry["override"]})
        if who:
            rv["reviewer"] = who
        _write_json(c.dir / "review.json", rv)
        _log(c.dir, {"time": now(), "kind": "review", "step": step, "action": body.get("action", "approve"), "by": who})
    return {"ok": True}


def act_reviewer(ws: Workspace, name: str, body: dict) -> dict:
    d = ws.case_dir(name)
    with ws.lock(name):
        _remember(d, {"reviewer": (body.get("by") or "").strip()[:80]})
    return {"ok": True}


def overlay(ws: Workspace, name: str) -> dict:
    """Points and lines drawn over the meshes: landmarks, plate outline, baton tips, airway points."""
    import numpy as np
    c = Case(ws, name)
    r = c.record
    if not r:
        return {}
    lm = r["landmarks"]
    pts = [{"name": "apex", "p": lm.get("apex")}, {"name": "incisive papilla", "p": lm.get("incisive_papilla")},
           {"name": "right tuberosity", "p": lm.get("tuberosity_right")}, {"name": "left tuberosity", "p": lm.get("tuberosity_left")}]
    outline = None
    op = c.dir / "plate_outline.npy"
    if op.exists():
        o = np.load(op)
        outline = np.asarray(o, dtype=float).round(2).tolist()
    aw = (r.get("airway") or {}).get("landmarks", {})
    air = [{"name": k, "p": v["value"], "source": v.get("source")} for k, v in aw.items()
           if v.get("kind") == "point" and isinstance(v.get("value"), list)]
    tips = {d["id"]: d.get("tip") for d in devices(r) if d.get("tip")}
    return {"landmarks": [p for p in pts if p["p"]], "outline": outline, "airway": air, "tips": tips,
            "border_y": (r.get("attachment") or {}).get("posterior_border_y")}


# ------------------------------------------------------------------ HTTP

class Handler(BaseHTTPRequestHandler):
    ws: Workspace = None  # set by serve()
    port: int = 0
    server_version = "PEBPStudio/1"

    def log_message(self, fmt, *args):  # keep the terminal quiet; the case log has the actions
        pass

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        return host in {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}

    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj, default=str).encode(), "application/json")

    def _err(self, code: int, msg: str):
        self._json({"error": msg}, code)

    def do_GET(self):
        if not self._host_ok():
            return self._err(403, "only this computer can use PEBP Studio")
        u = urlparse(self.path)
        parts = [unquote(p) for p in u.path.strip("/").split("/") if p]
        try:
            if not parts:
                return self._static("index.html")
            if parts[0] == "static" and len(parts) == 2:
                return self._static(parts[1])
            if parts[:2] == ["api", "cases"]:
                return self._json({"cases": self.ws.list_cases(), "workspace": str(self.ws.root),
                                   "moldstudio": _moldstudio_available()})
            if parts[:2] == ["api", "vocabulary"]:
                from ..fitting import MODIFICATIONS
                from ..airway import LANDMARKS
                return self._json({"modifications": {k: {"unit": v[0], "usually": v[1], "text": v[2]} for k, v in MODIFICATIONS.items()},
                                   "airway": {k: {"kind": v[0], "text": v[1]} for k, v in LANDMARKS.items()
                                              if k not in ("pns", "baton_root", "baton_tip")}})
            if parts[:2] == ["api", "jobs"] and len(parts) == 3:
                j = self.ws.jobs.get(parts[2])
                return self._json(j) if j else self._err(404, "no such job")
            if parts[0] == "api" and parts[1] == "case" and len(parts) >= 3:
                name = parts[2]
                if len(parts) == 3:
                    return self._json(Case(self.ws, name).summary())
                if parts[3] == "overlay":
                    return self._json(overlay(self.ws, name))
                if parts[3] == "files" and len(parts) > 4:
                    return self._case_file(name, "/".join(parts[4:]), "download" in parse_qs(u.query))
            return self._err(404, "not found")
        except ValueError as e:
            return self._err(400, str(e))

    def do_POST(self):
        if not self._host_ok() or self.headers.get("X-PEBP-Studio") != "1":
            return self._err(403, "only this computer can use PEBP Studio")
        u = urlparse(self.path)
        parts = [unquote(p) for p in u.path.strip("/").split("/") if p]
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_UPLOAD:
            return self._err(413, "file too large")
        raw = self.rfile.read(n) if n else b""
        try:
            if parts == ["api", "cases"]:
                return self._json(act_new_case(self.ws, json.loads(raw or b"{}")))
            if len(parts) == 4 and parts[:2] == ["api", "case"]:
                name, action = parts[2], parts[3]
                if action == "scan":
                    return self._json(act_upload(self.ws, name, raw, unquote(self.headers.get("X-Filename", ""))))
                body = json.loads(raw or b"{}")
                fn = {"synthetic": act_synthetic, "design": act_design, "adjust": act_adjust, "airway": act_airway,
                      "mold": act_mold, "settings": act_settings, "choose": act_choose, "review": act_review,
                      "reviewer": act_reviewer, "route": act_route, "kit": act_kit, "worn": act_worn,
                      "choose_worn": act_choose_worn, "reveal": act_reveal, "cleft": act_cleft,
                      "catalog": act_catalog, "catalog_choice": act_catalog_choice}.get(action)
                if fn:
                    return self._json(fn(self.ws, name, body))
            return self._err(404, "not found")
        except PermissionError as e:
            return self._err(409, str(e))
        except RuntimeError as e:
            return self._err(409, str(e))
        except (ValueError, KeyError, TypeError) as e:
            return self._err(400, str(e))

    def _static(self, fn: str):
        p = (STATIC / fn).resolve()
        if p.parent != STATIC or not p.is_file():
            return self._err(404, "not found")
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        if p.suffix in (".js", ".html", ".css"):
            ctype += "; charset=utf-8"
        extra = {"Content-Security-Policy": "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
                                            "script-src 'self'; frame-src 'self'; connect-src 'self'"} if p.suffix == ".html" else None
        self._send(200, p.read_bytes(), ctype, extra)

    def _case_file(self, name: str, rel: str, download: bool):
        d = self.ws.case_dir(name)
        p = (d / rel).resolve()
        if d not in p.parents or not p.is_file() or p.suffix.lower() not in SERVE_EXT:
            return self._err(404, "not found")
        body = p.read_bytes()
        if p.suffix == ".html":
            # the generated viewers load three.js from a CDN: serve the bundled copy so the studio works offline
            body = body.replace(CDN_THREE.encode(), b"/static/three.min.js")
        ctype = {".stl": "model/stl", ".md": "text/markdown; charset=utf-8", ".3mf": "model/3mf"}.get(
            p.suffix.lower(), mimetypes.guess_type(p.name)[0] or "application/octet-stream")
        extra = {"Content-Disposition": f'attachment; filename="{name}_{p.name}"'} if download else None
        self._send(200, body, ctype, extra)


def _moldstudio_available() -> bool:
    try:
        from ..moldstudio_bridge import MOLDSTUDIO_ROOT
        return (MOLDSTUDIO_ROOT / "moldstudio").is_dir()
    except Exception:
        return False


def make_server(workspace: Path, port: int = 8780) -> ThreadingHTTPServer:
    """The studio's HTTP server, bound to 127.0.0.1 only (port 0 picks a free port)."""
    ws = Workspace(workspace)
    handler = type("StudioHandler", (Handler,), {"ws": ws})
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    handler.port = httpd.server_address[1]
    return httpd


def serve(workspace: Path, port: int = 8780, open_browser: bool = True) -> None:
    httpd = make_server(workspace, port)
    ws = httpd.RequestHandlerClass.ws
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print(f"PEBP Studio on {url}  (workspace {ws.root}; this computer only; Ctrl-C to stop)", flush=True)
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
