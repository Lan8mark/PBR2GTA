"""Isolated release smoke. Launch Blender --factory-startup --python this_file -- VERSION.

BLENDER_USER_RESOURCES must point to the per-case profile. Downloads/dependencies
are prepared by scripts/check_sollumz_releases.py. Never open a user .blend here.
"""
import bpy, sys, os, json, traceback, importlib, hashlib, zipfile, time, tomllib
from pathlib import Path
from xml.etree import ElementTree as ET

REPO=Path(__file__).resolve().parents[2]
ADDON_VERSION=tomllib.loads((REPO/'src/pbr2gta_blender/blender_manifest.toml').read_text())['version']
VERSION=sys.argv[sys.argv.index('--')+1]
ROOT=Path(os.environ.get('PBR2GTA_CASE_DIR',str(REPO/'build/compat-matrix'/VERSION)))
SOURCE=Path(os.environ.get('PBR2GTA_SOLLUMZ_SOURCE',str(REPO/'build/compat-matrix'/VERSION/'source')))
PACKAGE=os.environ.get('PBR2GTA_SOLLUMZ_PACKAGE','Sollumz')
RUN=ROOT/('run-'+os.environ.get('PBR2GTA_MATRIX_RUN',str(time.time_ns())))
RUN.mkdir(exist_ok=True)
sys.stdout=open(ROOT/'blender.log','w',encoding='utf-8',buffering=1)
sys.stderr=sys.stdout
rows=[]

def record(name, **data):
    rows.append({'case':name,**data})
    (ROOT/'progress.json').write_text(json.dumps(rows,indent=2,default=str))

def value(mat,name):return float(s.bridge.find_node(mat,name).outputs[0].default_value)

def export(name,obj,legacy=False,custom=False,surfaces=None):
    expected=surfaces if surfaces is not None else [.79] if obj==s.other_model else [.37,.63,.37,.88]
    s.select(obj)
    target=RUN/name;target.mkdir(exist_ok=True)
    prefs_before=s.compat.snapshot(s.export_prefs)
    before_disabled=s.params(s.disabled)
    last=RUN/'diagnostics/last-failure.txt'
    old=last.read_text() if last.exists() else None
    op=bpy.ops.sollumz.export_assets_legacy if legacy else bpy.ops.sollumz.export_assets
    original_start=s.integration._start_pbr2gta_export
    def start_custom(operator,context,selected_only):
        settings=s.compat.effective_settings(operator,s.export_prefs)
        settings.target_formats={'NATIVE','CWXML'}
        settings.target_versions={'GEN8','GEN9'}
        settings.limit_to_selected=True
        return original_start(operator,context,True)
    if custom:s.integration._start_pbr2gta_export=start_custom
    try:
        returned=op(directory=str(target),direct_export=True,**({'use_custom_settings':True} if custom else {}))
    finally:s.integration._start_pbr2gta_export=original_start
    assert 'CANCELLED' not in returned
    deadline=time.monotonic()+120
    # Selection may change while the worker runs. The session must retain obj.
    s.select(s.other_model if obj!=s.other_model else s.first_model)
    selected=[o.name for o in bpy.context.selected_objects]
    while bpy.context.window_manager.pbr2gta_running:
        assert time.monotonic()<deadline,'worker timeout'
        yield .1
    if last.exists() and last.read_text()!=old:
        raise AssertionError((Path(last.read_text())/'summary.json').read_text())
    assert s.compat.snapshot(s.export_prefs)==prefs_before
    assert s.params(s.disabled)==before_disabled
    assert [o.name for o in bpy.context.selected_objects]==selected
    assert all(node.texture_properties.embedded for node in s.source_nodes)
    if name!='sidecar_without_conversion':
        assert not list(target.rglob('*.png')),(name,list(target.rglob('*.png')))
    xmls=[p for p in target.rglob('*.xml') if p.name.endswith(('.ydr.xml','.ydd.xml'))]
    assert xmls,(name,list(target.rglob('*')))
    assets=[]
    for p in xmls:
        xml=ET.parse(p)
        params=[float(item.attrib['x']) for item in xml.iter('Item') if item.attrib.get('name','').casefold()=='specularfresnel']
        assert sorted(round(v,2) for v in params)==sorted(expected),(name,params)
        assets.append({'name':str(p.relative_to(target)),'surface':params})
    if not legacy and (custom or 'NATIVE' in getattr(s.export_prefs,'target_formats',set())):
        from szio.gta5 import try_load_asset
        natives=list(target.rglob('*.ydr'))+list(target.rglob('*.ydd'))
        assert natives
        for p in natives:
            asset=try_load_asset(p)
            assert asset is not None
            drawables=list(asset.drawables.values()) if hasattr(asset,'drawables') else [asset]
            values=[round(float(param.value[0]),2) for drawable in drawables
                    for shader in drawable.shader_group.shaders for param in shader.parameters
                    if param.name.casefold()=='specularfresnel']
            assert sorted(values)==sorted(expected),(str(p),values,expected)
    assert list(target.rglob('*.ytd.xml'))
    ytd=importlib.import_module(s.ops.__package__+'.ytdexport')
    headers=[]
    for p in target.rglob('*.dds'):
        h=ytd.parse_dds_header(p)
        assert (h.width,h.height,h.mip_levels)==(512,512,1 if h.format_name=='D3DFMT_A8R8G8B8' else 8)
        headers.append(h.format_name)
    expected_formats={'D3DFMT_DXT1','D3DFMT_ATI2',
                      'D3DFMT_A8R8G8B8' if name=='weapon_palette' else 'D3DFMT_DXT5'}
    assert expected_formats<=set(headers),(name,headers)
    record(name,assets=assets,dds_formats=sorted(set(headers)),disabled_preserved=True,preferences_restored=True,selection_preserved=True)

