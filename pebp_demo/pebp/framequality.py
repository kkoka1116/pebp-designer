"""Endoscopy frame-quality filter: drop unusable frames before any measurement.

The first stage of the planned endoscopy pipeline (open-data report, experiment 5): every frame of a
fitting video is sorted into one of four classes, and only frames called *informative* go on to the
landmark detector. The class scheme is the one used by the public laryngoscopy frame sets
(informative / blurred / saliva-or-specular / underexposed).

The baseline is deliberately small and dependency-free (numpy + scipy + Pillow): 24 hand-made image
features (brightness, sharpness, specular highlights, colour, texture) and a class-weighted
multinomial logistic regression. The model is saved as plain JSON, never pickle.

Training data:
  * the IBSU-1432 laryngoscopy frame set on Zenodo (doi 10.5281/zenodo.7929648, record licence read
    from Zenodo at download time and stored in SOURCE.json), fetched with `pebp frames fetch`
    into pebp_demo/data/ (git-ignored, never committed);
  * synthetic frames from `synthetic_frame()`, which exist only to test the code. Scores on them say
    nothing about real endoscopy.

Limits (project heuristic / inference, see docs/FRAME_QUALITY_FILTER.md): adult frames, narrow-band
imaging, no infants, no Robin sequence, no plate in view, no landmarks. A frame-quality call is a data
filter, not a clinical finding.

Frames from clinical fitting videos are patient data. `classify` runs locally and its CSV output is
patient data too.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
import tarfile
import time
import urllib.request
import zipfile
from pathlib import Path

import numpy as np
from scipy import ndimage
from scipy.optimize import minimize

CLASSES = ("informative", "blurred", "saliva_specular", "underexposed")
MODEL_VERSION = 1
SIZE = 160  # frames are resized to SIZE x SIZE before features are taken
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}

ZENODO_RECORD = "7929648"
ZENODO_DOI = "10.5281/zenodo.7929648"
ZENODO_API = "https://zenodo.org/api/records/{rid}"

# Folder or file-name tokens that name a class. Single letters are the short codes the public frame
# sets use for their class folders (I, B, S, U).
_ALIASES = {
    "informative": "informative", "info": "informative", "inf": "informative", "i": "informative", "good": "informative",
    "blurred": "blurred", "blurry": "blurred", "blur": "blurred", "b": "blurred",
    "saliva_specular": "saliva_specular", "saliva": "saliva_specular", "specular": "saliva_specular",
    "salivaspecular": "saliva_specular", "saliva_and_specular": "saliva_specular", "specularity": "saliva_specular",
    "reflections": "saliva_specular", "s": "saliva_specular",
    "underexposed": "underexposed", "under_exposed": "underexposed", "underexposure": "underexposed",
    "dark": "underexposed", "u": "underexposed",
}

FEATURES = (
    "y_mean", "y_std", "y_p05", "y_p50", "y_p95", "frac_dark", "frac_very_dark", "frac_bright",
    "frac_specular", "specular_largest", "specular_blobs", "log_lap_var", "lap_var_norm", "lap_var_valid",
    "log_tenengrad", "edge_density", "hf_ratio", "y_entropy", "sat_mean", "sat_std",
    "r_mean", "g_mean", "b_mean", "rb_ratio",
)


# ---------------------------------------------------------------- features

def _norm(s: str) -> str:
    return re.sub(r"[\s\-]+", "_", s.strip().lower())


def label_from_path(path: Path, root: Path | None = None) -> str | None:
    """Class from the nearest folder name that names one, else from a file-name token."""
    parts = list(path.relative_to(root).parts[:-1] if root else path.parts[:-1])
    for p in reversed(parts):
        n = _norm(p)
        if n in _ALIASES:
            return _ALIASES[n]
        for tok in re.split(r"[_.]", n):
            if len(tok) > 1 and tok in _ALIASES:
                return _ALIASES[tok]
    stem = _norm(path.stem)
    for tok in re.split(r"[_.\d]+", stem):
        if len(tok) > 1 and tok in _ALIASES:
            return _ALIASES[tok]
    m = re.match(r"^([ibsu])(?:[_\-]?\d)", stem)  # e.g. B_0012.png, s012.png
    return _ALIASES[m.group(1)] if m else None


def load_image(path: Path, size: int = SIZE) -> np.ndarray:
    from PIL import Image
    with Image.open(path) as im:
        im = im.convert("RGB").resize((size, size), Image.BILINEAR)
        return np.asarray(im, dtype=np.float64) / 255.0


def features(img: np.ndarray) -> np.ndarray:
    """24 features of an RGB frame with values in 0..1 (see FEATURES for the order)."""
    img = np.clip(np.asarray(img, dtype=np.float64), 0.0, 1.0)
    r, g, b = img[..., 0], img[..., 1], img[..., 2]
    y = 0.299 * r + 0.587 * g + 0.114 * b
    mx, mn = img.max(-1), img.min(-1)
    sat = np.where(mx > 1e-6, (mx - mn) / np.maximum(mx, 1e-6), 0.0)

    p05, p50, p95 = np.percentile(y, [5, 50, 95])
    spec = (mx > 0.90) & (sat < 0.25)
    lab, nblob = ndimage.label(spec)
    largest = (np.bincount(lab.ravel())[1:].max() / y.size) if nblob else 0.0

    lap = ndimage.laplace(y)
    lap_var = lap.var()
    valid = (y > 0.08) & ~ndimage.binary_dilation(spec, iterations=2)
    lap_valid = lap[valid].var() / max(y[valid].var(), 1e-6) if valid.sum() > 50 else 0.0
    gx, gy = ndimage.sobel(y, 1), ndimage.sobel(y, 0)
    grad = np.hypot(gx, gy)

    F = np.abs(np.fft.fftshift(np.fft.fft2(y - y.mean()))) ** 2
    h, w = y.shape
    yy, xx = np.mgrid[:h, :w]
    rad = np.hypot((yy - h / 2) / (h / 2), (xx - w / 2) / (w / 2))
    hf = F[rad > 0.25].sum() / max(F.sum(), 1e-12)

    hist, _ = np.histogram(y, bins=32, range=(0, 1))
    pr = hist[hist > 0] / y.size
    ent = float(-(pr * np.log2(pr)).sum())

    return np.array([
        y.mean(), y.std(), p05, p50, p95, (y < 0.08).mean(), (y < 0.03).mean(), (y > 0.85).mean(),
        spec.mean(), largest, np.log1p(nblob), np.log1p(1e4 * lap_var), lap_var / max(y.var(), 1e-6), lap_valid,
        np.log1p(1e3 * (grad ** 2).mean()), (grad > 0.25).mean(), hf, ent, sat.mean(), sat.std(),
        r.mean(), g.mean(), b.mean(), r.mean() / max(b.mean(), 1e-3),
    ])


# ---------------------------------------------------------------- synthetic frames (code tests only)

def _tissue(rng: np.random.Generator, n: int) -> np.ndarray:
    """An NBI-like mucosal texture: smooth tone, a curvilinear vessel net and fine grain."""
    yy, xx = np.mgrid[:n, :n] / n
    tone = 0.45 + 0.15 * np.sin(2 * np.pi * (rng.uniform(0.3, 1.2) * xx + rng.uniform(0, 1))) * np.cos(2 * np.pi * rng.uniform(0.3, 1.0) * yy)
    field = ndimage.gaussian_filter(rng.normal(size=(n, n)), rng.uniform(2.5, 5.0))
    field /= field.std() + 1e-9
    vessels = np.exp(-(field / rng.uniform(0.08, 0.16)) ** 2)  # thin ridges along the zero set
    fine = ndimage.gaussian_filter(rng.normal(size=(n, n)), 0.7)
    base = np.clip(tone - 0.30 * vessels + 0.03 * fine, 0, 1)
    hue = np.array([rng.uniform(0.55, 0.75), rng.uniform(0.55, 0.75), rng.uniform(0.45, 0.65)])  # NBI: brown/teal
    img = base[..., None] * hue / hue.max()
    img[..., 2] += 0.12 * vessels  # vessels read teal-brown in NBI
    return np.clip(img, 0, 1)


def _vignette(img: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    n = img.shape[0]
    yy, xx = np.mgrid[:n, :n] / (n / 2) - 1
    rad = np.hypot(xx, yy)
    r0 = rng.uniform(1.0, 1.25)
    return img * np.clip((r0 - rad) / 0.15, 0, 1)[..., None]


def synthetic_frame(cls: str, rng: np.random.Generator, n: int = SIZE) -> np.ndarray:
    """One synthetic frame of class `cls`. For testing the code only; not a model of real endoscopy."""
    img = _tissue(rng, n)
    if rng.random() < 0.5:  # informative frames may still carry a few small highlights
        for _ in range(rng.integers(1, 3)):
            cy, cx = rng.uniform(0.2, 0.8, 2) * n
            yy, xx = np.mgrid[:n, :n]
            img = np.maximum(img, np.exp(-((yy - cy) ** 2 + (xx - cx) ** 2) / (2 * rng.uniform(0.8, 1.6) ** 2))[..., None])
    if cls == "blurred":
        img = ndimage.gaussian_filter(img, (rng.uniform(1.8, 4.0), rng.uniform(1.8, 4.0), 0))
        if rng.random() < 0.5:
            img = ndimage.uniform_filter1d(img, int(rng.integers(5, 12)), axis=int(rng.integers(0, 2)))
    elif cls == "saliva_specular":
        yy, xx = np.mgrid[:n, :n]
        film = np.zeros((n, n))
        for _ in range(rng.integers(4, 12)):
            cy, cx = rng.uniform(0.1, 0.9, 2) * n
            sy, sx = rng.uniform(2, 12, 2)
            film = np.maximum(film, np.exp(-((yy - cy) ** 2 / (2 * sy ** 2) + (xx - cx) ** 2 / (2 * sx ** 2))))
        film = np.clip(1.6 * film, 0, 1)
        haze = ndimage.gaussian_filter(rng.random((n, n)), 6) * rng.uniform(0.2, 0.5)
        img = img * (1 - film[..., None]) + film[..., None]
        img = np.clip(img + haze[..., None] * (1 - img), 0, 1)
    elif cls == "underexposed":
        img = rng.uniform(0.04, 0.16) * img ** rng.uniform(1.1, 1.6)
    elif cls != "informative":
        raise ValueError(cls)
    img = _vignette(img, rng)
    return np.clip(img + rng.normal(0, 0.01, img.shape), 0, 1)


def write_synthetic_set(out: Path, n_per_class: int, seed: int = 0, short_names: bool = False) -> dict:
    """Write PNGs into one folder per class (I/B/S/U with short_names), plus a fake 'video' group per 10 frames."""
    from PIL import Image
    rng = np.random.default_rng(seed)
    short = {"informative": "I", "blurred": "B", "saliva_specular": "S", "underexposed": "U"}
    counts = {}
    for cls in CLASSES:
        d = Path(out) / (short[cls] if short_names else cls)
        d.mkdir(parents=True, exist_ok=True)
        for k in range(n_per_class):
            a = (synthetic_frame(cls, rng) * 255).round().astype(np.uint8)
            Image.fromarray(a).save(d / f"vid{k // 10:02d}_{k:04d}.png")
        counts[cls] = n_per_class
    (Path(out) / "SYNTHETIC.txt").write_text("Synthetic frames for testing the frame-quality code only. Not endoscopy.\n", encoding="utf-8")
    return counts


# ---------------------------------------------------------------- dataset

def scan_dataset(root: Path, group_regex: str | None = None) -> dict:
    """Labelled image paths under root. Groups (e.g. video or patient) come from group_regex's first match."""
    root = Path(root)
    paths, labels, groups, skipped = [], [], [], []
    rx = re.compile(group_regex) if group_regex else None
    for p in sorted(root.rglob("*")):
        if p.suffix.lower() not in IMAGE_EXT or not p.is_file() or any(s.startswith(".") or s == "__MACOSX" for s in p.parts):
            continue
        lab = label_from_path(p, root)
        if lab is None:
            skipped.append(str(p.relative_to(root)))
            continue
        paths.append(p); labels.append(CLASSES.index(lab))
        if rx:
            m = rx.search(str(p.relative_to(root)))
            groups.append(m.group(1) if m and m.groups() else (m.group(0) if m else "?"))
    return {"paths": paths, "y": np.array(labels, dtype=int), "groups": groups or None, "skipped": skipped}


