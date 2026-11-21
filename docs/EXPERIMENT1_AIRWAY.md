# Experiment 1: airway landmarks, plate metrics and a reach check on paper

Status: done on synthetic cases only (2026-11-18). No real scans were used. Nothing here is a clinical claim: the reach check asks whether the geometry falls in a plausible envelope built from population norms, not whether a plate would work for an infant.

Labels follow the project rules: **[published]** with a citation, **[heuristic]** for a choice this project made, **[inference]** for reasoning from published numbers.

## What was added

Code: `pebp_demo/pebp/airway.py`, the `python -m pebp airway` command, and three new checks in `scripts/phase0_check.py` (18/18 pass). The design record schema is now `pebp-design/0.3`; older records are upgraded on load without losing anything.

Every `design`, `adjust`, `fitting log` and `mold` run now fills three blocks in `design.json`, and `design` and `airway` write `airway_report.md` next to it.

### 1. `airway`: one landmark vocabulary

All points are in the design frame (x right, y anterior, z superior, occlusal plane z = 0, mm). Each entry records its `source`: `design`, `scan_proxy`, `ct`, `mri`, `endoscopy`, `manual` or `unmeasured`.

| name | kind | how it is filled now |
|---|---|---|
| `pns` | point | **proxy**: the midline point of the plate's posterior border on the (blocked-out) palatal surface [heuristic]. The real PNS is not visible on an intraoral scan and is not a midline bony point in a cleft palate. Imaging overwrites it. |
| `baton_root` | point, per device | computed: where the centreline leaves the posterior border, at plate mid-thickness |
| `baton_tip` | point, per device | computed: the baton's most inferior point |
| `vallecula` | point | unmeasured until imaging |
| `epiglottis_tip` | point | unmeasured until imaging or endoscopy |
| `tb_ppw_distance_mm` | scalar | unmeasured. Definition: shortest midsagittal distance from tongue base to posterior pharyngeal wall, D4 in Hu 2026 [published] |
| `tb_epiglottic_angle_deg` | scalar | unmeasured. Definition: angle formed by the stomion-line midpoint, the tongue base and the epiglottic base, A2 in Hu 2026 [published]; taking the vertex at the tongue base is this project's reading [heuristic] |

When a vallecula or epiglottis tip is stored, the record derives the PNS-to-landmark distance and each device's tip-to-landmark distance. Values from CT or MRI must be registered into the design frame first; that registration is experiment 3 (known-transform virtual try-in).

```bash
python -m pebp airway out/case/design.json --set vallecula=0,-24,-24 --set tb_ppw_distance_mm=3.5 --source ct --note "..."
```

### 2. `tpp_metrics`: every device in the Bioengineering 2022 scheme

Following Aretxabaleta et al. 2022 (doi 10.3390/bioengineering9120773), read in full through PubMed Central [published]:

- main base plane (MBP) through the incisive papilla (IP) and the two tuberosity points (Q1, Q2);
- five sections parallel to the MBP along the effective extension, with centroids PS1–PS5;
- EL (effective length) = distance from a centroid to its projection on the MBP; SL (sagittal length) = distance from IP to that projection;
- width and thickness from two perpendicular planes through each centroid, plus section area;
- angles between successive skeleton lines, the first from IP to PS1;
- S0 = the smallest of the sections perpendicular to the cleft extension at the points that divide it into five equal segments;
- V = volume of the effective extension (below the MBP, behind the plate border).

Choices this project made [heuristic]:

- The paper's text does not give the section spacing, so sections sit at i/6 of the tip's depth below the MBP (i = 1..5) and none falls on the rounded tip.
- IP, Q1 and Q2 are the scan landmarks, which lie on the plate's intaglio. The paper picks them on the scanned plate by hand.
- Angles are interior angles in 3D (180° = straight).
- V comes from a Manifold boolean. If a saved STL does not reload as a closed volume, V is integrated from 0.25 mm sections instead. On a closed case the two agreed to 0.1 mm³ (694.0 against 694.0).

### 3. `reach_check`: the standard extension against infant norms

Published numbers used:

| quantity | value | source |
|---|---|---|
| hard palate at birth | 27.9 mm | Vorperian 2005 [published] |
| soft palate at birth | 23.6 mm | Vorperian 2005 [published] |
| tongue length, tip to valleculae, at birth | 56.8 mm | Vorperian 2005 [published] |
| hyoid level below PNS at birth | 25.5 mm | Vorperian 2005 [published] |
| larynx level below PNS at birth | 29.7 mm | Vorperian 2005 [published] |
| Robin tongue position vs controls, under 4 years | 5 mm higher, 9 mm more posterior | Perez 2020 [published, read from the abstract record] |
| EL5 / SL5 difference between two real plates for similar patients | 10 mm / 8 mm | Bioengineering 2022 [published] |
| tongue-dorsum path, PNS level to vallecula | 56.8 − 27.9 ≈ 28.9 mm, curvilinear | [inference] |

