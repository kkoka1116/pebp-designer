# Carrying the accepted baton to the next plate

Status: built and tested on synthetic cases only (2026-11-18). No real scans were used. Nothing here is a clinical claim.

Labels follow the project rules: **[published]** with a citation, **[heuristic]** for a choice this project made, **[inference]** for reasoning from published facts.

## Why

- Infants outgrow the plate. Tübingen routinely makes a second plate at 3–4 months [published: Weismann et al. 2026, doi 10.1002/cre2.70291].
- Tübingen makes later plates from a new impression or scan, with the previous plate as the template and no new digital prototype [published: Aretxabaleta et al. 2025, Bioengineering 12:1063].
- Until now the software had no way to do that. Every new scan started again from the standard extension and three prototypes, and nothing added up how much had been ground off at the bedside along a device's history.

The aim is to skip the prototype round for the second and later plates. Whether a given infant's next plate can skip it stays the clinical team's decision.

## Commands

```bash
# the device's history, and the running total of bedside shortening
python -m pebp lineage out/case/design.json --device C1 [--json]

# the next plate: the new scan, with C1's baton as the template; registers the carried design as T
python -m pebp carry out/case/design.json NEW_SCAN.stl --device C1 --out out/case_plate2

# cast it (T is the default device for tooling when nothing was changed after it)
python -m pebp mold out/case_plate2

# optional: also print P1–P3 centred on the carried baton (-2, 0, +2 mm) for a try-in
python -m pebp carry out/case/design.json NEW_SCAN.stl --device C1 --out out/case_plate2 --prototypes
```

`--device` is required. Which plate is the template is a clinical choice. Without it, `carry` stops and names the device the record suggests: the device in the last sleep study with the plate in, else the last cast plate, else the device accepted at the last fitting session.

From Python (for PEBP Studio): `pebp.carry.lineage(record, "C1")`, `pebp.carry.suggest_device(record)` and `pebp.carry.carry_forward(prev_design, new_scan, out_dir, "C1", prototypes=False)`.

## What is carried, and what is not

Carried [heuristic]:

- **The baton exactly as it left the clinic.** Every bedside and lab change is already in the device's parameters: length, angle, distal curve, AP and lateral offset, width and thickness.
- **Its pose relative to the plate.** It is placed by the same rule as every other device, from the new plate's posterior border at the new plate's mid-thickness. On the synthetic test, the root-to-tip vector and the arm length came out the same on the new plate to within 0.01 mm. The baton moves with the posterior border as the palate grows.
- **The plate settings.** Thickness, flange, block-out, graded profile, border relief and final-plate thickness come from the previous record. The plate itself is rebuilt from the new scan.
- **The extension variant** (cleft or no-cleft). If the new scan on its own would get the other variant, for example after the cleft narrows, the record flags it for review.
- **The clinical settings** (tip target, sleep index and threshold), because it is the same infant. Flagged for confirmation.

Not carried:

- **No growth correction.** The baton is not lengthened or re-angled for the larger palate. That is a clinical decision, and the record flags it. No published rule for it was found [published absence].
- **No airway landmarks.** CT, MRI or endoscopy values from the previous plate describe a younger infant. They are listed in the flags and left for re-measurement.
- **The safety wire is not carried.** A new cast plate gets a new wire, placed fresh at 1 cm after the root to 1.5–2 mm before the tip [published: Aretxabaleta 2025]. Its tip reserve therefore starts again.

## What the record holds

The new `design.json` gains:

- `carried_design`: the T device (file, SHA-256, parameters, tip, QC).
- `carried_from`:
  - the previous record's path and SHA-256, the device id and kind, and the previous scan's SHA-256;
  - `generation`: plate 2, 3 and so on;
  - `lineage`: the chain (for example `P2 -> A1 -> A2`), each step's changes, `bedside_shortening_total_mm` for that plate, `bedside_shortening_all_plates_mm` across every plate, the lab length change, and the device against the published standard;
  - `growth`: arch depth, intertuberosity width, posterior-border position, tip position and arm length, old against new. Each is in its own plate's design frame, with the origin midway between the tuberosities;
  - `flags`.

T is part of the device registry (P1–P3, **T**, A<n>, F, C<n>), so fitting sessions, sleep studies, `adjust`, `mold`, `polyjet` and the airway metrics all accept it.

## Tests

`scripts/phase0_check.py` adds four checks, all on synthetic palates:

1. **Pose kept.** A small palate (−1 SD) gets a bedside shortening of 1.5 mm and a lab curve of 10°. It is carried onto a larger, randomly rotated palate (+1 SD). The parameters are identical, the arm length matches within 0.05 mm, the root-to-tip vector matches within 0.1 mm, and QC passes.
2. **Lineage recorded.** The chain is `P2 -> A1 -> A2`, the bedside total is 1.5 mm, the plate is generation 2, and the growth flag is set. `carry` without `--device` is refused.
3. **T goes to tooling by default**, and no prototypes are made unless asked.
4. **Third plate.** A further 0.5 mm bedside shortening on the second plate is carried to a third, with `--prototypes`. The total over all plates is 2.0 mm, P2 equals T, and P1–P3 sit at T's offset −2, 0 and +2 mm.

## Limits

- Synthetic palates only. On synthetic palates, growth is a uniform size change, which flatters the pose check. Real growth also changes vault shape and cleft width. A Robin cleft narrowed 6.4 mm over about 20 weeks under a Stanford plate [published: Kim 2024, doi 10.1177/10556656241261846].
- Placement relative to the posterior border is this project's reading of "previous plate as template" [heuristic]. It has not been compared with a technician's manual transfer. A real check would scan an old plate and its successor, both made by Tübingen's method, and compare the baton poses.
- The bedside-shortening total counts length taken off at the bedside along the device's chain. It is only as good as what was logged. Scanning the ground device (`fitting measure --apply`) makes it a measurement rather than an estimate.
