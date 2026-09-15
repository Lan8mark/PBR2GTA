import bpy,importlib,traceback,hashlib
from pathlib import Path
from types import SimpleNamespace
from szio.gta5 import try_load_asset
import pbr2gta_audit as s

def hashes(folder):return {str(p.relative_to(folder)):hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.rglob('*') if p.is_file()}
def wait_done():
    while bpy.context.window_manager.pbr2gta_running:yield .1
    assert not s.integration.has_pending_export()
def sequence():
    # Validate both generations, both formats, and actual DDS headers.
    ytd=importlib.import_module(s.ops.__package__+'.ytdexport')
    headers=[]
    for folder in [s.root_path/'exports/baseline',s.root_path/'exports/mixed']:
        for p in folder.rglob('*.dds'):
            h=ytd.parse_dds_header(p)
            assert h.width==512 and h.height==512
            assert h.mip_levels==(1 if h.format_name=='D3DFMT_A8R8G8B8' else 8)
            headers.append({'file':str(p.relative_to(s.root_path)),'format':h.format_name,'mips':h.mip_levels})
    assert {'D3DFMT_DXT1','D3DFMT_DXT5','D3DFMT_ATI2','D3DFMT_A8R8G8B8'} <= {h['format'] for h in headers}
    rows=[]
    for version in ('gen8','gen9'):
        for extension in ('.ydr','.ydr.xml'):
            path=s.root_path/'exports/mixed'/version/('audit_weapon'+extension)
            asset=try_load_asset(path);assert asset is not None
            for shader in asset.shader_group.shaders:
                params={p.name.casefold():p.value for p in shader.parameters}
                assert abs(params['specularfresnel'][0]-.46)<1e-5
                rows.append([version,extension,params['specularfresnel'][0]])
    s.record('dds_and_native_verified',{'headers':headers,'weapon_readback':rows})

    # Public modal cancellation and the lifecycle handlers use the same termination path.
    cancelled=[]
    for trigger in ('ESC','undo_pre','load_pre'):
        previous=s.params(s.other)
        s.start('cancel_'+trigger,roots=[s.other_model])
        op=s.ops._active_export_operator; process=op._process
        if trigger=='ESC':assert op.modal(bpy.context,SimpleNamespace(type='ESC'))=={'CANCELLED'}
        else:
            handlers=getattr(bpy.app.handlers,trigger)
            assert s.ops._cancel_for_scene_change in handlers
            s.ops._cancel_for_scene_change(None)
        assert process.poll() is not None
        assert not bpy.context.window_manager.pbr2gta_running and not s.integration.has_pending_export()
        assert not hashes(s.output) and s.params(s.other)==previous
        cancelled.append({'trigger':trigger,'worker_pid':process.pid,'returncode':process.returncode})
    s.record('cancel_lifecycle_verified',{'cases':cancelled,'mode':'ESC modal event and registered lifecycle callbacks invoked through MCP'})

    for change in ('source','surface','assignment'):
        previous=s.params(s.other)
        original_source=s.other.pbr2gta.base_color;original_surface=s.other.pbr2gta.surface
        s.start('race_'+change,roots=[s.other_model])
        if change=='source':s.other.pbr2gta.base_color=s.copied.pbr2gta.base_color
        elif change=='surface':s.other.pbr2gta.surface=.22
        else:s.other_model.data.materials[0]=s.shared
        yield from wait_done()
        assert not hashes(s.output) and s.params(s.other)==previous
        s.other.pbr2gta.base_color=original_source;s.other.pbr2gta.surface=original_surface;s.other_model.data.materials[0]=s.other
    s.record('input_races_verified',{'source':True,'surface':True,'assignment':True})

    # Failure after DDS/YDR publication must restore the entire existing directory.
    artifacts=importlib.import_module(s.ops.__package__+'.artifacts')
    folder=s.root_path/'exports/publish_failure';folder.mkdir(parents=True,exist_ok=True)
    for name in ('audit_separate.ydr','audit_separate.ytd.xml','audit_other_diffuse.dds','notes.txt'):(folder/name).write_text('original '+name)
    before=hashes(folder);previous=s.params(s.other)
    bindings={node:s.bridge.find_node(s.other,node).image for node in s.bridge.SAMPLERS.values()}
    replace=artifacts.os.replace
    def fail(source,dest):
        if Path(dest)==folder/'audit_separate.ytd.xml' and 'files' in Path(source).parts:raise PermissionError('injected publication failure')
        return replace(source,dest)
    artifacts.os.replace=fail
    try:
        s.start('publish_failure',roots=[s.other_model]);yield from wait_done()
    finally:artifacts.os.replace=replace
    assert hashes(folder)==before and s.params(s.other)==previous
    assert all(s.bridge.find_node(s.other,node).image==image for node,image in bindings.items())
    s.record('publication_rollback_verified',{'all_files_restored':True,'images_restored':True,'parameters_restored':True,'before':before})

    # Embedded export removes only this asset's old external sidecar.
    for node in bindings:s.bridge.find_node(s.other,node).texture_properties.embedded=True
    folder=s.root_path/'exports/embedded';folder.mkdir(parents=True,exist_ok=True)
    (folder/'audit_separate.ytd.xml').write_text('stale external sidecar')
    (folder/'notes.txt').write_text('keep')
    try:
        s.start('embedded',roots=[s.other_model]);yield from wait_done()
        assert not (folder/'audit_separate.ytd.xml').exists()
        assert (folder/'notes.txt').read_text()=='keep'
        asset=try_load_asset(folder/'audit_separate.ydr');assert asset is not None
        assert len(asset.shader_group.embedded_textures)>0
    finally:
        for node in bindings:s.bridge.find_node(s.other,node).texture_properties.embedded=False
    s.record('embedded_verified',{'native_readback':True,'stale_sidecar_removed':True,'foreign_file_preserved':True})

    # Fake-user and user-created images survive replacement.
    image=s.bridge.find_node(s.other,'DiffuseSampler').image;image.use_fake_user=True
    s.start('retained_preview',roots=[s.other_model],preview=True);yield from wait_done()
    assert image.name in bpy.data.images and image.use_fake_user
    image.use_fake_user=False
    cleanup=importlib.import_module(s.ops.__package__+'.image_cleanup')
    cleanup.queue_images([image])
    while cleanup._pending:yield .1
    assert s.other.pbr2gta.base_color.name in bpy.data.images
    s.record('retained_images_verified',{'fake_user':True,'source_png':True})
    s.start('last_retry',roots=[s.other_model]);yield from wait_done()
    assert try_load_asset(s.output/'audit_separate.ydr') is not None
    s.record('extra_complete',{'passed':True})
s.extra=sequence()
def tick():
    try:return next(s.extra)
    except StopIteration:return None
    except Exception:
        s.record('extra_failed',{'case':getattr(s,'case',''),'traceback':traceback.format_exc()});return None
bpy.app.timers.register(tick,first_interval=.1)
result={'started':True}
