/* PEBP Studio front end. Talks only to the local server; every build is a `pebp` command run there. */
'use strict';

const $ = (s, el = document) => el.querySelector(s);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmt = (v, d = 1) => (v === null || v === undefined || Number.isNaN(+v)) ? '–' : (+v).toFixed(d);

async function api(path, body, raw) {
  const opts = { headers: { 'X-PEBP-Studio': '1' } };
  if (body !== undefined) {
    opts.method = 'POST';
    if (raw) { opts.body = body; opts.headers['Content-Type'] = 'application/octet-stream'; Object.assign(opts.headers, raw); }
    else { opts.body = JSON.stringify(body); opts.headers['Content-Type'] = 'application/json'; }
  }
  const r = await fetch(path, opts);
  const j = await r.json().catch(() => ({ error: `HTTP ${r.status}` }));
  if (!r.ok) throw Object.assign(new Error(j.error || `HTTP ${r.status}`), { status: r.status });
  return j;
}

const S = { cases: [], caseName: null, c: null, vocab: null, open: 'case', moldstudio: false, showMold: false, error: {}, mods: [{ code: 'shorten_mm', amount: 2 }] };

// ---------------------------------------------------------------- 3D view
const canvas = $('#c');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, preserveDrawingBuffer: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
const scene = new THREE.Scene();
const darkQ = matchMedia('(prefers-color-scheme: dark)');
function setBg() { scene.background = new THREE.Color(darkQ.matches ? 0x111418 : 0xf4f5f7); }
setBg(); darkQ.addEventListener('change', setBg);
const camera = new THREE.PerspectiveCamera(32, 1, 1, 3000);
scene.add(new THREE.HemisphereLight(0xffffff, 0x667788, 0.85));
const key = new THREE.DirectionalLight(0xffffff, 0.75); key.position.set(60, -80, 120); scene.add(key);
const fill = new THREE.DirectionalLight(0xffffff, 0.35); fill.position.set(-80, 60, -60); scene.add(fill);
const grid = new THREE.GridHelper(120, 12, 0x888888, 0x888888); grid.rotation.x = Math.PI / 2; grid.material.opacity = 0.18; grid.material.transparent = true; scene.add(grid);
const root = new THREE.Group(); scene.add(root);
const target = new THREE.Vector3(0, 8, -6);
let theta = -1.05, phi = 1.2, dist = 150;
const VIEWS = { iso: [-1.05, 1.2], below: [Math.PI / 2, Math.PI - 0.01], side: [0, Math.PI / 2], front: [Math.PI / 2, Math.PI / 2] };
function updateCam() {
  camera.position.set(target.x + dist * Math.sin(phi) * Math.cos(theta), target.y + dist * Math.sin(phi) * Math.sin(theta), target.z + dist * Math.cos(phi));
  camera.up.set(0, 0, 1); camera.lookAt(target);
}
document.querySelectorAll('[data-view]').forEach(b => b.onclick = () => { [theta, phi] = VIEWS[b.dataset.view]; updateCam(); });
let drag = null;
canvas.addEventListener('pointerdown', e => { drag = { x: e.clientX, y: e.clientY, pan: e.shiftKey || e.button === 2 }; canvas.setPointerCapture(e.pointerId); });
canvas.addEventListener('pointermove', e => {
  if (!drag) return; const dx = e.clientX - drag.x, dy = e.clientY - drag.y; drag.x = e.clientX; drag.y = e.clientY;
  if (drag.pan) {
    const f = new THREE.Vector3(); camera.getWorldDirection(f);
    const r = new THREE.Vector3().crossVectors(f, camera.up).normalize(); const u = new THREE.Vector3().crossVectors(r, f).normalize();
    target.addScaledVector(r, -dx * dist * 0.0015); target.addScaledVector(u, dy * dist * 0.0015);
  } else { theta -= dx * 0.008; phi = Math.min(Math.PI - 0.02, Math.max(0.02, phi - dy * 0.008)); }
  updateCam();
});
canvas.addEventListener('pointerup', () => drag = null);
canvas.addEventListener('contextmenu', e => e.preventDefault());
canvas.addEventListener('wheel', e => { e.preventDefault(); dist = Math.min(800, Math.max(25, dist * (1 + e.deltaY * 0.001))); updateCam(); }, { passive: false });
function resize() { const w = canvas.clientWidth, h = canvas.clientHeight; if (!w || !h) return; renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix(); }
new ResizeObserver(resize).observe(canvas); updateCam();
(function loop() { requestAnimationFrame(loop); renderer.render(scene, camera); })();

function parseSTL(buf) {
  const dv = new DataView(buf);
  const n = buf.byteLength >= 84 ? dv.getUint32(80, true) : 0;
  let pos;
  if (buf.byteLength === 84 + n * 50) {
    pos = new Float32Array(n * 9); let off = 84;
    for (let i = 0; i < n; i++) { off += 12; for (let k = 0; k < 9; k++) { pos[i * 9 + k] = dv.getFloat32(off, true); off += 4; } off += 2; }
  } else {                              // ASCII STL
    const txt = new TextDecoder().decode(buf); const v = [];
    const re = /vertex\s+([-+\d.eE]+)\s+([-+\d.eE]+)\s+([-+\d.eE]+)/g; let m;
    while ((m = re.exec(txt))) v.push(+m[1], +m[2], +m[3]);
    pos = new Float32Array(v);
  }
  const g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.BufferAttribute(pos, 3)); g.computeVertexNormals(); return g;
}
const geoCache = new Map();
async function geometry(file, stamp) {
  const k = `${S.caseName}/${file}@${stamp}`;
  if (!geoCache.has(k)) geoCache.set(k, fetch(`/api/case/${encodeURIComponent(S.caseName)}/files/${file}`).then(r => { if (!r.ok) throw new Error(file); return r.arrayBuffer(); }).then(parseSTL));
  return geoCache.get(k);
}
const COLORS = { scan: '#a7b0ba', plate: '#e0b84a', P1: '#4f9cf0', P2: '#36b37e', P3: '#f06a6a', F: '#b084f0', A: '#ff9f43', C: '#8e7cc3', K: '#2bb3c0', S: '#d9468f', base: '#c9a227', wire: '#6b7280' };
const CAST_KINDS = ['prototype', 'adjusted', 'final_groove'];
const colorOf = id => COLORS[id] || COLORS[id[0]] || '#999';
let layerState = {};   // name -> visible, kept per case while you move between steps

async function showLayers(layers, overlays) {
  const token = Symbol(); showLayers.token = token;
  const built = [];
  for (const L of layers) {
    try { const g = await geometry(L.file, L.stamp); built.push({ L, g }); } catch (e) { /* missing file: skip */ }
  }
  if (showLayers.token !== token) return;
  root.clear();
  const box = $('#layers'); box.innerHTML = '';
  for (const { L, g } of built) {
    const m = new THREE.Mesh(g, new THREE.MeshStandardMaterial({ color: L.color, opacity: L.opacity ?? 1, transparent: (L.opacity ?? 1) < 1, roughness: 0.55, metalness: 0.03, side: THREE.DoubleSide, depthWrite: (L.opacity ?? 1) >= 1 }));
    const vis = layerState[L.name] ?? L.on ?? true; m.visible = vis; m.renderOrder = (L.opacity ?? 1) < 1 ? 2 : 1; root.add(m);
    const lab = document.createElement('label');
    lab.innerHTML = `<input type="checkbox" ${vis ? 'checked' : ''}><span class="sw" style="background:${L.color}"></span><span>${esc(L.name)}</span>`;
    lab.querySelector('input').onchange = e => { m.visible = e.target.checked; layerState[L.name] = e.target.checked; };
    box.appendChild(lab);
  }
  for (const o of overlays || []) root.add(o);
}
function sphere(p, color, r = 0.9) { const m = new THREE.Mesh(new THREE.SphereGeometry(r, 18, 12), new THREE.MeshBasicMaterial({ color, depthTest: false })); m.position.set(...p); m.renderOrder = 5; return m; }
function polyline(pts, color, closed) {
  const v = pts.map(p => new THREE.Vector3(p[0], p[1], p[2] ?? 0)); if (closed) v.push(v[0].clone());
  const l = new THREE.Line(new THREE.BufferGeometry().setFromPoints(v), new THREE.LineBasicMaterial({ color, depthTest: false })); l.renderOrder = 4; return l;
}
function label(text, p, color = '#1c1f24', dx = 0, dy = 0) {
  const c = document.createElement('canvas'); const ctx = c.getContext('2d'); ctx.font = '600 28px system-ui'; const w = ctx.measureText(text).width + 16;
  c.width = w; c.height = 40; ctx.font = '600 28px system-ui'; ctx.fillStyle = 'rgba(255,255,255,.85)'; ctx.fillRect(0, 0, w, 40); ctx.fillStyle = color; ctx.fillText(text, 8, 29);
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(c), depthTest: false })); s.scale.set(w / 16, 40 / 16, 1); s.position.set(p[0] + (dx || 0), p[1] + (dy || 0), p[2] + 2); s.renderOrder = 6; return s;
}

