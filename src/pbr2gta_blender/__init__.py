# SPDX-License-Identifier: GPL-3.0-or-later

bl_info = {
    "name": "PBR2GTA",
    "author": "PBR2GTA",
    "version": (0, 2, 13),
    "blender": (4, 2, 0),
    "location": "Material Properties > PBR2GTA Material",
    "description": "Local PBR texture conversion companion for Sollumz",
    "category": "Material",
}


def register() -> None:
    from . import operators, preview_icons, properties, shader_guide_ui, sollumz_integration, ui, texture_set_ui

    properties.register()
    operators.register()
    texture_set_ui.register()
    shader_guide_ui.register()
    sollumz_integration.register()
    preview_icons.register()
    ui.register()


def unregister() -> None:
    from . import operators, preview_icons, properties, shader_guide_ui, sollumz_integration, ui, texture_set_ui

    ui.unregister()
    texture_set_ui.unregister()
    shader_guide_ui.unregister()
    preview_icons.unregister()
    sollumz_integration.unregister()
    operators.unregister()
    properties.unregister()
