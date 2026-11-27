"""Offline viewer for a patient's catalog of modular versions: `pebp catalog CASE --viewer`.

One self-contained HTML file (three.js r128, MIT, inlined from the studio's bundled copy, so the page loads
nothing from the internet). Every base, every baton and the oriented scan are embedded. A clinician narrows the
120 versions with the same five choices as `pebp catalog` / PEBP Studio, sees what each choice does, and
compares any version against the standard baton on the same base or against a second version.

The file is written into the case folder. Built from a real scan it is patient data: it stays on the computer
it was made on.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

import numpy as np
import trimesh

from . import cleft as cleftmod

THREE = Path(__file__).resolve().parent / "studio" / "static" / "three.min.js"

EXPLAIN = {
    "cleft_type": {
        "hard_soft": "Tübingen cleft baton [published]. The cleft gets the cleft block-out, carried back past the plate border; "
                     "the root leaves the plate 2 mm behind the tuberosity line, over the cleft [project heuristic].",
        "soft_only": "Tübingen no-cleft baton [published: Aretxabaleta 2025 use it for soft palate and uvular clefts]. The hard "
                     "palate is intact: every gap is bridged as missing scan data and the root sits on the tuberosity line, "
                     "off the soft palate [project heuristic].",
        "none": "Tübingen no-cleft baton [published], with the same plate as a soft palate cleft [project heuristic].",
    },
    "plate_profile": {
        "uniform": "One thickness everywhere, as Tübingen make it [published].",
        "graded": "Full thickness at the baton root, over the cleft and at the bow-wire tags; thinner elsewhere, never below "
                  "1.5 mm [project heuristic, not bench-tested].",
    },
    "border_relief": {
        "False": "The border as designed.",
        "True": "Flange capped 1 mm above the crest, labial frenulum notched, tuberosities relieved 0.3 mm: the commonest "
                "ulcer sites [project heuristic].",
    },
    "d_length_mm": "Straight tail of the baton longer or shorter than the standard; mostly moves the tip down or up.",
    "d_angle_deg": "Baton swung toward (+) or away from (-) the tongue base; mostly moves the tip forward or back.",
}

HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Device Catalog</title>
<style>
:root { --bg:#f6f7f9; --panel:#fff; --ink:#1c1f24; --muted:#5b6470; --line:#e1e5ea; --accent:#2f6fed; --sel:#eef3ff; --pass:#1f9d55; --warn:#b7791f; --fail:#d33; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --bg:#101317; --panel:#181c22; --ink:#e8ebef; --muted:#9aa3ad; --line:#2a3039; --accent:#6ea0ff; --sel:#1c2638; } }
:root[data-theme="dark"] { --bg:#101317; --panel:#181c22; --ink:#e8ebef; --muted:#9aa3ad; --line:#2a3039; --accent:#6ea0ff; --sel:#1c2638; }
* { box-sizing:border-box; } body { margin:0; background:var(--bg); color:var(--ink); font:14px/1.45 -apple-system,"Segoe UI",Inter,sans-serif; }
#app { display:grid; grid-template-columns:400px 1fr; height:100vh; } #side { background:var(--panel); border-right:1px solid var(--line); padding:14px 16px; overflow-y:auto; overflow-x:hidden; }
#detail td { overflow-wrap:anywhere; vertical-align:top; }
#view { position:relative; } canvas { display:block; width:100%; height:100%; }
h1 { font-size:16px; margin:0 0 2px; } .sub { color:var(--muted); font-size:12px; }
h2 { font-size:12px; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); margin:14px 0 6px; }
.flag { background:#fff4e0; color:#6b4a00; border-radius:6px; padding:6px 8px; font-size:12px; margin:8px 0; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) .flag { background:#3a2c10; color:#f3d79a; } }
.opt { border:1px solid var(--line); border-radius:7px; padding:6px 8px; margin:4px 0; cursor:pointer; }
.opt.on { border-color:var(--accent); background:var(--sel); } .opt.off { opacity:.45; }
.opt b { font-weight:600; } .opt .n { float:right; color:var(--muted); font-size:12px; } .opt .d { color:var(--muted); font-size:12px; }
.row { display:flex; gap:6px; flex-wrap:wrap; } .chip { border:1px solid var(--line); border-radius:14px; padding:2px 9px; cursor:pointer; font-size:13px; background:var(--panel); color:var(--ink); }
.chip.on { border-color:var(--accent); background:var(--sel); } .chip[disabled] { opacity:.35; cursor:default; }
table { width:100%; border-collapse:collapse; font-size:12.5px; } td,th { padding:3px 4px; border-bottom:1px solid var(--line); text-align:left; } th { color:var(--muted); font-weight:500; }
tr.v { cursor:pointer; } tr.v.on { background:var(--sel); } .num { text-align:right; font-variant-numeric:tabular-nums; }
.st.pass { color:var(--pass); } .st.warn { color:var(--warn); } .st.fail { color:var(--fail); }
button { font:inherit; border:1px solid var(--line); background:var(--panel); color:var(--ink); border-radius:6px; padding:3px 9px; cursor:pointer; }
#bar { position:absolute; top:10px; left:10px; display:flex; gap:6px; } #legend { position:absolute; top:10px; right:10px; background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:6px 10px; font-size:12.5px; }
#legend label { display:block; } .sw { display:inline-block; width:10px; height:10px; border-radius:2px; margin:0 5px 0 3px; }
#hint { position:absolute; bottom:10px; left:10px; color:var(--muted); font-size:12px; background:var(--panel); padding:3px 8px; border-radius:6px; border:1px solid var(--line); }
@media (max-width:800px) { #app { grid-template-columns:1fr; grid-template-rows:auto 60vh; height:auto; } }
</style></head>
<body><div id="app"><div id="side">
<h1>Device catalog · <span id="case"></span></h1><div class="sub" id="counts"></div>
<div class="flag" id="flag"></div>
<div id="choices"></div>
<h2 id="lefth"></h2><div id="list"></div>
<h2>Compare</h2>
<div class="row"><label><input type="radio" name="cmp" value="none" checked> nothing</label><label><input type="radio" name="cmp" value="std"> standard baton on the same base</label><label><input type="radio" name="cmp" value="other"> another version</label></div>
<select id="other" style="margin-top:6px;width:100%"></select>
<div id="detail"></div>
</div>
<div id="view"><canvas id="c"></canvas>
<div id="bar"><button data-v="iso">3D</button><button data-v="side">Side</button><button data-v="below">From below</button><button data-v="back">From behind</button></div>
<div id="legend"></div><div id="hint">drag: turn · shift-drag: pan · wheel: zoom · x patient's right, y anterior, z superior · mm</div></div></div>
<script>__THREE__</script>
<script>
'use strict';
const D = __DATA__;
const $ = s => document.querySelector(s);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmt = (v, d = 1) => v === null || v === undefined ? '–' : (+v).toFixed(d);
const KEYS = [['cleft_type', 'Cleft type'], ['plate_profile', 'Plate profile'], ['border_relief', 'Border relief'], ['d_length_mm', 'Baton length step (mm)'], ['d_angle_deg', 'Baton angle step (°)']];
const LBL = { hard_soft: 'Cleft of the hard and soft palate', soft_only: 'Cleft of the soft palate or uvula only', none: 'No cleft', uniform: 'Uniform', graded: 'Graded, low profile', 'true': 'On', 'false': 'Off' };
const F = { cleft_type: null, plate_profile: null, border_relief: null, d_length_mm: null, d_angle_deg: null };
let sel = null;
$('#case').textContent = D.case_name; $('#flag').textContent = D.flag;
$('#counts').textContent = `${D.counts.bases} bases × batons = ${D.counts.versions} versions · ${D.qc.pass} pass, ${D.qc.warn} warn, ${D.qc.fail} fail QC`;
const valsOf = k => k === 'cleft_type' ? Object.keys(D.choices.cleft_type) : D.choices[k].map(String);
function match(v, skip) {
  for (const [k] of KEYS) { if (k === skip || F[k] === null) continue;
    if (k === 'cleft_type') { if (!v.cleft_types.includes(F[k])) return false; } else if (String(v[k]) !== F[k]) return false; }
  return true;
}
const countIf = (k, val) => D.versions.filter(v => match(v, k) && (k === 'cleft_type' ? v.cleft_types.includes(val) : String(v[k]) === val)).length;
function render() {
  let h = '';
  for (const [k, t] of KEYS) {
    h += `<h2>${t} <span style="text-transform:none;letter-spacing:0">· ${esc(D.basis[k])}</span></h2>`;
    if (['cleft_type', 'plate_profile', 'border_relief'].includes(k)) {
      h += valsOf(k).map(val => { const n = countIf(k, val), on = F[k] === val;
        const ex = D.explain[k][val] || D.explain[k][val === 'true' ? 'True' : 'False'] || '';
        return `<div class="opt ${on ? 'on' : ''} ${n ? '' : 'off'}" data-k="${k}" data-val="${esc(val)}"><b>${esc(LBL[val] || val)}</b><span class="n">${n}</span><div class="d">${esc(ex)}</div></div>`; }).join('');
    } else {
      h += `<div class="row">` + valsOf(k).map(val => { const n = countIf(k, val);
        return `<button class="chip ${F[k] === val ? 'on' : ''}" data-k="${k}" data-val="${esc(val)}" ${n ? '' : 'disabled'}>${(+val > 0 ? '+' : '') + val} (${n})</button>`; }).join('') + `</div><div class="sub">${esc(D.explain[k])}</div>`;
    }
  }
  $('#choices').innerHTML = h + `<div style="margin-top:8px"><button id="clear">Clear choices</button></div>`;
  document.querySelectorAll('[data-k]').forEach(e => e.onclick = () => { if (e.disabled) return; const k = e.dataset.k, val = e.dataset.val; F[k] = F[k] === val ? null : val; render(); });
  $('#clear').onclick = () => { for (const k in F) F[k] = null; render(); };
  const left = D.versions.filter(v => match(v));
  if (!sel || !left.find(v => v.id === sel)) sel = (left.find(v => v.standard) || left[0] || {}).id || null;
  $('#lefth').textContent = `${left.length} of ${D.versions.length} versions left`;
  const show = left.slice(0, 60);
  $('#list').innerHTML = `<table><tr><th>Version</th><th>Baton</th><th>Plate</th><th>L / T</th><th class="num">tip Δy</th><th class="num">tip Δz</th><th>QC</th></tr>` +
    show.map(v => `<tr class="v ${v.id === sel ? 'on' : ''}" data-id="${v.id}"><td><b>${v.id}</b></td><td>${v.baton_variant === 'cleft' ? 'cleft' : 'no-cleft'}</td><td>${v.plate_profile}${v.border_relief ? ', relief' : ''}</td><td>${v.code}</td>
      <td class="num">${fmt(v.tip_shift_vs_standard_mm.y_anterior)}</td><td class="num">${fmt(v.tip_shift_vs_standard_mm.z_superior)}</td><td class="st ${v.qc.overall}">${v.qc.overall}</td></tr>`).join('') + `</table>` +
    (left.length > show.length ? `<div class="sub">${left.length - show.length} more: choose more to shorten the list.</div>` : '');
  document.querySelectorAll('tr.v').forEach(r => r.onclick = () => { sel = r.dataset.id; render(); });
  $('#other').innerHTML = D.versions.map(v => `<option value="${v.id}">${v.id} · ${v.baton_variant === 'cleft' ? 'cleft' : 'no-cleft'} ${v.code} · ${v.plate_profile}${v.border_relief ? ', relief' : ''}</option>`).join('');
  if (otherId) $('#other').value = otherId;
  detail(); scene3d();
}
let otherId = null;
document.querySelectorAll('input[name=cmp]').forEach(e => e.onchange = () => scene3d());
$('#other').onchange = e => { otherId = e.target.value; document.querySelector('input[name=cmp][value=other]').checked = true; scene3d(); };
function detail() {
  const v = D.versions.find(x => x.id === sel); if (!v) { $('#detail').innerHTML = ''; return; }
  const b = D.bases[v.base];
  $('#detail').innerHTML = `<h2>${v.id}</h2><table>
    <tr><td>Serves</td><td>${v.cleft_types.map(t => esc(LBL[t])).join('; ')}</td></tr>
    <tr><td>Baton</td><td>Tübingen ${v.baton_variant === 'cleft' ? 'cleft' : 'no-cleft'} extension, ${esc(v.code)} (${v.baton})</td></tr>
    <tr><td>Tip vs the standard</td><td>${fmt(v.tip_shift_vs_standard_mm.y_anterior)} mm forward, ${fmt(v.tip_shift_vs_standard_mm.z_superior)} mm up</td></tr>
    <tr><td>Base</td><td>${esc(v.base)}: ${esc(v.plate_profile)}, relief ${v.border_relief ? 'on' : 'off'}, midline bulge ${fmt(b.posterior_bulge_mm)} mm, ${fmt(b.volume_mm3, 0)} mm³</td></tr>
    <tr><td>Socket</td><td>${b.socket_drop_mm > 0 ? `lowered ${fmt(b.socket_drop_mm)} mm toward the tongue to keep plate over the tunnel; the baton sits that much lower than a one-piece plate` : 'at the plate level'}</td></tr>
    <tr><td>Bridging</td><td>${esc(b.bridging)}</td></tr>
    ${(b.bridging_warnings || []).map(w => `<tr><td>Check</td><td>${esc(w)}</td></tr>`).join('')}
    ${v.qc.checks.map(c => `<tr><td>${esc(c.check)}</td><td class="st ${c.status}">${c.status} · ${esc(c.value)}</td></tr>`).join('')}
    <tr><td>Make it</td><td><code>pebp catalog ${esc(D.case_name)} --make ${v.id}</code>: cast base and its tooling, the stock final baton with its wire, and a one-piece fallback mold, with a PLAN.md</td></tr>
    <tr><td>Files</td><td><code>${esc(v.base_file)}</code><br><code>${esc(v.baton_file)}</code></td></tr></table>`;
}
// ---------------------------------------------------------------- 3D
const canvas = $('#c'), renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
const scene = new THREE.Scene(), dark = matchMedia('(prefers-color-scheme: dark)');
const setBg = () => scene.background = new THREE.Color(dark.matches ? 0x111418 : 0xf4f5f7); setBg(); dark.addEventListener('change', setBg);
const cam = new THREE.PerspectiveCamera(32, 1, 1, 3000);
scene.add(new THREE.HemisphereLight(0xffffff, 0x667788, 0.85));
const k1 = new THREE.DirectionalLight(0xffffff, 0.75); k1.position.set(60, -80, 120); scene.add(k1);
const k2 = new THREE.DirectionalLight(0xffffff, 0.35); k2.position.set(-80, 60, -60); scene.add(k2);
const root = new THREE.Group(); scene.add(root);
const target = new THREE.Vector3(0, 0, -4); let th = -1.05, ph = 1.2, dist = 140;
const VIEWS = { iso: [-1.05, 1.2], side: [0, Math.PI / 2], below: [Math.PI / 2, Math.PI - 0.01], back: [-Math.PI / 2, Math.PI / 2] };
const upd = () => { cam.position.set(target.x + dist * Math.sin(ph) * Math.cos(th), target.y + dist * Math.sin(ph) * Math.sin(th), target.z + dist * Math.cos(ph)); cam.up.set(0, 0, 1); cam.lookAt(target); };
document.querySelectorAll('[data-v]').forEach(b => b.onclick = () => { [th, ph] = VIEWS[b.dataset.v]; upd(); });
let drag = null;
canvas.onpointerdown = e => { drag = { x: e.clientX, y: e.clientY, pan: e.shiftKey || e.button === 2 }; canvas.setPointerCapture(e.pointerId); };
canvas.onpointermove = e => { if (!drag) return; const dx = e.clientX - drag.x, dy = e.clientY - drag.y; drag.x = e.clientX; drag.y = e.clientY;
  if (drag.pan) { const f = new THREE.Vector3(); cam.getWorldDirection(f); const r = new THREE.Vector3().crossVectors(f, cam.up).normalize(), u = new THREE.Vector3().crossVectors(r, f).normalize();
    target.addScaledVector(r, -dx * dist * 0.0015); target.addScaledVector(u, dy * dist * 0.0015); } else { th -= dx * 0.008; ph = Math.min(Math.PI - 0.02, Math.max(0.02, ph - dy * 0.008)); } upd(); };
canvas.onpointerup = () => drag = null; canvas.oncontextmenu = e => e.preventDefault();
canvas.addEventListener('wheel', e => { e.preventDefault(); dist = Math.min(600, Math.max(25, dist * (1 + e.deltaY * 0.001))); upd(); }, { passive: false });
const resize = () => { const w = canvas.clientWidth, h = canvas.clientHeight; if (!w || !h) return; renderer.setSize(w, h, false); cam.aspect = w / h; cam.updateProjectionMatrix(); };
new ResizeObserver(resize).observe(canvas); upd(); (function loop() { requestAnimationFrame(loop); renderer.render(scene, cam); })();
const b64 = (s, T) => { const bin = atob(s), u = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i); return new T(u.buffer); };
const geo = {};
function g(key) { if (!geo[key]) { const m = D.meshes[key], G = new THREE.BufferGeometry();
  G.setAttribute('position', new THREE.BufferAttribute(b64(m.v, Float32Array), 3)); G.setIndex(new THREE.BufferAttribute(b64(m.f, Uint32Array), 1)); G.computeVertexNormals(); geo[key] = G; } return geo[key]; }
const vis = {};
function scene3d() {
  root.clear(); const v = D.versions.find(x => x.id === sel); const L = [];
  L.push(['scan', 'Scan (design frame)', '#a7b0ba', 0.4]);
  if (v) { L.push([v.base_file, `${v.base} base`, '#d9b13b', 1]); L.push([v.baton_file, `${v.id} baton`, v.baton_variant === 'cleft' ? '#2b8fd6' : '#e07b2a', 1]);
    const mode = document.querySelector('input[name=cmp]:checked').value;
    const o = mode === 'std' ? D.versions.find(x => x.base === v.base && x.standard) : mode === 'other' ? D.versions.find(x => x.id === ($('#other').value || otherId)) : null;
    if (o && o.id !== v.id) { if (o.base !== v.base) L.push([o.base_file, `${o.base} base (compare)`, '#9b8cff', 0.45]); L.push([o.baton_file, `${o.id} baton (compare)`, '#9b8cff', 0.45]); } }
  const lg = $('#legend'); lg.innerHTML = '';
  for (const [key, name, col, op] of L) {
    const m = new THREE.Mesh(g(key), new THREE.MeshStandardMaterial({ color: col, transparent: op < 1, opacity: op, depthWrite: op >= 1, roughness: .55, side: THREE.DoubleSide }));
    m.visible = vis[name] ?? true; m.renderOrder = op < 1 ? 2 : 1; root.add(m);
    const lab = document.createElement('label'); lab.innerHTML = `<input type="checkbox" ${m.visible ? 'checked' : ''}><span class="sw" style="background:${col}"></span>${esc(name)}`;
    lab.querySelector('input').onchange = e => { m.visible = vis[name] = e.target.checked; }; lg.appendChild(lab);
  }
}
render();
</script></body></html>
"""