Lee 2016 (doi 10.1001/jamaoto.2016.1010, read in full) reports Robin and control differences in SD units after size normalisation, so no millimetre offset could be taken from it. What it gives is direction only: in Robin infants the tongue sits higher and more posterior, and the hyoid sits lower relative to the palate [published]. Hu 2026 (doi 10.3389/fneur.2026.1784503, read in full) supplies the D4 and A2 definitions. Its tables give odds ratios, not group means.

## Results on synthetic cases

Six synthetic Robin palates (`synth --preset prs`, sizes at −1, 0 and +1 SD of the Willershausen 2024 norms, with and without a cleft), each with the auto-chosen published Tübingen extension. The numbers are for P2, with P1 and P3 in brackets where they differ.

| variant | palate length (apex to PNS proxy), mm | arm centreline, mm | straight PNS to tip, mm | tip below PNS proxy, mm | EL end / SL end, mm | α1, ° | S0 w × t, mm | V eff, mm³ |
|---|---|---|---|---|---|---|---|---|
| cleft −1 SD | 25.6 | 39.7 | 33.1 (34.3 / 32.1) | 27.6 | 21.3 / 41.1 | 103 | 10.4 × 3.3 | 692 |
| cleft 0 | 27.3 | 39.7 | 33.1 | 27.6 | 21.3 / 42.8 | 103 | 10.4 × 3.3 | 694 |
| cleft +1 SD | 28.8 | 39.7 | 33.1 | 27.6 | 20.8 / 44.7 | 103 | 10.4 × 3.3 | 673 |
| no cleft −1 SD | 24.8 | 38.1 | 32.9 (34.1 / 31.9) | 27.5 | 23.2 / 40.2 | 120 | 10.4 × 3.2 | 810 |
| no cleft 0 | 27.5 | 38.1 | 32.9 | 27.5 | 23.2 / 42.9 | 120 | 10.4 × 3.2 | 809 |
| no cleft +1 SD | 29.1 | 38.1 | 32.9 | 27.5 | 23.0 / 44.6 | 120 | 10.4 × 3.2 | 803 |

What the check says:

1. **Scale is plausible.** The synthetic palate length (24.8 to 29.1 mm) brackets the 27.9 mm at-birth hard palate, so the synthetic cases are not grossly off-scale [inference].
2. **On paper, the standard extension sits outside the normal-newborn envelope, by 2 to 5 mm.** The tip lands about 27.6 mm below the PNS proxy, which is 2.1 mm below the normal-newborn hyoid level. The straight PNS-to-tip distance (32 to 34 mm) is 3 to 5 mm longer than the rough 28.9 mm tongue-dorsum path, and a straight line should be shorter than any curved path between the same points [inference]. The arm centreline (38 to 40 mm) is longer still, but it also includes the run along the soft palate, so it is the weakest of the three comparisons.
3. **The more likely reading is that the crude envelope is too tight, not that the extension is too long** [inference]. The tip depth is set by the published extension's fixed 26.6 mm drop below its foot, plus where the synthetic vault puts the root, so it hardly changes with palate size. Robin infants are known to have a hyoid that sits lower relative to the palate (Lee 2016, direction only). Vorperian's norms are fitted means for normal infants, and their "below PNS" is measured in a head frame, not this occlusal-plane frame. A proxy PNS on the oral palatal surface also sits below the bony PNS, which, if anything, makes the gap larger. Settling this needs a measured infant PNS-to-epiglottis-tip distance, and none is tabulated in the open literature.
4. **The 2 mm prototype set spans less than half of the published variation** [inference]. P1 to P3 cover 4.0 mm front to back (SL5 spread 4.0 mm). That compares with the 9 mm posterior tongue displacement in Robin sequence (Perez 2020) and the 8 mm SL5 difference between two real plates (Bioengineering 2022). The endoscopy-driven changes (`adjust`, A<n>) have to cover the rest, which matches how the plates are fitted in practice.
5. **The section metrics behave as expected.** The cleft variant's α1 is about 103° and the no-cleft variant's about 120°, consistent with the no-cleft file's flatter arc. S0 thickness stays at or above 3.2 mm on every prototype, so it meets the published 3 mm minimum. The thinned A5 and the groove final F show up at S0 (3.0 and 1.85 mm). Shortened devices show a smaller EL end (A1: 18.6 mm against P2's 21.0 mm).

## Limits

- Synthetic palates only, all built from one generator, so the spread across cases understates real variation.
- The PNS is a proxy, and the airway landmarks are empty until imaging fills them.
- The S0 and section metrics follow the paper's description. Its Grasshopper program was not available, so values are not guaranteed to match theirs to the millimetre.
- A pre-existing issue was noticed but not fixed. In the no-cleft cases, prototype STLs that pass the in-memory "mesh watertight" QC check do not reload as watertight from disk. The volume falls back to the section integral there. This is worth a look before printing.

## Next

Experiment 2 (triage and segment real infant CT) can now write straight into this vocabulary: `vallecula`, `epiglottis_tip`, `pns`, `tb_ppw_distance_mm`, `tb_epiglottic_angle_deg`. Its first job should be the missing number, PNS to epiglottis tip in newborns, which would replace point 3's inference with a measurement.
