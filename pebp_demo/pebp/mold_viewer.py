"""Self-contained HTML viewer for the printed mold kit: exploded demold sequence, cut-away, checks."""
from __future__ import annotations

import base64
import json
from pathlib import Path

import numpy as np
import trimesh

# file stem, label, colour, opacity, visible, demold phase (or None), role, suggested print material
PARTS = [
    ("working_model", "Working model", "#b9bec6", 1.0, True, None,
     "Palate with the cleft blocked out; forms the tissue side of the plate.",
     "Rigid dental model resin; a separation-model resin if available"),
    ("counter_oral", "Oral counter", "#4fb3a5", 0.38, True, 2,
     "Forms the oral surface of the plate; carries the vents and bow-wire grooves. Lifts straight up.",
     "Rigid resin with heat deflection above ~60 °C"),
    ("counter_left", "Extension half, left", "#e2a33c", 0.42, True, 0,
     "Encloses the left half of the baton and half of the pour sprue; holds three wire pins. Slides off to the left.",
     "Same rigid resin as the oral counter"),
    ("counter_right", "Extension half, right", "#8f7ae6", 0.42, True, 1,
     "Encloses the right half of the baton and half of the pour sprue; holds three wire pins. Slides off to the right.",
     "Same rigid resin as the oral counter"),
    ("device_master", "Appliance (cast PMMA)", "#e0566b", 1.0, True, 3,
     "What comes out of the mold. Also printable as a try-in or reference part.",
     "Cast in cold-cure PMMA; print only as a reference"),
    ("safety_wire", "Safety wire 1.8 × 0.8 mm", "#59616b", 1.0, True, 3,
     "Stainless twisted wire, bent on the jig and held centred by the pins.",
     "Stainless steel (not printed)"),
    ("wire_jig", "Wire-bending jig (1:1)", "#7aa0c4", 1.0, False, None,
     "Lay the wire in the groove and bend it to match before the pour.",
     "Any rigid resin or FDM"),
    ("cradle", "Cradle", "#9aa3ad", 0.25, False, None,
     "Open tray that keeps the four pieces closed during the pour and pressure cure.",
     "FDM PLA or PETG"),
]

PHASES = ["Assembled", "Left half slid off", "Right half slid off", "Oral counter lifted", "Appliance lifted off the model"]

HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PEBP Mold Kit</title>
<style>
:root { --bg:#f6f7f9; --panel:#ffffff; --ink:#1c1f24; --muted:#5b6470; --line:#e1e5ea; --accent:#2f6fed; --pass:#1f9d55; --warn:#c7841a; --fail:#d33; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --bg:#101317; --panel:#181c22; --ink:#e8ebef; --muted:#9aa3ad; --line:#2a3039; --accent:#6ea0ff; } }
:root[data-theme="dark"] { --bg:#101317; --panel:#181c22; --ink:#e8ebef; --muted:#9aa3ad; --line:#2a3039; --accent:#6ea0ff; }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--ink); font: 14px/1.45 -apple-system, "Segoe UI", Inter, sans-serif; }
#app { display:grid; grid-template-columns: 340px 1fr; height:100vh; }
#side { background:var(--panel); border-right:1px solid var(--line); padding:16px; overflow:auto; }
#view { position:relative; }
canvas { display:block; width:100%; height:100%; }
h1 { font-size:16px; margin:0 0 4px; }
.sub { color:var(--muted); font-size:12px; margin-bottom:12px; }
h2 { font-size:12px; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); margin:16px 0 6px; }
.part { display:grid; grid-template-columns: 18px 12px 1fr; gap:6px; align-items:start; padding:5px 0; border-bottom:1px solid var(--line); }
.part input { margin:2px 0 0; }
.sw { width:12px; height:12px; border-radius:3px; margin-top:3px; }
.pn { font-weight:600; font-size:13px; }
.pr { color:var(--muted); font-size:12px; }
.pm { font-size:11px; color:var(--muted); font-style:italic; }
table { width:100%; border-collapse:collapse; font-size:12px; }
td { padding:3px 0; border-bottom:1px solid var(--line); vertical-align:top; }
td:last-child { text-align:right; color:var(--muted); }
.pass { color:var(--pass) !important; } .warn { color:var(--warn) !important; } .fail { color:var(--fail) !important; }
.btns { display:flex; gap:6px; flex-wrap:wrap; margin:8px 0; }
button { font:inherit; font-size:12px; padding:4px 8px; border:1px solid var(--line); background:var(--bg); color:var(--ink); border-radius:6px; cursor:pointer; }
button:hover, button.on { border-color:var(--accent); }
button.on { color:var(--accent); }
input[type=range] { width:100%; }
#phase { font-size:13px; font-weight:600; margin-top:2px; }
#hud { position:absolute; left:12px; bottom:10px; font-size:11px; color:var(--muted); background:var(--panel); padding:4px 8px; border-radius:6px; border:1px solid var(--line); }
ol { padding-left:18px; margin:4px 0; font-size:12px; } li { margin:2px 0; }
@media (max-width: 760px) { #app { grid-template-columns: 1fr; grid-template-rows: auto 1fr; } #side { max-height:48vh; border-right:none; border-bottom:1px solid var(--line); } }
</style>
</head>
<body>
<div id="app">
  <div id="side">
    <h1>PEBP Mold Kit</h1>
    <div class="sub" id="sub"></div>
    <h2>Demold sequence</h2>
    <input id="demold" type="range" min="0" max="4" step="0.01" value="0">
    <div id="phase">Assembled</div>
    <div class="btns">
      <button id="play">Play</button><button id="cut">Cut-away</button>
      <button data-view="iso">3D</button><button data-view="front">Front</button><button data-view="side">Side</button><button data-view="top">Top</button>
    </div>
    <h2>Parts</h2>
    <div id="parts"></div>
    <h2>Key numbers</h2>
    <table id="nums"></table>
    <h2>Checks</h2>
    <table id="checks"></table>
    <h2>Notes</h2>
    <ol id="notes"></ol>
  </div>
  <div id="view"><canvas id="c"></canvas><div id="hud">pour orientation · drag: rotate · shift-drag: pan · wheel: zoom · grid 10 mm</div></div>
</div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
<script>
const DATA = __DATA__;
const MAN = DATA.manifest;

function b64(s, T) { const bin = atob(s); const u8 = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i); return new T(u8.buffer); }
function geom(P) {
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(b64(P.v, Float32Array), 3));
  g.setIndex(new THREE.BufferAttribute(b64(P.f, Uint32Array), 1));
  g.computeVertexNormals(); return g;
}

const canvas = document.getElementById('c');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, preserveDrawingBuffer: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.localClippingEnabled = true;
const scene = new THREE.Scene();
const dark = matchMedia('(prefers-color-scheme: dark)').matches;
scene.background = new THREE.Color(dark ? 0x101317 : 0xf6f7f9);
const camera = new THREE.PerspectiveCamera(35, 1, 1, 3000);
scene.add(new THREE.HemisphereLight(0xffffff, 0x667788, 0.9));
const key = new THREE.DirectionalLight(0xffffff, 0.8); key.position.set(80, -90, 160); scene.add(key);
const fill = new THREE.DirectionalLight(0xffffff, 0.35); fill.position.set(-90, 70, 40); scene.add(fill);
const grid = new THREE.GridHelper(200, 20, 0x888888, 0x555555); grid.rotation.x = Math.PI / 2; grid.material.opacity = 0.22; grid.material.transparent = true; scene.add(grid);

const n = new THREE.Vector3(...DATA.split_normal), p0 = new THREE.Vector3(...DATA.split_point);
const clip = new THREE.Plane(n.clone().negate(), n.dot(p0));
const meshes = {};
const partsDiv = document.getElementById('parts');
for (const P of DATA.parts) {
  const m = new THREE.Mesh(geom(P), new THREE.MeshStandardMaterial({ color: P.color, opacity: P.opacity, transparent: P.opacity < 1, depthWrite: P.opacity >= 1, roughness: 0.55, metalness: P.stem === 'safety_wire' ? 0.6 : 0.05, side: THREE.DoubleSide }));
  m.visible = P.on; m.userData = P; m.renderOrder = P.opacity < 1 ? 2 : 1;
  if (P.offset) m.position.set(...P.offset);
  scene.add(m); meshes[P.stem] = m;
  const row = document.createElement('label'); row.className = 'part';
  row.innerHTML = `<input type="checkbox" ${P.on ? 'checked' : ''}><span class="sw" style="background:${P.color}"></span><span><span class="pn">${P.label}</span><br><span class="pr">${P.role}</span><br><span class="pm">${P.material}</span></span>`;
  row.querySelector('input').onchange = e => { m.visible = e.target.checked; };
  partsDiv.appendChild(row);
}