def feature_matrix(paths, size: int = SIZE) -> np.ndarray:
    return np.vstack([features(load_image(p, size)) for p in paths]) if len(paths) else np.zeros((0, len(FEATURES)))


# ---------------------------------------------------------------- classifier

def _softmax(Z: np.ndarray) -> np.ndarray:
    Z = Z - Z.max(1, keepdims=True)
    P = np.exp(Z)
    return P / P.sum(1, keepdims=True)


def train(X: np.ndarray, y: np.ndarray, l2: float = 1e-2, threshold: float = 0.5, provenance: dict | None = None) -> dict:
    """Class-balanced multinomial logistic regression on standardised features. Returns a JSON-able model."""
    k, (n, d) = len(CLASSES), X.shape
    mu, sd = X.mean(0), X.std(0)
    sd[sd < 1e-9] = 1.0
    Xs = (X - mu) / sd
    cnt = np.bincount(y, minlength=k).astype(float)
    w = np.where(cnt[y] > 0, n / (k * np.maximum(cnt[y], 1)), 0.0)
    Y = np.eye(k)[y]

    def f(theta):
        W, b = theta[:d * k].reshape(d, k), theta[d * k:]
        P = _softmax(Xs @ W + b)
        loss = -(w * np.log(P[np.arange(n), y] + 1e-12)).sum() / w.sum() + 0.5 * l2 * (W ** 2).sum()
        G = (P - Y) * w[:, None] / w.sum()
        return loss, np.concatenate([(Xs.T @ G + l2 * W).ravel(), G.sum(0)])

    res = minimize(f, np.zeros(d * k + k), jac=True, method="L-BFGS-B", options={"maxiter": 1000})
    return {
        "kind": "pebp.framequality.logreg", "version": MODEL_VERSION, "classes": list(CLASSES), "features": list(FEATURES),
        "image_size": SIZE, "mean": mu.tolist(), "std": sd.tolist(),
        "W": res.x[:d * k].reshape(d, k).tolist(), "b": res.x[d * k:].tolist(), "l2": l2,
        "keep_threshold": threshold, "train_counts": dict(zip(CLASSES, cnt.astype(int).tolist())),
        "optimizer": {"converged": bool(res.success), "iterations": int(res.nit), "loss": float(res.fun)},
        "provenance": provenance or {},
        "note": "Baseline frame-quality filter. Data filter only, no clinical meaning.",
    }


