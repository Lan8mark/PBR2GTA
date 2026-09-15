import bpy
from bpy.props import StringProperty
from bpy_extras.io_utils import ImportHelper
from .texture_sets import detect_set
from .bridge import material_profile, primary_outputs
from .image_cleanup import queue_images


class PBR2GTA_OT_detect_texture_set(bpy.types.Operator, ImportHelper):
    bl_idname = 'pbr2gta.detect_texture_set'
    bl_label = 'Auto Detect Texture Set'
    bl_description = 'Select any PNG in a set; match its sibling maps by filename using the current workflow'
    bl_options = {'UNDO'}
    filename_ext = '.png'
    filter_glob: StringProperty(default='*.png', options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        material = getattr(context.object, 'active_material', None)
        return (material is not None and not context.window_manager.pbr2gta_running
                and material_profile(material).status == 'supported')

    def invoke(self, context, event):
        self._material = context.object.active_material
        self._workflow = self._material.pbr2gta.workflow
        return ImportHelper.invoke(self, context, event)

    def execute(self, context):
        loaded = []
        old = {}
        try:
            material = getattr(self, '_material', None) or context.object.active_material
            settings = material.pbr2gta
            if context.window_manager.pbr2gta_running:
                raise ValueError('Wait for the current conversion to finish.')
            if material_profile(material).status != 'supported':
                raise ValueError('This shader does not use a PBR texture set.')
            if settings.workflow != getattr(self, '_workflow', settings.workflow):
                raise ValueError('Workflow changed. Open Auto Detect again.')
            paths = detect_set(bpy.path.abspath(self.filepath), settings.workflow)
            if 'normal' not in primary_outputs(material):
                paths.pop('normal', None)
            # Load all inputs before modifying any slot. Source PNGs are user
            # data; never recolor or delete existing shared image datablocks.
            images = {}
            existing = {i.as_pointer() for i in bpy.data.images}
            for role, path in paths.items():
                image = bpy.data.images.load(str(path), check_existing=True)
                if image.as_pointer() not in existing:
                    loaded.append(image)
                images[role] = image
            roles = list(paths)
            if 'normal' in primary_outputs(material) and 'normal' not in roles:
                roles.append('normal')
            old = {role: getattr(settings, role) for role in roles}
            for role in roles:
                setattr(settings, role, images.get(role))
        except (ValueError, RuntimeError, OSError, ReferenceError) as exc:
            for role, image in old.items():
                setattr(settings, role, image)
            # Only images loaded by this failed operation are disposable.
            queue_images(loaded)
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, f'Assigned {len(paths)} maps to {material.name}.')
        return {'FINISHED'}


def register():
    bpy.utils.register_class(PBR2GTA_OT_detect_texture_set)


def unregister():
    bpy.utils.unregister_class(PBR2GTA_OT_detect_texture_set)
