"""Checks of PEBP Studio's local server on synthetic data only.

    .venv/bin/python scripts/studio_check.py           (about 30 s; --mold adds the mold build, about a minute more)
    .venv/bin/python scripts/studio_check.py --exe "dist/PEBP Studio/pebp-studio"   (the same, against the download)

Starts the server on a free port of 127.0.0.1, walks a synthetic case through the steps over HTTP the way
the browser does, and asserts the rules the UI is there to keep: the server answers only this computer
and only its own page, approvals need a name and go in order, a STOP finding holds an approval until
"approve anyway" is ticked and the override is recorded, a change under an approval marks it changed,
the mold cannot be built before Checks is approved, rebuilding a record with changed devices needs a
confirmation and archives the old record, and a clinical choice records who made it.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pebp.studio.server import make_server  # noqa: E402

results = []


def check(name, ok, detail=""):
    ok = bool(ok)
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f": {detail}" if detail else ""))


class Client:
    def __init__(self, port):
        self.base = f"http://127.0.0.1:{port}"

    def req(self, path, body=None, headers=None, raw=None):
        h = {"X-PEBP-Studio": "1", **(headers or {})}
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        r = urllib.request.Request(self.base + path, data=data, headers=h, method="POST" if data is not None else "GET")
        try:
            with urllib.request.urlopen(r) as f:
                return f.status, json.loads(f.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def wait(self, case, timeout=600):
        t0 = time.time()
        while time.time() - t0 < timeout:
            _, c = self.req(f"/api/case/{case}")
            if not c.get("running"):
                return c
            time.sleep(0.5)
        raise SystemExit("job did not finish")

    def steps(self, case):
        return {s["step"]: s for s in self.req(f"/api/case/{case}")[1]["steps"]}


def kit_route(c: "Client", ws: Path) -> None:
    """The two-visit chairside route (docs/KIT_CLI.md), on a fresh synthetic case."""
    case = "kit01"
    c.req("/api/cases", {"name": case})
    c.req(f"/api/case/{case}/reviewer", {"by": "T"})
    c.req(f"/api/case/{case}/synthetic", {"preset": "prs", "asym": 1.0})
    c.wait(case)
    c.req(f"/api/case/{case}/cleft", {"value": "hard_soft", "by": "T"})
    c.req(f"/api/case/{case}/review", {"step": "case", "by": "T"})
    c.req(f"/api/case/{case}/design", {})
    c.wait(case)
    for st in ("landmarks", "plate"):
        c.req(f"/api/case/{case}/review", {"step": st, "by": "T"})
    c.req(f"/api/case/{case}/route", {"route": "kit", "by": "T"})
    s = c.req(f"/api/case/{case}")[1]
    check("kit route swaps the steps", [x["step"] for x in s["steps"]] == ["case", "landmarks", "plate", "kit", "visit2"]
          and s["steps"][2]["status"] == "approved", str([x["step"] for x in s["steps"]]))
    code, _ = c.req(f"/api/case/{case}/mold", {})
    check("cast-plate mold refused on the kit route", code in (400, 409), str(code))
    code, _ = c.req(f"/api/case/{case}/worn", {"kit": "K5"})
    check("worn plate refused before Visit 1 is approved", code == 409, str(code))
    c.req(f"/api/case/{case}/kit", {"final_thickness": 2.5})
    s = c.wait(case)
    k = (s["record"] or {}).get("kit") or {}
    check("Visit 1 builds K1-K15 and the final base", len(k.get("batons", [])) == 15 and s["record"].get("chairside_base")
          and s["last_job"]["status"] == "done", s["last_job"]["stderr"][-200:])
    st = {x["step"]: x for x in s["steps"]}
    check("Visit 1 shows the route's stored flag", any(f["level"] == "warn" and k["flag"] in f["text"] for f in st["kit"]["findings"]))
    code, j = c.req(f"/api/case/{case}/review", {"step": "kit", "by": "T", "override": st["kit"]["stops"] > 0})
    check("Visit 1 approved", code == 200, j.get("error", ""))
    # every modular version for this patient, narrowed by the clinician's choices
    c.req(f"/api/case/{case}/catalog", {})
    s = c.wait(case)
    cat = s.get("catalog") or {}
    check("catalog builds 8 bases, 30 batons and 120 versions, none failing", cat.get("counts") == {"bases": 8, "batons": 30, "versions": 120}
          and not cat.get("stale") and all(v["qc"]["overall"] != "fail" for v in cat.get("versions", [])),
          f"{cat.get('counts')} {s['last_job']['stderr'][-200:]}")
    from pebp.catalog import narrow
    n = narrow(cat, cleft_type="hard_soft", plate_profile="uniform", border_relief="off", d_length_mm=2, d_angle_deg=0)
    check("narrowing by cleft type, plate and baton leaves one version, with the cleft baton",
          n["count"] == 1 and n["versions"][0]["id"] == "C-U-K8" and n["versions"][0]["baton_variant"] == "cleft",
          str([v["id"] for v in n["versions"]]))
    n2 = narrow(cat, cleft_type="soft_only")
    check("a soft palate cleft narrows to the no-cleft group (60 versions)",
          n2["count"] == 60 and {v["baton_variant"] for v in n2["versions"]} == {"nocleft"}, str(n2["count"]))
    code, j = c.req(f"/api/case/{case}/catalog_choice", {"by": "T", "filters": n["chosen"], "version": "C-U-K8"})
    s = c.req(f"/api/case/{case}")[1]
    check("the narrowing and the version are recorded with who chose them",
          code == 200 and (s.get("catalog_choice") or {}).get("version") == "C-U-K8" and s["catalog_choice"]["by"] == "T", str(j))
    code, _ = c.req(f"/api/case/{case}/catalog_choice", {"by": "T", "version": "X-9"})
    check("an unknown catalog version is refused", code == 400, str(code))
    code, _ = c.req(f"/api/case/{case}/worn", {"kit": "K99"})
    check("unknown kit baton refused", code == 400, str(code))
    c.req(f"/api/case/{case}/worn", {"kit": "K8"})
    s = c.wait(case)
    w = s["worn"]
    check("Visit 2 builds S1 from K8 with its chairside steps", w and w["id"] == "S1" and w["from"] == "K8" and w.get("steps"),
          s["last_job"]["stderr"][-200:])
    st = {x["step"]: x for x in s["steps"]}
    code, j = c.req(f"/api/case/{case}/review", {"step": "visit2", "by": "T", "override": st["visit2"]["stops"] > 0})
    check("Visit 2 approved", code == 200, j.get("error", ""))
    c.req(f"/api/case/{case}/kit", {"final_thickness": 2.8})      # a thicker base: a different base to cast
    c.wait(case)
    st = c.steps(case)
    check("a new final base expires Visit 1 and holds Visit 2", st["kit"]["status"] == "changed" and st["visit2"]["status"] == "locked"
          and any("earlier final base" in f["text"] for f in st["visit2"]["findings"]),
          f"{st['kit']['status']}, {st['visit2']['status']}")
    code, _ = c.req(f"/api/case/{case}/design", {})
    c.req(f"/api/case/{case}/design", {"confirm_reset": True})
    c.wait(case)
    check("a design rebuild archives kit/ and catalog/", code == 409 and not (ws / case / "kit").exists() and list((ws / case / "history").glob("kit_*"))
          and not (ws / case / "catalog").exists() and list((ws / case / "history").glob("catalog_*")))


def main():
    do_mold = "--mold" in sys.argv
    ws = Path(tempfile.mkdtemp(prefix="pebp_studio_"))
    exe = sys.argv[sys.argv.index("--exe") + 1] if "--exe" in sys.argv else None
    if exe:
        # the downloadable app (packaging/build.py): the same checks against its own server and build steps
        import socket
        import subprocess
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        proc = subprocess.Popen([exe, "studio", "--workspace", str(ws), "--port", str(port), "--no-browser"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        httpd = type("Frozen", (), {"server_address": ("127.0.0.1", port),
                                    "shutdown": lambda self: (proc.terminate(), proc.wait(10))})()
        for _ in range(60):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2)
                break
            except OSError:
                time.sleep(1)
    else:
        httpd = make_server(ws, 0)
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
    c = Client(port)
    try:
        # the server answers only this computer, and only requests its own page can make
        code, _ = c.req("/api/cases", headers={"Host": "evil.example:80"})
        check("foreign Host header refused (DNS rebinding)", code == 403, str(code))
        r = urllib.request.Request(c.base + "/api/cases", data=b'{"name":"x"}', method="POST")
        try:
            urllib.request.urlopen(r); code = 200
        except urllib.error.HTTPError as e:
            code = e.code
        check("POST without the studio header refused", code == 403, str(code))
        check("server bound to 127.0.0.1 only", httpd.server_address[0] == "127.0.0.1", httpd.server_address[0])
        code, _ = c.req("/api/cases", {"name": "../escape"})
        check("case name outside the workspace refused", code == 400, str(code))

        case = "synth01"
        c.req("/api/cases", {"name": case})
        c.req(f"/api/case/{case}/synthetic", {"preset": "prs", "asym": 1.0})
        c.wait(case)
        code, _ = c.req(f"/api/case/{case}/files/../../etc/passwd")
        check("file outside the case folder refused", code == 404, str(code))
        code, _ = c.req(f"/api/case/{case}/reveal", {"path": "../../etc/passwd"})
        check("Show case folder refuses paths outside the case", code == 400, str(code))
        # a binary STL whose triangle-count field is 0 (some scanner exporters write that) still loads
        raw = (ws / case / "scan.stl").read_bytes()
        zeroed = raw[:80] + (0).to_bytes(4, "little") + raw[84:]
        c.req("/api/cases", {"name": "zerocount"})
        code, j = c.req("/api/case/zerocount/scan", raw=zeroed, headers={"X-Filename": "scan.stl"})
        n = (len(raw) - 84) // 50
        code2, s0 = c.req("/api/case/zerocount")
        check("binary STL with a zero triangle count is repaired on upload",
              code == 200 and j.get("triangles") == n and j.get("repair")
              and any(f["level"] == "warn" for f in s0["steps"][0]["findings"]), f"{code} {j}")
        # a blank file of the right length (all zero bytes, e.g. a failed download) is refused
        c.req("/api/cases", {"name": "blank"})
        code, j = c.req("/api/case/blank/scan", raw=bytes(len(raw)), headers={"X-Filename": "scan.stl"})
        check("blank STL (all zeros) refused at upload", code == 400 and "no scan shape" in j.get("error", ""), f"{code} {j.get('error', '')[:60]}")
        code, j = c.req(f"/api/case/{case}/review", {"step": "case"})
        check("approval needs a name", code == 400 and "name" in j.get("error", ""), j.get("error", ""))
        code, j = c.req(f"/api/case/{case}/review", {"step": "landmarks", "by": "T"})
        check("approvals go in order", code == 409, j.get("error", ""))
        c.req(f"/api/case/{case}/reviewer", {"by": "T"})
        code, j = c.req(f"/api/case/{case}/review", {"step": "case", "by": "T"})
        check("Case step held until the clinician enters the cleft type", code == 409, j.get("error", "")[:60])
        code, j = c.req(f"/api/case/{case}/cleft", {"value": "yes", "by": "T"})
        check("the old yes/no answer is refused: the cleft type has three answers", code == 400, j.get("error", "")[:60])
        code, _ = c.req(f"/api/case/{case}/cleft", {"value": "none", "by": "T"})
        c.req(f"/api/case/{case}/review", {"step": "case", "by": "T"})
        c.req(f"/api/case/{case}/design", {"plate_profile": "uniform"})
        s = c.wait(case)
        lmf = c.steps(case)["landmarks"]["findings"]
        cs = s["record"].get("cleft_status") or {}
        check("baton and bridging follow the entered cleft type, not the detector; the gap it read as a cleft is flagged",
              s["record"]["extension_mode"] == "tuebingen-nocleft" and cs.get("value") == "none" and cs.get("entered_by") == "T"
              and cs.get("posterior_bulge_mm") == 0.0 and (cs.get("reclassified_gap") or {}).get("area_mm2", 0) > 0
              and any(f["level"] == "warn" and "'no cleft' was entered" in f["text"] for f in lmf),
              f"{s['record'].get('extension_mode')} {cs.get('value')} bulge {cs.get('posterior_bulge_mm')}")
        c.req(f"/api/case/{case}/cleft", {"value": "soft_only", "by": "T"})
        lmf = c.steps(case)["landmarks"]["findings"]
        check("changing the cleft type after the build holds the Landmarks step until it is detected again",
              any(f["level"] == "stop" and "changed to 'soft_only'" in f["text"] for f in lmf), "")
        c.req(f"/api/case/{case}/cleft", {"value": "hard_soft", "by": "T"})
        c.req(f"/api/case/{case}/review", {"step": "case", "by": "T"})
        c.req(f"/api/case/{case}/design", {"plate_profile": "uniform"})
        s = c.wait(case)
        check("design builds P1-P3 through `pebp design`", [d["id"] for d in s["devices"]] == ["P1", "P2", "P3"]
              and s["last_job"]["commands"][0].startswith("pebp design"), str([d["id"] for d in s["devices"]]))
        for st in ("landmarks", "plate"):
            c.req(f"/api/case/{case}/review", {"step": st, "by": "T"})
        st = c.steps(case)
        check("landmarks and plate approved", st["landmarks"]["status"] == st["plate"]["status"] == "approved")

        code, j = c.req(f"/api/case/{case}/settings", {"tip_target": "in_vallecula", "by": ""})
        check("clinical choice needs a name", code == 400, j.get("error", ""))
        c.req(f"/api/case/{case}/settings", {"tip_target": "in_vallecula", "by": "T"})
        rec = json.loads((ws / case / "design.json").read_text(encoding="utf-8"))
        rv = json.loads((ws / case / "review.json").read_text(encoding="utf-8"))
        check("tip target stored in the record with who set it", rec["clinical_settings"]["tip_target"] == "in_vallecula"
              and any(e.get("tip_target") == "in_vallecula" and e["by"] == "T" for e in rv["log"]))

        # an over-narrowed device fails QC; choosing it puts a STOP on the step
        c.req(f"/api/case/{case}/adjust", {"from": "P2", "mods": [{"code": "narrow_mm", "amount": 6}], "where": "lab"})
        s = c.wait(case)
        a1 = next(d for d in s["devices"] if d["id"] == "A1")
        check("change builds A1 through `pebp adjust`", a1["qc"]["overall"] == "fail" and a1["from"] == "P2", a1["qc"]["overall"])
        c.req(f"/api/case/{case}/choose", {"device": "A1", "by": "T"})
        code, j = c.req(f"/api/case/{case}/review", {"step": "changes", "by": "T"})
        check("STOP finding holds the approval", code == 409 and "STOP" in j.get("error", ""), j.get("error", ""))
        code, _ = c.req(f"/api/case/{case}/review", {"step": "changes", "by": "T", "override": True})
        rv = json.loads((ws / case / "review.json").read_text(encoding="utf-8"))
        check("approve anyway is recorded with the findings it overrode",
              code == 200 and rv["steps"]["changes"]["override"] and "A1" in rv["steps"]["changes"]["override"][0])
        c.req(f"/api/case/{case}/review", {"step": "changes", "action": "withdraw", "by": "T"})

        c.req(f"/api/case/{case}/adjust", {"from": "P2", "mods": [{"code": "shorten_mm", "amount": 1.5}], "where": "lab"})
        c.wait(case)
        c.req(f"/api/case/{case}/choose", {"device": "A2", "by": "T"})
        code, _ = c.req(f"/api/case/{case}/review", {"step": "changes", "by": "T"})
        check("passing device approves without override", code == 200, str(code))
        code, j = c.req(f"/api/case/{case}/mold", {})
        check("mold refused before Checks is approved", code == 409, j.get("error", ""))
        c.req(f"/api/case/{case}/review", {"step": "checks", "by": "T"})
        check("checks approved", c.steps(case)["checks"]["status"] == "approved")

        # a change under an approval marks it changed and holds what follows
        c.req(f"/api/case/{case}/settings", {"tip_target": "above_epiglottis_rim", "by": "T"})
        st = c.steps(case)
        check("a change under an approval marks it changed", st["changes"]["status"] == "changed" and st["checks"]["status"] == "locked",
              f"{st['changes']['status']}, {st['checks']['status']}")
        c.req(f"/api/case/{case}/review", {"step": "changes", "by": "T"})
        c.req(f"/api/case/{case}/review", {"step": "checks", "by": "T"})

        if do_mold:
            c.req(f"/api/case/{case}/mold", {"final_thickness": 2.5, "wall": 6})
            s = c.wait(case)
            check("mold built through `pebp mold` for the chosen device", s["mold"] is not None and s["mold"]["device"] == "A2"
                  and s["mold"]["validation"]["ok"], s["last_job"]["stderr"][-300:] if not s["mold"] else s["mold"]["device"])
            st = c.steps(case)
            check("casting does not disturb earlier approvals", st["checks"]["status"] == "approved", st["checks"]["status"])

        code, j = c.req(f"/api/case/{case}/design", {"plate_profile": "graded", "thin_thickness": 1.5})
        check("rebuild with changed devices needs confirmation", code == 409, j.get("error", "")[:60])
        c.req(f"/api/case/{case}/design", {"plate_profile": "graded", "thin_thickness": 1.5, "confirm_reset": True})
        s = c.wait(case)
        hist = list((ws / case / "history").glob("design_*.json"))
        check("rebuild archives the old record (2: the cleft re-entry rebuilt once) and keeps the clinical settings",
              len(hist) == 2 and not s["record"]["modification_log"] and s["record"]["clinical_settings"]["tip_target"] == "above_epiglottis_rim"
              and "plate_profile" in s["record"].get("design_choices", {}) and c.steps(case)["plate"]["status"] == "changed",
              f"{len(hist)} archived; plate step {c.steps(case)['plate']['status']}")
        from pebp.studio.server import kit_available
        if kit_available():
            kit_route(c, ws)
        else:
            print("skip  two-visit chairside route: this checkout has no `pebp kit` / `pebp chairside`")
    finally:
        httpd.shutdown()
        shutil.rmtree(ws, ignore_errors=True)
    print(f"{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
