"""Checks for the endoscopy frame-quality filter, on synthetic frames only (no network, no patient data).

    .venv/bin/python scripts/framequality_check.py        (about 30 seconds)

Covers: class labels read from folder names (long and I/B/S/U short forms), the Zenodo downloader
against a local fake record (MD5 check, archive extraction, SOURCE.json, refusal of a bad checksum and
of a zip that writes outside its folder), grouped cross-validation that never splits a video, training
and the JSON model round trip, held-out evaluation, the keep filter and the classify CSV.
Scores on synthetic frames test the code; they say nothing about real endoscopy.
"""
from __future__ import annotations

import csv
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "out" / "framequality_check"
results = []


def check(name, ok, detail):
    results.append((ok, name, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")


def run(*args, ok=True):
    r = subprocess.run([sys.executable, "-m", "pebp", "frames", *map(str, args)], cwd=ROOT, capture_output=True, text=True)
    if ok and r.returncode != 0:
        raise SystemExit(f"pebp frames {' '.join(map(str, args))} failed:\n{r.stdout}\n{r.stderr}")
    return r


def fake_record(zpath: Path, md5: str, name: str = "frames.zip") -> Path:
    meta = {"metadata": {"title": "Synthetic stand-in record", "doi": "10.0000/synthetic", "license": {"id": "cc-by-4.0"},
                         "creators": [{"name": "Synthetic"}]},
            "files": [{"key": name, "checksum": f"md5:{md5}", "links": {"self": zpath.resolve().as_uri()}}]}
    p = zpath.with_name(zpath.stem + "_record.json")
    p.write_text(json.dumps(meta))
    return p


def main():
    from pebp import framequality as fq

    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True)

    # labels from paths
    cases = {"I/a.png": "informative", "B/x_1.png": "blurred", "S/1.png": "saliva_specular", "U/1.png": "underexposed",
             "vid3/Saliva-Specular/f.png": "saliva_specular", "blurred_frames/0001.png": "blurred",
             "all/underexposed_0012.png": "underexposed", "all/B_0012.png": "blurred", "all/frame_0012.png": None}
    got = {k: fq.label_from_path(Path("root") / k, Path("root")) for k in cases}
    check("class from folder or file name", got == cases, f"{sum(got[k] == v for k, v in cases.items())}/{len(cases)}")

    # downloader against a local fake Zenodo record
    src_dir = OUT / "src"
    fq.write_synthetic_set(src_dir, 40, seed=1, short_names=True)
    zpath = OUT / "frames.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        for p in src_dir.rglob("*"):
            if p.is_file():
                z.write(p, p.relative_to(src_dir))
    md5 = hashlib.md5(zpath.read_bytes()).hexdigest()
    dl = OUT / "data"
    r = run("fetch", "--out", dl, "--api-url", fake_record(zpath, md5).resolve().as_uri())
    src = json.loads((dl / "SOURCE.json").read_text())
    ds = fq.scan_dataset(dl / "frames")
    check("fetch: checksum, extract, SOURCE.json", src["files"][0]["md5"] == md5 and src["license"] == "cc-by-4.0"
          and src["commercial_use_ok"] and len(ds["y"]) == 160 and np.bincount(ds["y"]).tolist() == [40] * 4,
          f"{len(ds['y'])} frames, licence {src['license']}")

    bad = run("fetch", "--out", OUT / "bad", "--api-url", fake_record(zpath, "0" * 32).resolve().as_uri(), ok=False)
    check("fetch refuses a bad checksum", bad.returncode != 0 and "MD5 mismatch" in bad.stderr and not (OUT / "bad" / "raw" / "frames.zip").exists(),
          bad.stderr.strip().splitlines()[-1][:90])

    slip = OUT / "slip.zip"
    with zipfile.ZipFile(slip, "w") as z:
        z.writestr("../escaped.png", b"x")
    ev = run("fetch", "--out", OUT / "slip", "--api-url",
             fake_record(slip, hashlib.md5(slip.read_bytes()).hexdigest(), "slip.zip").resolve().as_uri(), ok=False)
    check("fetch refuses a zip that writes outside its folder", ev.returncode != 0 and "unsafe path" in ev.stderr
          and not (OUT / "slip" / "frames" / "escaped.png").exists(), "rejected")

    nc = OUT / "nc_record.json"
    meta = json.loads(fake_record(zpath, md5).read_text()); meta["metadata"]["license"] = {"id": "cc-by-nc-4.0"}
    nc.write_text(json.dumps(meta))
    rn = run("fetch", "--out", OUT / "nc", "--api-url", nc.resolve().as_uri())
    check("fetch warns on a non-commercial licence", "warning" in rn.stderr and "cc-by-nc" in rn.stderr, "warned")

    # grouped folds never split a video
    groups = [p.stem.split("_")[0] + "/" + p.parent.name for p in ds["paths"]]
    fold = fq.folds(ds["y"], 4, 0, groups)
    split = sum(len(set(fold[[g == h for g in groups]])) > 1 for h in set(groups))
    per_fold = [np.bincount(ds["y"][fold == f], minlength=4).min() for f in range(4)]
    check("grouped CV keeps each video in one fold", split == 0 and min(per_fold) > 0, f"{len(set(groups))} videos, min class per fold {min(per_fold)}")

    # train with CV, model round trip
    tr = OUT / "train"
    run("train", dl / "frames", "--out", tr, "--folds", 4, "--group-regex", r"([IBSU]/vid\d+)_")
    m = json.loads((tr / "metrics.json").read_text())
    check("cross-validated baseline on synthetic frames", m["macro_f1"] >= 0.9 and m["cv"]["grouped"] and m["source"]["synthetic"],
          f"macro F1 {m['macro_f1']:.3f}, accuracy {m['accuracy']:.3f}, {m['cv']['n_groups']} groups")
    model = fq.load_model(tr / "frame_quality_model.json")
    X = fq.feature_matrix(ds["paths"][:20])
    direct = fq.predict_proba(fq.train(fq.feature_matrix(ds["paths"]), ds["y"]), X)
    check("model JSON round trip", np.allclose(fq.predict_proba(model, X), direct, atol=1e-6) and (tr / "confusion.png").stat().st_size > 5000,
          f"max diff {np.abs(fq.predict_proba(model, X) - direct).max():.1e}")

    # held-out synthetic set from another seed
    ho = OUT / "heldout"
    run("synth", "--out", ho, "--n", 30, "--seed", 9)
    run("eval", ho, "--model", tr / "frame_quality_model.json", "--out", OUT / "eval")
    e = json.loads((OUT / "eval" / "metrics.json").read_text())
    kf = e["keep_filter"]
    check("held-out evaluation and keep filter", e["macro_f1"] >= 0.85 and kf["unusable_kept_fraction"] <= 0.1 and kf["recall_informative_kept"] >= 0.85,
          f"macro F1 {e['macro_f1']:.3f}; kept {kf['kept']}, {kf['unusable_kept_fraction']:.0%} unusable kept")

    # classify CSV
    c = OUT / "classify.csv"
    run("classify", ho / "informative", ho / "underexposed", "--model", tr / "frame_quality_model.json", "--out", c)
    rows = list(csv.DictReader(open(c)))
    keep_inf = np.mean([r["keep"] == "1" for r in rows if "/informative/" in r["path"]])
    keep_dark = np.mean([r["keep"] == "1" for r in rows if "/underexposed/" in r["path"]])
    sums = max(abs(sum(float(r[f"p_{k}"]) for k in fq.CLASSES) - 1) for r in rows)
    check("classify writes one row per frame", len(rows) == 60 and sums < 1e-3 and keep_inf >= 0.85 and keep_dark <= 0.1,
          f"{len(rows)} rows, kept {keep_inf:.0%} informative, {keep_dark:.0%} dark")

    n_ok = sum(ok for ok, *_ in results)
    print(f"\n{n_ok}/{len(results)} checks passed")
    sys.exit(0 if n_ok == len(results) else 1)


if __name__ == "__main__":
    main()
