"""Keep enabled material parameters calibrated independently of export."""
import math

_syncing = False


def sync_material(material):
    global _syncing
    if _syncing or material is None:
        return
    settings = getattr(material, 'pbr2gta', None)
    if settings is None:
        return
    error = ''
    _syncing = True
    try:
        if settings.enabled:
            from .bridge import shader_parameter_patch, validate_parameter_patch, find_node, patch_material
            expected = shader_parameter_patch(material)
            validate_parameter_patch(material, expected)
            # Blender stores float32 sockets. Never rewrite equal values on
            # every depsgraph update: that creates a perpetual update loop.
            changed = {}
            for name, values in expected.items():
                node = find_node(material, name)
                count = min(len(values), len(node.outputs))
                if any(not math.isclose(float(node.outputs[i].default_value), float(values[i]),
                                        rel_tol=1e-6, abs_tol=1e-7) for i in range(count)):
                    changed[name] = values
            if changed:
                patch_material(material, changed)
    except (ValueError, RuntimeError, AttributeError, ReferenceError, TypeError) as exc:
        error = str(exc)
    finally:
        _syncing = False
    try:
        if settings.calibration_error != error:
            settings.calibration_error = error
    except (AttributeError, ReferenceError, RuntimeError):
        pass  # A linked/read-only material cannot receive a diagnostic property.


def material_updated(settings, context):
    del context
    sync_material(getattr(settings, 'id_data', None))
