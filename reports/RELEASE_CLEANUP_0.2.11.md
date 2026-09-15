# Release cleanup verification — 0.2.11

Verified 2026-09-15 from the complete installable ZIP.

SHA-256: `a2c4899d376a4531d3cf7004a7ef7761ad791e2d04b46465d42647c77d466a10`.

The package contains 156 files and occupies 50,993,756 bytes (48.63 MiB), down from 64,532,428 bytes (61.54 MiB). Runtime conversion, shader calibration and export code are unchanged.

| Finding | Resolution | Verification |
| --- | --- | --- |
| Local paths in shader-reference resources | Replace machine-specific provenance with portable source identifiers; catalog generation uses the same policy | Re-scan final ZIP, tracked source and prepared Git history |
| Personal paths and scene names in diagnostics/history | Redact public evidence, preserve original evidence privately; prepare sanitized history and historical ZIPs | 323 history blobs and all 9 historical ZIPs checked for the original personal identifiers |
| Missing dependency licenses | Collect licenses using distribution metadata, include Python's Windows license and incorporated-software notices, fail on missing notices | Release-package checks and tests for missing notices and prohibited content |
| Default source/documentation version mismatch | Align current source, README and release version at 0.2.11 | Manifest, source and installed ZIP verification |
| Oversized OpenCV collection | Remove optional video plugin, classifier data, typing files and development catalogs from ZIP | End-to-end preview and export from the packaged worker |
| JPEG XMP metadata | Remove XMP segments without re-encoding JPEG image data | Decoded pixels are identical for all three images |
| Technical TODO label | Explain that the slot uses existing DDS | Loaded UI code verified after installation |

## Checks

- 108 Python tests passed, including three release-package tests covering missing notices, private-path rejection with upstream examples preserved, and accidental development files.
- All eight official Sollumz release cases passed: 2.7.0, 2.7.1, 2.7.2, 2.8.0, 2.8.1, 2.8.2, 2.8.3 and 2.9.0. The matrix installed this exact ZIP before each case.
- Store Blender 5.2.1 with the customized Sollumz 2.8.2 passed 15 cases in an isolated profile. The Microsoft Store launcher was used to start this installation.
- Checks include preview, YDR/YDD, mixed enabled/disabled materials, XML/native routes where available, DDS headers/mip counts, palette, live values, selection preservation, damaged parameters, Undo/Redo and save/reopen.
- The same ZIP was installed in the user's running Store Blender. All 156 file hashes matched; preferences and scene state were preserved, and obsolete package files were removed.
- Gitleaks found no confirmed secrets in the tracked source or frozen worker strings. Its three package matches are the previously reviewed OpenCV type-name false positives at `cv2/gapi/__init__.py:229,234,235`.
- The sanitized history was scanned with Gitleaks; no confirmed secrets were found. A separate byte-content check found no original personal identifiers in the final ZIP, all prepared historical ZIPs, or the 323 inspected Git blobs.

## Evidence

- [Package identity and coverage](evidence/release-cleanup-0.2.11/package.json)
- [Eight-release matrix](evidence/release-cleanup-0.2.11/matrix.json)
- [Store Blender with custom Sollumz](evidence/release-cleanup-0.2.11/store-custom.json)
- [User installation](evidence/release-cleanup-0.2.11/installation.json)
- [Image pixel preservation](evidence/release-cleanup-0.2.11/images.json)

## Publication status

On 2026-09-15 the owner deleted the original private repository and authorized
publication of a new repository at the same address. The new repository is public
and has a different GitHub ID. The cleaned branches and tags and all ten prereleases
were restored. GitHub asset digests match the verified local ZIPs and checksum files.

Unauthenticated web and raw-file requests confirm public access. Authenticated
API checks verify all ten releases (the anonymous API was rate limited). The known
old commit returns HTTP 422, and its original resource returns HTTP 404; neither is
accessible through the new repository. This verifies the tested endpoints, not
erasure of GitHub's internal retention backups.

The publication check re-scanned 26 commits with Gitleaks and inspected 331 Git
blobs and all ten ZIPs for the original personal identifiers; none were found.
Repository README preview labels were updated for public availability. Release
ZIPs were preserved byte-for-byte, including their historical documentation, so
the installed-package verification above still identifies the same artifact.
Original history and distribution backups remain local and were not published.

These results identify the ZIP above. They do not certify later builds or amount to a complete vulnerability audit of every bundled native dependency. Historical executable code was not modified during metadata cleanup; original archives are preserved privately.

## Author rename — 2026-09-15

The public project and release documentation now use `cre8vy`. The current ZIP
changes only four Markdown documents; all other entries, including executable
code and resources, are byte-identical to the tested ZIP identified above.
The full functional matrix was not repeated for this documentation-only update.

Current ZIP SHA-256: `a2b5550e41bbdd05272feb45026f9f26b6aca390ae7e05fcc00601ca785a432d`.
The public repository retains one initial commit and the 0.2.11 prerelease.
