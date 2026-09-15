# Sollumz compatibility

PBR2GTA uses the installed Sollumz export operator and its effective settings. Texture conversion stays local; NVTT is installed separately.

| Sollumz releases | Export available in Sollumz | Compatibility API |
|---|---|---|
| 2.7.0, 2.7.1, 2.7.2 | CodeWalker XML | Preference-based XML export |
| 2.8.0 | Native GEN8/GEN9 and CodeWalker XML | Nested custom settings |
| 2.8.1, 2.8.2, 2.8.3, 2.9.0 | Native GEN8/GEN9 and CodeWalker XML | Flat custom settings |

The older releases are checked in Blender 4.2; the native exporters are checked in Blender 4.5. Use a Blender version supported by your Sollumz release and install its requested dependencies. In particular, the original PyMateria 0.1.0 package requested by Sollumz 2.8.0 does not load correctly in the tested Blender 5.2 / Python 3.13 environment.

With **Use PBR2GTA** enabled, approved value parameters are maintained directly on the material. Surface changes apply immediately. Missing or incompatible parameter nodes are reported in the material panel; PBR2GTA does not rebuild the material. Disabled materials remain under manual control.

Export snapshots the effective settings, converts PNGs, temporarily applies those settings and the original object selection, calls Sollumz, and restores preferences and selection. DDS, assets and YTD sidecars are staged together before publication.

Future releases using these capabilities can use the same adapter. A future Sollumz change can still break compatibility; a new version is considered verified only after its own smoke run.

## Repeating the release checks

Use the [source tree for the matching addon release](https://github.com/cre8vy/PBR2GTA/tree/v0.2.13), which includes the test runner. On Windows with `gh`, the project's Python environment, the full addon ZIP and NVTT installed:

```powershell
python scripts/check_sollumz_releases.py --prepare --run
```

`--prepare` discovers all published stable Sollumz releases starting at 2.7, downloads their official ZIPs and installs their declared dependencies into isolated directories. `--run` launches each release with an isolated Blender profile, installs the addon ZIP and records results. Use `--versions 2.8.0,2.9.0` for a subset, and `--blender-old` / `--blender` to specify Blender executables (Python 3.11 builds).

The smoke covers material calibration, manual value edits, opt-out, shader help, preview, YDR/YDD, all test LODs, a shared and a disabled material, selection changes, settings restoration, DDS headers, XML parameter readback, Native loading where available, legacy export, broken parameter reporting and scene history. It creates temporary scenes; it never opens a user project.

Results and launcher logs: `build/compat-matrix/<version>/`. Source ZIP hashes: `build/compat-matrix/releases.json`. Timeout results report the isolated process ID for inspection instead of terminating an arbitrary Blender process.
