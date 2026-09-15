import bpy,pbr2gta_audit as s,importlib
from types import SimpleNamespace
ops=s.ops
original=ops.find_nvtt
try:
    ops.find_nvtt=lambda *args:None
    try:answer=bpy.ops.pbr2gta.validate_nvtt('EXEC_DEFAULT')
    except RuntimeError:answer={'CANCELLED'}
    assert not s.prefs.nvtt_sha256
    missing=s.prefs.nvtt_error
finally:ops.find_nvtt=original
# Both opening paths share exactly this static callback and one Close operator.
records=[]
class Layout:
    def __getattr__(self,name):
        if name in ('row','box','column','split'):return lambda *a,**k:self
        if name=='operator':return lambda identifier,**kw:(records.append((identifier,kw)) or SimpleNamespace())
        return lambda *a,**k:None
ops.PBR2GTA_OT_setup_nvtt.draw(SimpleNamespace(layout=Layout()),bpy.context)
assert len([r for r in records if r[1].get('text')=='Close'])==1
manual=bpy.ops.pbr2gta.setup_nvtt('INVOKE_DEFAULT')
assert manual=={'FINISHED'}
assert bpy.ops.pbr2gta.validate_nvtt('EXEC_DEFAULT')=={'FINISHED'}
result=s.record('nvtt_missing_manual_verified',{'missing_error':missing,'manual_popup':sorted(manual),'close_buttons':1,'retry_ready':not s.prefs.nvtt_error})