def predict_proba(model: dict, X: np.ndarray) -> np.ndarray:
    if list(model.get("features", [])) != list(FEATURES):
        raise ValueError("model was trained on a different feature set")
    Xs = (X - np.array(model["mean"])) / np.array(model["std"])
    return _softmax(Xs @ np.array(model["W"]) + np.array(model["b"]))


def save_model(model: dict, path: Path):
    Path(path).write_text(json.dumps(model, indent=1), encoding="utf-8")


def load_model(path: Path) -> dict:
    m = json.loads(Path(path).read_text(encoding="utf-8"))
    if m.get("kind") != "pebp.framequality.logreg":
        raise ValueError(f"{path} is not a frame-quality model")
    return m


# ---------------------------------------------------------------- evaluation

def folds(y: np.ndarray, k: int, seed: int = 0, groups=None) -> np.ndarray:
    """Fold index per sample: stratified, and grouped (no group split across folds) when groups are given."""
    rng = np.random.default_rng(seed)
    fold = np.empty(len(y), dtype=int)
    if groups is None:
        for c in np.unique(y):
            idx = rng.permutation(np.flatnonzero(y == c))
            fold[idx] = np.arange(len(idx)) % k
        return fold
    groups = np.asarray(groups)
    ug = np.unique(groups)
    major = {g: np.bincount(y[groups == g]).argmax() for g in ug}
    load = np.zeros((k, len(CLASSES)))
    order = sorted(ug, key=lambda g: (-(groups == g).sum(), rng.random()))
    for g in order:
        m = groups == g
        f = int(np.argmin(load[:, major[g]] + 1e-3 * load.sum(1)))
        fold[m] = f
        load[f] += np.bincount(y[m], minlength=len(CLASSES))
    return fold


