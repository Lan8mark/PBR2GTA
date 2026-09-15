# Development

The Blender extension lives in `src/pbr2gta_blender`. The local worker and conversion code live in `src/pbr2gta_core`. Runtime shader manifests are included alongside both packages.

Use Python 3.11+ on Windows:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest
```

Tests that run real texture compression require a separately installed NVTT. No NVIDIA executables are included in the repository or release.

Build the installable Blender ZIP:

```powershell
.\scripts\build.ps1
```

The script runs tests, packages the local worker with PyInstaller, includes the Blender extension and dependency notices, and creates `release/PBR2GTA_Addon_<version>.zip` with `SHA256SUMS.txt`. The build replaces its staging directory and the ZIP for that version; other release ZIPs are retained. User documentation and required runtime files are packaged; reports and development catalogs remain in the repository. License completeness and private-path checks run before packaging.

The website and cloud service are separate projects. This repository contains the local Blender product. The comparison layout is editable in `docs/media/comparison.html`; its original screenshots are stored beside it.
