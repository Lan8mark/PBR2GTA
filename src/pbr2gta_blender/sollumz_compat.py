"""Capability-based access to Sollumz export settings and object collection.

No version comparisons: use the registered operator and its effective settings.
"""
import importlib
from contextlib import contextmanager

FALLBACK_FIELDS = ('target_formats', 'target_versions', 'limit_to_selected',
                   'exclude_skeleton', 'export_with_ytyp', 'apply_transforms',
                   'ymap_exclude_entities', 'ymap_box_occluders',
                   'ymap_model_occluders', 'ymap_car_generators', 'mesh_domain')


def effective_settings(operator, preferences):
    if getattr(operator, 'use_custom_settings', False):
        return getattr(operator, 'custom_settings', operator)
    return preferences


def copy_value(value):
    if isinstance(value, set):
        return set(value)
    if type(value).__name__ == 'bpy_prop_array':
        return tuple(value)
    return value


def snapshot(settings, template=None):
    if settings is None:
        raise ValueError('Sollumz export settings are unavailable.')
    rna = getattr(settings, 'bl_rna', None)
    if template is not None:
        # Flat operators also expose Blender's own properties (bl_idname,
        # directory, etc.). Only the destination settings define export fields.
        names = snapshot(template).keys()
    elif rna is not None:
        names = [p.identifier for p in rna.properties
                 if not p.is_readonly and p.type in {'BOOLEAN', 'INT', 'FLOAT', 'STRING', 'ENUM'}
                 and p.identifier not in {'name', 'rna_type'}]
    else:
        names = [name for name in FALLBACK_FIELDS if hasattr(settings, name)]
    return {name: copy_value(getattr(settings, name)) for name in names}


@contextmanager
def temporary_settings(preferences, values):
    # Validate and capture everything before the first write.
    saved = {name: copy_value(getattr(preferences, name)) for name in values}
    try:
        for name, value in values.items():
            setattr(preferences, name, value)
        yield
    finally:
        for name, value in saved.items():
            setattr(preferences, name, value)


def collect_objects(module, context, selected_only):
    collect = getattr(module, '_collect_objects_for_export', None)
    if collect is not None:
        return collect(context, selected_only)
    # 2.7 exposes the same hierarchy resolver from sollumz_helper, but keeps
    # collection on its operator instance rather than a module function.
    helper = importlib.import_module(module.__package__ + '.sollumz_helper')
    roots = {}
    for obj in context.selected_objects if selected_only else context.scene.objects:
        parent = helper.find_sollumz_parent(obj)
        if parent is not None:
            roots[parent.as_pointer()] = parent
    return list(roots.values())


@contextmanager
def temporary_selection(view_layer, roots):
    selected = tuple(view_layer.objects.selected)
    active = view_layer.objects.active
    visibility = []
    try:
        for obj in selected:
            obj.select_set(False, view_layer=view_layer)
        for obj in roots:
            if obj.name not in view_layer.objects:
                raise ValueError(f'{obj.name}: original export root is excluded from the view layer.')
            visibility.append((obj, obj.hide_select, obj.hide_viewport, obj.hide_get(view_layer=view_layer)))
            obj.hide_select = False
            obj.hide_viewport = False
            obj.hide_set(False, view_layer=view_layer)
            obj.select_set(True, view_layer=view_layer)
            if not obj.select_get(view_layer=view_layer):
                raise ValueError(f'{obj.name}: original export root cannot be selected in this view layer.')
        view_layer.objects.active = roots[0] if roots else None
        yield
    finally:
        for obj in tuple(view_layer.objects.selected):
            obj.select_set(False, view_layer=view_layer)
        for obj, locked, viewport, hidden in visibility:
            obj.hide_set(hidden, view_layer=view_layer)
            obj.hide_viewport = viewport
            obj.hide_select = locked
        for obj in selected:
            obj.select_set(True, view_layer=view_layer)
        view_layer.objects.active = active
