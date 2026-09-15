# Shader reference

The `?` button beside the current shader opens its reference. The filename is
captured from the selected material, including the exact preset suffix.
Choose **EN** or **RU** at the top of the window. The three sections cover
maps/channels, vertex colors/UV, and parameters. The Evidence section has been
removed, including its formulas, source paths and coverage controls.

Both language versions ship with the add-on. English descriptions are stored
in `shader_guide_en.json`; the runtime never contacts a translation service.
The language preference also appears in the add-on settings.

The initial data and formatting/resolution code were imported from the author's
custom Sollumz on 2026-09-14 (`ydr/shader_guide.py`,
`ydr/shader_guide_data.json`, `ydr/operators/shader_guide.py`). The data file
matched the installed custom Sollumz byte-for-byte. PBR2GTA now owns its bundled
copy; future updates must preserve feature merging and preset overrides.

PBR2GTA uses the ordinary Sollumz `szio` shader registry, loaded lazily when
opening a reference. It does not import the custom Sollumz guide modules.
Installing PBR2GTA before Sollumz therefore does not require those modules.

Initial live registry audit: 296 presets / 249 base shaders; 230 complete and
66 partial evidence records. Partial coverage is retained honestly. Reference
coverage does not imply that all slots support automatic PBR conversion.
