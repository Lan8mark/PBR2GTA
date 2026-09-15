# Normal-map mip correction — 0.2.13 Alpha

Verified 2026-09-17 using NVIDIA Texture Tools 3.2.5.

## Reproduction and cause

Compress a constant RGB `(128,128,255)` PNG with the previous normal profile. Decode each BC5U block independently: mip 0 contains RG `(128,128)`, but every smaller mip contains `(104,104)`. This changes the surface direction. Constant tilted normals also change: RG `(204,128)` becomes `(156,98)`.

Controlled CLI comparisons isolate `-normal`: removing only that flag preserves the neutral normal. Keeping `-normal` and changing Kaiser to box, or removing the explicit no-gamma flag, still reproduces the defect. The values are consistent with normalizing packed unsigned RGB without first expanding it to signed vectors. `-tonormal` is the separate height-to-normal conversion option; it was never used here.

## Change

Ready normal maps use linear channel filtering (`-no-mip-gamma-correct`) without the affected `-normal` path. Existing BC5U/BC3 selection, alpha policy, compression quality, Kaiser filter and 4×4 endpoint remain. Transport profile sources and generated manifests agree with the command. The worker version changes the conversion cache key; old cache entries are not reused.

Already exported DDS files must be regenerated with Preview or export. Existing DDS slots that explicitly copy user-supplied files remain unchanged.

## Verification

- **120 Python tests pass**, including seven new real-NVTT decoded-pixel cases: neutral and tilted normals in BC5U and BC3 with alpha, plus alternating opposite tilts. Every mip is checked, down to 4×4.
- Worker extracted from the release ZIP produces neutral RG values on every level, rather than merely passing DDS header validation.
- Installed ZIP: Blender 4.5.11 / Sollumz 2.9.0 — **24 scenarios pass**; Store Blender 5.2.1 / custom Sollumz 2.8.2 — **25 scenarios pass**. Both installations verify all 157 packaged files against the ZIP. Preview, YDR/YDD export, naming, disabled materials and rollback scenarios pass.
- Three exported normal DDS files from each Blender run were decoded independently: neutral RG survives every mip.
- Other Sollumz releases were not rerun for this compressor-only change; the previous compatibility matrix remains historical evidence. No in-game rendering test is claimed.

ZIP SHA-256: `afcd6bf1482df3121f7acc2dcd55573016d16681122d86e80a8dafbebd8c5b5f`.

Machine-readable evidence: [verification.json](evidence/normal-mips-0.2.13/verification.json). Regression tests: [test_normal_mips.py](../tests/test_normal_mips.py).