// ---------------------------------------------------------------- data
async function loadCases(select) {
  const j = await api('/api/cases'); S.cases = j.cases; S.moldstudio = j.moldstudio; S.workspace = j.workspace;
  const sel = $('#casesel');
  sel.innerHTML = S.cases.length ? S.cases.map(c => `<option value="${esc(c.name)}">${esc(c.name)}${c.synthetic ? ' (synthetic)' : ''}</option>`).join('') : '<option value="">no cases yet</option>';
  const names = S.cases.map(c => c.name); const want = [select, S.caseName].find(n => n && names.includes(n)) || names[0];
  if (want) { sel.value = want; await openCase(want); } else render();
}
async function openCase(name) {
  $('#reveal').disabled = !name;
  if (name !== S.caseName) { layerState = {}; S.showMold = false; S.error = {}; }
  S.caseName = name; localStorageSet('pebp.case', name);
  await refresh(true);
}
async function refresh(pickStep) {
  if (!S.caseName) return render();
  S.c = await api(`/api/case/${encodeURIComponent(S.caseName)}`);
  if (pickStep) { const s = S.c.steps.find(s => s.status !== 'approved'); S.open = s ? s.step : 'mold'; }
  render();
  if (S.c.running) pollJob(S.c.running.id);
}
function localStorageSet(k, v) { try { localStorage.setItem(k, v); } catch (e) { } }
function localStorageGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }

let polling = null;
async function pollJob(id) {
  if (polling === id) return; polling = id;
  showJob({ status: 'running', kind: '', commands: [] });
  while (true) {
    let j; try { j = await api(`/api/jobs/${id}`); } catch (e) { break; }
    showJob(j); renderSteps();
    if (j.status !== 'running') break;
    await new Promise(r => setTimeout(r, 700));
  }
  polling = null;
  await refresh(false);
}
function showJob(j) {
  const line = $('#jobline');
  if (!j) return;
  const s = j.status === 'running' ? `Building: ${j.kind}…` : j.status === 'done' ? `Done: ${j.kind}` : `FAILED: ${j.kind} (exit ${j.rc ?? '?'})`;
  line.innerHTML = `<b>${esc(s)}</b> <span class="muted">${esc((j.commands || []).join(' ; '))}</span>`;
  $('#log').textContent = (j.stdout || '') + (j.stderr ? '\n--- errors ---\n' + j.stderr : '');
  if (j.status === 'failed') { $('#log').hidden = false; $('#togglelog').textContent = 'Hide output'; }
}
$('#togglelog').onclick = () => { const l = $('#log'); l.hidden = !l.hidden; $('#togglelog').textContent = l.hidden ? 'Show output' : 'Hide output'; };

async function run(step, path, body) {
  S.error[step] = null;
  try { const j = await api(`/api/case/${encodeURIComponent(S.caseName)}/${path}`, body); if (j && j.id && j.status) { S.c.running = j; renderSteps(); await pollJob(j.id); } else await refresh(false); }
  catch (e) { S.error[step] = e.message; renderSteps(); }
}

// ---------------------------------------------------------------- rendering
const reviewer = () => (S.c && S.c.review && S.c.review.reviewer) || '';
function render() {
  const c = S.c;
  const b = [`<span class="badge local" title="The server listens on 127.0.0.1 only">this computer only</span>`];
  if (c && c.has_scan) b.push(c.meta.synthetic === false ? '<span class="badge real">REAL SCAN: keep on this computer</span>' : '<span class="badge synthetic">synthetic case</span>');
  if (c && c.record) b.push(`<span class="badge">${esc(c.record.schema)}</span>`);
  $('#badges').innerHTML = b.join('');
  renderSteps(); renderView();
}

function chip(st, building) {
  if (building) return '<span class="chip building">building…</span>';
  const t = { todo: 'to do', locked: 'waiting', review: 'REVIEW', approved: 'approved', changed: 'changed since approval' }[st.status];
  return `<span class="chip ${st.status}">${t}${st.stops && st.status !== 'approved' ? ` · ${st.stops} stop` : ''}</span>`;
}

function renderSteps() {
  const el = $('#steps');
  if (!S.c) { el.innerHTML = `<div class="step-body" style="display:block;padding:16px"><p>No case open.</p><p class="muted">Make a new case with the button at the top. Workspace: ${esc(S.workspace || '')}</p></div>`; return; }
  const running = S.c.running && S.c.running.status === 'running';
  const focus = document.activeElement && document.activeElement.id;
  el.innerHTML = S.c.steps.map((st, i) => `
    <div class="step ${st.status} ${S.open === st.step ? 'open' : ''}" data-step="${st.step}">
      <div class="step-head"><span class="step-num">${st.status === 'approved' ? '✓' : i + 1}</span><span class="step-title">${esc(st.title)}</span>${chip(st, running && S.open === st.step)}</div>
      <div class="step-body">${S.open === st.step ? body(st, running) : ''}</div>
    </div>`).join('');
  el.querySelectorAll('.step-head').forEach(h => h.onclick = () => { S.open = h.parentElement.dataset.step; S.showMold = S.open === 'mold' && S.showMold; renderSteps(); renderView(); });
  wire(el.querySelector('.step.open .step-body'), S.open);
  if (renderSteps.last !== S.open) { renderSteps.last = S.open; const o = el.querySelector('.step.open'); if (o) el.scrollTop = Math.max(0, o.offsetTop - el.offsetTop - 60); }
  if (focus && document.getElementById(focus)) document.getElementById(focus).focus();
}

function findingsHTML(fs) {
  if (!fs.length) return '<p class="muted">No findings.</p>';
  return '<ul class="findings">' + fs.map(f => `<li class="${f.level}"><span class="lvl">${f.level.toUpperCase()}</span>${esc(f.text)}${f.basis ? `<span class="basis">${esc(f.basis)}</span>` : ''}</li>`).join('') + '</ul>';
}
function approveHTML(st, running) {
  const ap = st.approval;
  let h = '<div class="approve">';
  if (st.status === 'approved') {
    h += `<p class="approved-by">Approved by ${esc(ap.by)} · ${esc(new Date(ap.time).toLocaleString())}${ap.override ? ' · approved anyway over ' + ap.override.length + ' stop finding(s)' : ''}</p>
      <div class="row"><button data-act="withdraw">Send back</button><span class="hint">withdraws this approval and every one after it</span></div>`;
  } else if (st.status === 'locked') {
    h += '<p class="muted">Approve the steps above first.</p>';
  } else if (st.status !== 'todo') {
    if (st.status === 'changed') h += `<p class="err">The design changed after ${esc(ap.by)} approved it. Review it again.</p>`;
    if (st.stops) h += `<div class="row"><label><input type="checkbox" id="override-${st.step}"> Approve anyway: I have read the STOP finding${st.stops > 1 ? 's' : ''} above</label></div>`;
    h += `<div class="row"><button class="primary" data-act="approve" ${running ? 'disabled' : ''}>${st.step === 'mold' ? 'Approve the tooling for printing' : 'Approve and continue'}</button>${reviewer() ? `<span class="hint">as ${esc(reviewer())}</span>` : '<span class="hint err">enter your name on the Case step</span>'}</div>`;
  }
  if (S.error['approve:' + st.step]) h += `<p class="err">${esc(S.error['approve:' + st.step])}</p>`;
  return h + '</div>';
}
const errHTML = step => S.error[step] ? `<p class="err">${esc(S.error[step])}</p>` : '';

