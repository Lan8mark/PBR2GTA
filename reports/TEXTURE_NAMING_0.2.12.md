# Texture naming verification — 0.2.12 Alpha

Verified 2026-09-16 from the installed release ZIP.

SHA-256: `94406c66b97e2011429c14e809ef304999067c2947e464b66b9c237d4cecbd05`. Package: 157 files, 50,995,718 bytes.

## Cause and fix

Version 0.2.11 rejected two configured materials with the same output filename before starting conversion. The original failure was reproduced from its installed ZIP: `Duplicate output texture name: same_shared_d.dds`.

Generated filenames now use material names: `Chain` becomes `chain_d.dds`, `chain_s.dds`, `chain_n.dds`; `Chain.001` retains its numeric suffix. Additional slots use their sampler names. Saved manual names and old sampler filenames no longer select conversion output names.

Names are allocated across the open file with case-insensitive collision checks. Collisions use persistent material identifiers with expanding suffixes; names assigned to retained textures are reserved. Material copies receive independent identifiers. Preview, export and the Advanced display use the same allocator. Rename/allocation changes during conversion cancel before application.

The worker protocol and conversion/calibration algorithms are unchanged. Output names already participate in the cache key. Assignment validation checks the worker's files against the planned material/sampler mapping and checks Sollumz's resulting texture name. Sollumz obtains it from the image filepath in the tested versions; no new writable node property is required.

## Results

113 Python tests passed, including real NVTT conversion, renamed cache outputs, normalized-name collisions, retained names, long/non-ASCII names and expanding identifier prefixes.

| Scenario | Result |
| --- | --- |
| Two materials on one mesh, common PNG sources and identical legacy output names | PASS: six distinct DDS names, shared material converted once |
| Different PNG sources | PASS: distinct diffuse bytes and correct material assignments |
| Native/XML texture references and YTD filenames | PASS: read back and resolved to DDS, including YDD/all test LODs |
| Preview, repeated preview/cache, then export | PASS: names and bindings agree |
| Disabled material retaining an existing DDS | PASS: reserved name, original image/path/file preserved |
| Normalized-name collision and copied material | PASS: distinct names; original UUID preserved |
| Rename during worker | PASS: specific cancellation diagnostic; final directory and image assignments unchanged |
| Injected failure after image application | PASS: material/file rollback and successful retry |
| Undo/Redo and save/reopen | PASS: naming snapshot preserved |
| Palette and ordinary texture profiles | PASS: RGBA8 palette without mipmaps; profile-specific BC1/BC3/BC5U and 512px chains ending at 4px |

## Installed ZIP matrix

| Sollumz | Blender | Recorded cases | Result |
| --- | --- | --- | --- |
| 2.7.0 | 4.2.0 | 23 | PASS |
| 2.7.1 | 4.2.0 | 23 | PASS |
| 2.7.2 | 4.2.0 | 23 | PASS |
| 2.8.0 | 4.5.11 LTS | 25 | PASS |
| 2.8.1 | 4.5.11 LTS | 25 | PASS |
| 2.8.2 | 4.5.11 LTS | 25 | PASS |
| 2.8.3 | 4.5.11 LTS | 25 | PASS |
| 2.9.0 | 4.5.11 LTS | 24 | PASS |
| Custom 2.8.2 | 5.2.1 LTS Store | 25 | PASS |

Each run installed this exact ZIP and compared every packaged file against the installed copy. Tests used isolated profiles and temporary scenes; no user project was overwritten. The oldest supported Blender branch was tested with 4.2.0. Readback covers exported data and references, not in-game rendering.

## Evidence and reproduction

- [Original failure](evidence/texture-naming-0.2.12/baseline.json)
- [Package identity, cases and readback counts](evidence/texture-naming-0.2.12/verification.json)
- Run `python scripts/check_sollumz_releases.py --run` after preparing the documented isolated environments.
- Run `tests/blender/naming_readback.py` with the matching Python 3.11 dependencies and the per-version result directory for independent reference checks.

Release hygiene checks found no old author name in the ZIP and no confirmed secrets. The three Gitleaks matches are unchanged OpenCV G-API type declarations, reviewed as false positives. NVTT is not bundled.
