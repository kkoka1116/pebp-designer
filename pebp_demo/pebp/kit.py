"""Modular try-in kit: one base plate made for the patient, plus a stock set of slide-in batons.

Today a try-in uses three printed prototypes 2 mm apart (P1-P3). A baton that turns out too long is
ground at the bedside; one that turns out too short means lab work and a second endoscopy
(Aretxabaleta 2025, doi 10.3390/bioengineering12101063). The kit puts more options in the room:

  base     the patient's prototype plate with a socket block at the midline of the posterior border:
           a closed tunnel under the plate, open only at the back
  batons   the published Tübingen extension in a grid of lengths and angles (default 5 x 3 = 15),
           each cut at the plate border and given a plug that slides forward into the tunnel. Every
           baton is the same shape in the attachment frame whatever the patient, so a stock set can be
           printed ahead of time and kept bagged
  lock     one length of dental floss through the side holes of the socket and the plug, tied: it stops
           the plug sliding out, and its tail stays outside the mouth as a tether

Every kit baton K<n> is registered in the design record with its parameters, exactly like an A<n>,
so the one chosen at the try-in goes straight to `pebp mold --device K<n>` (a one-piece cast plate), or,
as a research route, to `pebp chairside --kit K<n>` (a stock wired baton fixed into the worn plate; see
chairside.py). The slide-in kit itself is for the supervised try-in only and is never worn.

Status: project heuristic, untested. The kit's geometry, the floss lock and the grid are this
project's proposal; nothing here has been printed, pull-tested or used.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

import numpy as np
import trimesh

from .extension import ExtensionParams, engrave_number, make_extension, tuebingen_params
from .meshutil import manifold_boolean

LABEL = ("project heuristic: modular try-in kit, untested (no print, pull-off, fracture or cleaning data); "
         "try-in under direct endoscopic supervision only, never worn")


@dataclass
class KitParams:
    variant: str = "cleft"                       # Tübingen extension variant: cleft | nocleft
    lengths: tuple = (-2.0, 0.0, 2.0, 4.0, 6.0)  # mm change in the straight tail (grind-down covers the short side)
    angles: tuple = (-8.0, 0.0, 8.0)             # deg; + swings the tail toward the tongue base (8 deg moves the tip about 2 mm, like P1-P3)
    width: float = 10.4                          # mm; the published width
    # socket and plug, in the attachment frame (y from the posterior border, z from the plate's mid-thickness)
    # 4.5 x 2.5 x 5 mm since 2026-11-21 (was 7 x 2 x 8): same bending section, a smaller footprint so the tunnel
    # stays under the plate on curved palates without lowering the baton (scripts/socket_fit.py)
    plug_width: float = 4.5
    plug_height: float = 2.5
    plug_length: float = 5.0                     # how far the plug runs forward under the plate
    clearance: float = 0.15                      # per side, plug to tunnel
    floor: float = 1.0                           # tunnel floor (tongue side)
    side_wall: float = 1.5
    min_roof: float = 1.0                        # plate left above the tunnel, checked against the scan
    floss_d: float = 1.2                         # floss hole in the socket walls
    floss_d_plug: float = 1.5                    # in the plug (lines up despite the clearance)
    floss_y: float = 2.5                         # mm in front of the border (mid-plug)
    neck_length: float = 4.0                     # ramp from the arm's tongue side down to the plug, behind the border
    prototype_thickness: float = 2.0             # base plate

    def tunnel(self):
        """(x half width, z bottom, z top, y front) of the tunnel relative to (0, border_y, z_mid)."""
        top = -self.prototype_thickness / 2.0
        h = self.plug_height + 2 * self.clearance
        return self.plug_width / 2.0 + self.clearance, top - h, top, self.plug_length + self.clearance

    def boss_bottom(self) -> float:
        return self.tunnel()[1] - self.floor


def library(kp: KitParams) -> list[dict]:
    """The baton grid. Codes read L<length change>/T<angle change>; K<n> numbering runs length-major."""
    base = tuebingen_params(kp.variant)
    out = []
    n = 0
    for dl in kp.lengths:
        for da in kp.angles:
            n += 1
            ep = replace(base, effective_length=base.effective_length + dl, tilt_deg=base.tilt_deg + da,
                         width_base=kp.width, width_tip=kp.width)
            out.append({"id": f"K{n}", "n": n, "code": f"L{dl:+g}/T{da:+g}", "d_length_mm": dl, "d_angle_deg": da,
                        "length_index": kp.lengths.index(dl) + 1, "angle_index": kp.angles.index(da), "params": asdict(ep)})
    return out


def _box(lo, hi) -> trimesh.Trimesh:
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    b = trimesh.creation.box(extents=hi - lo)
    b.apply_translation((lo + hi) / 2.0)
    return b


def _xcyl(y, z, r, half=20.0) -> trimesh.Trimesh:
    c = trimesh.creation.cylinder(radius=r, height=2 * half, sections=24)
    c.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [0, 1, 0]))
    c.apply_translation([0.0, y, z])
    return c


def _hull(points) -> trimesh.Trimesh:
    return trimesh.convex.convex_hull(np.asarray(points, float))


def baton_piece(entry: dict, kp: KitParams, border_y: float = 0.0, z_mid: float = 0.0, mark: bool = True, floss: bool = True) -> trimesh.Trimesh:
    """One kit baton as a printable solid: the extension behind the border, a neck, and the plug.
    With border_y = z_mid = 0 this is the stock part, the same for every patient."""
    ep = ExtensionParams(**entry["params"])
    arm, path, _ = make_extension(ep, border_y, z_mid, f"tuebingen-{kp.variant}")
    keep = _box([-30.0, border_y - 80.0, z_mid - 80.0], [30.0, border_y, z_mid + 40.0])
    arm = manifold_boolean("intersection", [arm, keep])
    hx, zb, zt, yf = kp.tunnel()
    c = kp.clearance
    px, pzb, pzt = hx - c, z_mid + zb + c, z_mid + zt - c
    plug = _box([-px, border_y - 0.5, pzb], [px, border_y + kp.plug_length, pzt])
    arm_bot = z_mid - ep.thickness / 2.0
    y0 = border_y - kp.neck_length
    neck = _hull([[sx * px, border_y, z] for sx in (-1, 1) for z in (pzb, pzt)]
                 + [[sx * px, border_y - 0.01, pzb] for sx in (-1, 1)]
                 + [[sx * px, y0, z] for sx in (-1, 1) for z in (arm_bot + 0.6, arm_bot + 1.2)])
    piece = manifold_boolean("union", [arm, neck, plug])
    if floss:
        piece = manifold_boolean("difference", [piece, _xcyl(border_y + kp.floss_y, z_mid + zb + (zt - zb) / 2.0, kp.floss_d_plug / 2.0)])
    if mark and path is not None:
        # length step as dimples on the tongue side (as P1-P3 are numbered); angle as 0-2 dimples on the plug's underside
        piece = engrave_number(piece, path, entry["length_index"], ep, border_y)
        for k in range(entry["angle_index"]):
            s = trimesh.creation.icosphere(subdivisions=2, radius=0.6)
            s.apply_translation([-2.0 + 4.0 * k, border_y + kp.plug_length - 2.0, pzb])
            piece = manifold_boolean("difference", [piece, s])
    piece.metadata["path"] = path
    return piece


ROOF_MARGIN = 0.25   # mm over min_roof, for the sampling grid
DROP_REVIEW_MM = 1.0  # a lowered attachment beyond this is flagged for review [project heuristic]


def drop_check(drop: float) -> dict:
    from .qc import _check
    return _check("attachment lowered for the tunnel", drop <= DROP_REVIEW_MM, drop,
                  f"socket and baton moved toward the tongue to keep plate over the tunnel; above {DROP_REVIEW_MM} mm the baton "
                  "sits noticeably lower than the one-piece design: review at the try-in (project heuristic)", level="warn")


def socket_drop(hfb, border_y: float, z_mid: float, kp: KitParams) -> float:
    """How far the whole attachment (socket and plug, so every kit baton with it) must move toward the tongue so that
    at least min_roof of plate is left between the tunnel and the scanned surface everywhere over the tunnel.
    0 where the palate is flat enough there (the synthetic palate); on real palates the surface falls away to the
    sides and front of the midline root point that sets z_mid [project heuristic]."""
    hx, zb, zt, yf = kp.tunnel()
    g = np.array([[x, y] for x in np.linspace(-hx, hx, 13) for y in np.linspace(border_y - 1.0, border_y + yf, 13)])
    h = np.asarray(hfb(g), dtype=float)
    h = h[np.isfinite(h)]
    if not len(h):
        return 0.0
    need = float(h.min()) - (kp.min_roof + ROOF_MARGIN)
    return round(max(0.0, (z_mid + zt) - need), 2)


def socket_base(plate: trimesh.Trimesh, hfb, outline, border_y: float, z_mid: float, kp: KitParams, front: float | None = None,
                floss: bool = True):
    """The patient's prototype plate with the socket block. Returns (mesh, info)."""
    from .plate import tissue_clip_solid
    hx, zb, zt, yf = kp.tunnel()
    wx = hx + kp.side_wall
    zfloor = z_mid + kp.boss_bottom()
    y1 = border_y + yf + 1.5 if front is None else front   # front wall of the tunnel (or of a pocket in front of it)
    # block top above the scanned surface over its footprint (the tissue clip trims it), also when the socket was dropped
    hb = np.asarray(hfb(np.array([[x, y] for x in (-wx, 0.0, wx) for y in np.linspace(border_y, y1 + 4.0, 7)])), dtype=float)
    ztop = max(z_mid + 6.0, float(np.nanmax(hb)) + 1.0 if np.isfinite(hb).any() else z_mid + 6.0)
    # block with a ramp in front, so its front edge does not catch the tongue
    boss = _hull([[sx * wx, y, z] for sx in (-1, 1) for y in (border_y, y1) for z in (zfloor, ztop)]
                 + [[sx * wx, y1 + 4.0, z] for sx in (-1, 1) for z in (z_mid + zt, ztop)])
    base = manifold_boolean("union", [plate, boss])
    base = manifold_boolean("difference", [base, tissue_clip_solid(outline, hfb)])
    tunnel = _box([-hx, border_y - 1.0, z_mid + zb], [hx, border_y + yf, z_mid + zt])
    zc = z_mid + zb + (zt - zb) / 2.0
    base = manifold_boolean("difference", [base, tunnel] + ([_xcyl(border_y + kp.floss_y, zc, kp.floss_d / 2.0)] if floss else []))
    # the plate left above the tunnel, from the scan's (blocked) surface
    xs = np.linspace(-hx, hx, 9)
    ys = np.linspace(border_y, border_y + yf, 9)
    g = np.array([[x, y] for x in xs for y in ys])
    roof = hfb(g) - (z_mid + zt)
    i = int(np.argmin(roof))
    info = {"tunnel_mm": {"width": round(2 * hx, 2), "height": round(zt - zb, 2), "depth": round(yf, 2)},
            "block_mm": {"width": round(2 * wx, 2), "below_plate_tongue_surface": round(-(kp.boss_bottom() - (-kp.prototype_thickness / 2)), 2),
                         "length_incl_ramp": round(y1 + 4.0 - border_y, 2)},
            "roof_min_mm": round(float(roof[i]), 2), "roof_min_at_xy": g[i].round(2).tolist(),
            "floss_hole": {"y": round(border_y + kp.floss_y, 2), "z": round(zc, 2), "d_socket": kp.floss_d, "d_plug": kp.floss_d_plug}}
    return base, info