function body(st, running) {
  const c = S.c, r = c.record, dis = running ? 'disabled' : '';
  const f = findingsHTML(st.findings), ap = approveHTML(st, running);
  switch (st.step) {
    case 'case': {
      let h = `<div class="field"><label for="reviewer">Your name or initials</label><input id="reviewer" value="${esc(reviewer())}" placeholder="recorded with each approval"></div>`;
      if (!c.has_scan) {
        h += `<h4>Scan</h4><p>Add the infant's maxillary intraoral scan (STL). It is copied into the case folder on this computer and goes nowhere else.</p>
          <div class="row"><input type="file" id="scanfile" accept=".stl" ${dis}></div>
          <h4>Or a demo case</h4><p class="muted">A synthetic Robin-sequence palate (published neonatal morphology, Willershausen 2024), rotated at random like a raw scan.</p>
          <div class="row"><label><input type="checkbox" id="syncleft" checked> cleft palate</label><button id="synth" ${dis}>Make a synthetic scan</button></div>`;
      } else {
        h += `<h4>Scan</h4><table><tr><td>Source</td><td>${esc(c.meta.scan_source || 'scan.stl')}</td></tr>
          ${c.meta.triangles ? `<tr><td>Triangles</td><td class="num">${c.meta.triangles.toLocaleString()}</td></tr>` : ''}
          ${r ? `<tr><td>SHA-256</td><td><code>${esc(r.source_scan.sha256.slice(0, 16))}…</code></td></tr>` : ''}</table>`;
      }
      const cl = (c.meta.cleft || {}).value;
      const CT = [['hard_soft', 'Cleft of the hard and soft palate', 'Tübingen cleft baton. The cleft gets the cleft block-out, carried back past the plate border; the baton root leaves the plate 2 mm behind the tuberosity line, over the cleft.'],
                  ['soft_only', 'Cleft of the soft palate or uvula only', 'Tübingen no-cleft baton (Tübingen 2025 use it for these clefts). The hard palate is intact: any gap in the scan is bridged as missing data, and the root leaves the plate on the tuberosity line, off the soft palate.'],
                  ['none', 'No cleft', 'Tübingen no-cleft baton, with the same plate as a soft palate cleft.']];
      h += `<h4>Cleft type <span class="tag clin">clinical choice</span></h4>
        <p class="hint">From the examination, not the scan: a hole in a scan can look like a cleft, and the scan does not show the soft palate. It picks the baton and how the plate bridges the palate.</p>
        ${CT.map(([v, t, d]) => `<div class="choice ${cl === v ? 'selected' : ''}"><label><input type="radio" name="cleft" value="${v}" ${cl === v ? 'checked' : ''} ${dis}> ${t}</label><span class="tag pub">baton: Tübingen 2025</span><span class="tag heur">plate: project heuristic</span><div class="hint">${d}</div></div>`).join('')}
        ${(cl === 'yes' || cl === 'no') ? `<p class="err">Entered as “${cl === 'yes' ? 'cleft palate' : 'no cleft'}” before this question had three answers. Choose again.</p>` : ''}
        ${c.meta.cleft ? `<span class="hint">entered by ${esc(c.meta.cleft.by)}</span>` : ''}${errHTML('cleft')}`;
      return h + errHTML('case') + f + ap;
    }
    case 'landmarks': {
      const o = (c.review.design_options) || {};
      let h = `<p>The software finds the arch, the tuberosities and the cleft, and turns the scan into the design frame. Check the points on the model: a wrong frame gives a plate that passes every rule and still does not fit.</p>`;
      h += `<div class="field"><label for="backend">Landmark finder</label><select id="backend"><option value="builtin">built-in</option><option value="moldstudio" ${S.moldstudio ? '' : 'disabled'}>MoldStudio classifier${S.moldstudio ? '' : ' (not found on this computer)'}</option></select></div>`;
      h += `<div class="row"><button class="primary" id="detect" ${dis || (c.has_scan ? '' : 'disabled')}>${r ? 'Detect again' : 'Detect landmarks'}</button><span class="hint">runs <code>pebp design</code> with the plate settings below (about 5 s)</span></div>`;
      if (r) {
        const lm = r.landmarks, w = lm.cleft_width_profile || [];
        h += `<h4>Measured</h4><table>
          <tr><td>Arch depth</td><td class="num">${fmt(lm.arch_depth_mm)} mm</td></tr>
          <tr><td>Intertuberosity width</td><td class="num">${fmt(lm.intertuberosity_width_mm)} mm</td></tr>
          <tr><td>Cleft</td><td class="num">${lm.cleft_present ? 'found' : 'not found'}</td></tr>
          ${lm.cleft_present ? `<tr><td>Widest cleft</td><td class="num">${fmt(Math.max(...w.map(p => p[1])))} mm</td></tr><tr><td>Cleft margin level</td><td class="num">${fmt(lm.cleft_margin_z_mm, 2)} mm</td></tr>` : ''}
          <tr><td>Finder</td><td class="num">${esc((lm.backend || {}).backend || 'builtin')}</td></tr></table>`;
      }
      return h + errHTML('landmarks') + f + ap;
    }
    case 'plate': {
      const o = c.review.design_options || {}; const dc = (r && r.design_choices) || {};
      const prof = o.plate_profile || 'uniform';
      let h = `<p>Choose the plate. The default is the Tübingen plate: one thickness everywhere.</p>
        <div class="choice ${prof === 'uniform' ? 'selected' : ''}"><label><input type="radio" name="prof" value="uniform" ${prof === 'uniform' ? 'checked' : ''}> Uniform</label><span class="tag pub">Tübingen 2025</span>
          <div class="hint">2 mm for the prototypes, 2.5–3 mm for the final plate.</div></div>
        <div class="choice ${prof === 'graded' ? 'selected' : ''}"><label><input type="radio" name="prof" value="graded" ${prof === 'graded' ? 'checked' : ''}> Graded, low profile</label><span class="tag heur">project heuristic</span>
          <div class="hint">Full thickness at the baton root, over the cleft and at the bow-wire tags; thinner elsewhere, never below 1.5 mm. Not tested on the bench.</div>
          <div class="field"><label for="thin">Thinnest wall (mm)</label><input type="number" id="thin" step="0.1" min="1.5" max="3" value="${o.thin_thickness ?? 1.5}"></div></div>
        <div class="choice ${o.border_relief ? 'selected' : ''}"><label><input type="checkbox" id="relief" ${o.border_relief ? 'checked' : ''}> Border relief</label><span class="tag heur">project heuristic</span>
          <div class="hint">Caps the vestibular flange 1 mm above the crest, notches the labial frenulum 3 mm, relieves each tuberosity 0.3 mm: the commonest ulcer sites (Weismann 2026). Confirm retention at the try-in.</div></div>
        <div class="field"><label for="pth">Prototype plate (mm)</label><input type="number" id="pth" step="0.1" min="1.5" max="4" value="${o.plate_thickness ?? 2.0}"></div>
        <div class="field"><label for="fth">Final plate (mm)</label><input type="number" id="fth" step="0.1" min="1.5" max="4" value="${o.final_thickness ?? 2.5}"></div>
        <details class="adv"><summary>Advanced</summary>
          <div class="field"><label for="ext">Baton</label><select id="ext">
            ${[['auto', 'published Tübingen extension, cleft or no-cleft to match (default)'], ['tuebingen-cleft', 'Tübingen, cleft variant'], ['tuebingen-nocleft', 'Tübingen, no-cleft variant'], ['parametric', 'parametric model (not the published shape)']].map(([v, t]) => `<option value="${v}" ${(o.extension || 'auto') === v ? 'selected' : ''}>${t}</option>`).join('')}</select></div>
          <div class="field"><label for="flange">Buccal rim (mm)</label><input type="number" id="flange" step="0.1" value="${o.flange ?? 1.5}"></div>
          <div class="field"><label for="blockout">Cleft block-out (mm)</label><input type="number" id="blockout" step="0.1" value="${o.block_out ?? 2.5}"></div>
          <div class="row"><label><input type="checkbox" id="final" ${o.final ? 'checked' : ''}> Also make F, the final design with a wire groove (older groove-and-cover route)</label></div>
        </details>`;
      if (c.kit_available) h += `<h4>How the worn plate is made</h4>
        <div class="choice ${c.route === 'cast' ? 'selected' : ''}"><label><input type="radio" name="route" value="cast" ${c.route === 'cast' ? 'checked' : ''}> One-piece cast plate</label><span class="tag pub">Tübingen 2025</span>
          <div class="hint">Try in P1–P3, change them, then cast the final plate with its safety wire in printed tooling.</div></div>
        <div class="choice ${c.route === 'kit' ? 'selected' : ''}"><label><input type="radio" name="route" value="kit" ${c.route === 'kit' ? 'checked' : ''}> Two-visit chairside kit</label><span class="tag clin">untested research route</span>
          <div class="hint">Visit 1: a try-in socket base with 15 kit batons, and the worn plate's final base cast before visit 2. Visit 2: the baton that fitted under endoscopy is picked up chairside into the base. Nothing in this route has been printed, cast, bonded or worn.</div></div>${errHTML('route')}`;
      const resetNeeded = r && ((r.modification_log || []).length || (r.cast_plates || []).length || (r.fitting_sessions || []).length || r.kit);
      if (resetNeeded) h += `<div class="row"><label><input type="checkbox" id="confirmreset"> I understand: rebuilding archives the changed devices and cast plates</label></div>`;
      h += `<div class="row"><button class="primary" id="build" ${dis}>${r ? 'Rebuild plate and prototypes' : 'Build'}</button><span class="hint">P1–P3: the baton 2 mm back, centred, 2 mm forward</span></div>`;
      if (r) {
        const p2 = (r.prototypes || [])[1] || {}; const info = r.plate || {};
        h += `<h4>Result</h4><table>
          <tr><td>Baton</td><td>${esc(r.extension_mode)}</td></tr>
          <tr><td>Plate profile</td><td>${dc.plate_profile ? 'graded, ' + fmt(dc.plate_profile.thin_mm) + ' mm thinnest' : 'uniform'}</td></tr>
          <tr><td>Border relief</td><td>${dc.border_relief ? `on (flange trimmed ${fmt(dc.border_relief.trimmed_mm2, 0)} mm², notch ${fmt(dc.border_relief.notch_removed_mm2, 0)} mm²)` : 'off'}</td></tr>
          <tr><td>Root pad</td><td>${info.root_pad && info.root_pad.area_mm2 ? fmt(info.root_pad.area_mm2) + ' mm²' : 'none needed'}</td></tr>
          <tr><td>P2 QC</td><td><span class="st ${p2.qc && p2.qc.overall}">${esc(p2.qc && p2.qc.overall)}</span></td></tr></table>`;
      }
      return h + errHTML('plate') + f + ap;
    }
    case 'changes': return changesBody(st, running, f, ap);
    case 'checks': return checksBody(st, running, f, ap);
    case 'mold': return moldBody(st, running, f, ap);
    case 'kit': return kitBody(st, running, f, ap);
    case 'visit2': return visit2Body(st, running, f, ap);
  }
  return '';
}

