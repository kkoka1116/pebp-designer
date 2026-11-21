# Phase 0 bench protocol

This protocol covers the physical half of Phase 0 in `../reports/Software PEBP fabrication plan.md`. The software half (structured endoscopy and sleep records, the new QC rules, the Tübingen extension as default, and measurement of scanned parts) is in `../pebp_demo` and is checked by `pebp_demo/scripts/phase0_check.py`.

Nothing here touches an infant or patient data. Every part is made from synthetic palates built on published Robin-sequence morphology.

## What the bench must decide

| Question | Test | Gate (from the plan) |
|---|---|---|
| Is the printed four-piece mold at least as good as Tübingen's silicone index for making the final plate? | 1. Casting accuracy, wire position and porosity | Cast plates match their design no worse than silicone-index plates |
| Does the cast plate fail safely? | 2. Fracture load and break pattern | Every wired specimen at or above 52.1 N, and every break held by the wire (pattern A or B) |
| Which resin should the try-in prototypes be printed in? | 3. Prototype strength and visibility | Prototype resin visible on endoscopy and strong enough not to snap at insertion |
| Are the printed parts themselves accurate? | 4. Print checks on tooling and prototypes | Recorded, used to explain test 1 results |

Agree the acceptance margins in the analysis section with the clinical team before the first cast, and record that agreement with a date.

## Test designs

Make three synthetic palates at the published Robin-sequence mean and ±1 SD (Willershausen 2024), plus one no-cleft case. For each, run the design step and generate the casting tooling:

```bash
cd pebp_demo
for sd in -1 0 1; do
  .venv/bin/python -m pebp synth  --preset prs --sd $sd --out out/bench_sd$sd
  .venv/bin/python -m pebp design out/bench_sd$sd/scan.stl --out out/bench_sd$sd
  .venv/bin/python -m pebp mold   out/bench_sd$sd          # tooling for P2; prints ok=True and registers C1
done
.venv/bin/python -m pebp synth  --preset prs --no-cleft --out out/bench_nocleft
.venv/bin/python -m pebp design out/bench_nocleft/scan.stl --out out/bench_nocleft
.venv/bin/python -m pebp mold   out/bench_nocleft
```

Use only cases where `mold` prints `ok=True`. Each case then has:

- `mold/`: the tooling;
- `cast_1.stl`: the plate in the design frame, which is the reference every cast is measured against;
- `design.json`: the record.

The main case is `bench_sd0`. The others show whether results hold across palate sizes.

## Materials and settings, the same for both methods

The settings below are Tübingen's (Aretxabaleta 2025; Dentaurum Orthocryl instructions):

- **Resin and wire**: cold-cure PMMA (Orthocryl) from one lot, with flat twisted stainless steel safety wire 1.8 × 0.8 mm (Dentaurum 312-106-00), bent on `mold/wire_jig.stl`.
- **Pressure pot**: 2.2 bar in water at 40-46 °C for at least 20 minutes, not interrupted during the first 12.
- **Soak**: 3 days in water before any test that depends on residual monomer, including the fracture test conditioning below.
- **Mix ratio**: record the ratio actually used for every cast. Tübingen found the recommended 2.5:1 monomer-to-polymer mix too viscous to flow in a mold and thinned it on the technician's judgement (Aretxabaleta 2021). This is the variable most likely to separate the two methods.
- **Fairness**: the same technician, or each technician casting equal numbers by both methods; casting order randomised; the same separating medium on printed resin, recorded by name.

## Test 1: casting accuracy, wire position and porosity

**Groups**, per case:

- **A: printed four-piece mold.** Assemble working model, oral counter and both baton halves in the cradle, with the wire held on the six notched pins. Pour through the sprue at the tip, then pressure-cure. After demolding, fill the six 2 mm pin holes with fresh resin and pressure-cure again.
- **B: Tübingen silicone index.** Print `mold/working_model.stl` and `mold/device_master.stl`, seat the master on the model, and take an A-silicone putty index (Tübingen: 86 Shore A). Remove the master and build the plate in the index by salt-and-pepper layering. Pressure-cure, then grind the plate to 2-3 mm. Cut the groove, set the wire (10 mm after the root to 1.5-2 mm before the tip) and cover it with resin.
- **Optional C:** the one-piece flexible counter `mold/alternatives/counter_onepiece_flex.stl`, printed in a Silicone 40A-class resin and paired with the groove-and-cover wire as in B.