def plug_strength(kp: KitParams, ep: ExtensionParams) -> dict:
    """Section modulus of the plug at the tunnel mouth against the baton's own section (bending toward the
    tongue). A comparison of shapes only: the load and the printed resin's strength are not known."""
    zp = kp.plug_width * kp.plug_height ** 2 / 6.0
    zb = ep.width_base * ep.thickness ** 2 / 6.0
    return {"plug_mm3": round(zp, 2), "baton_mm3": round(zb, 2), "ratio": round(zp / zb, 2)}


def export_stock(out: Path, kp: KitParams) -> dict:
    """The patient-independent stock set: every kit baton in the attachment frame (border at y = 0,
    plate mid-thickness at z = 0), print-ready. Returns the manifest."""
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    rows = []
    for e in library(kp):
        m = baton_piece(e, kp)
        fn = f"{e['id']}_{e['code'].replace('/', '_').replace('+', 'p').replace('-', 'm')}.stl"
        m.export(out / fn)
        rows.append({"id": e["id"], "code": e["code"], "file": fn, "watertight": bool(m.is_watertight),
                     "bodies": int(m.body_count), "volume_mm3": round(float(m.volume), 1)})
    man = {"kind": "pebp-kit-stock", "variant": kp.variant, "params": asdict(kp), "batons": rows, "flag": LABEL,
           "frame": "attachment frame: plate posterior border at y = 0 (y anterior), plate mid-thickness at z = 0 (z superior)",
           "source": "Tübingen standard extension (Aretxabaleta 2025, CC BY 4.0), deformed by the project's skeleton deformer"}
    (out / "kit_stock_manifest.json").write_text(json.dumps(man, indent=2), encoding="utf-8")
    return man