const PHASES = DATA.phases, DIST = [42, 42, 62, 26];
const slider = document.getElementById('demold');
function applyDemold() {
  const s = parseFloat(slider.value);
  for (const k in meshes) {
    const P = meshes[k].userData; if (P.phase === null || P.phase === undefined) continue;
    let off = new THREE.Vector3(...(P.offset || [0, 0, 0]));
    const t = Math.min(1, Math.max(0, s - P.phase));
    off.addScaledVector(new THREE.Vector3(...P.pull), t * DIST[P.phase]);
    meshes[k].position.copy(off);
  }
  document.getElementById('phase').textContent = PHASES[Math.min(4, Math.floor(s + 1e-6))] + (s % 1 > 0.02 && s < 4 ? ' …' : '');
}
slider.oninput = applyDemold;
let playing = null;
document.getElementById('play').onclick = () => {
  if (playing) { cancelAnimationFrame(playing); playing = null; return; }
  if (parseFloat(slider.value) >= 4) slider.value = 0;
  const step = () => { slider.value = Math.min(4, parseFloat(slider.value) + 0.012); applyDemold(); if (parseFloat(slider.value) < 4) playing = requestAnimationFrame(step); else playing = null; };
  playing = requestAnimationFrame(step);
};
let cut = false;
document.getElementById('cut').onclick = (e) => {
  cut = !cut; e.target.classList.toggle('on', cut);
  for (const k of ['working_model', 'counter_oral', 'counter_left', 'counter_right', 'cradle', 'device_master']) if (meshes[k]) { meshes[k].material.clippingPlanes = cut ? [clip] : []; meshes[k].material.needsUpdate = true; }
};

const c = DATA.center; const target = new THREE.Vector3(c[0], c[1], c[2]);
let theta = -1.0, phi = 1.05, dist = 210;
function updateCam() { camera.position.set(target.x + dist * Math.sin(phi) * Math.cos(theta), target.y + dist * Math.sin(phi) * Math.sin(theta), target.z + dist * Math.cos(phi)); camera.up.set(0, 0, 1); camera.lookAt(target); }
const views = { iso: [-1.0, 1.05], front: [-Math.PI / 2, Math.PI / 2], side: [Math.PI, Math.PI / 2], top: [-Math.PI / 2, 0.02] };
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
canvas.addEventListener('wheel', e => { e.preventDefault(); dist = Math.min(900, Math.max(40, dist * (1 + e.deltaY * 0.001))); updateCam(); }, { passive: false });
function resize() { const w = canvas.clientWidth, h = canvas.clientHeight; renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix(); }
window.addEventListener('resize', resize); resize(); updateCam(); applyDemold();
(function loop() { requestAnimationFrame(loop); renderer.render(scene, camera); })();

// side panel
const V = MAN.validation, F = MAN.features;
document.getElementById('sub').textContent = `${DATA.case_name} · ${MAN.notes[0]}`;
const nums = [["Mold block", V.block_mm.join(' × ') + ' mm'], ["Cavity (incl. sprue, vents)", (V.cavity_volume_mm3 / 1000).toFixed(2) + ' ml'],
  ["Appliance volume", (V.device_volume_mm3 / 1000).toFixed(2) + ' ml'], ["Resin to mix", '≈ ' + MAN.pmma_needed_ml + ' ml'],
  ["Safety wire length", F.safety_wire.length_mm + ' mm'], ["Wire pins", F.safety_wire.pin_stations.length + ' per side, ' + F.safety_wire.pin_diameter_mm + ' mm'],
  ["Rim trimmed for demolding", V.draft_removed_mm3 + ' mm³']];
