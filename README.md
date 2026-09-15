<p align="center">
  <img src="src/pbr2gta_blender/assets/branding/pbr2gta.png" alt="PBR2GTA logo" width="64" height="64">
</p>

# PBR2GTA

**A Blender multitool for GTA V shaders.**

PBR conversion, automatic DDS preparation and a shader reference — integrated into your Sollumz workflow.

English · [Русский](README.ru.md)

[Download](https://github.com/cre8vy/PBR2GTA/releases) · [Quick start](docs/QUICKSTART.md) · [Development](docs/DEVELOPMENT.md)

## Comparison · 512 × 512 textures

![Before / Blender PBR reference / After](docs/media/comparison.png)

| Before | Blender reference | After |
| --- | --- | --- |
| Substance Painter + Clear Baker | PBR material in Blender | Substance Painter + PBR2GTA |
| [Full image](docs/media/before.jpg) | [Full image](docs/media/blender-reference.jpg) | [Full image](docs/media/after.jpg) |

## Features

| | What you get |
| --- | --- |
| **Predictable PBR conversion** | Metallic/Roughness and Specular/Gloss workflows with texture conversion and matching material settings. |
| **Work with PNG** | Automatic DDS format selection, compression and mipmaps for supported texture slots. |
| **Shader reference** | Contextual instructions for 296 shader presets, available directly in Blender in English and Russian. |

## Where to find PBR2GTA

Select an object and its Sollumz shader material. In the **Properties** editor, open **Material Properties** (the red sphere icon), then expand **PBR2GTA** below the Sollumz panel.

<img src="docs/media/material-properties-panel.png" alt="PBR2GTA panel in Blender Material Properties, below Sollumz" width="455">

The panel contains **Use PBR2GTA**, workflow and PNG inputs, **Auto Detect Texture Set**, **Surface** and **Preview in Sollumz**. The **`?`** buttons beside the shader name and Surface open their respective guides.

Export through Sollumz's **Export RAGE Assets** command. NVTT setup is in **Preferences → Add-ons → PBR2GTA**.

## Know your shader

Click **`?` beside the current shader** to open instructions for that exact preset:

- **Maps** — texture slots, channel meanings and usage notes.
- **Vertex / UV** — vertex colors and UV requirements.
- **Parameters** — material controls, default values and their purpose.

Switch between **EN / RU** in the reference window. The guide works offline and is available independently of PBR conversion.

## Convert PBR materials

PBR2GTA converts source maps into GTA diffuse/specular textures and applies the corresponding settings for supported shaders. **Surface** controls the material response; **Preview in Sollumz** lets you inspect the converted material in Blender.

### Metallic / Roughness

![Base Color + Metallic + Roughness → GTA diffuse/specular](docs/media/metallic-workflow.webp)

### Specular / Gloss

![Diffuse + Specular + Gloss → GTA diffuse/specular](docs/media/specular-workflow.webp)

Both workflows support a separate normal map.

**Auto Detect Texture Set** fills the current material's inputs from a folder. Choose your workflow, click the button and select any PNG from the desired set. Matching uses the web batch naming rules (for example, `Toad_BaseColor`, `Toad_Metallic`, `Toad_Roughness`, `Toad_Normal`). Other sets and materials are left untouched; missing or duplicate maps are reported before assigning anything. Normal maps must use DirectX orientation.

## Automatic DDS conversion

DDS names follow the material: `Chain` produces `chain_d.dds`, `chain_s.dds` and `chain_n.dds`. Each material gets its own textures, even when source PNGs are shared. Name collisions are resolved automatically; Advanced shows the resulting filenames.

Work with PNG — PBR2GTA selects the optimal DDS format for each supported texture slot, taking its purpose and alpha-channel requirements into account. It handles compression and generates mipmaps down to **4 × 4 pixels**. Conversion runs locally through NVIDIA Texture Tools; palette textures retain their dedicated profile without mipmaps.

## Export through Sollumz

With **Use PBR2GTA** enabled, shader parameters stay calibrated in the material. Changing **Surface** updates the shader immediately. Disabling PBR2GTA stops this control and leaves the current values available for manual editing.

For **YDR and YDD**, each enabled material keeps its own Surface setting across all Drawables and LOD meshes. Shared materials are converted once. Export writes the material's existing values; PNG conversion and DDS preparation still run automatically.

Export with the usual **Export RAGE Assets** command in Sollumz. For external textures, PBR2GTA also creates **`ytd.xml` and a DDS folder** alongside the YDR or YDD export.

## Installation

**Requirements:** Windows x64 · Blender 4.2+ · Sollumz 2.7+ · NVIDIA Texture Tools. See the [tested releases and export formats](docs/SOLLUMZ_COMPATIBILITY.md).

1. Install Sollumz and download the [PBR2GTA ZIP](https://github.com/cre8vy/PBR2GTA/releases).
2. In Blender, open **Edit → Preferences → Get Extensions → Install from Disk**, select the ZIP and enable PBR2GTA.
3. In PBR2GTA settings, click **Check installation** for NVTT. If it is missing, use **Download from NVIDIA** to install the Standalone Application, then run the check.

To convert a material, enable **PBR2GTA** in Material Properties, choose a workflow and assign your PNGs. Adjust **Surface**, preview and export.

[Detailed setup and supported inputs →](docs/QUICKSTART.md)

---

**v0.2.13 · Alpha**

[GPL-3.0-or-later](LICENSE) · [Third-party notices](THIRD_PARTY_NOTICES.md) · [Media credits](docs/media/README.md)