def sequence():
    global s
    sys.path.insert(0,str(SOURCE))
    dep_path=ROOT/'deps_path.txt'
    dep={'2.8.0':'280','2.8.1':'281','2.8.2':'281','2.8.3':'283','2.9.0':'290'}.get(VERSION)
    if dep_path.exists():sys.path.insert(0,dep_path.read_text().strip())
    elif dep:sys.path.insert(0,str(ROOT.parent/('deps'+dep)))
    import addon_utils
    assert addon_utils.enable(PACKAGE,default_set=True),'Sollumz did not load'
    archive=REPO/f'release/PBR2GTA_Addon_{ADDON_VERSION}.zip'
    ret=bpy.ops.extensions.package_install_files(filepath=str(archive),repo='user_default',enable_on_install=True,overwrite=True)
    assert ret=={'FINISHED'}
    addon=importlib.import_module('bl_ext.user_default.pbr2gta')
    installed=Path(addon.__file__).parent
    addon_version=tomllib.loads((installed/'blender_manifest.toml').read_text())['version']
    assert addon_version==ADDON_VERSION
    with zipfile.ZipFile(archive) as package:
        files=[info for info in package.infolist() if not info.is_dir()]
        for info in files:
            relative=Path(*Path(info.filename).parts[1:])
            assert hashlib.sha256((installed/relative).read_bytes()).digest()==hashlib.sha256(package.read(info)).digest(),str(relative)
    record('installed',blender=bpy.app.version_string,sollumz=VERSION,zip_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
           installed_files_verified=len(files),addon_version=addon_version,process_id=os.getpid(),
           operator_properties=list(bpy.ops.sollumz.export_assets.get_rna_type().properties.keys()))
    code=(REPO/'tests/blender/fixture.py').read_text()
    exec(compile(code,'fixture.py','exec'),{'TEST_ROOT':str(RUN)})
    import pbr2gta_audit as s
    s.source_nodes=[]
    for material in (s.other,s.shared):
        source=material.node_tree.nodes.new('ShaderNodeTexImage')
        source.name='PBR_Source_Roughness'
        source.image=material.pbr2gta.roughness
        source.texture_properties.embedded=True
        s.source_nodes.append(source)
    duplicate=s.other.node_tree.nodes.new('ShaderNodeTexImage')
    duplicate.name='PBR_Duplicate_Roughness'
    duplicate.image=bpy.data.images.load(s.other.pbr2gta.roughness.filepath,check_existing=False)
    duplicate.texture_properties.embedded=True
    s.source_nodes.append(duplicate)
    s.bridge.find_node(s.other,'DiffuseSampler').texture_properties.embedded=True
    s.compat=importlib.import_module(s.ops.__package__+'.sollumz_compat')
    calibration=importlib.import_module(s.ops.__package__+'.calibration')
    for m in s.materials:calibration.sync_material(m)
    assert abs(value(s.other,'SpecularFresnel')-.79)<1e-6
    s.other.pbr2gta.surface=.52
    assert abs(value(s.other,'SpecularFresnel')-.52)<1e-6
    s.bridge.find_node(s.other,'SpecularIntensityMult').outputs[0].default_value=7
    bpy.context.view_layer.update()
    yield .2
    assert abs(value(s.other,'SpecularIntensityMult')-.3)<1e-6
    s.other.pbr2gta.enabled=False
    s.bridge.find_node(s.other,'SpecularIntensityMult').outputs[0].default_value=9
    bpy.context.view_layer.update();yield .2
    assert value(s.other,'SpecularIntensityMult')==9
    s.other.pbr2gta.enabled=True
    assert abs(value(s.other,'SpecularIntensityMult')-.3)<1e-6
    s.other.pbr2gta.surface=.79
    record('live_calibration',surface_immediate=True,manual_edit_corrected=True,disabled_not_controlled=True)
    guide=importlib.import_module(s.ops.__package__+'.shader_guide_ui')
    manager=importlib.import_module(s.pkg+'.ydr.shader_materials').ShaderManager
    catalog=list(manager._shaders)
    missing=[filename for filename in catalog if not guide.get_resolved_shader_guide(filename).exists]
    assert not missing,(len(catalog),missing)
    record('shader_guide',resolved=len(catalog))
    s.select(s.first_model)
    session=s.integration.capture_default_session(bpy.context)
    s.select(s.other_model)
    prefs=s.compat.snapshot(s.export_prefs)
    selected=[o.name for o in bpy.context.selected_objects]
    original=s.integration.export_without_intercept
    def fail_export(*a,**kw):raise RuntimeError('Injected synchronous export failure')
    s.integration.export_without_intercept=fail_export
    try:
        try:session.export(bpy.context,RUN/'failure')
        except RuntimeError as exc:assert 'Injected' in str(exc)
        else:raise AssertionError('Expected failure')
    finally:s.integration.export_without_intercept=original
    assert s.compat.snapshot(s.export_prefs)==prefs
    assert [o.name for o in bpy.context.selected_objects]==selected
    assert all(node.texture_properties.embedded for node in s.source_nodes)
    record('failed_resume_restore',passed=True)
    s.start('preview',roots=[s.other_model],preview=True)
    while bpy.context.window_manager.pbr2gta_running:yield .1
    assert not s.status()['error']
    record('preview',passed=True)
    if hasattr(s.export_prefs,'target_formats'):
        s.export_prefs.target_formats={'NATIVE','CWXML'}
        s.export_prefs.target_versions={'GEN8','GEN9'}
    yield from export('ydr',s.other_model)
    yield from export('ydd',s.first_model)
    if hasattr(s.export_prefs,'target_formats'):
        s.export_prefs.target_formats={'CWXML'}
        s.export_prefs.target_versions={'GEN8'}
        yield from export('custom_settings_ydd',s.first_model,custom=True)
    if hasattr(importlib.import_module(s.pkg+'.sollumz_operators'),'SOLLUMZ_OT_export_assets_legacy'):
        yield from export('legacy_ydd',s.first_model,legacy=True)
    # Existing DDS still produce YTD even when PBR conversion is disabled.
    s.other.pbr2gta.enabled=False
    yield from export('sidecar_without_conversion',s.other_model)
    s.other.pbr2gta.enabled=True
    s.other.pbr2gta.workflow='spec_gloss'
    s.other.pbr2gta.diffuse=s.other.pbr2gta.base_color
    s.other.pbr2gta.specular=s.other.pbr2gta.metallic
    s.other.pbr2gta.gloss=s.other.pbr2gta.roughness
    yield from export('spec_gloss',s.other_model)
    s.weapon=s.material('weapon',shader='weapon_normal_spec_palette.sps',surface=.46)
    s.weapon_root=s.obj('weapon','sollumz_drawable')
    s.weapon_model=s.model('weapon_model',s.weapon,s.weapon_root)
    for entry,definition,profile in s.bridge.material_slot_entries(s.weapon):
        if definition['transport_profile_id']=='PALETTE_RGBA8':
            entry.image=s.weapon.pbr2gta.base_color;entry.output_name='audit_weapon_palette'
    if hasattr(s.export_prefs,'target_formats'):
        s.export_prefs.target_formats={'NATIVE','CWXML'}
        s.export_prefs.target_versions={'GEN8','GEN9'}
    yield from export('weapon_palette',s.weapon_model,surfaces=[.46])
    assert any(importlib.import_module(s.ops.__package__+'.ytdexport').parse_dds_header(p).format_name=='D3DFMT_A8R8G8B8'
               for p in (RUN/'weapon_palette').rglob('*.dds'))
    # Missing parameter must be visible before conversion, with no node rebuild.
    node=s.bridge.find_node(s.other,'SpecularFresnel');s.other.node_tree.nodes.remove(node)
    bpy.context.view_layer.update();yield .2
    assert 'SpecularFresnel' in s.other.pbr2gta.calibration_error
    record('damaged_parameter',reported=s.other.pbr2gta.calibration_error)
    sys.path.insert(0,str(REPO/'tests/blender'))
    from texture_naming_case import run as naming_cases
    yield from naming_cases(s,RUN,record)
    # History uses a separate material; Undo may recreate every datablock.
    history=s.create_shader('normal_spec.sps');history.name='CalibrationHistory'
    history.use_fake_user=True;history.pbr2gta.enabled=True;history.pbr2gta.surface=.31
    def naming_snapshot():
        plan=s.bridge.texture_name_plan(prepare=True)
        return {m.name:s.bridge.material_output_names(m,plan) for m in bpy.data.materials
                if m.name in {'Chain','Chain.001','Chain copy'}}
    expected_naming=naming_snapshot()
    bpy.ops.ed.undo_push(message='PBR2GTA baseline')
    history.pbr2gta.surface=.66
    bpy.ops.ed.undo_push(message='PBR2GTA Surface')
    bpy.ops.ed.undo()
    history=bpy.data.materials['CalibrationHistory']
    assert abs(history.pbr2gta.surface-.31)<1e-6
    assert abs(value(history,'SpecularFresnel')-.31)<1e-6
    bpy.ops.ed.redo()
    history=bpy.data.materials['CalibrationHistory']
    assert abs(value(history,'SpecularFresnel')-.66)<1e-6
    record('undo_redo',passed=True)
    assert naming_snapshot()==expected_naming
    record('naming_undo_redo',passed=True)
    saved=RUN/'calibration.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(saved))
    bpy.ops.wm.open_mainfile(filepath=str(saved),load_ui=False)
    # Return control to Blender after replacing the window manager. Quitting
    # within open_mainfile's original timer context crashes Blender 4.5 itself.
    yield .3
    history=bpy.data.materials['CalibrationHistory']
    assert abs(value(history,'SpecularFresnel')-.66)<1e-6
    history.pbr2gta.surface=.42
    assert abs(value(history,'SpecularFresnel')-.42)<1e-6
    record('save_reopen',passed=True)
    assert naming_snapshot()==expected_naming
    record('naming_save_reopen',passed=True)
    (ROOT/'result.json').write_text(json.dumps({'passed':True,'cases':rows},indent=2,default=str))

steps=sequence()
def tick():
    try:return next(steps)
    except StopIteration:
        bpy.ops.wm.quit_blender();return None
    except Exception:
        error=traceback.format_exc();print(error)
        (ROOT/'result.json').write_text(json.dumps({'passed':False,'error':error,'cases':rows},indent=2,default=str))
        bpy.ops.wm.quit_blender();return None
bpy.app.timers.register(tick,first_interval=.2,persistent=True)
