# Changelog

PEBP Designer and PEBP Studio. Research use only, not for clinical use. Versions follow [semantic versioning](https://semver.org); before 1.0, a minor version may change design records and outputs.

## 0.1.0 (unreleased)

The first numbered release, and the first with downloads.

### Added

- **Downloads.** PEBP Studio frozen with its own Python for macOS (Apple silicon and Intel) and Windows x64, built by `.github/workflows/release.yml` and smoke-tested on synthetic scans before each release. Install steps in `docs/INSTALL.md`.
- A research-use-only notice on every Studio screen, in the downloads (`RESEARCH_USE_ONLY.txt`) and in `pebp --version`.
- `pebp --version`.
- `scripts/studio_check.py --exe PATH` runs the Studio server checks against a built download.

### Fixed

- On Windows, writing the airway report and other files with Greek letters or arrows failed (cp1252 default encoding). The download runs in Python's UTF-8 mode.
- Device STLs that were closed solids in memory but not after being written and read back. Cutters that lay exactly along a surface (the number dimples and wire groove on the flat faces of the no-cleft extension, and the tissue clip along the fitting surface) left pinched meshes. The cutters now stand 0.05 mm (dimples, groove) or 5 µm (tissue clip) off the surface, and QC checks the STL round trip for every written device.

### Included from the first public code drop

- Scan to design: landmarks, design frame, cleft block-out, base plate, and the published Tübingen standard extension (cleft and no-cleft variants) as the default baton.
- Three try-in prototypes, P1–P3, 2 mm apart; endoscopy fitting form; changes made by deforming the published baton; device registry (P, A, F, C).
- QC against the published Tübingen 2025 rules and the Bioengineering 2022 plate measurement scheme.
- Printed tooling for casting the final plate in cold-cure PMMA with its safety wire: four-piece mold, cradle, wire jig and demold simulation.
- A modular try-in kit, a two-visit chairside route and a per-patient catalog of modular versions (research routes, untested).
- PEBP Studio, the six-step clinician UI that runs on 127.0.0.1 only.
