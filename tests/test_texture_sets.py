from pathlib import Path
import pytest
from pbr2gta_blender.texture_sets import classify, detect_set


def files(folder, *names):
    folder.mkdir(parents=True, exist_ok=True)
    for name in names:
        (folder / name).touch()
    return folder / names[0]


def test_set_isolation_and_case(tmp_path):
    anchor = files(tmp_path, 'Toad_BaseColor.PNG', 'Toad_metallic.png', 'TOAD_roughness.png', 'Toad_normal.png',
                   'Other_BaseColor.png', 'Other_metallic.png', 'Other_roughness.png', 'Toad_gta_d.png')
    found = detect_set(anchor, 'metal_rough')
    assert set(found) == {'base_color', 'metallic', 'roughness', 'normal'}
    assert all(p.name.casefold().startswith('toad_') for p in found.values())


def test_conflicts_not_silently_selected(tmp_path):
    anchor = files(tmp_path, 'X_base_color.png', 'X_albedo.png', 'X_m.png', 'X_r.png')
    with pytest.raises(ValueError, match='Ambiguous.*base_color'):
        detect_set(anchor, 'metal_rough')


def test_incomplete(tmp_path):
    anchor = files(tmp_path, 'X_basecolor.png', 'X_roughness.png')
    with pytest.raises(ValueError, match='missing: metallic'):
        detect_set(anchor, 'metal_rough')


def test_spec_gloss_and_role_only_names(tmp_path):
    anchor = files(tmp_path, 'diffuse.png', 'specular.png', 'smoothness.png')
    assert set(detect_set(anchor, 'spec_gloss')) == {'diffuse', 'specular', 'gloss'}


@pytest.mark.parametrize('name', ['X_orm.png', 'X_ao.png', 'X_gta_normal_spec.png', 'X_gta_d.png', 'X_normal.dds', 'random.png', 'X_NormalGL.png'])
def test_non_sources_excluded(name):
    assert classify(Path(name), 'metal_rough') is None


def test_web_aliases_and_boundaries():
    assert classify(Path('Toad_base_colour_map.png'), 'metal_rough') == ('toad', 'base_color')
    assert classify(Path('Toad_шероховатость.png'), 'metal_rough') == ('toad', 'roughness')
    assert classify(Path('normals.png'), 'metal_rough')[1] == 'normal'
    assert classify(Path('foobar.png'), 'metal_rough') is None
