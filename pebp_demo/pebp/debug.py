"""Debug plots: where did the landmarks land on this scan?"""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import trimesh

from .landmarks import find_landmarks


def plot_case(scan_path: str, out_png: str, outline: np.ndarray | None = None, title: str = "", backend: str = "builtin"):
    m = trimesh.load(scan_path, force="mesh")
    if backend == "moldstudio":
        from .moldstudio_bridge import classify_landmarks
        om, lm, hf, _, trimmed = classify_landmarks(m)
        om = trimmed
    else:
        om, lm, hf = find_landmarks(m)
    v = om.vertices
    fig, ax = plt.subplots(1, 3, figsize=(18, 6))
    sc = ax[0].scatter(v[:, 0], v[:, 1], c=v[:, 2], s=1, cmap="viridis", vmin=np.percentile(v[:, 2], 1), vmax=np.percentile(v[:, 2], 99))
    ax[0].plot(lm.crest_ring[:, 0], lm.crest_ring[:, 1], "r.-", lw=1, ms=3, label="crest ring")
    ax[0].plot(*lm.apex[:2], "k*", ms=12, label="apex"); ax[0].plot(*lm.tuberosity_l[:2], "ks", ms=8); ax[0].plot(*lm.tuberosity_r[:2], "ks", ms=8, label="tuberosities")
    polys = lm.cleft_polygon if isinstance(lm.cleft_polygon, list) else ([lm.cleft_polygon] if lm.cleft_polygon is not None else [])
    for i, q in enumerate(polys):
        ax[0].plot(q[:, 0], q[:, 1], "m-", lw=1.5, label="cleft / block-out" if i == 0 else None)
    if outline is not None:
        ax[0].plot(outline[:, 0], outline[:, 1], "c-", lw=1.5, label="plate outline")
    ax[0].set_aspect("equal"); ax[0].set_title("top view (x patient-right, y anterior), colour = z"); ax[0].legend(fontsize=8); plt.colorbar(sc, ax=ax[0])
    mid = v[np.abs(v[:, 0]) < 1.5]
    ax[1].scatter(mid[:, 1], mid[:, 2], s=2, c="gray", label="|x|<1.5 mm")
    ax[1].plot(lm.crest_ring[:, 1], lm.crest_ring[:, 2], "r.", ms=3)
    ax[1].axhline(0, color="k", lw=0.5); ax[1].set_aspect("equal"); ax[1].set_title("sagittal midline (y, z)"); ax[1].set_xlabel("y anterior ->"); ax[1].set_ylabel("z superior ->")
    band = v[np.abs(v[:, 1] - 0.5 * (lm.tuberosity_l[1] + lm.tuberosity_r[1]) - 8.0) < 1.5]
    ax[2].scatter(band[:, 0], band[:, 2], s=2, c="gray"); ax[2].plot(lm.crest_ring[:, 0], lm.crest_ring[:, 2], "r.", ms=3)
    ax[2].axhline(0, color="k", lw=0.5); ax[2].set_aspect("equal"); ax[2].set_title("coronal slice 8 mm anterior of tuberosities (x, z)")
    fig.suptitle(f"{title or Path(scan_path).name}  | arch depth {lm.to_json()['arch_depth_mm']} mm, width {lm.to_json()['intertuberosity_width_mm']} mm, cleft {lm.cleft_present}")
    fig.tight_layout(); fig.savefig(out_png, dpi=90); plt.close(fig)
    return lm


if __name__ == "__main__":
    backend = "builtin"
    for p in sys.argv[1:]:
        if p.startswith("--backend="):
            backend = p.split("=", 1)[1]; continue
        out = Path("out/debug") ; out.mkdir(parents=True, exist_ok=True)
        png = out / (Path(p).stem.replace(" ", "_") + f"_{backend}.png")
        try:
            plot_case(p, str(png), backend=backend)
            print("wrote", png)
        except Exception as e:
            print("FAILED", p, repr(e))
