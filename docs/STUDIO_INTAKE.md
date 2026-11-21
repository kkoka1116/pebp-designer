# PEBP Studio: what to have ready before a case

Fill this in (on paper or in a copy of this file kept on this Mac) before you open the app. Each item says where it goes in the app. Nothing here is decided by the software: the clinical choices are yours, and the app records who made them.

Do not write the patient's name, birth date or record number anywhere in the app. Use a case code.

## 1. Before you start

| What | Example | Where it goes |
|---|---|---|
| Case code | `case07` (letters, numbers, `-`, `_`, `.`; no spaces) | **New case** |
| Cleft type: hard and soft palate, soft palate or uvula only, or none | from the examination | Step 1, "Cleft type". A cleft of the hard and soft palate takes the Tübingen cleft baton; a cleft of the soft palate or uvula only, or no cleft, takes the no-cleft baton (both published, Aretxabaleta et al. 2025). It also sets how the plate bridges the palate. The app never decides it from the scan, which cannot show the soft palate. |
| Your name or initials | `KK` | Step 1, "Your name or initials". It is saved with every approval. |
| The maxillary intraoral scan, as STL | exported from the scanner, whole palate and both tuberosities | Step 1, "Choose File". Put the file in `Scans to design/` first. |

**Scan checklist.** The landmark finder needs the whole hard palate, the alveolar ridges to the tuberosities on both sides, and the cleft margins if there is a cleft. A scan that stops short of the tuberosities gives a wrong design frame. (Project heuristic, from how the finder works: it places the origin midway between the tuberosities.)

## 2. Plate choices (step 3)

| Choice | Default | Basis |
|---|---|---|
| Plate profile: uniform or graded (low profile) | uniform | Uniform is the Tübingen plate (published, Aretxabaleta et al. 2025). Graded is a project heuristic, not bench tested. |
| Thinnest wall, if graded | 1.5 mm | project heuristic |
| Border relief (flange cap, frenulum notch, tuberosity relief) | off | project heuristic, aimed at the commonest ulcer sites |
| Prototype plate thickness | 2.0 mm | Tübingen 2025 |
| Final plate thickness | 2.5 mm (2.5–3 mm) | Tübingen 2025 |
| Route: one-piece cast plate or two-visit chairside kit | cast plate | The kit route appears only when that code is installed, and is an untested research route. |

## 3. Clinical choices (step 4)

These start unset, and the app will not set them for you.

| What | Options | Notes |
|---|---|---|
| Tip target | above the epiglottis rim, or in the vallecula | Used only to flag try-in findings. The app logs who set it. |
| Imaging values (optional) | vallecula, epiglottis tip (x, y, z in mm), tongue-base to posterior-pharyngeal-wall distance, tongue base–epiglottic angle | Only after the imaging has been registered to the design frame. Used for a plausibility check. |

## 4. After each endoscopy try-in (step 4)

Bring the notes from the try-in in this form, one line per change:

| Device tried | Change | Amount | Lab or bedside | Note |
|---|---|---|---|---|
| P2 | shorten | 1.5 mm | lab | tip on the epiglottis |
| | | | | |

The change menu in the app lists the vocabulary (shorten, lengthen, angle, curve, narrow and so on), with the usual amount for each. Then pick the device to cast.

## 5. Before casting (step 6)

| What | Default |
|---|---|
| Device to cast | chosen in step 4 |
| Final plate thickness | 2.5 mm |
| Mold wall | 6 mm |