document.getElementById('nums').innerHTML = nums.map(r => `<tr><td>${r[0]}</td><td>${r[1]}</td></tr>`).join('');
const tol = V.tolerance_mm3;
const allWT = Object.values(V.watertight).every(x => x);
const maxOv = Math.max(...Object.values(V.piece_overlaps_mm3)), maxIn = Math.max(...Object.values(V.device_inside_piece_mm3));
const rows = [["All parts watertight, one body each", allWT && Object.values(V.body_counts).every(x => x === 1)],
  ["Pieces do not overlap", maxOv <= tol, maxOv.toFixed(2) + ' mm³'],
  ["No piece intrudes into the appliance", maxIn <= tol, maxIn.toFixed(2) + ' mm³'],
  ["One connected cavity", V.cavity_pieces_mm3.filter(x => x > 0.5).length === 1],
  ["Appliance fully inside the cavity", V.device_outside_cavity_mm3 <= tol, V.device_outside_cavity_mm3.toFixed(2) + ' mm³'],
  ["Pins clear of the wire", V.pins_clear_of_wire_mm3 <= 0.05, V.pins_clear_of_wire_mm3.toFixed(2) + ' mm³'],
  ["Wire fully inside the appliance", V.wire_inside_device_mm3 <= 0.05, V.wire_inside_device_mm3.toFixed(2) + ' mm³']]
  .concat(V.demold.map(d => [`Demold: ${d.piece} comes free`, d.free, 'max ' + Math.max(...Object.values(d.overlap_mm3_at_mm)).toFixed(2) + ' mm³']));
document.getElementById('checks').innerHTML = rows.map(r => `<tr><td>${r[0]}</td><td class="${r[1] ? 'pass' : 'fail'}">${r[1] ? 'pass' : 'FAIL'}${r[2] ? ' · ' + r[2] : ''}</td></tr>`).join('') +
  `<tr><td colspan="2" style="color:var(--muted);text-align:left">Tolerance ${tol} mm³: residual overlaps are films a few microns thick where surfaces touch.</td></tr>`;
document.getElementById('notes').innerHTML = MAN.notes.slice(1).concat([F.safety_wire.note, F.bow_grooves.note]).map(t => `<li>${t}</li>`).join('');
</script>
</body>
</html>
"""


def _pack(m: trimesh.Trimesh) -> dict:
    return {"v": base64.b64encode(np.asarray(m.vertices, dtype=np.float32).tobytes()).decode(),
            "f": base64.b64encode(np.asarray(m.faces, dtype=np.uint32).tobytes()).decode()}


def write_mold_viewer(mold_dir: Path) -> Path:
    mold_dir = Path(mold_dir)
    man = json.loads((mold_dir / "mold_manifest.json").read_text(encoding="utf-8"))
    pulls = {d["piece"]: d["pull_direction_pour_frame"] for d in man["validation"]["demold"]}
    pull_of = {"counter_left": pulls.get("counter_left"), "counter_right": pulls.get("counter_right"),
               "counter_oral": pulls.get("counter_oral"), "device_master": pulls.get("cast appliance"),
               "safety_wire": pulls.get("cast appliance")}
    loaded = {stem: trimesh.load(mold_dir / f"{stem}.stl", force="mesh") for stem, *_ in PARTS if (mold_dir / f"{stem}.stl").exists()}
    core = [loaded[k] for k in ("working_model", "counter_oral", "counter_left", "counter_right") if k in loaded]
    lo = np.min([m.bounds[0] for m in core], axis=0); hi = np.max([m.bounds[1] for m in core], axis=0)
    parts = []
    for stem, label, colour, opacity, on, phase, role, material in PARTS:
        if stem not in loaded:
            continue
        offset = [0.0, 0.0, 0.0]
        if stem == "wire_jig":                       # park the jig beside the flask
            offset = [float(hi[0] + 12 - loaded[stem].bounds[0][0]), float(lo[1] - loaded[stem].bounds[0][1]), 0.0]
        parts.append({"stem": stem, "label": label, "color": colour, "opacity": opacity, "on": on, "phase": phase,
                      "pull": pull_of.get(stem), "offset": offset, "role": role, "material": material, **_pack(loaded[stem])})
    sp = man.get("viewer", {})
    data = {"parts": parts, "manifest": man, "phases": [p for p in
            ["Assembled", "Left half slid off", "Right half slid off", "Oral counter lifted", "Appliance lifted off the model"]],
            "split_normal": sp.get("split_normal_pour", [1, 0, 0]), "split_point": sp.get("split_point_pour", [0, 0, 0]),
            "center": ((lo + hi) / 2).round(2).tolist(), "case_name": Path(man["case"]).name}
    out = mold_dir / "mold_viewer.html"
    out.write_text(HTML.replace("__DATA__", json.dumps(data)), encoding="utf-8")
    return out
