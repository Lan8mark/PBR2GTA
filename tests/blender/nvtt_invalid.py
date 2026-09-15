import sys,bpy
s=sys.modules['pbr2gta_audit']
assert not bpy.context.window_manager.pbr2gta_running
s.status()
path=s.root_path/'invalid-nvtt/nvcompress.exe'
path.parent.mkdir(exist_ok=True)
path.write_text('not an executable; audit invalid-installation fixture',encoding='ascii')
s.select(s.other_model)
s.prefs.nvcompress_path=str(path)
s.prefs.nvtt_sha256=''
try:
    answer=bpy.ops.sollumz.export_assets('EXEC_DEFAULT',directory=str(s.root_path/'exports/invalid_nvtt'),direct_export=True,use_custom_settings=True,limit_to_selected=True,target_formats={'CWXML'},target_versions={'GEN8'})
    data={'returned':sorted(answer)}
except RuntimeError as exc:
    data={'error':str(exc)}
data.update({'nvtt_error':s.prefs.nvtt_error,'hash_empty':not s.prefs.nvtt_sha256,'running':bpy.context.window_manager.pbr2gta_running,'pending':s.integration.has_pending_export()})
result=s.record('nvtt_invalid_verified',data)
