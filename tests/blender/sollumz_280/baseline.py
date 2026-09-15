import bpy, importlib, traceback, time
from pathlib import Path
import pbr2gta_audit as s
from szio.gta5 import try_load_asset

def files(folder):return [str(p.relative_to(folder)) for p in folder.rglob('*') if p.is_file()]
def sequence():
    s.record('environment',{'blender':bpy.app.version_string,'sollumz':importlib.import_module(s.pkg).bl_info['version'],
       'deps':importlib.import_module(s.pkg+'.dependencies').dependencies_available_state(),
       'operator_properties':list(bpy.ops.sollumz.export_assets.get_rna_type().properties.keys())})
    s.export_prefs.target_formats={'NATIVE','CWXML'}
    s.export_prefs.target_versions={'GEN8'}
    # Positive control: stock Sollumz, without the PBR2GTA export hook.
    s.integration.unregister()
    for name,root in [('ydr',s.other_model),('ydd',s.first_model)]:
        s.select(root);dest=s.root_path/('stock_'+name);dest.mkdir()
        returned=bpy.ops.sollumz.export_assets(directory=str(dest),direct_export=True)
        assets=[p for p in dest.rglob('*') if p.name.endswith(('.ydr','.ydr.xml','.ydd','.ydd.xml'))]
        assert returned=={'FINISHED'} and assets
        assert all(try_load_asset(p) is not None for p in assets)
        s.record('stock_'+name,{'status':sorted(returned),'files':files(dest),'readback':True})
    s.integration.register()
    s.start('preview',roots=[s.other_model],preview=True)
    while bpy.context.window_manager.pbr2gta_running:yield .1
    s.record('preview_verified',s.status())
    s.record('baseline_complete',{'done':True})

steps=sequence()
def tick():
    try:return next(steps)
    except StopIteration:return None
    except Exception:
        s.record('failed',{'traceback':traceback.format_exc()});traceback.print_exc();return None
bpy.app.timers.register(tick,first_interval=.1)
