# PEBP Studio: the clinician UI

PEBP Studio lets a clinician take one case from the scan to the casting tooling in a browser, step by step, without the command line. It is a front end to the same `python -m pebp` commands in `pebp_demo/README.md`. It does no geometry of its own, so a design made in the studio is the same as one made by typing the commands. Every command it runs is shown at the bottom of the window and appended to `studio_log.jsonl` in the case folder.

```bash
cd pebp_demo
.venv/bin/python -m pebp studio                 # opens http://127.0.0.1:8780 in the browser; cases live in out/
.venv/bin/python -m pebp studio --workspace /path/to/cases --port 8790 --no-browser
.venv/bin/python scripts/studio_check.py        # the server's rules on synthetic data: 25 checks, +2 with --mold, +11 for the kit route when `pebp kit` exists
```

## The download

The [Releases page](https://github.com/kkoka1116/pebp-designer/releases) has PEBP Studio frozen with its own Python for macOS and Windows (`pebp_demo/packaging/`, built and smoke-tested by `.github/workflows/release.yml`). On macOS it opens in its own window; on Windows it opens in the browser, with a console window that stops it when closed. Its starter folder is the one described below, except that on Windows it is `PEBP Studio` in the user folder, since Windows Desktops often sync to OneDrive. Install steps: [INSTALL.md](INSTALL.md).

## The desktop app from source (macOS)

The same studio also runs in its own window, as a Mac app, instead of a browser tab:

```bash
cd pebp_demo
.venv/bin/pip install -r requirements-app.txt   # pywebview, once
.venv/bin/python -m pebp app --install          # writes ~/Applications/PEBP Studio.app and the starter folder
open ~/Applications/"PEBP Studio.app"           # or double-click it
```

- **The window.** `pebp app` starts the studio server on 127.0.0.1 (port 8781, or a free one) and shows it in a native window (pywebview, which uses the system WebKit). Closing the window stops the server. It asks first if a build is still running. The server, its checks and the steps are the same as in the browser.
- **The .app.** `PEBP Studio.app` is a small launcher, not a frozen copy. It runs this checkout's `.venv`, so a `git pull` updates it with nothing to reinstall. Its log is `~/Library/Logs/PEBP Studio.log`.
- **The starter folder.** By default this is `~/Desktop/PEBP Studio/`. If the Desktop syncs to iCloud it is `~/PEBP Studio/` instead, so real scans don't leave the Mac. It holds:
  - `1 START HERE - walkthrough.md` (from `docs/STUDIO_WALKTHROUGH.md`);
  - `2 Case intake sheet.md` (from `docs/STUDIO_INTAKE.md`);
  - two synthetic sample scans;
  - `Scans to design/` for real scans;
  - `Cases/`, the app's workspace;
  - a shortcut to the app.

  Re-running `--install` refreshes the two documents and the app. It leaves cases and scans alone.
- **Downloads and folders.** File links open a Save dialog. **Show case folder** opens the case in Finder; in a browser it does the same through the local server.

## Patient data stays on the computer

- The server listens on 127.0.0.1 only, and answers only requests whose Host header is `127.0.0.1` or `localhost`. That blocks DNS-rebinding pages.
- Every POST needs the header `X-PEBP-Studio: 1`, which a form on another site cannot set.
- A scan picked in the browser is copied into the case folder in the workspace and goes nowhere else. The default workspace is `pebp_demo/out/`, which git ignores.
- three.js is bundled (`pebp/studio/static/three.min.js`, r128, MIT, licence beside it). The page loads nothing from the internet, and the generated mold viewer is served with the bundled copy too.
- A case with a real scan shows a red "REAL SCAN: keep on this computer" badge. Real scans stay on the computer they were made on.

## The steps

The layout follows MoldStudio's stepper. Each step is built by the software, shown, and approved by a named person before the next one opens. Approvals are stored in `review.json` beside `design.json`.

| Step | What the clinician does | Command run |
|---|---|---|
| 1 Case | Enters their name or initials, which go with every approval. Adds the STL scan, or makes a synthetic demo case. Enters the cleft type: hard and soft palate, soft palate or uvula only, or none (a clinical entry; it picks the Tübingen cleft or no-cleft baton and how the plate bridges the palate, see `docs/CLEFT_TYPE_AND_CATALOG.md`; a disagreement with the scan analysis is flagged on Landmarks). | `pebp synth` (demo only) |
| 2 Landmarks | Checks the apex, incisive papilla, tuberosities, the plate outline and the cleft on the model. Chooses the built-in finder or MoldStudio's classifier (offered only when MoldStudio is installed at `MOLDSTUDIO_ROOT`). | `pebp design` |
| 3 Plate and prototypes | Chooses a uniform plate (Tübingen, the default) or a graded, low-profile plate, and border relief; both are labelled project heuristics. Sets prototype and final thickness. Under Advanced: the baton variant, rim, block-out, and the groove final F. | `pebp design` |
| 4 Baton changes | Shows P1–P3 and any A<n> on the model. Records each try-in change in the fitting vocabulary (shorten, lengthen, angle, curve and so on, lab or bedside), which builds a new A<n>. Chooses the device to cast. Sets the tip target, a clinical choice that records who set it. Under Advanced: imaging values (vallecula, epiglottis tip, TB–PPW distance, TB–epiglottic angle). | `pebp adjust`, `pebp airway --set` |
| 5 Checks | A QC table for each prototype and the chosen device, each rule marked Tübingen or heuristic, plus the reach table against published infant norms, labelled plausibility only. | none (reads the record) |
| 6 Mold | Builds the four-piece mold, cradle and wire jig for the chosen device, shows the validation and demold results, opens the mold viewer with its demold slider, and links each file to download. | `pebp mold --device <id>` |

## The two-visit chairside route (untested research route)

When the checkout has `pebp kit` and `pebp chairside` (PR #9, contract in `docs/KIT_CLI.md`), the Plate step offers a choice of how the worn plate is made. The default is the one-piece cast plate (Tübingen). The other option is the two-visit chairside kit, tagged "untested research route". Choosing it swaps steps 4 to 6 for two steps:

| Step | What the clinician does | Command run |
|---|---|---|
| 4 Visit 1: try-in kit and final base | Builds the socket base and K1–K15 (five lengths by three angles), then the worn plate's final base and its casting tooling. Each baton is listed with its tip shift and QC, and any one can be shown on the model. There are download links for the kit and the base tooling. | `pebp kit`, then `pebp chairside --base` |
| 5 Visit 2: worn plate | After the try-in (logged with `pebp fitting log`, naming the K baton), chooses the K baton that fitted. This builds S<n> on the cast base and shows its QC, the wire, the pick-up volume and the chairside steps. | `pebp chairside --kit K<n>` |

- **Flag text.** The route's flags come from the record (`kit.flag`, `chairside_base.flag`, `S<n>.flag`), so a change in their wording reaches the UI without a Studio change.
- **QC failures.** The chairside commands exit 1 when they wrote their files but QC failed. Studio counts that as a finished build and shows the failure as a STOP finding.
- **Order of the visits.** Visit 2 cannot run until Visit 1 is approved.
- **Rebuilding the final base.** A rebuilt base (for example a different final thickness) expires Visit 1. Any S<n> built on the old base then gets a STOP until it is built again.
- **Changing route.** Changing the route withdraws every approval after the Plate step.
- **Design rebuilds.** A `pebp design` rebuild archives the whole `kit/` folder with the old record.
- **Not in the stepper.** The stock-tooling and bench commands take no case, so they are not in the case stepper.

## How the rules are kept

- **Findings.** Each step lists what the software found. STOP is red, WARN amber, INFO grey, and each finding names its basis: published (Tübingen 2025), project heuristic, geometry check, or plausibility against published norms.
- **The STOP gate.** A STOP finding holds the approval, for example a chosen device that fails QC or a mold that fails its own validation. Ticking "Approve anyway: I have read the STOP findings" releases it. The override is written into `review.json` with the text of every finding it overrode. This is MoldStudio's gate.
- **Approvals expire when the design changes.** Each approval stores a fingerprint of what was approved: the scan and landmarks, the plate parameters and prototype files, the device list with the chosen device and clinical settings, the QC results, or the mold manifest. If the design changes under an approval, the step reads "changed since approval" and every later step waits until it is approved again. "Send back" withdraws a step and every step after it.
- **The mold waits for Checks.** The mold cannot be built until Checks is approved.
- **Rebuilds archive, not overwrite.** `pebp design` starts a new design record. When the current record holds changed devices, cast plates or try-ins, the studio refuses to rebuild until the person ticks that they understand. It then copies the old `design.json` and `review.json` to `history/`, and moves the old mold there. The tip target and any imaging values are carried into the new record.
- **Clinical choices.** The software never picks the tip target; it starts unset and the step says so. A person sets it, and their name is logged.

## Not in the studio yet

- **Moving a landmark by hand.** The studio shows the detected landmarks for review, but cannot move them. Overriding a tuberosity would change the design frame, which is a geometry change with its own phase0 run. It is the next thing to add if reviewers find frames they want to correct.
- **The full endoscopy fitting form, sleep studies, bench measurement and PolyJet export.** These are still CLI only (`pebp fitting`, `pebp sleep`, `pebp measure`, `pebp polyjet`). The studio's change form is the modification part of the fitting form, logged the way `pebp adjust` logs it.
- **The mold crash on the README quick-start case** was fixed in PR #5. If `pebp mold` fails for another reason, the studio shows a failed build with the error in the output panel.