def _pack(m: trimesh.Trimesh) -> dict:
    return {"v": base64.b64encode(np.asarray(m.vertices, dtype=np.float32).tobytes()).decode(),
            "f": base64.b64encode(np.asarray(m.faces, dtype=np.uint32).tobytes()).decode()}


def write(case_dir: Path, cat: dict) -> Path:
    case_dir = Path(case_dir)
    meshes = {}
    scan = case_dir / "scan_oriented.stl"
    if scan.exists():
        meshes["scan"] = _pack(trimesh.load(scan, force="mesh"))
    for v in cat["versions"]:
        for f in (v["base_file"], v["baton_file"]):
            if f not in meshes:
                meshes[f] = _pack(trimesh.load(case_dir / f, force="mesh"))
    qc = {k: sum(1 for v in cat["versions"] if v["qc"]["overall"] == k) for k in ("pass", "warn", "fail")}
    slim = [{k: v[k] for k in ("id", "base", "baton", "cleft_types", "baton_variant", "plate_profile", "border_relief", "d_length_mm",
                               "d_angle_deg", "code", "standard", "base_file", "baton_file", "tip_shift_vs_standard_mm", "qc")}
            for v in cat["versions"]]
    data = {"case_name": case_dir.name, "flag": cat["flag"], "counts": cat["counts"], "qc": qc, "versions": slim,
            "bases": {b["id"]: {**{k: b[k] for k in ("posterior_bulge_mm", "volume_mm3", "bridging", "bridging_warnings")},
                                "socket_drop_mm": b["attachment"].get("socket_drop_mm", 0.0)} for b in cat["bases"]},
            "choices": {**cat["choices"], "border_relief": ["true" if x else "false" for x in cat["choices"]["border_relief"]]},
            "basis": cat["choice_basis"], "explain": EXPLAIN, "meshes": meshes}
    html = HTML.replace("__THREE__", THREE.read_text(encoding="utf-8")).replace("__DATA__", json.dumps(data))
    out = case_dir / "catalog" / "catalog_viewer.html"
    out.write_text(html, encoding="utf-8")
    return out
