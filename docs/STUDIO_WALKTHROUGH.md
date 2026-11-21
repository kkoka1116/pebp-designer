# PEBP Studio walkthrough

This walks you through PEBP Studio from opening it to having the casting tooling ready to print. Do it once with a sample scan before you use a real one. The practice run takes about 15 minutes.

PEBP Studio is research software. It is not a cleared medical device. Every number it shows is a measurement for the clinical team to weigh, and it never makes the clinical choices.

## What is in this folder

| Item | What it is |
|---|---|
| `PEBP Studio.app` | The app. Double-click it. (The real app lives in `~/Applications`; this is a shortcut.) |
| `1 START HERE - walkthrough.md` | This file. |
| `2 Case intake sheet.md` | What to have ready before a real case: the scan, your plate choices, the clinical choices and the try-in notes. |
| `Sample scans/` | Two synthetic scans, with and without a cleft, made from published neonatal measurements. They are not from a patient. Use them to practise. |
| `Scans to design/` | Put a real scan here before you add it in the app. It stays on this Mac. |
| `Cases/` | The app keeps every case here, one folder per case: the design record, the approvals, the devices and the mold files. |

**Patient data stays on this Mac.** The app has no internet connection of its own. It runs a small server that only this computer can reach, and shows it in its own window. Do not email, upload or sync real scans or anything in their case folders, and do not move this folder into iCloud Drive, Dropbox or OneDrive.

## How the app is laid out

- **Left:** the steps. Each step is built by the software, shown to you, and approved by you before the next one opens. A step's chip reads *to do*, *review*, *approved* or *changed*.
- **Middle:** the 3D model. Drag to turn it, shift-drag to pan, and scroll to zoom. The buttons at the top left give the standard views (3D, from below, side, front). The list at the top right turns layers on and off.
- **Bottom:** the command each build ran. Click **Show output** to see everything it printed.
- **Top:** the case picker, **New case**, and **Show case folder**, which opens the case's folder in Finder.

**Approvals.** Every approval records your name and the time. If something you approved changes later, for example the plate is rebuilt, that step reads *changed* and the steps after it wait until you approve it again. **Send back** withdraws a step and every step after it.

**Findings.** Each step lists what the software found. A STOP is red, a WARN is amber and an INFO is grey. Each finding says what it is based on: the published Tübingen rules (Aretxabaleta et al. 2025), a project heuristic, a geometry check, or a comparison with published infant norms. A STOP holds the approval. You can release it by ticking "Approve anyway: I have read the STOP findings". The app then records the override, with the findings you overrode.

## Practice run with a sample scan

### 1. Open the app and make a case

1. Double-click **PEBP Studio.app**. The window opens on an empty workspace.
2. Click **New case**, type `practice1`, and click **Create**. Use a code like this for real cases too, never a patient's name.

### 2. Step 1, Case

1. Type your initials in **Your name or initials**. They go with every approval you make.
2. Under **Scan**, click **Choose File** and pick `Sample scans/synthetic cleft palate.stl`.
   - The badge at the top turns red: "REAL SCAN: keep on this computer". The app treats every file you add as a real scan, because it cannot tell the difference. That is the safe default.
   - The other way to get a scan, **Make a synthetic scan**, makes a fresh one and labels the case synthetic.
3. The scan appears in the model view. Turn it round. It is rotated at random, as a raw scan from the scanner would be.
4. Under **Cleft type**, choose *Cleft of the hard and soft palate*. The sample has one. This is a clinical entry from the examination, not something the software reads off the scan: a hole in a scan can look like a cleft, and the scan does not show the soft palate. A child with a cleft of the soft palate or uvula only takes the no-cleft baton, like a child with no cleft. The choice picks the baton and how the plate bridges the palate, and the step won't approve until it's set.
5. Click **Approve and continue**.

### 3. Step 2, Landmarks

1. Leave **Landmark finder** on *built-in*. The MoldStudio classifier is offered when MoldStudio is installed on this Mac.
2. Click **Detect landmarks**. It takes about 5 seconds.
3. Check the model. The scan is now turned into the design frame, with the occlusal plane flat and the midline running front to back. Check the marks:
   - the incisive papilla at the front;
   - the two tuberosities at the back, joined by a line;
   - the palatal apex;
   - the plate outline, in blue.

   The cleft is not drawn; its width and margin level are in the table below.

   A wrong point here gives a plate that passes every rule and still does not fit.
4. Read the **Measured** table: the arch depth, the intertuberosity width and the cleft width. If the scan is a real one, compare these with what you see in the mouth.
5. Click **Approve and continue**.

The app cannot move a landmark by hand yet. If a point is wrong, rescan, or note it for the design team.

### 4. Step 3, Plate and prototypes

1. Choose the plate:
   - **Uniform**, the Tübingen plate and the default: one thickness everywhere.
   - **Graded, low profile**, a project heuristic: thinner away from the baton root and the cleft, never below 1.5 mm. It has not been bench tested.