**Numbers.** On `bench_sd0`, make 8 plates per group:

- all 8 are scanned and radiographed;
- 6 then go to test 2;
- 2 are sectioned for porosity.

On each of `bench_sd-1`, `bench_sd1` and `bench_nocleft`, make 2 per group, scanned and radiographed only. Record every failed cast and why. A failed pour counts in the results; it is never quietly replaced.

**Scanning and measurement.** Scan each plate on one lab scanner, the same for both groups (Tübingen measured finished plates on a 20 µm extraoral scanner; Aretxabaleta 2022). Then:

```bash
.venv/bin/python -m pebp measure out/bench_sd0/cast_1.stl scans/A-03.stl --record out/bench_sd0/design.json --out results/A-03.json
.venv/bin/python -m pebp fitting measure out/bench_sd0/design.json --device C1 --scan scans/A-03.stl --out results/A-03_baton.json
```

The first command aligns the scan to the design and reports exact surface distances in three regions. Its signed values are + for extra material and - for missing material.

- **intaglio**: the tissue side, i.e. palatal fit;
- **plate_other**: the oral surface and rim;
- **baton**.

The second reports baton length against the design (+ means shorter) and its width and thickness at three points along the arm.

Alignment is a rigid best fit over the whole plate. A warp of the palatal surface therefore shows up mostly in the intaglio, but partly in the other regions too. In the synthetic check, a 0.15 mm palatal sink read -0.05 mm on the intaglio, -0.03 mm on the rest of the plate and 0.00 mm on the baton. Compare the groups on the same measure; don't read the numbers as absolute fit.

As a complement to the scan, seat each plate on a fresh print of the working model. Record rocking (yes or no) and the largest gap at the posterior border and the ridge crest, using a feeler gauge or fit-checker silicone.

**Wire.** Radiograph every wired plate before any destructive test. Measure:

- the distance from the wire end to the tip (pass: 1.5-2 mm);
- the start, 8-12 mm after the root;
- whether the wire runs inside the baton for its whole length with no exposure.

If radiography is not available, the two sectioned plates per group are the fallback, and that should be recorded as a limitation.

**Porosity.** Section the two sacrificial plates per group at three planes: mid-palate coronal, the baton root, and mid-baton. Count pores of 0.2 mm or more under ×10 magnification and report pores per cm² of cut face. Inspect group A's pin-hole fills separately for gaps at the interface. Also give every plate Tübingen's pre-delivery check: steam clean, then look for bubbles and microcracks.

**Process record per plate:**

- hands-on and total time;
- mix ratio;
- incomplete fill, and where;
- demolding problems (a piece cracked, or force was needed);
- surface finish and any residue from the printed tooling;
- for group B, the grinding and groove time.

## Test 2: fracture load and break pattern

Follow Aretxabaleta 2021 so the results compare with the published reference:

- **Specimens**: 6 wired plates per group from test 1, all after the 3-day soak.
- **Conditioning**: stored in water at 37 °C for 50 h.
- **Loading**: three-point: two supports at the front of the palate, the load applied at the spur tip.
- **Machine**: universal testing machine at 2 mm/min, stopping at 14 mm deflection.

**Record** for each specimen:

- peak load (N) and deflection at peak;
- break pattern:
  - A: no break;
  - B: broken but held by the wire;
  - C: partly detached from the wire;
  - D: spur fully detached;
- where the fracture started (baton root, a pin-hole fill, the wire end, a bow exit);
- photos.

**Pass**: every specimen at or above 52.1 N, and every specimen pattern A or B. The 52.1 N threshold is the adult maximum posterior tongue force; no infant data exist, and the authors expect infant forces to be considerably lower. For reference, hand-made Orthocryl with a wire broke at 231.0 ± 44.5 N, all pattern B.

A pin-hole fill as the origin of a C or D break is a specific failure of group A, and should be reported as such even if the group passes overall.

## Test 3: prototype resin

**Candidates** are the team's choice, for example:

- an opaque coloured tray resin, which is Tübingen's current choice (Detax Freeprint tray; confirm the exact product, colour and class with Detax, because the 2025 paper is inconsistent);
- the PolyJet profile from `pebp polyjet`, whose `PRINT_NOTES.md` lists the materials and their contact ratings;
- a clear resin marked with black marker, as the comparator Tübingen found inferior.

