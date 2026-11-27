"""Self-contained HTML viewer: embeds the case STLs and the design record."""
from __future__ import annotations

import base64
import json
from pathlib import Path

LAYERS = [
    ("scan_oriented.stl", "Scan (oriented)", "#9aa3ad", 0.55, True),
    ("scan_trimmed.stl", "Scan (trimmed to arch)", "#7c8a99", 0.7, False),
    ("base_plate.stl", "Base plate", "#e0b84a", 1.0, False),
    ("prototype_1.stl", "Prototype 1 (2 mm posterior)", "#4f9cf0", 1.0, False),
    ("prototype_2.stl", "Prototype 2 (centred)", "#36b37e", 1.0, True),
    ("prototype_3.stl", "Prototype 3 (2 mm anterior)", "#f06a6a", 1.0, False),
    ("final_design.stl", "Final design (wire groove)", "#b084f0", 1.0, False),
    ("carried_design.stl", "Carried design T (previous plate's baton)", "#2bb3c0", 1.0, True),
]

HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PEBP Designer</title>
<style>
:root { --bg:#f6f7f9; --panel:#ffffff; --ink:#1c1f24; --muted:#5b6470; --line:#e1e5ea; --accent:#2f6fed; --pass:#1f9d55; --warn:#c7841a; --fail:#d33; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --bg:#101317; --panel:#181c22; --ink:#e8ebef; --muted:#9aa3ad; --line:#2a3039; --accent:#6ea0ff; } }
:root[data-theme="dark"] { --bg:#101317; --panel:#181c22; --ink:#e8ebef; --muted:#9aa3ad; --line:#2a3039; --accent:#6ea0ff; }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--ink); font: 14px/1.45 -apple-system, "Segoe UI", Inter, sans-serif; }
#app { display:grid; grid-template-columns: 300px 1fr; height:100vh; }
#side { background:var(--panel); border-right:1px solid var(--line); padding:16px; overflow:auto; }
#view { position:relative; }
canvas { display:block; width:100%; height:100%; }
h1 { font-size:16px; margin:0 0 4px; }
.sub { color:var(--muted); font-size:12px; margin-bottom:14px; }
h2 { font-size:12px; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); margin:16px 0 6px; }
label.layer { display:flex; align-items:center; gap:8px; padding:4px 0; cursor:pointer; }
.sw { width:12px; height:12px; border-radius:3px; flex:none; }
table { width:100%; border-collapse:collapse; font-size:12px; }
td { padding:3px 0; border-bottom:1px solid var(--line); vertical-align:top; }
td:last-child { text-align:right; color:var(--muted); }
.pass { color:var(--pass); } .warn { color:var(--warn); } .fail { color:var(--fail); }
.btns { display:flex; gap:6px; flex-wrap:wrap; margin:8px 0; }
button { font:inherit; font-size:12px; padding:4px 8px; border:1px solid var(--line); background:var(--bg); color:var(--ink); border-radius:6px; cursor:pointer; }
button:hover { border-color:var(--accent); }
#hud { position:absolute; left:12px; bottom:10px; font-size:11px; color:var(--muted); background:var(--panel); padding:4px 8px; border-radius:6px; border:1px solid var(--line); }
@media (max-width: 760px) { #app { grid-template-columns: 1fr; grid-template-rows: auto 1fr; } #side { max-height:45vh; border-right:none; border-bottom:1px solid var(--line); } }
</style>
</head>
<body>
<div id="app">
  <div id="side">
    <h1>PEBP Designer</h1>
    <div class="sub">Scan to pre-epiglottic baton plate: base plate, three prototypes 2 mm apart, design record.</div>
    <div class="btns">
      <button data-view="front">Front</button><button data-view="side">Side</button><button data-view="below">From below</button><button data-view="iso">3D</button>
      <button id="wire">Wireframe</button>
    </div>
    <h2>Layers</h2>
    <div id="layers"></div>
    <h2>Landmarks</h2>
    <table id="lm"></table>
    <h2>Extension</h2>
    <table id="ext"></table>
    <h2>Quality checks (prototype 2)</h2>
    <table id="qc"></table>
    <div id="modlog"></div>
  </div>
  <div id="view"><canvas id="c"></canvas><div id="hud">drag: rotate · shift-drag: pan · wheel: zoom · grid 10 mm</div></div>
</div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
<script>
const DATA = __DATA__;
const RECORD = __RECORD__;

function parseSTL(b64) {
  const bin = atob(b64); const buf = new ArrayBuffer(bin.length); const u8 = new Uint8Array(buf);
  for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
  const dv = new DataView(buf); const n = dv.getUint32(80, true);
  const pos = new Float32Array(n * 9); const nor = new Float32Array(n * 9);
  let off = 84;
  for (let i = 0; i < n; i++) {
    const nx = dv.getFloat32(off, true), ny = dv.getFloat32(off + 4, true), nz = dv.getFloat32(off + 8, true); off += 12;
    for (let k = 0; k < 3; k++) {
      pos[i * 9 + k * 3] = dv.getFloat32(off, true); pos[i * 9 + k * 3 + 1] = dv.getFloat32(off + 4, true); pos[i * 9 + k * 3 + 2] = dv.getFloat32(off + 8, true); off += 12;
      nor[i * 9 + k * 3] = nx; nor[i * 9 + k * 3 + 1] = ny; nor[i * 9 + k * 3 + 2] = nz;
    }
    off += 2;
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  g.computeVertexNormals();
  return g;
}

const canvas = document.getElementById('c');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
const scene = new THREE.Scene();
const dark = matchMedia('(prefers-color-scheme: dark)').matches;
scene.background = new THREE.Color(dark ? 0x101317 : 0xf6f7f9);
const camera = new THREE.PerspectiveCamera(35, 1, 1, 2000);
scene.add(new THREE.HemisphereLight(0xffffff, 0x667788, 0.9));
const key = new THREE.DirectionalLight(0xffffff, 0.8); key.position.set(60, -80, 120); scene.add(key);
const fill = new THREE.DirectionalLight(0xffffff, 0.35); fill.position.set(-80, 60, -40); scene.add(fill);
// anatomical frame: x patient-right, y anterior, z superior. three.js: keep as-is, camera looks along -y toward the palate.
const grid = new THREE.GridHelper(120, 12, 0x888888, 0x555555); grid.rotation.x = Math.PI / 2; grid.material.opacity = 0.25; grid.material.transparent = true; scene.add(grid);
const axes = new THREE.AxesHelper(15); scene.add(axes);

const meshes = {};
const layersDiv = document.getElementById('layers');
let wireframe = false;
for (const L of DATA.layers) {
  const g = parseSTL(L.b64);
  const m = new THREE.Mesh(g, new THREE.MeshStandardMaterial({ color: L.color, opacity: L.opacity, transparent: L.opacity < 1, roughness: 0.55, metalness: 0.05, side: THREE.DoubleSide }));
  m.visible = L.on; scene.add(m); meshes[L.file] = m;
  const lab = document.createElement('label'); lab.className = 'layer';
  lab.innerHTML = `<input type="checkbox" ${L.on ? 'checked' : ''}><span class="sw" style="background:${L.color}"></span><span>${L.name}</span>`;
  lab.querySelector('input').onchange = e => { m.visible = e.target.checked; };
  layersDiv.appendChild(lab);
}
document.getElementById('wire').onclick = () => { wireframe = !wireframe; for (const k in meshes) meshes[k].material.wireframe = wireframe; };

// orbit
const target = new THREE.Vector3(0, 10, 0);
let theta = -0.9, phi = 1.15, dist = 140;
function updateCam() {
  camera.position.set(target.x + dist * Math.sin(phi) * Math.cos(theta), target.y + dist * Math.sin(phi) * Math.sin(theta), target.z + dist * Math.cos(phi));
  camera.up.set(0, 0, 1); camera.lookAt(target);
}
const views = { front: [Math.PI / 2, Math.PI / 2], side: [0, Math.PI / 2], below: [Math.PI / 2, Math.PI - 0.01], iso: [-0.9, 1.15] };
document.querySelectorAll('[data-view]').forEach(b => b.onclick = () => { [theta, phi] = views[b.dataset.view]; updateCam(); });
let drag = null;
canvas.addEventListener('pointerdown', e => { drag = { x: e.clientX, y: e.clientY, pan: e.shiftKey }; canvas.setPointerCapture(e.pointerId); });
canvas.addEventListener('pointermove', e => {
  if (!drag) return; const dx = e.clientX - drag.x, dy = e.clientY - drag.y; drag.x = e.clientX; drag.y = e.clientY;
  if (drag.pan) { const r = new THREE.Vector3(); camera.getWorldDirection(r); const right = new THREE.Vector3().crossVectors(r, camera.up).normalize(); const up = new THREE.Vector3().crossVectors(right, r).normalize(); target.addScaledVector(right, -dx * dist * 0.0015); target.addScaledVector(up, dy * dist * 0.0015); }
  else { theta -= dx * 0.008; phi = Math.min(Math.PI - 0.02, Math.max(0.02, phi - dy * 0.008)); }
  updateCam();
});
canvas.addEventListener('pointerup', () => drag = null);
canvas.addEventListener('wheel', e => { e.preventDefault(); dist = Math.min(600, Math.max(30, dist * (1 + e.deltaY * 0.001))); updateCam(); }, { passive: false });

function resize() { const w = canvas.clientWidth, h = canvas.clientHeight; renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix(); }
window.addEventListener('resize', resize); resize(); updateCam();
(function loop() { requestAnimationFrame(loop); renderer.render(scene, camera); })();

// side panel
const lm = RECORD.landmarks || {};
const rows = [["Arch depth", lm.arch_depth_mm + " mm"], ["Intertuberosity width", lm.intertuberosity_width_mm + " mm"],
  ["Cleft", lm.cleft_present ? "yes" : "no"], ["Cleft margin level", lm.cleft_margin_z_mm + " mm"],
  ["Cleft floor (block-out)", (RECORD.plate && RECORD.plate.cleft_floor_z) + " mm"],
  ["Max cleft width", lm.cleft_width_profile && lm.cleft_width_profile.length ? Math.max(...lm.cleft_width_profile.map(p => p[1])).toFixed(1) + " mm" : "-"]];
document.getElementById('lm').innerHTML = rows.map(r => `<tr><td>${r[0]}</td><td>${r[1]}</td></tr>`).join('');
const e = RECORD.extension_base || {};
const total = (e.cleft_section_length + Math.PI / 180 * (90 + e.tilt_deg) * e.bend_radius + e.effective_length).toFixed(1);
const mode = RECORD.extension_mode || 'parametric';
document.getElementById('ext').innerHTML = [["Mode", mode], ["Cleft section", e.cleft_section_length + " mm"], ["Bend radius", e.bend_radius + " mm"], ["Effective section", e.effective_length + " mm"],
  ["Total length", total + " mm"], ["Width base → tip", e.width_base + " → " + e.width_tip + " mm"], ["Thickness", e.thickness + " mm"], ["Tilt / distal curve", e.tilt_deg + "° / " + e.distal_curve_deg + "°"]]
  .map(r => `<tr><td>${r[0]}</td><td>${r[1]}</td></tr>`).join('');
const p2 = (RECORD.prototypes || []).find(p => p.number === 2) || (RECORD.prototypes || [])[0] || RECORD.carried_design;
if (p2) document.getElementById('qc').innerHTML = p2.qc.checks.map(c => `<tr><td>${c.check}</td><td class="${c.status}">${c.status} · ${c.value}</td></tr>`).join('');
const log = RECORD.modification_log || [];
if (log.length) document.getElementById('modlog').innerHTML = '<h2>Modification log</h2>' + log.map(m => `<div style="font-size:12px;margin:4px 0">${m.id || ('A' + m.n)} from ${m.from || ('P' + m.from_prototype)}${m.where ? ' (' + m.where + ')' : ''}: ${JSON.stringify(m.changes)} <span style="color:var(--muted)">${m.note || ''}</span> → ${m.file}${m.qc ? ' · QC ' + m.qc.overall : ''}</div>`).join('');
</script>
</body>
</html>
"""


def write_viewer(case_dir: Path) -> Path:
    layers = []
    for fn, name, color, opacity, on in LAYERS:
        p = case_dir / fn
        if p.exists():
            layers.append({"file": fn, "name": name, "color": color, "opacity": opacity, "on": on,
                           "b64": base64.b64encode(p.read_bytes()).decode()})
    for p in sorted(case_dir.glob("adjusted_*.stl")):
        layers.append({"file": p.name, "name": f"Adjusted design {p.stem.split('_')[1]}", "color": "#ff9f43", "opacity": 1.0, "on": False,
                       "b64": base64.b64encode(p.read_bytes()).decode()})
    record = json.loads((case_dir / "design.json").read_text(encoding="utf-8")) if (case_dir / "design.json").exists() else {}
    html = HTML.replace("__DATA__", json.dumps({"layers": layers})).replace("__RECORD__", json.dumps(record))
    out = case_dir / "viewer.html"
    out.write_text(html, encoding="utf-8")
    return out
