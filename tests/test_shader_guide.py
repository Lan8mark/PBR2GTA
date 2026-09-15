from types import SimpleNamespace

from pbr2gta_blender.shader_guide import load_guide_data, resolve_shader_guide


def test_bundled_guide_contains_full_source_catalog():
    data = load_guide_data()
    assert len(data["base_guides"]) == 249
    assert len(data["preset_overrides"]) == 201
    assert len(data["evidence"]) == 93


def test_unknown_shader_does_not_receive_another_shaders_instructions():
    shader = SimpleNamespace(preset_name="unknown.sps", base_name="unknown",
                             render_bucket=0, parameters=[], layouts=[])
    resolved = resolve_shader_guide(shader)
    assert not resolved.exists
    assert resolved.coverage == "missing"