def build(case_dir: Path, kp: KitParams | None = None, stock: bool = True) -> dict:
    """Make the kit for a case: the socket base, every kit baton placed on it (for preview and QC), and
    the monolithic equivalent of each (the shape a cast plate from it would have). Registers K<n>."""
    from . import record as rec
    from .cli import _build_one
    from .context import rebuild
    from .qc import arm_length, overall, _check
    case_dir = Path(case_dir)
    kp = kp or KitParams()
    ctx = rebuild(case_dir, plate_thickness=kp.prototype_thickness)
    record = ctx["rec"]
    mode = record.get("extension_mode", "")
    if mode != f"tuebingen-{kp.variant}":
        if mode.startswith("tuebingen-"):
            kp = replace(kp, variant=mode.replace("tuebingen-", ""))
        else:
            raise ValueError(f"the kit is built from the published extension; this case uses {mode!r}")
    by, zm0 = ctx["border_y"], ctx["z_mid"]
    drop = socket_drop(ctx["hfb"], by, zm0, kp)
    zm = zm0 - drop
    kdir = case_dir / "kit"; (kdir / "batons").mkdir(parents=True, exist_ok=True)
    base, binfo = socket_base(ctx["plate"], ctx["hfb"], ctx["outline"], by, zm, kp)
    base.export(kdir / "base.stl")
    g = {"plate": ctx["plate"], "hfb": ctx["hfb"], "outline": ctx["outline"], "pp": ctx["pp"],
         "lm_json": record["landmarks"], "border_y": by, "z_mid": zm}
    std = record.get("standard")
    tip0 = None
    entries = []
    for e in library(kp):
        ep = ExtensionParams(**e["params"])
        piece = baton_piece(e, kp, by, zm)
        piece.export(kdir / "batons" / f"{e['id']}.stl")
        clash = float(manifold_boolean("intersection", [piece, base]).volume) if piece.is_volume and base.is_volume else None
        mono = _build_one(g, ep, 0, mode, "prototype", std)
        fn = f"kit/{e['id']}_equivalent.stl"
        mono["mesh"].export(case_dir / fn)
        tip = mono["tip"]
        if e["d_length_mm"] == 0 and e["d_angle_deg"] == 0:
            tip0 = tip
        checks = [c for c in mono["qc"]["checks"] if c["check"] not in ("mesh watertight", "single body")]
        checks += [
            _check("kit base watertight", base.is_watertight and base.body_count == 1, f"{bool(base.is_watertight)}, {base.body_count} body",
                   "the socket base is one closed solid for printing"),
            _check("kit baton watertight", piece.is_watertight and piece.body_count == 1, f"{bool(piece.is_watertight)}, {piece.body_count} body",
                   "one closed solid for printing"),
            _check("baton clear of base", clash is not None and clash < 0.05, None if clash is None else round(clash, 3),
                   "plug and base do not overlap when assembled (mm3)"),
            _check("plate above the tunnel", binfo["roof_min_mm"] >= kp.min_roof, binfo["roof_min_mm"],
                   f">= {kp.min_roof} mm of plate between the tunnel and the scanned surface (project heuristic)"),
            drop_check(drop),
        ]
        entries.append({**e, "file": fn, "piece_file": f"kit/batons/{e['id']}.stl", "sha256": rec.file_sha256(case_dir / fn),
                        "tip": tip.round(2).tolist(), "arm_length_mm": round(arm_length(mono["mesh"], mono["path"], ep), 2),
                        "qc": {"checks": checks, "overall": overall(checks)}})
    if tip0 is not None:
        for e in entries:
            d = np.array(e["tip"]) - tip0
            e["tip_shift_vs_standard_mm"] = {"y_anterior": round(float(d[1]), 2), "z_superior": round(float(d[2]), 2)}
    tips = np.array([e["tip"] for e in entries])
    envelope = {"tip_y_range_mm": round(float(np.ptp(tips[:, 1])), 2), "tip_z_range_mm": round(float(np.ptp(tips[:, 2])), 2),
                "prototype_set_tip_y_range_mm": 4.0,
                "note": "P1-P3 move the whole baton 2 mm front to back; the kit moves the tip by length (mostly down) and angle (mostly front to back)"}
    ep_std = tuebingen_params(kp.variant)
    record["kit"] = {"flag": LABEL, "variant": kp.variant, "params": asdict(kp), "base_file": "kit/base.stl",
                     "base_sha256": rec.file_sha256(kdir / "base.stl"), "socket": binfo,
                     "attachment": {"border_y": round(by, 2), "z_mid_plate": round(zm0, 2), "socket_drop_mm": drop, "z_mid": round(zm, 2)},
                     "plug_strength_vs_baton": plug_strength(kp, ep_std), "tip_envelope": envelope, "batons": entries,
                     "lock": "dental floss through the socket and plug holes, tied; tail kept outside the mouth as a tether",
                     "use": "supervised endoscopic try-in only; single patient; the chosen K<n> goes to `pebp mold --device K<n>`",
                     "time": rec.now()}
    record.setdefault("design_choices", {})["try_in_kit"] = {"value": True, "grid": {"lengths_mm": list(kp.lengths), "angles_deg": list(kp.angles)},
                                                            "chosen_by": "designer (pebp kit)", "flag": LABEL, "time": rec.now()}
    from . import airway
    airway.update(record, case_dir, devices=[e["id"] for e in entries])
    rec.save(record, case_dir / "design.json")
    if stock:
        export_stock(kdir / "stock", kp)
    return record["kit"]
