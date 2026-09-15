# Changelog

## 0.2.13 — Alpha: normal-map mip correction

- Fix normal-map mip pixels corrupted by NVTT 3.2.5's `-normal` path. Ready normals now use linear channel filtering without gamma correction, retaining the existing BC5U/BC3 selection and 4×4 mip limit.
- Invalidate previous conversion cache entries so regenerated normals cannot reuse the affected DDS files. Run Preview or export again to regenerate textures.
- Add decoded-pixel regression checks for neutral, tilted and alternating normals, including BC3 alpha masks.

## 0.2.12 — Alpha: material-based texture naming

- Name generated DDS files after the material (`chain_d.dds`, `chain_s.dds`, `chain_n.dds`), including preview and additional sampler slots.
- Resolve name collisions across the open file automatically, preserving texture names used by disabled materials and untouched slots.
- Allow distinct materials to share PNG sources or use different sources without conflicting legacy output names.
- Show calculated names in Advanced; saved manual output names no longer affect conversion.
- Validate exported texture assignments and cancel safely if naming inputs change during conversion. DDS formats, mipmaps and calibration formulas are unchanged.

## 0.2.11 — release packaging and privacy cleanup

- Remove local machine paths from shader reference provenance and published diagnostics. Catalog generation now records portable source identifiers.
- Package Python, NumPy and OpenCV licenses explicitly and fail the build if required notices or privacy checks fail.
- Exclude optional OpenCV video plugins, classifier data, typing files and development catalogs from the installable ZIP.
- Refresh packaged documentation and clarify the existing-DDS slot label. Conversion, calibration and export behavior are unchanged.

## 0.2.10 — exclude auxiliary PNG sources from export

- Prevent Sollumz from embedding or copying PNG inputs on auxiliary image nodes of converted PBR2GTA materials. Shader samplers and unrelated textures keep their existing export behavior.
- Restore the source nodes' Embedded flags after success or failure. Source images remain in the material.
- Extend the installed-ZIP release matrix with embedded PNG source nodes and a real embedded DDS sampler.

## 0.2.9 — live shader parameters and Sollumz compatibility

- Maintain calibrated shader values while Use PBR2GTA is enabled, including Surface edits, node edits and scene history. Report damaged parameter nodes in the material panel.
- Keep PNG conversion and YTD creation on export. Resume Sollumz using its effective preferences, restoring settings and selection after completion or failure.
- Adapt to legacy XML, nested settings and flat settings by API capabilities. Support the shader catalog and texture sidecars without requiring szio on older Sollumz.
- Add a repeatable installed-ZIP compatibility runner for every published Sollumz release from 2.7 onward.

## 0.2.8 — texture set detection

- Add Auto Detect Texture Set to supported PBR materials, using the web batch filename dictionary for both workflows.
- Select any PNG in the desired set to assign its sibling maps. Reject incomplete or ambiguous sets before changing material inputs.
- Keep detection scoped to the originating material and block it during conversion.

## 0.2.7 — reliable export and material previews

- Keep the original export objects and settings while textures convert, even when selection changes. Reject changed material inputs before applying results.
- Stage YDR/YDD, DDS and YTD XML together. Restore destination files and materials after failed export or publication; preserve recovery backups if restoration is blocked.
- Validate required calibration nodes before conversion and report the affected material and parameter.
- Restrict cache cleanup to owned entries, with a lock during conversion. Existing cache folders and preview files are preserved.
- Isolate copied material previews and release replaced, unused generated images after successful operations.
- Fix the NVTT setup popup and retry flow.
- Edit the English shader reference for correct terminology, channel names and numeric ranges.

## 0.2.6 — YDD material calibration

- Use Sollumz's asset and material collectors to process all Drawable members and LOD meshes in a YDD; shared materials are converted once.
- Calibrate supported PBR materials before both modern YDR/YDD export and legacy CodeWalker XML export. Disabled materials retain their settings.
- Give copied materials distinct conversion IDs to prevent one material's results from replacing another's.
- Generate an external YTD XML and DDS folder for exported Drawable Dictionaries as well as individual Drawables.

## 0.2.5 — preserve Drawable names

- Removed author watermarking from native YDR and YDR XML exports. Internal names now remain as exported by Sollumz.
- Shader parameter calibration, DDS conversion and YTD sidecar export remain active.

## 0.2.4 — mipmaps down to 4 × 4

- Stop generated DDS mipmaps at size 4; omit the 2 × 2 and 1 × 1 levels for square textures.
- Update DDS validation and transport manifests to match. Palettes still have no mipmaps.
- Invalidate previous worker cache entries so new conversions use the updated mip chain.

## 0.2.3 — English and Russian shader reference

- Added an EN / RU switch for the bundled shader reference, with a saved language preference.
- Removed the Evidence tab and its technical checking information from the reference window.
- Made English the primary repository documentation, with separate Russian README and quick start.

## 0.2.2 — shader reference

- Added a `?` next to the selected shader in PBR2GTA, available even when material conversion is disabled.
- Bundled the custom Sollumz shader reference: all 296 presets, with maps/channels, parameters, vertex colors, UV and evidence notes.
- Preserved preset-specific overrides and incomplete/uncertain evidence. The custom Sollumz guide module is no longer required by PBR2GTA.
- Validated all 1,184 preset/section combinations in Blender 5.2.1; 51 Python tests passed.

## 0.2.1 — private preview

- PBR conversion for supported Metallic/Roughness and Specular/Gloss materials.
- Local PNG-to-DDS workflow integrated with Sollumz export and material preview.
- Required NVTT setup with official download link, automatic discovery and BC5U validation.
- Click-to-open Surface reference with a scale and one Close button.
- Automatic external-texture YTD XML and DDS folder export.
- Internal Drawable name `cre8vy` for native YDR and YDR XML after PBR conversion.
- Clearer export diagnostics and rollback of temporary conversion changes on failure.

This is a private testing release. See `docs/QUICKSTART.md` for requirements and limitations.
