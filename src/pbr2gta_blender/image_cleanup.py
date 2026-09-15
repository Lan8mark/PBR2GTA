"""Retire generated images only after Blender's preview/render jobs release them."""
from __future__ import annotations

import bpy

_pending = {}


def queue_images(images):
    for image in images:
        if image is not None:
            try:
                _pending[image.as_pointer()] = image
            except ReferenceError:
                pass
    if _pending and not bpy.app.timers.is_registered(flush):
        bpy.app.timers.register(flush, first_interval=0.25)


def flush():
    # users == 0 does not account for a background Cycles material preview.
    # Deleting its source Image can otherwise crash Blender's native image cache.
    if any(bpy.app.is_job_running(job) for job in ("RENDER", "RENDER_PREVIEW")):
        return 0.25
    pinned = {
        image for screen in bpy.data.screens for area in screen.areas
        for space in area.spaces if (image := getattr(space, "image", None)) is not None
    }
    for pointer, image in list(_pending.items()):
        try:
            if image.users == 0 and not image.use_fake_user and not image.library and image not in pinned:
                bpy.data.images.remove(image)
        except ReferenceError:
            pass  # Undo or file loading already released this datablock.
        finally:
            _pending.pop(pointer, None)
    return None


def reset():
    if bpy.app.timers.is_registered(flush):
        bpy.app.timers.unregister(flush)
    _pending.clear()
