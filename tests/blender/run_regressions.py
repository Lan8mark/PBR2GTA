import bpy, sys, json, hashlib, traceback, importlib
from pathlib import Path
from szio.gta5 import try_load_asset
import pbr2gta_audit as s

def file_hashes(folder):
    return {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in folder.rglob('*') if p.is_file()}

def verify_ydd(folder, suffixes=('.ydd', '.ydd.xml')):
    rows = []
    for suffix in suffixes:
        asset = try_load_asset(folder / ('audit_dictionary'+suffix))
        assert asset is not None, (folder,suffix)
        for name, drawable in asset.drawables.items():
            for shader in drawable.shader_group.shaders:
                params = {p.name.casefold():p.value for p in shader.parameters}
                texture = params.get('diffusesampler') or ''
                expected = .63 if 'low' in texture else .37 if 'shared' in texture else .88
                assert abs(params['specularfresnel'][0]-expected)<1e-5, params
                assert abs(params['specularintensitymult'][0]-(.91 if expected==.88 else .3))<1e-5
                rows.append((suffix,name,texture,expected))
    return rows

def wait_done():
    while bpy.context.window_manager.pbr2gta_running:
        yield .1
    assert not s.integration.has_pending_export()
    cleanup=importlib.import_module(s.ops.__package__+'.image_cleanup')
    while cleanup._pending:
        yield .1

def preview_paths(mat):
    return {role: Path(s.bridge.find_node(mat,node).image.filepath) for role,node in s.bridge.SAMPLERS.items()}