def metrics(y: np.ndarray, P: np.ndarray, threshold: float = 0.5) -> dict:
    k = len(CLASSES)
    pred = P.argmax(1)
    cm = np.zeros((k, k), dtype=int)
    np.add.at(cm, (y, pred), 1)
    per = {}
    for i, c in enumerate(CLASSES):
        tp, fp, fn = cm[i, i], cm[:, i].sum() - cm[i, i], cm[i, :].sum() - cm[i, i]
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        per[c] = {"precision": prec, "recall": rec, "f1": 2 * prec * rec / (prec + rec) if prec + rec else 0.0, "n": int(cm[i].sum())}
    keep = P[:, 0] >= threshold
    inf = y == 0
    return {
        "n": int(len(y)), "accuracy": float((pred == y).mean()) if len(y) else 0.0,
        "macro_f1": float(np.mean([per[c]["f1"] for c in CLASSES if per[c]["n"]])) if len(y) else 0.0,
        "per_class": per, "confusion": {"rows_true_cols_pred": list(CLASSES), "matrix": cm.tolist()},
        "keep_filter": {
            "threshold_p_informative": threshold, "kept": int(keep.sum()),
            "precision_kept_are_informative": float(inf[keep].mean()) if keep.any() else 0.0,
            "recall_informative_kept": float(keep[inf].mean()) if inf.any() else 0.0,
            "unusable_kept_fraction": float(keep[~inf].mean()) if (~inf).any() else 0.0,
        },
    }


