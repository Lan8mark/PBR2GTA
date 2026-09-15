"""Naming regressions, run inside the isolated installed-ZIP matrix."""
import hashlib
from pathlib import Path
from xml.etree import ElementTree as ET

import bpy


def run(s, root, record):
    bridge = s.bridge
    first = s.material('naming_first', surface=.41)
    second = s.material('naming_second', surface=.62)
    first.name = 'Chain'
    second.name = 'Chain.001'
    drawable = s.obj('naming', 'sollumz_drawable')
    model = s.model('naming_first', first, drawable)
    # Two polygon material slots on one model, plus a shared material model.
    mesh = model.data
    mesh.clear_geometry()
    mesh.from_pydata([(0,0,0),(1,0,0),(0,1,0),(0,0,1)], [], [(0,1,2),(0,2,3)])
    mesh.materials.append(second)
    mesh.polygons[1].material_index = 1
    if not mesh.uv_layers: mesh.uv_layers.new(name='UVMap 0')
    if not mesh.color_attributes: mesh.color_attributes.new(name='Color 1',type='BYTE_COLOR',domain='CORNER')
    for item in mesh.color_attributes[0].data: item.color=(1,1,1,1)
    s.model('naming_shared', first, drawable)
    for mat in (first,second):
        mat.pbr2gta.diffuse_name = 'same_old_name'
        mat.pbr2gta.specular_name = 'same_old_spec'
        mat.pbr2gta.normal_name = 'same_old_normal'
    s.select(model)
    plan=bridge.build_export_plan(bpy.context,root/'naming-plan')
    assert len(plan)==2
    assert plan[0].request['output_names']['diffuse']=='chain_d.dds'
    assert plan[1].request['output_names']['diffuse']=='chain.001_d.dds'

    def hashes(directory):
        return {str(p.relative_to(directory)):hashlib.sha256(p.read_bytes()).hexdigest()
                for p in directory.rglob('*') if p.is_file()}

    def check(directory, different=False):
        xmls=list(directory.rglob('*.ydr.xml'))
        assert xmls
        for path in xmls:
            document=ET.parse(path)
            referenced={n.text for n in document.findall('.//Parameters/Item/Name') if n.text}
            assert {'chain_d','chain.001_d'}<=referenced,(path,referenced)
        for path in directory.rglob('*.ytd.xml'):
            names={n.text for n in ET.parse(path).findall('.//Name')}
            assert {'chain_d','chain.001_d'}<=names,(path,names)
        one=list(directory.rglob('chain_d.dds'))
        two=list(directory.rglob('chain.001_d.dds'))
        assert one and two
        assert (one[0].read_bytes()!=two[0].read_bytes())==different
        for mat,stem in [(first,'chain'),(second,'chain.001')]:
            node=bridge.find_node(mat,'DiffuseSampler')
            assert node.sollumz_texture_name==stem+'_d'
            assert Path(node.image.filepath).name==stem+'_d.dds'
        if hasattr(s.export_prefs,'target_formats'):
            from szio.gta5 import try_load_asset
            for path in directory.rglob('*.ydr'):
                asset=try_load_asset(path)
                assert asset is not None
                texture_names={p.value for shader in asset.shader_group.shaders
                               for p in shader.parameters if isinstance(p.value,str)}
                assert {'chain_d','chain.001_d'}<=texture_names,(path,texture_names)

    def start_export(name):
        directory=root/name
        directory.mkdir(exist_ok=True)
        s.select(model)
        bpy.ops.sollumz.export_assets('EXEC_DEFAULT',directory=str(directory),direct_export=True)
        return directory

    directory=start_export('naming_shared_sources')
    while bpy.context.window_manager.pbr2gta_running: yield .1
    check(directory)
    record('naming_shared_sources',materials=2,mesh_material_slots=2,shared_material_deduplicated=True)
    second.pbr2gta.base_color=bpy.data.images.load(str(s.source/'variant.png'),check_existing=False)
    directory=start_export('naming_different_sources')
    while bpy.context.window_manager.pbr2gta_running: yield .1
    check(directory,True)
    record('naming_different_sources',distinct_dds_verified=True)

    for iteration in range(2):
        s.start('naming_preview_'+str(iteration),roots=[model],preview=True)
        while bpy.context.window_manager.pbr2gta_running: yield .1
        assert bridge.find_node(first,'DiffuseSampler').sollumz_texture_name=='chain_d'
    directory=start_export('naming_after_preview')
    while bpy.context.window_manager.pbr2gta_running: yield .1
    check(directory,True)
    record('naming_preview_cache_export',passed=True)

    # A retained DDS is reserved without modifying the disabled material.
    retained=s.material('naming_retained',enabled=False)
    retained.use_fake_user=True
    retained_node=bridge.find_node(retained,'DiffuseSampler')
    retained_node.image=bridge.find_node(first,'DiffuseSampler').image
    retained_image=retained_node.image
    before=retained_image.filepath
    names=bridge.material_output_names(first)
    assert names['DiffuseSampler']!='chain_d.dds'
    s.start('naming_reserved_preview',roots=[model],preview=True)
    while bpy.context.window_manager.pbr2gta_running: yield .1
    assert Path(bridge.find_node(first,'DiffuseSampler').image.filepath).name==names['DiffuseSampler']
    assert retained_node.image==retained_image and retained_image.filepath==before
    assert Path(before).is_file()
    record('naming_disabled_reserved',passed=True)

    # Collision context is independent of the selected material subset.
    second.name='CHAIN!'
    plan=bridge.texture_name_plan(prepare=True)
    first_names=bridge.material_output_names(first,plan)
    second_names=bridge.material_output_names(second,plan)
    assert not set(first_names.values()) & set(second_names.values())
    assert first_names==bridge.material_output_names(first)
    s.start('naming_collision_preview',roots=[model],preview=True)
    while bpy.context.window_manager.pbr2gta_running: yield .1
    assert Path(bridge.find_node(first,'DiffuseSampler').image.filepath).name==first_names['DiffuseSampler']
    original_identifier=first.pbr2gta.material_uuid
    copied=first.copy()
    copied.name='Chain copy'
    copied.use_fake_user=True
    bridge.texture_name_plan(prepare=True)
    assert copied.pbr2gta.material_uuid!=first.pbr2gta.material_uuid
    assert first.pbr2gta.material_uuid==original_identifier
    record('naming_collisions_and_copy',passed=True)

    # Rename while conversion runs: final directory and assignments unchanged.
    second.name='Chain.001'
    directory=root/'naming_cancel'
    directory.mkdir(exist_ok=True)
    (directory/'unrelated.txt').write_text('preserve me')
    before_files=hashes(directory)
    before_images={node:node.image for mat in (first,second) for node in mat.node_tree.nodes if node.type=='TEX_IMAGE'}
    s.select(model)
    bpy.ops.sollumz.export_assets('EXEC_DEFAULT',directory=str(directory),direct_export=True)
    first.name='Renamed during conversion'
    while bpy.context.window_manager.pbr2gta_running: yield .1
    assert hashes(directory)==before_files
    assert all(node.image==image for node,image in before_images.items())
    diagnostic=Path((root/'diagnostics/last-failure.txt').read_text().strip())/'summary.json'
    assert 'changed during conversion' in diagnostic.read_text()
    first.name='Chain'
    record('naming_rename_cancel',files_and_materials_preserved=True)

    # Failure after image application must roll back existing outputs too.
    original=s.integration.resume_pending_export
    def fail(*args,**kwargs): raise RuntimeError('Injected naming export failure')
    s.ops.resume_pending_export=fail
    try:
        s.select(model)
        bpy.ops.sollumz.export_assets('EXEC_DEFAULT',directory=str(directory),direct_export=True)
        while bpy.context.window_manager.pbr2gta_running: yield .1
        assert hashes(directory)==before_files
        assert all(node.image==image for node,image in before_images.items())
        diagnostic=Path((root/'diagnostics/last-failure.txt').read_text().strip())/'summary.json'
        assert 'Injected naming export failure' in diagnostic.read_text()
    finally:s.ops.resume_pending_export=original
    record('naming_export_failure_rollback',passed=True)
    # The retry is successful after both cancellation paths.
    directory=start_export('naming_retry')
    while bpy.context.window_manager.pbr2gta_running: yield .1
    assert list(directory.rglob('*.ydr.xml'))
    record('naming_retry',passed=True)