2. Tick **Border relief** if you want to try it. It is a project heuristic aimed at the commonest ulcer sites.
3. Leave **Prototype plate** at 2.0 mm and **Final plate** at 2.5 mm. Tübingen uses 2.5–3 mm for the final plate.
4. Leave **Advanced** alone. It holds the baton variant, rim, block-out, and the older groove final F.
5. If the app shows **How the worn plate is made**, leave it on **One-piece cast plate**. The other option, the two-visit chairside kit, is covered at the end.
6. Click **Build**. The base plate appears on the scan. The app also makes the prototypes P1, P2 and P3: the same plate with the baton 2 mm back, centred, and 2 mm forward. Tick P2 in the layer list to see the baton. Step 4 shows all three.
7. Read the **Result** table and the findings, then click **Approve and continue**.

### 5. Step 4, Baton changes

This is where the endoscopy try-in results go. For practice, pretend the try-in showed P2's tip sitting on the epiglottis:

1. Leave **Change device** on *P2*.
2. In the change menu, pick **shorten** and type `1.5`.
3. Leave **Where** on *lab (new device)*, and type a note such as `tip on the epiglottis at try-in`.
4. Click **Build the changed device**. A new device, A1, appears, linked to P2. Turn P2 and A1 on together to see the difference.
5. Pick the device to cast with the round button at the right of its row. Choose **A1**.
6. Set the **Tip target**. This is a clinical choice. For practice pick either one and click **Save**. The app logs who set it.
7. Click **Approve and continue**.

To see the STOP gate, make a second change from P2: **narrow**, `6` mm. That narrows the baton by more than half its width, which breaks a Tübingen rule (the extension is never narrowed by more than half its width). Choose the new device, A2, to cast. The step now shows a STOP and refuses to approve until you tick "Approve anyway". Choose A1 again before you approve.

### 6. Step 5, Checks

1. Read the QC table: every rule for P1–P3 and for the device you chose. Each rule is marked *Tübingen* or *heuristic*. Hover over a row to see the rule.
2. Read the **Reach against published infant norms** table. It compares the baton's reach with published infant measurements (Vorperian 2005 and the Robin-sequence CT studies). It is a plausibility check only, not a clinical claim.
3. Click **Approve and continue**.

### 7. Step 6, Mold

1. Check **Device to cast**. It should read A1.
2. Leave **Final plate** at 2.5 mm and **Mold wall** at 6 mm.
3. Click **Build the mold**. It takes about a minute. Watch the bottom bar.
4. Read the results:
   - **Validation** should read *ok*;
   - **Cavity / appliance** volumes;
   - **PMMA to mix**;
   - the **Safety wire** length and where it runs. Tübingen runs the wire from about 1 cm after the root to 1.5–2 mm before the tip.
5. Click **Open the mold viewer**. Drag the demold slider to watch the four pieces come apart around the plate. Click **Show the design** to go back.
6. Under **Files**, click a file to save it. A Save dialog opens. Or click **Show case folder** at the top to see everything in Finder. The mold files are in `mold/`.
7. Click **Approve the tooling for printing**.

That is the whole cast route. Nothing in it has been printed or cast yet. The bench tests in `docs/PHASE0_BENCH_PROTOCOL.md` come first.

## A real case

1. Fill in the intake sheet (`2 Case intake sheet.md`).
2. Export the scan from the scanner as STL and put it in `Scans to design/`.
3. In the app, click **New case** and give it a code (`case01`), never the patient's name.
4. Follow the same steps as the practice run, with the real choices from the intake sheet.
5. After each endoscopy try-in, come back to step 4 and enter the changes. Every change makes a new device and keeps the link to the one it came from.

## The two-visit chairside kit (untested research route)

This route appears on step 3 only when that code is installed on this Mac. Choosing it replaces steps 4–6 with two visits:

- **Visit 1: try-in kit and final base.** Click **Build the kit and the final base**. You get a socket base with 15 kit batons (K1–K15: five lengths by three angles), and the worn plate's final base with its casting tooling. Pick a baton in the list to see it on the model.
- **Visit 2: worn plate.** After the endoscopy try-in, choose the K baton that fitted and click **Build the worn plate**. You get the worn plate S1, its safety wire, its QC, and the chairside steps.

Nothing in this route has been printed, cast, bonded or worn. The app flags it on every step.

## If something goes wrong

- **A build fails.** The bottom bar says FAILED. Click **Show output** to read the error, then rebuild that step.
- **Quitting during a build.** The app asks first, because the build's files may be incomplete.
- **Rebuilding the plate after making changes.** The app asks you to confirm. It then moves the old record, the changed devices and the mold into the case's `history/` folder, so nothing is lost. The tip target and any imaging values carry over.
- **The app does not open.** The log is at `~/Library/Logs/PEBP Studio.log`. The same studio also runs in a browser. In Terminal, run:

  ```bash
  cd path/to/pebp-designer/pebp_demo && .venv/bin/python -m pebp studio
  ```
- **Updating.** The app runs the code in the folder you downloaded. After a `git pull` there, quit and reopen the app. There is nothing to reinstall.