def cross_validate(X, y, k=5, seed=0, groups=None, l2=1e-2, threshold=0.5) -> dict:
    fold = folds(y, k, seed, groups)
    P = np.zeros((len(y), len(CLASSES)))
    for f in range(k):
        te = fold == f
        if not te.any():
            continue
        P[te] = predict_proba(train(X[~te], y[~te], l2), X[te])
    out = metrics(y, P, threshold)
    out["cv"] = {"folds": k, "seed": seed, "grouped": groups is not None,
                 "n_groups": int(len(set(groups))) if groups is not None else None}
    return out


def plot_confusion(m: dict, path: Path, title: str):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    cm = np.array(m["confusion"]["matrix"], dtype=float)
    rown = cm / np.maximum(cm.sum(1, keepdims=True), 1)
    fig, ax = plt.subplots(figsize=(5.2, 4.4), dpi=150)
    ax.imshow(rown, cmap="Blues", vmin=0, vmax=1)
    labels = ["informative", "blurred", "saliva /\nspecular", "under-\nexposed"]
    ax.set_xticks(range(4), labels, fontsize=8); ax.set_yticks(range(4), labels, fontsize=8)
    ax.set_xlabel("predicted"); ax.set_ylabel("true")
    for i in range(4):
        for j in range(4):
            ax.text(j, i, f"{int(cm[i, j])}\n{rown[i, j]:.0%}", ha="center", va="center", fontsize=7,
                    color="white" if rown[i, j] > 0.55 else "black")
    ax.set_title(title, fontsize=9)
    fig.tight_layout(); fig.savefig(path); plt.close(fig)