function changesBody(st, running, f, ap) {
  const c = S.c, r = c.record, dis = running ? 'disabled' : '';
  if (!r) return '<p class="muted">Build the plate first.</p>';
  const devs = c.devices.filter(d => CAST_KINDS.includes(d.kind));
  const chosen = c.review.chosen_device;
  const V = S.vocab.modifications;
  let h = `<p>After the endoscopy try-in, record what was changed. Each change makes a new device A<i>n</i> from the one it was made to, and keeps the link to it.</p>
    <h4>Devices</h4><div class="hint">Show on the model · cast this one</div>`;
  h += devs.map(d => `<div class="dev"><input type="checkbox" data-show="${esc(d.id)}" ${devVisible(d.id) ? 'checked' : ''}><span class="sw" style="background:${colorOf(d.id)}"></span><b>${esc(d.id)}</b>
      <span>${esc(d.label)} <span class="st ${d.qc && d.qc.overall}">${esc(d.qc && d.qc.overall)}</span></span><input type="radio" name="chosen" value="${esc(d.id)}" ${chosen === d.id ? 'checked' : ''} title="cast this device"></div>`).join('');
  h += `<h4>Make a change</h4>
    <div class="field"><label for="from">Change device</label><select id="from">${devs.map(d => `<option ${d.id === (S.from || chosen || 'P2') ? 'selected' : ''}>${esc(d.id)}</option>`).join('')}</select></div>`;
  h += S.mods.map((m, i) => `<div class="modrow"><select data-mcode="${i}">${Object.entries(V).filter(([k, v]) => v.unit !== 'flag').map(([k, v]) => `<option value="${k}" ${m.code === k ? 'selected' : ''}>${k.replace(/_(mm|deg)$/, '')} (${v.unit}, usually ${v.usually})</option>`).join('')}</select>
      <input type="number" step="0.1" min="0.1" data-mamt="${i}" value="${m.amount}"><button data-mdel="${i}" title="remove" ${S.mods.length < 2 ? 'disabled' : ''}>×</button></div>
      <div class="hint">${esc((V[m.code] || {}).text || '')}</div>`).join('');
  h += `<div class="row"><button id="addmod">Add another change</button></div>
    <div class="field"><label>Where</label><span class="stack"><label><input type="radio" name="where" value="lab" ${S.where !== 'bedside' ? 'checked' : ''}> lab (new device)</label>
      <label><input type="radio" name="where" value="bedside" ${S.where === 'bedside' ? 'checked' : ''}> bedside (same device, ground or bent)</label></span></div>
    <div class="field"><label for="note">Note</label><input id="note" placeholder="e.g. tip on the epiglottis at try-in" value="${esc(S.note || '')}"></div>
    <div class="row"><button class="primary" id="adjust" ${dis}>Build the changed device</button></div>${errHTML('changes')}`;
  const cs = r.clinical_settings || {};
  h += `<h4>Tip target <span class="tag clin">clinical choice</span></h4>
    <p class="hint">Set by the team, never by the software. It is used only to flag try-in findings.</p>
    <div class="row"><select id="tiptarget">${Object.entries(c.tip_targets).map(([k, t]) => `<option value="${k}" ${cs.tip_target === k ? 'selected' : ''}>${esc(t)}</option>`).join('')}</select><button id="settarget" ${dis}>Save</button></div>${errHTML('settings')}`;
  const aw = (r.airway || {}).landmarks || {};
  h += `<details class="adv"><summary>Imaging values (CT, MRI, endoscopy)</summary><p class="hint">Points in the design frame, mm, after registering the imaging. Plausibility only, no clinical claim.</p>
    ${Object.entries(S.vocab.airway).map(([k, v]) => `<div class="field"><label title="${esc(v.text)}">${esc(k.replace(/_/g, ' '))}</label><span class="row" style="margin:0"><input data-aw="${k}" placeholder="${v.kind === 'point' ? 'x, y, z' : 'value'}" value="${esc(aw[k] && aw[k].value != null ? [].concat(aw[k].value).join(', ') : '')}" style="width:120px">
      ${aw[k] && aw[k].value != null ? `<span class="hint">${esc(aw[k].source)}</span>` : ''}</span></div>`).join('')}
    <div class="row"><select id="awsource"><option>manual</option><option>ct</option><option>mri</option><option>endoscopy</option></select><button id="saveaw" ${dis}>Save imaging values</button></div>${errHTML('airway')}</details>`;
  return h + f + ap;
}

