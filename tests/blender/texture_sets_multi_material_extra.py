"""Run after texture_sets_multi_material.py completes."""
import bpy, shutil, hashlib, traceback
from pathlib import Path
from szio.gta5 import try_load_asset
import pbr2gta_audit as s


def sequence():
    model=bpy.data.objects['audit_multi_model']
    for role, sampler in [('diffuse','DiffuseSampler'),('specular','SpecSampler'),('normal','BumpSampler')]:
        source=next((s.root_path/'exports/multi_modern').rglob('audit_shared_'+role+'.dds'))
        target=s.source/('untouched_'+role+'.dds')
        shutil.copyfile(source,target)
        s.bridge.find_node(s.disabled,sampler).image=bpy.data.images.load(str(target))
    inputs={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in s.source.glob('*.dds')}
    previous=s.params(s.disabled)
    s.start('mixed_dds',roots=[model],target_versions={'GEN8','GEN9'})
    while bpy.context.window_manager.pbr2gta_running: yield .1
    assert not s.status()['error'],s.status()
    assert s.params(s.disabled)==previous
    assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==digest for p,digest in inputs.items())
    files=list(s.actual_output.rglob('untouched_*.dds'))
    assert len(files)>=3
    assert all(hashlib.sha256(p.read_bytes()).hexdigest()==inputs[str(s.source/p.name)] for p in files)
    for path in s.actual_output.rglob('*.ydr'):
        asset=try_load_asset(path)
        assert len(asset.shader_group.shaders)==4
        values=[round(float(next(p.value for p in sh.parameters if p.name.casefold()=='specularfresnel')[0]),2) for sh in asset.shader_group.shaders]
        assert sorted(values)==sorted([.37,.79,.88,.63])
    # Opting every material out still exports their existing DDS and parameters.
    for mat in (s.shared,s.other,s.low): mat.pbr2gta.enabled=False
    before={m.name:s.params(m) for m in s.materials}
    s.start('all_disabled',roots=[model])
    while bpy.context.window_manager.pbr2gta_running: yield .1
    assert not s.status()['error'],s.status()
    assert s.plan==[]
    assert before=={m.name:s.params(m) for m in s.materials}
    assert list(s.actual_output.rglob('*.ydr'))
    assert list(s.actual_output.rglob('*.ytd.xml'))
    s.record('multi_material_extra_verified',{'disabled_dds_byte_identical':True,'gen8_gen9':True,'all_disabled_no_conversion':True,'all_disabled_ytd':True})


steps=sequence()
def tick():
    try:return next(steps)
    except StopIteration:s.record('texture_multi_extra_complete',{'passed':True})
    except Exception:
        s.record('texture_multi_extra_failed',{'traceback':traceback.format_exc()})
        traceback.print_exc()
    return None
bpy.app.timers.register(tick,first_interval=.1)