def report_md(m: dict, title: str, source: dict) -> str:
    lines = [f"# {title}", "", f"Data: {source.get('description', '?')}", ""]
    if source.get("synthetic"):
        lines += ["**Synthetic frames.** These scores test the code only and say nothing about real endoscopy.", ""]
    if m.get("cv"):
        cv = m["cv"]
        lines += [f"{cv['folds']}-fold cross-validation, seed {cv['seed']}, "
                  + (f"grouped by {cv['n_groups']} groups." if cv["grouped"] else
                     "**not grouped**: frames from the same video can sit in both train and test folds, which inflates scores."), ""]
    lines += [f"Frames: {m['n']} · accuracy {m['accuracy']:.3f} · macro F1 {m['macro_f1']:.3f}", "",
              "| class | n | precision | recall | F1 |", "|---|---|---|---|---|"]
    for c, r in m["per_class"].items():
        lines.append(f"| {c} | {r['n']} | {r['precision']:.3f} | {r['recall']:.3f} | {r['f1']:.3f} |")
    kf = m["keep_filter"]
    lines += ["", f"Keep filter (keep when p(informative) ≥ {kf['threshold_p_informative']}): kept {kf['kept']}; "
              f"{kf['precision_kept_are_informative']:.1%} of kept frames are informative; "
              f"{kf['recall_informative_kept']:.1%} of informative frames kept; "
              f"{kf['unusable_kept_fraction']:.1%} of unusable frames wrongly kept.", "",
              "Limits: adult NBI laryngoscopy (if trained on IBSU-1432), no infants, no Robin sequence, no plate in view, "
              "no landmarks. A data filter, not a clinical finding.", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------- download (Zenodo)

def _md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_extract(archive: Path, dest: Path):
    dest = dest.resolve()
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                target = (dest / info.filename).resolve()
                if dest not in target.parents and target != dest:
                    raise ValueError(f"unsafe path in {archive.name}: {info.filename}")
            z.extractall(dest)
    elif tarfile.is_tarfile(archive):
        with tarfile.open(archive) as t:
            t.extractall(dest, filter="data")  # rejects absolute paths, '..' and links outside dest
    else:
        return False
    return True


def fetch_zenodo(dest: Path, record: str = ZENODO_RECORD, api_url: str | None = None, extract: bool = True) -> dict:
    """Download every file of a Zenodo record into dest/raw, check MD5s, extract archives into dest/frames.

    Uses Zenodo's public REST API with urllib's own User-Agent. Writes dest/SOURCE.json with title,
    creators, DOI, licence and checksums, for attribution. Nothing here is committed to git.
    """
    dest = Path(dest)
    raw, frames = dest / "raw", dest / "frames"
    raw.mkdir(parents=True, exist_ok=True)
    url = api_url or ZENODO_API.format(rid=record)
    with urllib.request.urlopen(url, timeout=60) as r:
        meta = json.loads(r.read().decode())
    md = meta.get("metadata", {})
    lic = md.get("license") or {}
    lic_id = (lic.get("id") if isinstance(lic, dict) else str(lic)) or "unknown"
    files_out = []
    for f in meta.get("files", []):
        key = f.get("key") or f.get("filename")
        if not key or "/" in key or key.startswith("."):
            raise ValueError(f"unexpected file name in record: {key!r}")
        link = (f.get("links") or {}).get("self") or (f.get("links") or {}).get("download") \
            or f"https://zenodo.org/records/{record}/files/{key}?download=1"
        want = str(f.get("checksum", "")).split(":", 1)[-1] or None
        target = raw / key
        if not (target.exists() and want and _md5(target) == want):
            tmp = target.with_suffix(target.suffix + ".part")
            with urllib.request.urlopen(link, timeout=600) as r, open(tmp, "wb") as fh:
                shutil.copyfileobj(r, fh, 1 << 20)
            got = _md5(tmp)
            if want and got != want:
                tmp.unlink()
                raise ValueError(f"MD5 mismatch for {key}: expected {want}, got {got}")
            tmp.replace(target)
        files_out.append({"key": key, "size": target.stat().st_size, "md5": _md5(target)})
        if extract:
            frames.mkdir(exist_ok=True)
            if not _safe_extract(target, frames / Path(key).stem) and target.suffix.lower() in IMAGE_EXT:
                shutil.copy2(target, frames / key)
    src = {
        "doi": md.get("doi") or meta.get("doi") or (ZENODO_DOI if record == ZENODO_RECORD else None),
        "record": record, "title": md.get("title"), "version": md.get("version"),
        "publication_date": md.get("publication_date"),
        "creators": [c.get("name") for c in md.get("creators", [])],
        "license": lic_id, "files": files_out, "fetched_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "commercial_use_ok": not any(t in lic_id.lower() for t in ("nc", "nd")) and lic_id.lower().startswith("cc-by"),
    }
    (dest / "SOURCE.json").write_text(json.dumps(src, indent=1), encoding="utf-8")
    return src


# ---------------------------------------------------------------- classify

def classify_paths(model: dict, paths, out_csv: Path, threshold: float | None = None) -> dict:
    thr = model.get("keep_threshold", 0.5) if threshold is None else threshold
    X = feature_matrix(paths, model.get("image_size", SIZE))
    P = predict_proba(model, X) if len(paths) else np.zeros((0, len(CLASSES)))
    with open(out_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["path", "label", "keep", *[f"p_{c}" for c in CLASSES]])
        for p, row in zip(paths, P):
            w.writerow([str(p), CLASSES[int(row.argmax())], int(row[0] >= thr), *[f"{v:.4f}" for v in row]])
    return {"n": len(paths), "kept": int((P[:, 0] >= thr).sum()) if len(paths) else 0, "threshold": thr,
            "by_label": {c: int((P.argmax(1) == i).sum()) for i, c in enumerate(CLASSES)}}
