import bpy, json, traceback, importlib
from pathlib import Path
from szio.gta5 import try_load_asset
import pbr2gta_audit as s

def sequence():
    for m in s.materials:m.pbr2gta.enabled=m!=s.disabled
    cls=importlib.import_module(s.pkg+'.sollumz_operators').ExportSettingsOverride
    bpy.types.Scene.pbr_smoke_override=bpy.props.PointerProperty(type=cls)
    custom=bpy.context.scene.pbr_smoke_override
    custom.target_formats={'CWXML'};custom.target_versions={'GEN8'};custom.limit_to_selected=True
    diag=s.root_path/'diagnostics/last-failure.txt'
    for name,root,legacy,override in [('ydr',s.other_model,False,False),('ydd',s.first_model,False,False),('legacy',s.first_model,True,False),('custom',s.other_model,False,True)]:
        s.select(root);dest=s.root_path/('verified_'+name);dest.mkdir(exist_ok=True)
        before={m.name:s.params(m) for m in s.materials};last=diag.read_text() if diag.exists() else None
        error=None;kwargs={'directory':str(dest),'direct_export':True}
        if override:kwargs.update(use_custom_settings=True,custom_settings=custom)
        op=bpy.ops.sollumz.export_assets_legacy if legacy else bpy.ops.sollumz.export_assets
        try:ret=op(**kwargs)
        except Exception as exc:ret={'EXCEPTION'};error=str(exc)
        while bpy.context.window_manager.pbr2gta_running:yield .1
        new=diag.read_text() if diag.exists() else None
        if new!=last:error=json.loads((Path(new)/'summary.json').read_text())['message']
        assets={}
        for p in dest.rglob('*'):
            if p.name.endswith(('.ydr','.ydr.xml','.ydd','.ydd.xml')):
                asset=try_load_asset(p)
                if p.name.endswith(('.ydd','.ydd.xml')):
                    assets[p.name]={'readback':asset is not None}
                else:
                    assets[p.name]=[[str(pa.name),str(pa.value)] for sh in asset.shader_group.shaders for pa in sh.parameters if pa.name.casefold()=='specularfresnel']
        s.record('verified_'+name,{'status':sorted(ret),'error':error,'files':[str(p.relative_to(dest)) for p in dest.rglob('*') if p.is_file()],
                       'readback':assets,'params_before':before,'params_after':{m.name:s.params(m) for m in s.materials}})
    # Directly show the custom-settings mismatch without starting a worker.
    fake=type('Fake',(),{'use_custom_settings':True,'custom_settings':custom})()
    s.export_prefs.limit_to_selected=False
    s.record('nested_settings_probe',{'requested_selected_only':custom.limit_to_selected,'actual_selected_only':s.integration._selected_only(fake,bpy.context),
            'captured':s.integration._export_kwargs(fake,bpy.context)})
    s.export_prefs.limit_to_selected=True
    del bpy.types.Scene.pbr_smoke_override
    s.record('routes_complete',{'done':True})

steps=sequence()
def tick():
    try:return next(steps)
    except StopIteration:return None
    except Exception:s.record('routes_failed',{'error':traceback.format_exc()});traceback.print_exc();return None
bpy.app.timers.register(tick,first_interval=.1)
