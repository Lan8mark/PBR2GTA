"""Temporary in-memory transport adapter, not an addon fix or supported release."""
import bpy, traceback, importlib, json
from pathlib import Path
from szio.gta5 import try_load_asset
import pbr2gta_audit as s
original=s.integration.export_without_intercept
def adapter(directory, operator_idname='sollumz.export_assets', **settings):
    prefs=s.integration._preference_export_settings(bpy.context)
    saved={key:s.integration._copy_rna_value(getattr(prefs,key)) for key in settings if hasattr(prefs,key)}
    try:
        for key in saved:setattr(prefs,key,settings[key])
        return original(directory,operator_idname,use_custom_settings=False)
    finally:
        for key,value in saved.items():setattr(prefs,key,value)

def sequence():
    # A second old-API fault in YTD target resolution (white-box probe).
    fake=type('OldCustom',(),{'__module__':s.pkg+'.sollumz_operators','bl_idname':'sollumz.export_assets',
        'directory':str(s.root_path/'probe'),'use_custom_settings':True,'custom_settings':s.export_prefs})()
    try:s.integration._write_sidecars(lambda *args:{'FINISHED'},fake,bpy.context)
    except Exception as exc:s.record('nested_sidecars_probe',{'error':str(exc)})
    s.integration.export_without_intercept=adapter
    try:
        for name,root in [('ydr',s.other_model),('ydd',s.first_model)]:
            s.select(root)
            dest=s.root_path/('adapter_'+name);dest.mkdir()
            old_disabled=s.params(s.disabled)
            prefs_before={k:s.integration._copy_rna_value(getattr(s.export_prefs,k)) for k in s.integration._EXPORT_SETTING_NAMES if hasattr(s.export_prefs,k)}
            ret=bpy.ops.sollumz.export_assets(directory=str(dest),direct_export=True)
            while bpy.context.window_manager.pbr2gta_running:yield .1
            assert s.params(s.disabled)==old_disabled
            assert prefs_before=={k:s.integration._copy_rna_value(getattr(s.export_prefs,k)) for k in prefs_before}
            assets=[p for p in dest.rglob('*') if p.name.endswith(('.ydr','.ydr.xml','.ydd','.ydd.xml'))]
            assert len(assets)==2,assets
            assert all(try_load_asset(p) is not None for p in assets)
            assert list(dest.glob('*.ytd.xml'))
            s.record('adapter_'+name,{'status':sorted(ret),'files':[str(p.relative_to(dest)) for p in dest.rglob('*') if p.is_file()],
                'readback':True,'disabled_preserved':True,'preferences_restored':True,'experimental_only':True})
    finally:s.integration.export_without_intercept=original
    s.record('causal_complete',{'done':True})
steps=sequence()
def tick():
    try:return next(steps)
    except StopIteration:return None
    except Exception:
        s.integration.export_without_intercept=original
        s.record('causal_failed',{'error':traceback.format_exc()});traceback.print_exc();return None
bpy.app.timers.register(tick,first_interval=.1)