function checksBody(st, running, f, ap) {
  const c = S.c, r = c.record;
  if (!r) return '<p class="muted">Build the plate first.</p>';
  const chosen = c.review.chosen_device;
  const cols = c.devices.filter(d => d.kind === 'prototype' || (d.id === chosen && CAST_KINDS.includes(d.kind)));
  const names = []; cols.forEach(d => ((d.qc || {}).checks || []).forEach(x => { if (!names.includes(x.check)) names.push(x.check); }));
  const basis = rule => /heuristic/i.test(rule) ? '<span class="tag heur">heuristic</span>' : /tübingen|tuebingen/i.test(rule) ? '<span class="tag pub">Tübingen</span>' : '';
  let h = `<p>Every rule, for each prototype${chosen ? ' and ' + esc(chosen) + ', the device to cast' : ''}. Hover a row for the rule.</p>
    <table><tr><th>Check</th>${cols.map(d => `<th>${esc(d.id)}</th>`).join('')}</tr>
    ${names.map(n => { const rule = (cols.map(d => ((d.qc || {}).checks || []).find(x => x.check === n)).find(Boolean) || {}).rule || '';
      return `<tr title="${esc(rule)}"><td>${esc(n)} ${basis(rule)}</td>${cols.map(d => { const x = ((d.qc || {}).checks || []).find(x => x.check === n); return `<td><span class="st ${x ? x.status : ''}">${x ? x.status : '–'}</span></td>`; }).join('')}</tr>`; }).join('')}</table>`;
  const rc = r.reach_check || {}; const n = (rc.checks || []).length, inside = (rc.checks || []).filter(x => x.within).length;
  h += `<h4>Reach against published infant norms</h4><p class="hint">${esc(rc.purpose || '')}. ${inside} of ${n} inside the envelope. Hover a row for the reasoning.</p>
    <table><tr><th>Measure</th><th class="num">Design</th><th class="num">Norm</th><th class="num">Margin</th></tr>
    ${(rc.checks || []).map(x => `<tr title="${esc(x.why || '')}"><td>${esc(x.check)} <span class="tag heur">${esc(x.label || '')}</span></td><td class="num">${fmt(x.value)}</td><td class="num">${fmt(x.reference)}</td><td class="num"><span class="st ${x.within ? 'pass' : 'warn'}">${x.margin_mm > 0 ? '+' : ''}${fmt(x.margin_mm, 2)}</span></td></tr>`).join('')}</table>`;
  return h + f + ap;
}

function moldBody(st, running, f, ap) {
  const c = S.c, m = c.mold, chosen = c.review.chosen_device, dis = running ? 'disabled' : '';
  const checksOk = (c.steps.find(s => s.step === 'checks') || {}).status === 'approved';
  let h = `<p>Printed tooling for casting the final plate in cold-cure PMMA with the safety wire: a working model, three counter pieces, a cradle and a wire jig.</p>
    <div class="field"><label>Device to cast</label><b>${esc(chosen || 'not chosen')}</b></div>
    <div class="field"><label for="mfth">Final plate (mm)</label><input type="number" id="mfth" step="0.1" min="2" max="4" value="${(c.review.design_options || {}).final_thickness ?? 2.5}"></div>
    <div class="field"><label for="wall">Mold wall (mm)</label><input type="number" id="wall" step="0.5" min="3" max="15" value="6"></div>
    <div class="row"><button class="primary" id="buildmold" ${dis || (checksOk ? '' : 'disabled')}>${m ? 'Rebuild the mold' : 'Build the mold'}</button>${checksOk ? '<span class="hint">about a minute</span>' : '<span class="hint">approve Checks first</span>'}</div>${errHTML('mold')}`;
  if (m) {
    const v = m.validation, w = m.features.safety_wire;
    h += `<h4>Mold</h4><table>
      <tr><td>Cast as</td><td>${esc(m.registered_as || 'not registered')} from ${esc(m.device)}</td></tr>
      <tr><td>Validation</td><td><span class="st ${v.ok ? 'pass' : 'fail'}">${v.ok ? 'ok' : 'FAILED'}</span></td></tr>
      <tr><td>Block</td><td class="num">${esc((v.block_mm || []).join(' × '))} mm</td></tr>
      <tr><td>Cavity / appliance</td><td class="num">${fmt(v.cavity_volume_mm3, 0)} / ${fmt(v.device_volume_mm3, 0)} mm³</td></tr>
      <tr><td>PMMA to mix</td><td class="num">about ${esc(m.pmma_needed_ml)} ml</td></tr>
      <tr><td>Safety wire</td><td class="num">${fmt(w.length_mm)} mm: ${fmt(w.starts_mm_after_border)} mm after the root to ${fmt(w.ends_mm_before_tip)} mm before the tip</td></tr></table>
      <h4>Demold</h4><table><tr><th>Piece</th><th>Free</th></tr>${v.demold.map(d => `<tr><td>${esc(d.piece)}</td><td><span class="st ${d.free ? 'pass' : 'fail'}">${d.free ? 'yes' : 'no'}</span></td></tr>`).join('')}</table>
      <div class="row"><button id="togglemold">${S.showMold ? 'Show the design' : 'Open the mold viewer'}</button></div>
      <h4>Files</h4><div class="files">${c.files.filter(p => p.startsWith('mold/') && /\.(stl|json|md|3mf)$/.test(p)).map(p => `<a href="/api/case/${encodeURIComponent(c.name)}/files/${p}?download=1" download>${esc(p.slice(5))}</a>`).join('')}</div>`;
  }
  return h + f + ap;
}

