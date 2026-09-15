# Shader and texture-slot catalog

Snapshot generated on 2026-09-12 from the standard Sollumz 2.9.0 registry and the author's custom shader reference. Source hashes and provenance are recorded in `coverage.json`.

## Coverage

- 249 base definitions expand to 296 exact `.sps` selectors.
- These selectors form 283 strict conversion-interface equivalence groups. Twelve groups contain aliases with matching base shader, render bucket, parameters and vertex layouts. This does not by itself establish identical shader bytecode.
- Two separate byte-identical FXC alias pairs were verified: `trees_normal_spec` / `trees_normal_diffspec`, and their `_tnt` variants.
- The registry contains 72 texture-slot names and 1,143 shader/slot pairs, each assigned a semantic profile.
- 1,098 pairs are active. The remaining 45 registry declarations produce no user-authored signal in active DXBC: 37 structural declarations and 8 explicitly documented unused reads. Inactive pairs remain in the inventory for completeness; they should not appear as conversion inputs in the UI.
- There are 135 conservative semantic profiles. Grouping uses channel reads, formulas and notes; slot names and artist-facing labels are excluded from the fingerprint.

## Files

| File | Contents |
| --- | --- |
| `shader_alias_groups.csv` | 12 conversion-interface alias groups |
| `shader_binary_alias_groups.csv` | 2 verified bytecode alias groups |
| `shader_equivalence_groups.csv` | All 283 groups, including singletons |
| `shader_equivalence.csv` | Reverse mapping for all 296 selectors |
| `shader_slot_inventory.csv` | All 1,143 pairs with UV, visibility, activity and semantic profile ID |
| `semantic_slot_portraits.csv` | Summary of 135 profiles and their assigned pairs |
| `semantic_slot_portraits.json` | Structured channel semantics, formulas and notes |
| `converter_slot_profiles.json` | 15 transport profiles, DDS format rules, NVTT arguments and assignments |
| `runtime_shader_slots.json` | Bundled manifest of 296 selectors, slots and runtime routes |
| `coverage.json` | Counts, provenance, SHA-256 hashes and semantic gaps; this snapshot has no uncovered pairs |

## Semantics and conversion

A semantic profile describes what a GTA shader reads. A transport profile describes the input, processing and DDS container required for that slot. These are separate decisions.

Supported Base/Spec/Normal routes use PBR2GTA conversion according to their semantics. Special 2D inputs such as height, masks, flow, noise and palettes preserve channel meanings, using their transport profile's mipmap and DDS rules. Engine-owned, inactive, cubemap, Texture3D and array resources are handled separately from ordinary PNG inputs.

Complete catalog coverage does not mean every slot supports arbitrary PBR conversion. Runtime decisions are encoded in the transport profiles and bundled manifests.

## Rebuild

```powershell
.venv\Scripts\python.exe scripts\build_shader_slot_catalog.py `
  --shaders-xml <standard-sollumz-Shaders.xml> `
  --guide-json src\pbr2gta_blender\shader_guide_data.json `
  --transport-profiles scripts\shader_slot_transport_profiles.json `
  --binary-aliases scripts\shader_binary_aliases.json `
  --runtime-output src\pbr2gta_core\runtime_shader_slots.json `
  --runtime-output src\pbr2gta_blender\runtime_shader_slots.json `
  --output-dir docs\shader_catalog
```

The generator fails on duplicate selectors. Tests also check selector, pair, profile and alias counts, and require complete assignment of inventory rows.
