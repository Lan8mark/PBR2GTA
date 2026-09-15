from types import SimpleNamespace as NS
import pytest
from pbr2gta_blender.export_sources import source_embedding_guard


@pytest.mark.parametrize('fail', [False, True])
def test_source_embedding_scope_and_restore(fail):
    source=NS(filepath='/sources/rough.png')
    duplicate=NS(filepath='/sources/rough.png')
    other=NS(filepath='/sources/unrelated.png')
    def node(name, image, embedded=True):
        return NS(type='TEX_IMAGE',name=name,image=image,texture_properties=NS(embedded=embedded))
    nodes=[node('PBR_Source',source),node('PBR_Duplicate',duplicate),
           node('DiffuseSampler',source),node('Unrelated',other),node('AlreadyExternal',source,False)]
    settings=NS(enabled=True,roughness=source,slots=[])
    material=NS(pbr2gta=settings,shader_properties=NS(filename='normal_spec.sps'),node_tree=NS(nodes=nodes))
    disabled=node('DisabledSource',source)
    off=NS(pbr2gta=NS(enabled=False),node_tree=NS(nodes=[disabled]))
    manager=NS(find_shader=lambda _:NS(parameter_map={'DiffuseSampler':object()}))
    try:
        with source_embedding_guard([material,off],manager,lambda p:p):
            assert [n.texture_properties.embedded for n in nodes]==[False,False,True,True,False]
            assert disabled.texture_properties.embedded
            if fail:raise RuntimeError('export failed')
    except RuntimeError:
        assert fail
    assert [n.texture_properties.embedded for n in nodes]==[True,True,True,True,False]
    assert disabled.texture_properties.embedded
