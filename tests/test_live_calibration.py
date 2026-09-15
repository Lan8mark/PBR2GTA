import sys
from types import SimpleNamespace as NS
from pbr2gta_blender.calibration import sync_material


def fixture(monkeypatch, value=.3, enabled=True, fail=False):
    node=NS(outputs=[NS(default_value=value)])
    material=NS(pbr2gta=NS(enabled=enabled,calibration_error=''))
    writes=[]
    def validate(*_):
        if fail:raise ValueError('missing SpecularIntensityMult')
    def patch(mat, values):
        writes.append(values)
        node.outputs[0].default_value=values['SpecularIntensityMult'][0]
    monkeypatch.setitem(sys.modules,'pbr2gta_blender.bridge',NS(
        shader_parameter_patch=lambda _: {'SpecularIntensityMult':[.3]},
        validate_parameter_patch=validate, find_node=lambda *_:node, patch_material=patch))
    return material,node,writes


def test_float32_value_does_not_create_update_loop(monkeypatch):
    m,n,w=fixture(monkeypatch,value=.30000001192092896)
    for _ in range(10):sync_material(m)
    assert not w


def test_manual_change_is_corrected_once(monkeypatch):
    m,n,w=fixture(monkeypatch,value=9)
    sync_material(m);sync_material(m)
    assert n.outputs[0].default_value==.3 and len(w)==1


def test_disabled_material_is_not_changed(monkeypatch):
    m,n,w=fixture(monkeypatch,value=9,enabled=False)
    sync_material(m)
    assert n.outputs[0].default_value==9 and not w


def test_invalid_material_has_visible_error_without_partial_write(monkeypatch):
    m,n,w=fixture(monkeypatch,value=9,fail=True)
    sync_material(m)
    assert 'SpecularIntensityMult' in m.pbr2gta.calibration_error
    assert n.outputs[0].default_value==9 and not w
