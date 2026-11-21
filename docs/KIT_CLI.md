# Kit and chairside CLI contract (for PEBP Studio)

This is the contract between the `pebp` command line and PEBP Studio for the two-visit chairside route. Studio calls only these commands and reads only `design.json` and the files listed here. If a command's shape changes, this file changes in the same commit.

**Status: research route, untested.** Nothing in it has been printed, cast, bonded or worn. Studio should show it with an "untested research route" tag, as it does for the graded plate. One-piece casting (`pebp mold`) stays the default route.

All commands run from `pebp_demo/` as `python -m pebp ...`. Paths below are relative to the case folder unless they say otherwise.

## Order of the steps

| When | Command | Needs first |
|---|---|---|
| Visit 1, after the scan | `pebp design SCAN --out CASE` | – |
| Visit 1 | `pebp kit CASE` | `design` |
| Visit 1 | `pebp chairside CASE --base` | `kit` |
| Between visits (lab) | cast the final base with `kit/chairside/base_tooling/` | `--base` |
| Visit 2, try-in | log the fitting with the existing form, naming K<n> | `kit` |
| Visit 2, after the try-in | `pebp chairside CASE --kit K<n>` | `--base` (refused otherwise) |
| Stock, once per kit | `pebp chairside --stock-tooling DIR` | – |
| Bench, once | `pebp bench DIR` | – |

`--base` has to run before `--kit`. The base is cast before the try-in, so the worn plate must be assembled on that same base. `--kit` rebuilds the base geometry and refuses (exit 2) if it no longer matches the stored one, within 0.5 mm³, or if the plate thickness differs. Then run `--base` again and cast the new base.

## A `pebp design` rebuild

`pebp design` writes a new `design.json`. The new record has no `kit`, `chairside_base` or `chairside_finals` blocks, and no `design_choices.try_in_kit`. The files these commands wrote stay on disk but no longer belong to the record.

When Studio archives the old record on a rebuild, it should move the whole `kit/` folder with it. That folder holds `kit/base.stl`, `kit/batons/`, `kit/K<n>_equivalent.stl`, `kit/stock/` and `kit/chairside/`. After the rebuild, the user runs `kit` and `chairside --base` again.

An S<n> that was physically made from the old base stays valid as a physical device. It is only in the archived record.

## Exit codes (all commands below)

| Code | Meaning |
|---|---|
| 0 | done; QC is `pass` or `warn` |
| 1 | done and files written, but QC (or a tooling check) is `fail` |
| 2 | refused input, via `_fail`; nothing written. The message is on stderr and starts with `error:` |

`pebp kit` keeps its existing behaviour: it exits 0 even when a baton's QC fails. Read each baton's `qc.overall`.

## QC shape

Every QC block is `{"checks": [{"check", "status", "value", "rule"}, ...], "overall": "pass"|"warn"|"fail"}`, built by `qc._check` and `qc.overall`, so Studio's existing table and STOP gate work unchanged. Every check this route adds is fail-level. Warnings come only from the shared prototype or final QC, for example "extension length vs standard" on the long kit batons.

## `pebp kit CASE`

Arguments:

| Argument | Default | Allowed |
|---|---|---|
| `--lengths` | `-2,0,2,4,6` | comma-separated mm |
| `--angles` | `-8,0,8` | comma-separated degrees |
| `--width` | `10.4` | `>= 5.2` (exit 2 below that: never narrowed by more than half) |
| `--no-stock` | off | skip writing `kit/stock/` |

`--stock DIR` with no case writes only the patient-independent set. A case built with `--extension parametric` is refused (exit 2).

Files: `kit/base.stl`, `kit/batons/K<n>.stl`, `kit/K<n>_equivalent.stl`, `kit/stock/*.stl` and `kit/stock/kit_stock_manifest.json`.

`design.json` keys:
- `kit`:
  - `flag`, `variant`, `params`, `base_file`, `base_sha256`;
  - `socket` (`roof_min_mm`, ...), `plug_strength_vs_baton`, `tip_envelope`;
  - `batons[]`, each with `id` K<n>, `code` such as `L+2/T+0`, `params`, `file`, `piece_file`, `tip`, `tip_shift_vs_standard_mm` and `qc`.
- `design_choices.try_in_kit` = `{value: true, grid, chosen_by, flag, time}`.
- `tpp_metrics` and `reach_check` gain K1–K15.

Checks per baton: the prototype QC, without its watertight and single-body checks, plus these (all fail-level):
- kit base watertight;
- kit baton watertight;
- baton clear of base (< 0.05 mm³);
- plate above the tunnel (≥ 1.0 mm).

## `pebp chairside CASE --base` (visit 1)