const fileLinks = (c, paths) => `<div class="files">${paths.filter(p => c.files.includes(p)).map(p => `<a href="/api/case/${encodeURIComponent(c.name)}/files/${p}?download=1" download>${esc(p.replace(/^kit\//, ''))}</a>`).join('')}</div>`;

function kitBody(st, running, f, ap) {
  const c = S.c, r = c.record, k = r && r.kit, b = r && r.chairside_base, dis = running ? 'disabled' : '';
  const plateOk = (c.steps.find(s => s.step === 'plate') || {}).status === 'approved';
  let h = `<p><span class="tag clin">untested research route</span></p>
    <p>Visit 1 makes two things: a <b>try-in kit</b> (a socket base and 15 batons, five lengths by three angles, swapped under endoscopy) and the <b>final base</b> of the worn plate with its casting tooling, cast before visit 2.</p>
    <div class="field"><label for="kfth">Final plate (mm)</label><input type="number" id="kfth" step="0.1" min="2.5" max="3" value="${(b && b.final_plate_thickness) || 2.5}"></div>
    <div class="row"><button class="primary" id="buildkit" ${dis || (plateOk ? '' : 'disabled')}>${k ? 'Rebuild the kit and the base' : 'Build the kit and the final base'}</button><span class="hint">${plateOk ? 'about 30 s: runs <code>pebp kit</code> then <code>pebp chairside --base</code>' : 'approve the Plate step first'}</span></div>${errHTML('kit')}`;
  if (k) {
    const sel = S.kitShow || 'K5';
    h += `<h4>Kit batons</h4><div class="hint">Tip shift against the standard extension: y forward, z up (mm). Pick one to show on the model.</div>
      <table><tr><th></th><th>Baton</th><th>Length / angle</th><th class="num">tip y</th><th class="num">tip z</th><th>QC</th></tr>
      ${k.batons.map(x => `<tr><td><input type="radio" name="kitshow" value="${esc(x.id)}" ${sel === x.id ? 'checked' : ''}></td><td><b>${esc(x.id)}</b></td><td>${esc(x.code)}</td>
        <td class="num">${fmt((x.tip_shift_vs_standard_mm || {}).y_anterior)}</td><td class="num">${fmt((x.tip_shift_vs_standard_mm || {}).z_superior)}</td>
        <td><span class="st ${x.qc && x.qc.overall}">${esc(x.qc && x.qc.overall)}</span></td></tr>`).join('')}</table>
      <h4>Print for the try-in</h4>${fileLinks(c, ['kit/base.stl', ...k.batons.map(x => x.piece_file)])}`;
  }
  h += catalogHTML(c, running, plateOk);
  if (b) {
    h += `<h4>Final base</h4><table>
      <tr><td>Volume</td><td class="num">${fmt(b.volume_mm3, 0)} mm³</td></tr>
      <tr><td>Plate thickness</td><td class="num">${fmt(b.final_plate_thickness)} mm</td></tr>
      <tr><td>Plate over the pocket</td><td class="num">${fmt(b.pocket_roof_mm, 2)} mm</td></tr>
      <tr><td>PMMA to mix</td><td class="num">about ${esc((b.tooling || {}).pmma_needed_ml)} ml</td></tr>
      <tr><td>QC</td><td><span class="st ${b.qc && b.qc.overall}">${esc(b.qc && b.qc.overall)}</span></td></tr></table>
      <h4>Casting tooling for the base</h4>${fileLinks(c, [...Object.values((b.tooling || {}).files || {}), (b.tooling || {}).dir + '/manifest.json', b.file])}`;
  }
  return h + f + ap;
}

// ---------------------------------------------------------------- per-patient catalog of modular versions
const CAT_KEYS = [['cleft_type', 'Cleft type'], ['plate_profile', 'Plate profile'], ['border_relief', 'Border relief'], ['d_length_mm', 'Length step (mm)'], ['d_angle_deg', 'Angle step (°)']];
const CAT_LABEL = { hard_soft: 'hard and soft palate', soft_only: 'soft palate or uvula only', none: 'no cleft', uniform: 'uniform', graded: 'graded', true: 'on', false: 'off' };
function catFilters(c) {
  if (!S.catF || S.catF.case !== c.name) {
    const saved = (c.catalog_choice || {}).filters || {};
    S.catF = { case: c.name, f: Object.assign({ cleft_type: (c.record.cleft_status || {}).value || null }, Object.fromEntries(Object.entries(saved).filter(([, v]) => v !== null && v !== undefined))) };
  }
  return S.catF.f;
}
function catMatch(v, f, skip) {
  for (const [k] of CAT_KEYS) {
    if (k === skip || f[k] === null || f[k] === undefined || f[k] === '') continue;
    if (k === 'cleft_type') { if (!v.cleft_types.includes(f[k])) return false; }
    else if (String(v[k]) !== String(f[k])) return false;
  }
  return true;
}
function catalogHTML(c, running, plateOk) {
  const cat = c.catalog, dis = running ? 'disabled' : '';
  let h = `<h4>Every version for this patient <span class="tag heur">project heuristic</span></h4>
    <p class="hint">Builds every socket base (both cleft groups × uniform or graded × border relief off or on) and the 15 kit batons of each baton variant, so the choices below narrow a finished set. Nothing is chosen for you.</p>
    <div class="row"><button id="buildcat" ${dis || (plateOk ? '' : 'disabled')}>${cat ? 'Rebuild every version' : 'Build every version'}</button><span class="hint">${plateOk ? 'about 30 s: runs <code>pebp catalog --build</code>' : 'approve the Plate step first'}</span></div>${errHTML('catalog')}`;
  if (!cat) return h;
  if (cat.stale) h += `<p class="err">The design changed after this catalog was built. Rebuild it.</p>`;
  const f = catFilters(c);
  const left = cat.versions.filter(v => catMatch(v, f));
  h += `<div class="catf">${CAT_KEYS.map(([k, t]) => {
    const vals = k === 'cleft_type' ? Object.keys(cat.choices.cleft_type) : cat.choices[k];
    const n = val => cat.versions.filter(v => catMatch(v, f, k) && (k === 'cleft_type' ? v.cleft_types.includes(val) : String(v[k]) === String(val))).length;
    return `<div class="field"><label for="cf-${k}">${t} <span class="hint">${esc(cat.choice_basis[k] || '')}</span></label><select id="cf-${k}" data-k="${k}"><option value="">any</option>
      ${vals.map(val => `<option value="${esc(val)}" ${String(f[k]) === String(val) ? 'selected' : ''}>${esc(CAT_LABEL[val] ?? val)} (${n(val)})</option>`).join('')}</select></div>`;
  }).join('')}</div>`;
  h += `<p><b>${left.length}</b> of ${cat.versions.length} versions left.</p>`;
  const show = S.catShow && left.find(v => v.id === S.catShow) ? S.catShow : (left.length === 1 ? left[0].id : null);
  if (left.length <= 30) {
    h += `<table><tr><th></th><th>Version</th><th>Baton</th><th>Plate</th><th>Length / angle</th><th class="num">tip y</th><th class="num">tip z</th><th>QC</th></tr>
      ${left.map(v => `<tr><td><input type="radio" name="catshow" value="${esc(v.id)}" ${show === v.id ? 'checked' : ''}></td><td><b>${esc(v.id)}</b>${v.standard ? ' <span class="hint">standard</span>' : ''}</td>
        <td>${v.baton_variant === 'cleft' ? 'cleft' : 'no-cleft'} ${esc(v.baton)}</td><td>${esc(v.plate_profile)}${v.border_relief ? ', relief' : ''}</td><td>${esc(v.code)}</td>
        <td class="num">${fmt(v.tip_shift_vs_standard_mm.y_anterior)}</td><td class="num">${fmt(v.tip_shift_vs_standard_mm.z_superior)}</td>
        <td><span class="st ${v.qc.overall}">${esc(v.qc.overall)}</span></td></tr>`).join('')}</table>`;
  } else h += `<p class="hint">Choose more to list the versions (30 or fewer).</p>`;
  const sv = show && cat.versions.find(v => v.id === show);
  if (sv) h += `<h4>Print ${esc(sv.id)}</h4>${fileLinks(c, [sv.base_file, sv.baton_file])}`;
  const ch = c.catalog_choice;
  h += `<div class="row"><button id="catrecord" ${dis}>Record this narrowing${sv ? ' and ' + esc(sv.id) : ''}</button>${ch ? `<span class="hint">last recorded by ${esc(ch.by)}${ch.version ? ': ' + esc(ch.version) : ''}</span>` : ''}</div>`;
  return h;
}

function visit2Body(st, running, f, ap) {
  const c = S.c, r = c.record, k = (r && r.kit) || { batons: [] }, w = c.worn, dis = running ? 'disabled' : '';
  const v1 = (c.steps.find(s => s.step === 'kit') || {}).status === 'approved';
  let h = `<p><span class="tag clin">untested research route</span></p>
    <p>At the try-in, swap kit batons under endoscopy and log the session with the fitting form, naming the K baton (<code>pebp fitting log</code>). Then choose the baton that fitted: the software builds the worn plate S<i>n</i> on the cast base and the steps for the chairside pick-up.</p>
    <div class="row"><select id="wornkit">${k.batons.map(x => `<option value="${esc(x.id)}" ${(S.wornKit || (w && w.from) || 'K5') === x.id ? 'selected' : ''}>${esc(x.id)} · ${esc(x.code)} · QC ${esc(x.qc && x.qc.overall)}</option>`).join('')}</select>
      <button class="primary" id="buildworn" ${dis || (v1 ? '' : 'disabled')}>Build the worn plate</button></div>
    ${v1 ? '' : '<p class="hint">approve Visit 1 first</p>'}${errHTML('visit2')}`;
  const fins = (r && r.chairside_finals) || [];
  if (fins.length > 1) h += `<div class="field"><label for="wornsel">Worn plate to review</label><select id="wornsel">${fins.map(e => `<option ${w && w.id === e.id ? 'selected' : ''}>${esc(e.id)}</option>`).join('')}</select></div>`;
  if (w) {
    const wi = w.wire || {};
    h += `<h4>${esc(w.id)} from ${esc(w.from)} (${esc(w.code)})</h4><table>
      <tr><td>Safety wire</td><td class="num">${fmt(wi.length_mm)} mm, ${fmt(wi.end_mm_before_tip)} mm before the tip</td></tr>
      <tr><td>Wire past the joint</td><td class="num">${fmt(wi.crosses_joint_by_mm)} mm</td></tr>
      <tr><td>Pick-up resin</td><td class="num">${fmt(w.pickup_volume_mm3, 0)} mm³</td></tr>
      <tr><td>Plate over the pocket</td><td class="num">${fmt(w.pocket_roof_mm, 2)} mm</td></tr>
      <tr><td>QC</td><td><span class="st ${w.qc && w.qc.overall}">${esc(w.qc && w.qc.overall)}</span></td></tr></table>
      <h4>QC</h4><table>${((w.qc || {}).checks || []).map(x => `<tr title="${esc(x.rule)}"><td>${esc(x.check)}</td><td><span class="st ${x.status}">${x.status}</span></td></tr>`).join('')}</table>
      <h4>Chairside steps</h4><ol class="hint" style="color:var(--ink);padding-left:18px">${(w.steps || []).map(x => `<li>${esc(x)}</li>`).join('')}</ol>
      <h4>Files</h4>${fileLinks(c, [...new Set([w.file, ...Object.values(w.files || {})])])}`;
  }
  return h + f + ap;
}

let shown = null;
function devVisible(id) { if (shown === null || shown.case !== S.caseName) shown = { case: S.caseName, ids: new Set(['P2']) }; return shown.ids.has(id); }

function wire(el, step) {
  if (!el) return;
  el.querySelectorAll('[data-act]').forEach(b => b.onclick = async () => {
    const act = b.dataset.act; S.error['approve:' + step] = null;
    try { await api(`/api/case/${encodeURIComponent(S.caseName)}/review`, { step, action: act, by: reviewer(), override: !!($('#override-' + step) || {}).checked }); await refresh(act === 'approve'); }
    catch (e) { S.error['approve:' + step] = e.message; renderSteps(); }
  });
  const q = s => el.querySelector(s);
  if (step === 'case') {
    const rv = q('#reviewer'); if (rv) rv.onchange = async () => { await api(`/api/case/${encodeURIComponent(S.caseName)}/reviewer`, { by: rv.value }); await refresh(false); };
    const sf = q('#scanfile'); if (sf) sf.onchange = async () => {
      const file = sf.files[0]; if (!file) return; S.error.case = null;
      try { await api(`/api/case/${encodeURIComponent(S.caseName)}/scan`, await file.arrayBuffer(), { 'X-Filename': encodeURIComponent(file.name) }); await refresh(false); }
      catch (e) { S.error.case = e.message; renderSteps(); }
    };
    document.querySelectorAll('input[name=cleft]').forEach(el => el.onchange = () => {
      const by = (q('#reviewer') && q('#reviewer').value.trim()) || reviewer();
      run('cleft', 'cleft', { value: el.value, by });
    });
    const sy = q('#synth'); if (sy) sy.onclick = () => run('case', 'synthetic', { preset: 'prs', no_cleft: !q('#syncleft').checked, asym: 1.0 });
  }
  if (step === 'landmarks' || step === 'plate') {
    const opts = () => {
      const o = Object.assign({}, S.c.review.design_options || {});
      if (q('#backend')) o.backend = q('#backend').value;
      if (step === 'plate') Object.assign(o, {
        plate_profile: (el.querySelector('input[name=prof]:checked') || {}).value || 'uniform', thin_thickness: +q('#thin').value,
        border_relief: q('#relief').checked, plate_thickness: +q('#pth').value, final_thickness: +q('#fth').value,
        extension: q('#ext').value, flange: +q('#flange').value, block_out: +q('#blockout').value, final: q('#final').checked,
        confirm_reset: !!(q('#confirmreset') || {}).checked,
      });
      return o;
    };
    if (q('#backend')) q('#backend').value = (S.c.review.design_options || {}).backend || 'builtin';
    if (q('#detect')) q('#detect').onclick = () => run(step, 'design', opts());
    if (q('#build')) q('#build').onclick = () => run(step, 'design', opts());
    el.querySelectorAll('input[name=prof]').forEach(x => x.onchange = () => el.querySelectorAll('.choice').forEach(ch => ch.classList.toggle('selected', !!ch.querySelector('input:checked'))));
    if (q('#relief')) q('#relief').onchange = () => q('#relief').closest('.choice').classList.toggle('selected', q('#relief').checked);
  }
  if (step === 'changes') {
    el.querySelectorAll('[data-show]').forEach(x => x.onchange = () => { x.checked ? shown.ids.add(x.dataset.show) : shown.ids.delete(x.dataset.show); renderView(); });
    el.querySelectorAll('input[name=chosen]').forEach(x => x.onchange = async () => {
      try { await api(`/api/case/${encodeURIComponent(S.caseName)}/choose`, { device: x.value, by: reviewer() }); await refresh(false); } catch (e) { S.error.changes = e.message; renderSteps(); }
    });
    const keep = () => { S.from = q('#from').value; S.where = (el.querySelector('input[name=where]:checked') || {}).value; S.note = q('#note').value; };
    el.querySelectorAll('[data-mcode]').forEach(x => x.onchange = () => { keep(); S.mods[+x.dataset.mcode].code = x.value; renderSteps(); });
    el.querySelectorAll('[data-mamt]').forEach(x => x.onchange = () => { S.mods[+x.dataset.mamt].amount = +x.value; });
    el.querySelectorAll('[data-mdel]').forEach(x => x.onclick = () => { keep(); S.mods.splice(+x.dataset.mdel, 1); renderSteps(); });
    q('#addmod').onclick = () => { keep(); S.mods.push({ code: 'curve_deg', amount: 10 }); renderSteps(); };
    q('#adjust').onclick = async () => {
      keep(); const before = S.c.devices.length;
      await run('changes', 'adjust', { from: S.from, mods: S.mods, where: S.where || 'lab', note: S.note });
      if (S.c.devices.length > before) { const nd = S.c.devices[S.c.devices.length - 1]; if (nd.kind === 'adjusted') { devVisible(nd.id); shown.ids.add(nd.id); S.note = ''; renderSteps(); renderView(); } }
    };
    q('#settarget').onclick = () => run('settings', 'settings', { tip_target: q('#tiptarget').value, by: reviewer() });
    q('#saveaw').onclick = async () => {
      const aw = (S.c.record.airway || {}).landmarks || {};
      for (const x of el.querySelectorAll('[data-aw]')) {
        const k = x.dataset.aw, cur = aw[k] && aw[k].value != null ? [].concat(aw[k].value).join(', ') : '';
        if (x.value.trim() === cur) continue;
        await run('airway', 'airway', { name: k, value: x.value.trim(), source: q('#awsource').value });
        if (S.error.airway) break;
      }
    };
  }
  if (step === 'plate') el.querySelectorAll('input[name=route]').forEach(x => x.onchange = () => run('route', 'route', { route: x.value, by: reviewer() }));
  if (step === 'kit') {
    const b = q('#buildkit'); if (b) b.onclick = () => run('kit', 'kit', { final_thickness: +q('#kfth').value });
    el.querySelectorAll('input[name=kitshow]').forEach(x => x.onchange = () => { S.kitShow = x.value; renderView(); });
    const bc = q('#buildcat'); if (bc) bc.onclick = () => run('catalog', 'catalog', {});
    el.querySelectorAll('.catf select').forEach(x => x.onchange = () => {
      const f = catFilters(S.c); f[x.dataset.k] = x.value === '' ? null : x.value; S.catShow = null; renderSteps(); renderView();
    });
    el.querySelectorAll('input[name=catshow]').forEach(x => x.onchange = () => { S.catShow = x.value; renderSteps(); renderView(); });
    const cr = q('#catrecord'); if (cr) cr.onclick = () => {
      const f = catFilters(S.c); const left = S.c.catalog.versions.filter(v => catMatch(v, f));
      const version = S.catShow || (left.length === 1 ? left[0].id : null);
      run('catalog', 'catalog_choice', { by: reviewer(), filters: Object.fromEntries(CAT_KEYS.map(([k]) => [k, f[k] ?? null])), version });
    };
  }
  if (step === 'visit2') {
    const b = q('#buildworn'); if (b) b.onclick = () => { S.wornKit = q('#wornkit').value; run('visit2', 'worn', { kit: S.wornKit }); };
    const ws = q('#wornsel'); if (ws) ws.onchange = () => run('visit2', 'choose_worn', { by: reviewer(), worn: ws.value });
  }
  if (step === 'mold') {
    const b = q('#buildmold'); if (b) b.onclick = () => run('mold', 'mold', { final_thickness: +q('#mfth').value, wall: +q('#wall').value });
    const t = q('#togglemold'); if (t) t.onclick = () => { S.showMold = !S.showMold; renderSteps(); renderView(); };
  }
}

// what the model shows on each step
function renderView() {
  const c = S.c, frame = $('#moldframe'), empty = $('#empty');
  frame.hidden = true; canvas.hidden = false; empty.hidden = true; $('#viewbar').hidden = false; $('#legend').hidden = false;
  if (!c) { root.clear(); $('#layers').innerHTML = ''; empty.hidden = false; empty.textContent = 'Make or open a case to begin.'; return; }
  const r = c.record, stamp = r ? r.created : 'raw';
  const scanO = { file: 'scan_oriented.stl', name: 'Scan (design frame)', color: COLORS.scan, stamp };
  if (S.open === 'mold' && S.showMold && c.mold) {
    canvas.hidden = true; frame.hidden = false; $('#layers').innerHTML = ''; $('#viewbar').hidden = true; $('#legend').hidden = true;
    const src = `/api/case/${encodeURIComponent(c.name)}/files/mold/mold_viewer.html`; if (frame.getAttribute('src') !== src) frame.src = src; return;
  }
  if (!c.has_scan) { root.clear(); $('#layers').innerHTML = ''; empty.hidden = false; empty.textContent = 'No scan yet. Add one on the Case step.'; return; }
  if (!r || S.open === 'case') { showLayers([{ file: 'scan.stl', name: 'Scan as imported', color: COLORS.scan, stamp: c.meta.scan_sha256 || 'raw' }]); return; }
  const devs = Object.fromEntries(c.devices.map(d => [d.id, d]));
  const devLayer = (id, opacity = 1) => devs[id] && { file: devs[id].file, name: `${id}${devs[id].label ? ' · ' + devs[id].label : ''}`, color: colorOf(id), opacity, stamp: stamp + id + (devs[id].qc ? JSON.stringify(devs[id].tip) : '') };
  api(`/api/case/${encodeURIComponent(c.name)}/overlay`).then(ov => {
    const extra = [];
    if (S.open === 'landmarks') {
      const OFF = { 'apex': [0, 4], 'incisive papilla': [0, -3.5], 'right tuberosity': [5, -3], 'left tuberosity': [-5, -3] };
      (ov.landmarks || []).forEach(l => { extra.push(sphere(l.p, '#d62d6b', 0.8)); extra.push(label(l.name, l.p, '#9a1d4c', ...(OFF[l.name] || [0, 0]))); });
      if (ov.outline) extra.push(polyline(ov.outline.map(p => [p[0], p[1], 0.2]), '#2f6fed', true));
      const tr = ov.landmarks.find(l => l.name === 'right tuberosity'), tl = ov.landmarks.find(l => l.name === 'left tuberosity');
      if (tr && tl) extra.push(polyline([tr.p, tl.p], '#d62d6b'));
      showLayers([{ ...scanO, opacity: 1 }], extra);
    } else if (S.open === 'plate') {
      showLayers([{ ...scanO, opacity: 0.45 }, { file: 'base_plate.stl', name: 'Base plate', color: COLORS.plate, stamp }, devLayer('P2', 1) && { ...devLayer('P2'), on: false }].filter(Boolean), extra);
    } else if (S.open === 'changes') {
      devVisible('P2');
      const ls = [{ ...scanO, opacity: 0.35 }]; c.devices.filter(d => d.kind !== 'cast' && shown.ids.has(d.id)).forEach(d => ls.push(devLayer(d.id, shown.ids.size > 1 ? 0.85 : 1)));
      Object.entries(ov.tips || {}).forEach(([id, p]) => { if (shown.ids.has(id)) extra.push(sphere(p, colorOf(id), 0.7)); });
      (ov.airway || []).filter(a => !['pns'].includes(a.name)).forEach(a => { extra.push(sphere(a.p, '#7b2cbf', 0.9)); extra.push(label(a.name, a.p, '#5a189a')); });
      showLayers(ls, extra);
    } else if (S.open === 'kit') {
      const k = (r.kit || {}).batons || []; const sel = devs[S.kitShow || 'K5'] || devs[(k[0] || {}).id];
      const ls = [{ ...scanO, opacity: 0.35 }];
      const cat = c.catalog; const cv = cat && S.catShow && cat.versions.find(v => v.id === S.catShow);
      if (cv) {
        ls.push({ file: cv.base_file, name: `${cv.base} base (catalog)`, color: COLORS.base, stamp: cat.time });
        ls.push({ file: cv.baton_file, name: `${cv.id} baton (catalog)`, color: COLORS.K, stamp: cat.time });
        extra.push(sphere(cv.tip, COLORS.K, 0.7));
        showLayers(ls, extra); return;
      }
      if (sel) ls.push({ file: sel.file, name: `${sel.id} · ${sel.code} on the try-in base`, color: COLORS.K, stamp: stamp + JSON.stringify(sel.tip) });
      if (r.chairside_base) ls.push({ file: r.chairside_base.file, name: 'Final base (worn plate)', color: COLORS.base, stamp: r.chairside_base.sha256, on: false });
      if (sel && ov.tips && ov.tips[sel.id]) extra.push(sphere(ov.tips[sel.id], COLORS.K, 0.7));
      showLayers(ls, extra);
    } else if (S.open === 'visit2') {
      const w = c.worn; const ls = [{ ...scanO, opacity: 0.35 }];
      if (w) { ls.push({ file: w.file, name: `${w.id} · worn plate from ${w.from}`, color: COLORS.S, stamp: w.sha256, opacity: 0.8 });
        if ((w.files || {}).safety_wire) ls.push({ file: w.files.safety_wire, name: 'Safety wire', color: COLORS.wire, stamp: w.sha256 }); }
      showLayers(ls, extra);
    } else if (S.open === 'checks' || S.open === 'mold') {
      const id = c.review.chosen_device || 'P2'; const cast = c.devices.filter(d => d.kind === 'cast').slice(-1)[0];
      const ls = [{ ...scanO, opacity: 0.35 }, devLayer(id)]; if (S.open === 'mold' && cast) ls.push({ ...devLayer(cast.id), on: true });
      if (ov.tips && ov.tips[id]) extra.push(sphere(ov.tips[id], colorOf(id), 0.7));
      showLayers(ls.filter(Boolean), extra);
    }
  });
}

// ---------------------------------------------------------------- new case
$('#reveal').onclick = async () => {
  if (!S.caseName) return;
  try { const j = await api(`/api/case/${encodeURIComponent(S.caseName)}/reveal`, {}); if (!j.ok) alertLine(j.message); }
  catch (e) { alertLine(e.message); }
};
function alertLine(msg) { $('#jobline').innerHTML = `<b>${esc(msg)}</b>`; }
$('#newcase').onclick = () => { $('#newname').value = ''; $('#newdlg').showModal(); };
$('#newdlg').addEventListener('close', async () => {
  if ($('#newdlg').returnValue !== 'ok') return;
  try { const j = await api('/api/cases', { name: $('#newname').value.trim() }); await loadCases(j.name); }
  catch (e) { alertBox(e.message); }
});
function alertBox(msg) { $('#jobline').innerHTML = `<span class="err">${esc(msg)}</span>`; }
$('#casesel').onchange = e => e.target.value && openCase(e.target.value);

(async () => {
  S.vocab = await api('/api/vocabulary');
  await loadCases(localStorageGet('pebp.case'));
})();
