"""Run after fixture.py in a disposable installed-build scene."""
import bpy, importlib, shutil, traceback
from pathlib import Path
from szio.gta5 import try_load_asset
import pbr2gta_audit as s


def sequence():
    folder = s.root_path / 'detected'
    folder.mkdir()
    for name, source in [('Toad_BaseColor','color'), ('Toad_Metallic','metal'),
                         ('Toad_Roughness','rough'), ('Toad_Normal','normal'),
                         ('Other_BaseColor','variant'), ('Other_Metallic','metal'),
                         ('Other_Roughness','variant_rough')]:
        shutil.copyfile(s.source/(source+'.png'), folder/(name+'.png'))
    s.select(s.first_model)
    untouched = s.other.pbr2gta.base_color
    assert bpy.ops.pbr2gta.detect_texture_set(filepath=str(folder/'Toad_BaseColor.png')) == {'FINISHED'}
    assert s.shared.pbr2gta.base_color.filepath.endswith('Toad_BaseColor.png')
    assert s.shared.pbr2gta.normal.filepath.endswith('Toad_Normal.png')
    assert s.other.pbr2gta.base_color == untouched
    prior = s.shared.pbr2gta.base_color
    shutil.copyfile(folder/'Toad_BaseColor.png',folder/'Toad_Albedo.png')
    try:
        bpy.ops.pbr2gta.detect_texture_set(filepath=str(folder/'Toad_BaseColor.png'))
        raise AssertionError('Ambiguous set accepted')
    except RuntimeError as exc:
        assert 'Ambiguous' in str(exc)
    assert s.shared.pbr2gta.base_color == prior
    # A new set without Normal must not inherit the previous set's normal map.
    assert bpy.ops.pbr2gta.detect_texture_set(filepath=str(folder/'Other_BaseColor.png')) == {'FINISHED'}
    assert s.shared.pbr2gta.normal is None
    # Return to the complete normal-map set without touching the other material.
    (folder/'Toad_Albedo.png').unlink()
    bpy.ops.pbr2gta.detect_texture_set(filepath=str(folder/'Toad_BaseColor.png'))
    s.record('texture_detection', {'assigned':4, 'conflict_preserved':True, 'other_material_preserved':True, 'stale_normal_cleared':True})

    # One mesh, three faces, three material slots; also a shared instance and LOW.
    root = s.obj('multi','sollumz_drawable')
    model = s.model('multi_model',s.shared,root)
    mesh = model.data
    mesh.clear_geometry()
    mesh.from_pydata([(x,y,0) for x in (0,2,4) for y in (0,1,2)], [], [(0,1,2),(3,4,5),(6,7,8)])
    # Replace collinear triples with real triangles.
    for index in (2,5,8): mesh.vertices[index].co.x += 1
    mesh.materials.append(s.other)
    mesh.materials.append(s.disabled)
    for index, polygon in enumerate(mesh.polygons): polygon.material_index=index
    if not mesh.uv_layers: mesh.uv_layers.new(name='UVMap 0')
    for index, item in enumerate(mesh.uv_layers[0].data): item.uv = ((index % 3)==1, (index % 3)==2)
    if not mesh.color_attributes: mesh.color_attributes.new(name='Color 1',type='BYTE_COLOR',domain='CORNER')
    for item in mesh.color_attributes[0].data: item.color=(1,1,1,1)
    model.sz_lods.get_lod(s.enums.LODLevel.LOW).mesh = s.mesh('multi_low',s.low)
    s.model('multi_shared',s.shared,root)
    before_disabled = s.params(s.disabled)
    before_nodes = [(n.name, getattr(getattr(n,'image',None),'filepath',None)) for n in s.disabled.node_tree.nodes]
    rows=[]
    for legacy in (False, True):
        s.start('multi_legacy' if legacy else 'multi_modern',roots=[model],legacy=legacy)
        assert {p.material.name for p in s.plan} == {s.shared.name,s.other.name,s.low.name}
        assert len(s.plan)==3
        while bpy.context.window_manager.pbr2gta_running: yield .1
        assert not s.status()['error'], s.status()
        assert s.params(s.disabled)==before_disabled
        assert [(n.name,getattr(getattr(n,'image',None),'filepath',None)) for n in s.disabled.node_tree.nodes]==before_nodes
        exports = [p for p in s.actual_output.rglob('*') if p.name.endswith(('.ydr','.ydr.xml'))]
        assert exports
        for path in exports:
            asset=try_load_asset(path)
            assert asset is not None, path
            values=[]
            for shader in asset.shader_group.shaders:
                params={p.name.casefold():p.value for p in shader.parameters}
                values.append(round(float(params['specularfresnel'][0]),2))
            assert sorted(values)==sorted([.37,.79,.88,.63]), (path,values)
            rows.append({'file':str(path.relative_to(s.root_path)),'fresnel':values})
    s.record('multi_material_verified', {'exports':rows,'disabled_unchanged':True,'shared_converted_once':True,'low_included':True})


steps=sequence()
def tick():
    try: return next(steps)
    except StopIteration:
        s.record('texture_multi_complete',{'passed':True})
    except Exception:
        s.record('texture_multi_failed',{'traceback':traceback.format_exc()})
        traceback.print_exc()
    return None
bpy.app.timers.register(tick,first_interval=.1)