def sequence():
    s.record('baseline_verified',{'rows':verify_ydd(s.root_path/'exports/baseline')})
    s.reset_params(s.shared); s.reset_params(s.low)
    s.start('selection_race',roots=[s.first_model])
    s.select(s.other_model)
    yield from wait_done()
    assert bpy.context.active_object == s.other_model
    assert not (s.output/'audit_separate.ydr').exists()
    s.record('A03_selection_verified',{'rows':verify_ydd(s.output), 'selection':[o.name for o in bpy.context.selected_objects]})

    s.reset_params(s.shared); s.reset_params(s.low)
    s.start('legacy_race',roots=[s.first_model],legacy=True)
    s.select(s.other_model)
    yield from wait_done()
    assert bpy.context.active_object == s.other_model
    s.record('A03_legacy_verified',{'rows':verify_ydd(s.output,('.ydd.xml',))})

    s.reset_params(s.other)
    previous=s.params(s.other)
    s.start('disabled_race',roots=[s.other_model])
    s.other.pbr2gta.enabled=False
    yield from wait_done()
    assert s.params(s.other)==previous
    assert not file_hashes(s.output)
    s.other.pbr2gta.enabled=True
    s.record('A03_disable_verified',{'unchanged':True,'files':file_hashes(s.output)})

    s.start('preview_original',roots=[s.other_model],preview=True)
    yield from wait_done()
    old_paths=preview_paths(s.other)
    old_hashes={k:hashlib.sha256(p.read_bytes()).hexdigest() for k,p in old_paths.items()}
    s.copied=s.other.copy();s.copied.name='audit_copied_preview';s.materials.append(s.copied)
    s.copied_root=s.obj('copied','sollumz_drawable')
    s.copied_model=s.model('copied_model',s.copied,s.copied_root)
    s.copied.pbr2gta.base_color=bpy.data.images.load(str(s.source/'variant.png'),check_existing=False)
    s.copied.pbr2gta.roughness=bpy.data.images.load(str(s.source/'variant_rough.png'),check_existing=False)
    s.start('preview_copy',roots=[s.copied_model],preview=True)
    yield from wait_done()
    assert s.copied.pbr2gta.material_uuid != s.other.pbr2gta.material_uuid
    assert old_hashes=={k:hashlib.sha256(p.read_bytes()).hexdigest() for k,p in old_paths.items()}
    assert all(preview_paths(s.copied)[k] != p for k,p in old_paths.items())
    assert hashlib.sha256(preview_paths(s.copied)['diffuse'].read_bytes()).hexdigest()!=old_hashes['diffuse']
    s.record('A04_copy_verified',{'original':str(old_paths),'copy':str(preview_paths(s.copied)),'original_unchanged':True})

    def unused():
        return len([i for i in bpy.data.images if i.get('pbr2gta_generated') and i.users==0])
    counts=[unused()]
    for index in range(5):
        s.start('repeat_'+str(index),roots=[s.copied_model],preview=True)
        yield from wait_done()
        counts.append(unused())
    assert len(set(counts))==1,counts
    assert all('.pbr2gta-txn-' not in str(p) for p in preview_paths(s.copied).values())
    s.record('A07_repeat_verified',{'unused_generated_counts':counts})

    missing=s.material('missing_param');root=s.obj('missing_param','sollumz_drawable')
    model=s.model('missing_param_model',missing,root)
    missing.node_tree.nodes.remove(s.bridge.find_node(missing,'SpecularIntensityMult'))
    messages=[]
    for bad_type in (False,True):
        if bad_type:
            node=missing.node_tree.nodes.new('ShaderNodeValue');node.name='SpecularIntensityMult'
        try:
            s.start('missing_param_'+str(bad_type),roots=[model])
        except RuntimeError as exc:
            messages.append(str(exc))
        assert not bpy.context.window_manager.pbr2gta_running
        assert not file_hashes(s.output)
    assert len(messages)==2 and all('SpecularIntensityMult' in m and 'audit_missing_param' in m for m in messages)
    s.record('A06_preflight_verified',{'messages':messages})

    s.reset_params(s.other); previous=s.params(s.other)
    folder=s.root_path/'exports/late_ytd_failure';folder.mkdir(parents=True,exist_ok=True)
    (folder/'audit_separate.ydr').write_bytes(b'original YDR')
    (folder/'notes.txt').write_bytes(b'personal')
    before=file_hashes(folder)
    ytd=importlib.import_module('bl_ext.user_default.pbr2gta.ytdexport')
    original=ytd.write_ydr_texture_sidecar
    def fail(*args):
        original(*args)
        raise OSError('Injected YTD write failure after staged files exist')
    ytd.write_ydr_texture_sidecar=fail
    try:
        s.start('late_ytd_failure',roots=[s.other_model])
        yield from wait_done()
    finally:
        ytd.write_ydr_texture_sidecar=original
    assert file_hashes(folder)==before
    assert s.params(s.other)==previous
    s.record('A05_late_ytd_verified',{'files_unchanged':True,'parameters_unchanged':True,'hashes':before})

    folder=s.root_path/'exports/directory_conflict';folder.mkdir(parents=True,exist_ok=True)
    (folder/'audit_separate.ytd.xml').mkdir(exist_ok=True)
    (folder/'audit_separate.ydr').write_bytes(b'original YDR')
    before=file_hashes(folder)
    s.start('directory_conflict',roots=[s.other_model])
    yield from wait_done()
    assert file_hashes(folder)==before
    assert s.params(s.other)==previous
    s.record('A05_conflict_verified',{'files_unchanged':True,'parameters_unchanged':True})

    # The same sidecar failure must roll back exports without enabled PBR materials.
    s.other.pbr2gta.enabled=False
    folder=s.root_path/'exports/direct_failure';folder.mkdir(parents=True,exist_ok=True)
    (folder/'audit_separate.ydr').write_bytes(b'original')
    before=file_hashes(folder)
    ytd.write_ydr_texture_sidecar=fail
    try:
        try:s.start('direct_failure',roots=[s.other_model])
        except RuntimeError:pass
    finally:
        ytd.write_ydr_texture_sidecar=original
        s.other.pbr2gta.enabled=True
    assert file_hashes(folder)==before
    s.record('A05_no_pbr_verified',{'files_unchanged':True})

    # Recovery after failures must use the normal export path successfully.
    s.start('retry_success',roots=[s.other_model])
    yield from wait_done()
    assert try_load_asset(s.output/'audit_separate.ydr') is not None
    s.record('retry_verified',{'files':list(file_hashes(s.output))})

    cache=Path(s.prefs.cache_dir)
    for name in ('project.blend','notes.txt','diffuse.png'):(cache/name).write_bytes(b'keep')
    before={name:(cache/name).read_bytes() for name in ('project.blend','notes.txt','diffuse.png')}
    assert list((cache/'pbr2gta-cache-v2').rglob('manifest.json')), 'No real cache records to clear'
    assert bpy.ops.pbr2gta.clear_cache('EXEC_DEFAULT')=={'FINISHED'}
    assert not list((cache/'pbr2gta-cache-v2').rglob('manifest.json')), 'Sharded cache records remain'
    assert all((cache/name).read_bytes()==value for name,value in before.items())
    assert all(p.is_file() for p in preview_paths(s.copied).values())
    s.record('A02_clear_verified',{'foreign_files_preserved':True,'preview_preserved':True})

    s.record('regressions_complete',{'passed':True})

s.regressions=sequence()
def tick():
    try:
        return next(s.regressions)
    except StopIteration:
        return None
    except Exception:
        s.record('regressions_failed',{'traceback':traceback.format_exc(),'case':getattr(s,'case','')})
        return None
bpy.app.timers.register(tick,first_interval=.1)
result={'started':True}
