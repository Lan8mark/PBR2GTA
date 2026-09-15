import bpy, sys, importlib, types, json, shutil, time, tempfile, struct, zlib
from pathlib import Path

assert 'pbr2gta_audit' not in sys.modules
state = types.ModuleType('pbr2gta_audit')
sys.modules[state.__name__] = state
state.root_path = Path(TEST_ROOT) if 'TEST_ROOT' in globals() else Path(tempfile.mkdtemp(prefix='pbr2gta-regression-'))
(state.root_path/'evidence').mkdir(parents=True,exist_ok=True)
state.bridge = importlib.import_module('bl_ext.user_default.pbr2gta.bridge')
state.ops = importlib.import_module('bl_ext.user_default.pbr2gta.operators')
state.integration = importlib.import_module('bl_ext.user_default.pbr2gta.sollumz_integration')
assert not bpy.context.window_manager.pbr2gta_running
state.selection = list(bpy.context.selected_objects)
state.active = bpy.context.active_object
state.original_image_ptrs = {i.as_pointer() for i in bpy.data.images}
state.materials, state.meshes, state.objects, state.images = [], [], [], []
state.collection = bpy.data.collections.new('PBR2GTA_AUDIT_027')
bpy.context.scene.collection.children.link(state.collection)
state.prefs = state.bridge.addon_preferences(bpy.context)
state.export_prefs = state.integration._preference_export_settings(bpy.context)
state.saved_prefs = {name: getattr(state.prefs, name) for name in ('cache_dir','nvcompress_path','nvtt_sha256','nvtt_error','core_path_override')}
state.saved_selected_only = state.export_prefs.limit_to_selected
state.saved_pref_autosave = bpy.context.preferences.use_preferences_save
bpy.context.preferences.use_preferences_save = False
state.prefs.cache_dir = str(state.root_path/'cache')
state.export_prefs.limit_to_selected = True
state.pkg = state.integration._find_export_operator().__module__.rsplit('.',1)[0]
state.create_shader = importlib.import_module(state.pkg+'.ydr.shader_materials').create_shader
state.enums = importlib.import_module(state.pkg+'.sollumz_properties')
state.source = state.root_path/'sources'
state.source.mkdir(exist_ok=True)
def write_png(name, rgba):
    def chunk(kind, data):
        return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data))
    pixels=(b'\x00'+bytes(rgba)*512)*512
    (state.source/(name+'.png')).write_bytes(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',512,512,8,6,0,0,0))+chunk(b'IDAT',zlib.compress(pixels))+chunk(b'IEND',b''))
for name,rgba in {'color':(128,128,128,255),'metal':(100,100,100,255),'rough':(120,120,120,255),'normal':(128,128,128,255),'variant':(35,80,220,255),'variant_rough':(210,210,210,255)}.items():
    write_png(name,rgba)


def obj(name, kind, data=None, parent=None):
    o = bpy.data.objects.new('audit_'+name,data)
    state.collection.objects.link(o)
    o.sollum_type = kind
    o.parent = parent
    state.objects.append(o)
    return o

def material(name, enabled=True, surface=.37, shader='normal_spec.sps'):
    m = state.create_shader(shader)
    m.name = 'audit_'+name
    state.materials.append(m)
    m.pbr2gta.surface = surface
    if enabled:
        for role, filename in [('base_color','color'),('metallic','metal'),('roughness','rough'),('normal','normal')]:
            image = bpy.data.images.load(str(state.source/(filename+'.png')),check_existing=False)
            state.images.append(image)
            setattr(m.pbr2gta,role,image)
        for role in ('diffuse','specular','normal'):
            setattr(m.pbr2gta,role+'_name',m.name+'_'+role)
    m.pbr2gta.enabled = enabled
    reset_params(m)
    return m

def mesh(name, m):
    data = bpy.data.meshes.new('audit_'+name)
    state.meshes.append(data)
    data.from_pydata([(0,0,0),(1,0,0),(0,1,0)],[],[(0,1,2)])
    data.materials.append(m)
    uv = data.uv_layers.new(name='UVMap 0')
    for item, value in zip(uv.data,[(0,0),(1,0),(0,1)]):
        item.uv = value
    color = data.color_attributes.new(name='Color 1',type='BYTE_COLOR',domain='CORNER')
    for item in color.data:
        item.color = (1,1,1,1)
    data.update()
    return data

def model(name, m, root):
    data = mesh(name,m)
    o = obj(name,'sollumz_drawable_model',data,root)
    o.sz_lods.get_lod(state.enums.LODLevel.HIGH).mesh = data
    o.sz_lods.set_highest_lod_active()
    return o

def select(*objects):
    for o in bpy.context.selected_objects:
        o.select_set(False)
    for o in objects:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objects[0] if objects else None
    bpy.context.view_layer.update()

def reset_params(m):
    state.bridge.patch_material(m,{'SpecularIntensityMult':[.91],'SpecularFalloffMult':[23.0],'SpecularFresnel':[.88]})

def params(m):
    result = {}
    for name in ('SpecularIntensityMult','SpecularFalloffMult','SpecularFresnel','specMapIntMask'):
        node = state.bridge.find_node(m,name)
        if state.bridge.is_parameter_node(node):
            result[name] = [float(o.default_value) for o in node.outputs]
    return result

def record(name, data):
    (state.root_path/'evidence'/(name+'.json')).write_text(json.dumps(data,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    return data

def start(case, roots=None, legacy=False, preview=False, **kwargs):
    assert not bpy.context.window_manager.pbr2gta_running
    assert not state.integration.has_pending_export()
    if roots is not None:
        select(*roots)
    state.case = case
    state.output = state.root_path/'exports'/case
    state.case_started = time.time()
    state.output.mkdir(parents=True,exist_ok=True)
    state.case_evidence = state.root_path/'evidence'/case
    state.case_evidence.mkdir(exist_ok=True)
    if preview:
        returned = bpy.ops.pbr2gta.export_assets('EXEC_DEFAULT',inject_only=True)
    elif legacy:
        returned = bpy.ops.sollumz.export_assets_legacy('EXEC_DEFAULT',directory=str(state.output),direct_export=True)
    else:
        settings = dict(use_custom_settings=True,limit_to_selected=True,target_formats={'NATIVE','CWXML'},target_versions={'GEN8'})
        settings.update(kwargs)
        returned = bpy.ops.sollumz.export_assets('EXEC_DEFAULT',directory=str(state.output),direct_export=True,**settings)
    state.last_op = state.ops._active_export_operator
    state.job_dir = Path(state.last_op._job_dir) if state.last_op else None
    if state.last_op:
        state.process = state.last_op._process
        state.plan = state.last_op._planned
        state.actual_output = Path(state.last_op.directory)
    else:
        state.plan = []
        state.actual_output = state.output
    snapshot = {
        'returned': sorted(returned), 'running': bpy.context.window_manager.pbr2gta_running,
        'selection': [o.name for o in bpy.context.selected_objects],
        'planned': [m.material.name for m in state.plan],
        'output': str(state.actual_output), 'before_params': {m.name:params(m) for m in state.materials},
    }
    record(case+'_start',snapshot)
    if state.job_dir:
        captured_job_dir = state.job_dir
        def capture():
            for name in ('request.json','result.json','worker.jsonl'):
                src = captured_job_dir/name
                if src.is_file():
                    try:
                        shutil.copyfile(src,state.case_evidence/name)
                    except OSError:
                        pass
            return .04 if bpy.context.window_manager.pbr2gta_running else None
        bpy.app.timers.register(capture,first_interval=.01)
    return snapshot

def status():
    running = bpy.context.window_manager.pbr2gta_running
    last_error = None
    try:
        last_error = state.last_op._last_error if state.last_op else None
    except ReferenceError:
        pass
    data = {
        'case': state.case, 'running': running,'pending': state.integration.has_pending_export(),
        'error': last_error, 'selection': [o.name for o in bpy.context.selected_objects],
        'params': {m.name: params(m) for m in state.materials},
        'files': [str(p.relative_to(state.actual_output)) for p in state.actual_output.rglob('*') if p.is_file()],
        'generated_images': [{'name': i.name, 'users': i.users,'path': i.filepath} for i in bpy.data.images if i.as_pointer() not in state.original_image_ptrs and i.get('pbr2gta_generated')],
    }
    if not running:
        record(state.case+'_finish',data)
    return data

state.obj, state.material, state.mesh, state.model, state.select = obj, material, mesh, model, select
state.reset_params, state.params, state.start, state.status, state.record = reset_params,params,start,status,record
state.dictionary = obj('dictionary','sollumz_drawable_dictionary')
state.first = obj('first','sollumz_drawable',parent=state.dictionary)
state.second = obj('second','sollumz_drawable',parent=state.dictionary)
state.shared = material('shared')
state.low = material('low',surface=.63)
state.disabled = material('disabled',enabled=False)
state.first_model = model('first_model',state.shared,state.first)
state.first_model.sz_lods.get_lod(state.enums.LODLevel.LOW).mesh = mesh('low_mesh',state.low)
state.second_model = model('second_model',state.shared,state.second)
state.disabled_model = model('disabled_model',state.disabled,state.second)
state.separate = obj('separate','sollumz_drawable')
state.other = material('other',surface=.79)
state.other_model = model('other_model',state.other,state.separate)
select(state.first_model)
result = record('fixture',{'selection':[o.name for o in bpy.context.selected_objects], 'planned':[m.name for m in state.bridge.configured_materials(bpy.context,True)], 'cache':state.prefs.cache_dir})