Arguments: `--final-thickness`, default 2.5, allowed 2.5 to 3.0 (exit 2 outside that range).

Files:
- `kit/chairside/final_base.stl`, in the design frame;
- `kit/chairside/base_tooling/working_model.stl`, `counter_oral.stl` and `tunnel_core.stl`, in pour orientation;
- `kit/chairside/base_tooling/manifest.json`, with the validation and demold sequence.

`design.json` key `chairside_base`:
- `file`, `sha256`, `volume_mm3`, `final_plate_thickness`;
- `kit_params`, `chairside_params`;
- `socket`, `pocket`, `pocket_roof_mm`;
- `tooling` = `{dir, files, pmma_needed_ml, manifest_sha256}`;
- `material`, `qc`, `flag`, `time`.

A second `--base` replaces the block.

Checks (all fail-level):

| Check | Rule |
|---|---|
| base printable/castable | one closed solid |
| plate above the tunnel | ≥ 1.0 mm |
| plate above the pocket | ≥ 1.0 mm |
| tooling valid | pieces watertight and one body each; no overlaps; the cavity matches the base; core, counter and base each slide free |

## `pebp chairside CASE --kit K<n>` (visit 2)

Arguments: `--kit`, which must be a K<n> registered in `kit.batons` (exit 2 otherwise), and `--final-thickness`, which must equal the base's.

Files, in `kit/chairside/`:
- `K<n>_final_baton.stl`;
- `K<n>_wire.stl`;
- `K<n>_pickup.stl`;
- `K<n>_assembled.stl`.

`design.json`:
- `chairside_finals[]` gains one entry per run:
  - `n`, `id` S<n>, `from` K<n>, `code`, `file` (the assembled plate), `sha256`, `params`, `final_plate_thickness`;
  - `files` (including `final_base`), `base_sha256`;
  - `wire` (`length_mm`, `crosses_joint_by_mm`, `end_mm_before_tip`, ...);
  - `pickup_volume_mm3`, `pocket_roof_mm`, `chairside_params`;
  - `qc`, `flag`, `steps[]` (the chairside steps, for Studio's checklist) and `time`.
- `tpp_metrics` and `reach_check` gain S<n>.
- `record.device("S<n>")` returns kind `chairside`, wired true, with the reserve equal to `end_mm_before_tip`. `device_ids` lists K<n> and S<n>.

Checks: the final QC (plate thickness, safety wire present, root supported, ...), plus these (all fail-level):

| Check | Rule |
|---|---|
| wire enclosed | at least 0.25 mm of resin all round the wire |
| wire crosses the joint | ≥ 8 mm in front of the plate border |
| wire hooked in the pocket | the hook rises above the tunnel roof |
| plate above the pocket | ≥ 1.0 mm |
| plate above the tunnel | ≥ 1.0 mm |
| stock baton body | the PMMA around the wire is one closed solid |

## `pebp chairside --stock-tooling DIR [--only K5,K8] [--variant cleft|nocleft]`

This needs no case. It writes `DIR/K<n>/` containing:
- `mold_left.stl` and `mold_right.stl` (split at x = 0);
- `wire_jig.stl`;
- `final_baton.stl` and `safety_wire.stl`;
- `manifest.json`.

It also writes `DIR/stock_tooling_manifest.json`. All of it is in the attachment frame: border at y = 0, plate mid-thickness at z = 0. Exit 1 if any mold fails its checks.

## `pebp bench DIR [--baton K5] [--variant cleft|nocleft]`

This needs no case and makes synthetic specimens only. It writes:
- `coupon_S_*`, the base and its tooling, the baton tooling, the pick-up and the assembled coupon;
- `coupon_C*`, with its split mold and wire;
- `shear_substrate_tray.stl` and `shear_collar_half.stl`;
- `monomer_disc_tray.stl`;
- `bench_results_template.csv`;
- `bench_manifest.json`, with `tests[]`, the 8 tests and their pass rules.

Exit 1 if any specimen or tooling check fails. The protocol is `docs/CHAIRSIDE_BENCH.md`.

## Flag text

- `design_choices.try_in_kit.flag` and `kit.flag`: the `kit.LABEL` string.
- `chairside_base.flag` and each `chairside_finals[].flag`: `chairside.LABEL`, which reads "project heuristic: chairside pick-up of a stock wired baton into the worn plate, untested (no bond, fatigue, fracture, pull-out or cleaning data); research route, not for clinical use".
- Tooling manifests: `chairside_tooling.LABEL`. Bench: `bench.LABEL`.

Studio should show the stored `flag` string, not a copy of it, so a wording change reaches the UI without a Studio change.
