# PEBP Designer

Research software that turns an infant's maxillary intraoral scan into a design for the pre-epiglottic baton plate (PEBP, also called the Tübingen palatal plate), used to open the airway in infants with Robin sequence.

> **Research use only. Not for clinical use.** This is not a medical device. It has not been cleared or approved by any regulator, and nothing designed with it has been printed, cast, bench-tested or clinically validated. It is for research and teaching. Clinical choices stay with the clinician: the software stores and flags them and never makes them.

What it does:

- finds the landmarks, puts the scan in a fixed design frame and blocks out the cleft;
- builds the base plate and attaches the published Tübingen standard extension;
- writes three try-in prototypes, P1–P3, with the baton 2 mm back, centred and 2 mm forward;
- records each endoscopy try-in and turns its changes into new designs by deforming the published baton;
- checks every design against the published thickness, width and wire rules;
- makes printed tooling to cast the final plate in cold-cure PMMA with its safety wire, and simulates demolding;
- offers a modular try-in kit and a two-visit chairside route (research routes, untested);
- runs as **PEBP Studio**, a six-step app that stays on your own computer.

## Download PEBP Studio

Ready-to-run builds for macOS (Apple silicon and Intel) and Windows are on the [Releases page](https://github.com/kkoka1116/pebp-designer/releases); they need no Python. See [docs/INSTALL.md](docs/INSTALL.md) for the first start (the builds are not code-signed yet) and [CHANGELOG.md](CHANGELOG.md) for what changed.

## Install from source

Python 3.12.

```bash
cd pebp_demo
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/phase0_check.py      # synthetic regression suite, about 3 minutes, should be all PASS
```

## Try it on a synthetic palate

```bash
.venv/bin/python -m pebp synth  --out out/demo --preset prs
.venv/bin/python -m pebp design out/demo/scan.stl --out out/demo --final
.venv/bin/python -m pebp mold   out/demo
.venv/bin/python -m pebp viewer out/demo          # writes out/demo/viewer.html
.venv/bin/python -m pebp --help                   # every command
```

## PEBP Studio

```bash
.venv/bin/python -m pebp studio                   # opens http://127.0.0.1:8780, this computer only
```

On a Mac it also runs in its own window:

```bash
.venv/bin/pip install -r requirements-app.txt
.venv/bin/python -m pebp app --install            # writes ~/Applications/PEBP Studio.app and a starter folder with sample scans
```

The .app is a small launcher that runs this folder's code; after updating the code, quit and reopen it. See `docs/STUDIO.md` and `docs/STUDIO_WALKTHROUGH.md`.

## Patient data

Real scans are patient data. The software never sends anything off the computer: Studio answers local requests only, and every output made from a real scan belongs with the scan. Do not upload real scans, outputs or screenshots of them anywhere, including issues on this repository.

## Design frame

x patient's right, y anterior, z superior, occlusal plane at z = 0, origin midway between the tuberosities, millimetres.

## Sources

- Aretxabaleta M, et al. 2025. Bioengineering 12:1063. The standard extension, the protocol and its rules (extension at least 3 mm thick, never narrowed by more than half its width, final plate 2.5–3 mm, safety wire from about 1 cm after the root to 1.5–2 mm before the tip).
- Aretxabaleta M, et al. 2022. Bioengineering 9:773, doi:10.3390/bioengineering9120773. Plate measurement scheme.
- Vorperian HK, et al. 2005. J Acoust Soc Am 117:338, doi:10.1121/1.1835958. Infant vocal tract norms used in the reach check.

## Licence

Code: PolyForm Noncommercial 1.0.0, see `LICENSE.md` and `NOTICE`. Free for personal research, teaching, and use by charities, schools, public research and public health organizations; commercial use needs permission. The Tübingen STLs in `external/tuebingen_supplement/` are CC BY 4.0, see the notice there. three.js (bundled in `pebp_demo/pebp/studio/static/`) is MIT.