**Strength**: 6 prototypes per resin, printed from `prototype_2.stl` with no wire, tested as in test 2. Without a wire, any break releases the spur. Pass: no break below 52.1 N. Report the patterns. The 2021 paper still recommended the tray resin for prototypes, because try-ins are supervised.

**Visibility**: there is no published phantom, so make a simple one:

1. Make a dark, wet tube with an 8-10 mm lumen, lined with pink silicone, and seat the prototype's tip at the far end.
2. View it with the same type of flexible endoscope used clinically, at fixed distances.
3. Capture still frames of each resin under the same light.
4. Have three observers, blind to the resin, score each frame from 0 (spur not seen) to 3 (tip edges and number dots clearly seen).

Report the scores and the agreement between observers. Tübingen found clear prototypes hard to locate under endoscopy, and marker contrast inferior to opaque colours.

## Test 4: print checks

- **Tooling**: scan the printed working model and counter pieces and compare each with its STL, e.g. `pebp measure mold/working_model.stl scans/model.stl`. These files are in pour orientation, so leave out `--record`. Dry-assemble the four pieces in the cradle and record any rocking and the parting-line gaps.
- **Prototypes**: `pebp measure prototype_2.stl scans/P2-01.stl --record design.json` for each resin, to separate print accuracy from casting accuracy in test 1.

## Analysis

Decide before casting:

- **Primary outcome**: the 95th-percentile absolute deviation of the intaglio (scan to design) on `bench_sd0`.
- **Proposed non-inferiority margin**: the printed mold's mean is at most 0.10 mm above the silicone index's mean. Apply the same rule to the baton region and to baton length. The clinical team confirms or changes the margin.
- **Reporting**: report every specimen's value, each group's mean with a 95% confidence interval, and the ±1 SD and no-cleft cases separately. With 8 plates per group this is a feasibility comparison, not a powered trial. Make no claims beyond the pre-set margin.
- **Wire, porosity and fracture** are pass or fail per specimen, against the rules above.
- **The printed mold goes forward** only if all of the following hold:
  - it meets the accuracy margin;
  - every wire is within 1.5-2 mm of the tip;
  - no plate has through-porosity in its sections;
  - every fracture specimen passes.

  Otherwise the final plate is made Tübingen's way. The software still supplies the working model, the wire jig, the design record and the QC.

## Records

- **Specimen IDs**: group letter plus number (A-01, B-01, C-01), written on the plate and in the record.
- **Files**: one folder per specimen holding the scan, the `measure` JSON outputs, the radiograph and photos.
- **One spreadsheet row per specimen**:
  - case, group, casting order, technician, resin lot, mix ratio;
  - pressure-pot temperature and time, soak days;
  - hands-on time, failed or not and why;
  - the intaglio, plate_other and baton p95 and signed means, and the baton length difference;
  - seated rocking and gaps;
  - wire end-to-tip and start distances, pore counts;
  - fracture load, deflection, pattern and origin.
- **Software version**: keep the `design.json` and `mold/mold_manifest.json` the plates were made from. They carry the SHA-256 of every file, so the exact design can be regenerated or checked.

## What this bench does not establish

Biocompatibility and residual monomer of the cast plates, fatigue under repeated loading, ulcer risk, and clinical fit on an infant are all outside it. They belong to the biocompatibility file and to Phases 2-4 of the plan.

## Sources

- Aretxabaleta M. et al. 2025, Tübingen complete protocol, *Bioengineering* 12(10):1063, https://doi.org/10.3390/bioengineering12101063. Materials, pressure pot, wire, index, pre-delivery checks.
- Aretxabaleta M. et al. 2021, fracture load of TPP materials, *Materials* 14(2):344, https://doi.org/10.3390/ma14020344. Test set-up, 52.1 N threshold, break patterns, mix viscosity.
- Aretxabaleta M. et al. 2022, measuring finished TPPs, *Bioengineering* 9(12):773, https://doi.org/10.3390/bioengineering9120773.
- Willershausen I. et al. 2024, Robin-sequence palate morphology, https://doi.org/10.1111/ocr.12818. Basis of the synthetic palates.
- Dentaurum Orthocryl instructions for use: pressure-pot settings and the 3-day water soak.
- Detailed notes: `../research_notes/Software PEBP fabrication plan/manufacturing_and_materials.md`.
