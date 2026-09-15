"""Exclude auxiliary PBR source nodes from Sollumz's embedded texture scan."""
import os
from contextlib import contextmanager

SOURCE_FIELDS = ('base_color', 'metallic', 'roughness', 'normal', 'diffuse', 'specular', 'gloss')


@contextmanager
def source_embedding_guard(materials, shader_manager, resolve_path):
    saved = []

    def path(image):
        filepath = getattr(image, 'filepath', '')
        return os.path.normcase(os.path.normpath(resolve_path(filepath))) if filepath else None

    try:
        for material in materials:
            settings = material.pbr2gta
            if not settings.enabled:
                continue
            shader = shader_manager.find_shader(material.shader_properties.filename)
            if shader is None:
                continue
            images = [getattr(settings, name, None) for name in SOURCE_FIELDS]
            images.extend(slot.image for slot in settings.slots)
            images = [image for image in images if image is not None]
            paths = {value for image in images if (value := path(image)) is not None}
            for node in material.node_tree.nodes:
                if node.type != 'TEX_IMAGE' or node.name in shader.parameter_map or node.image is None:
                    continue
                if node.image not in images and path(node.image) not in paths:
                    continue
                properties = node.texture_properties
                if properties.embedded:
                    saved.append(properties)
                    properties.embedded = False
        yield
    finally:
        for properties in reversed(saved):
            properties.embedded = True
