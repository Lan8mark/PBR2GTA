# Quick start

English · [Русский](QUICKSTART.ru.md)

## Setup

Install Sollumz, then install the PBR2GTA ZIP from Releases using **Install from Disk** in Blender's extension preferences.

The PBR2GTA preferences include NVTT setup:

- **Download from NVIDIA** opens the official website. Download the Standalone Application, run its installer and accept NVIDIA's license.
- **Check installation** finds `nvcompress.exe` and tests BC5U compression.
- **Choose nvcompress.exe** lets you select a custom installation.

Retry your conversion after validation succeeds. If Blender preference auto-save is disabled, save Preferences manually. Conversion runs locally; internet access is needed to obtain dependencies.

## Material workflow

1. Create a supported Sollumz shader material and enable **PBR2GTA** in Material Properties.
2. For **Metallic/Roughness**, assign Base Color, Metallic and Roughness. For **Specular/Gloss**, assign Diffuse, Specular and Gloss.
3. Assign **Normal (DirectX)** when required. Use saved PNG files.
4. Adjust **Surface** between 0 and 1. Its `?` button shows material reference values.
5. Use **Preview in Sollumz** to convert and assign textures for viewing.
6. Export the Drawable (YDR) or Drawable Dictionary (YDD) with **Export RAGE Assets**. A YDD contains Drawable children, each with its own model/LOD meshes.

For YDD, enable PBR2GTA on the materials you want to convert and set Surface per material. Shader parameters update immediately and stay controlled while PBR2GTA is enabled. Export converts PNGs across all Drawables and LODs, including hidden LOD meshes. Shared materials are processed once; disabled materials keep their settings. The legacy **Export CodeWalker XML** command uses the same texture conversion path.

PNGs remain your sources. PBR2GTA prepares DDS textures and supported shader settings. External DDS textures also produce a `ytd.xml` and texture folder; this feature works even when PBR conversion is disabled on the material.

## Shader reference · EN / RU

Click `?` next to the current shader name in PBR2GTA. Choose **EN** or **RU** at the top of the popup. The language preference is also available in add-on settings.

The reference contains **Maps**, **Vertex / UV** and **Parameters**. It remains accessible when material conversion is disabled. Click outside the popup or press Esc to close it.

All 296 shader variants are included. Reference coverage does not mean that every slot supports automatic PBR conversion. Descriptions are bundled locally; no translation service is used at runtime.

## Limitations

- The current package targets Windows x64.
- Conversion sources must be saved, single-file PNGs. Packed, generated, dirty and UDIM images are not supported.
- Full PBR conversion is available for supported profiles, including `normal_spec` and corresponding weapon shaders. A shader's presence in the catalog does not imply arbitrary PBR conversion support for all its slots.
- Special slots with distinct channel meanings may require ready-made DDS files or preserve existing assignments.
- YTD XML is intended for ordinary 2D textures. Dimension metadata for ready-made cubemap, volume and array DDS files has not been validated.
- Blender and GTA V use different renderers. Conversion does not guarantee identical appearance under every lighting condition.
