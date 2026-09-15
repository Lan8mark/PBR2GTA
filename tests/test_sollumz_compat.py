from types import SimpleNamespace as NS
import pytest
from pbr2gta_blender.sollumz_compat import effective_settings, snapshot, temporary_settings


@pytest.mark.parametrize('style', ['preferences', 'nested', 'flat'])
def test_settings_are_captured_from_the_active_source(style):
    prefs=NS(limit_to_selected=False,target_formats={'CWXML'})
    chosen=NS(limit_to_selected=True,target_formats={'NATIVE'},use_custom_settings=True)
    op=NS() if style=='preferences' else NS(use_custom_settings=True,custom_settings=chosen) if style=='nested' else chosen
    captured=snapshot(effective_settings(op,prefs))
    assert captured['limit_to_selected']==(style!='preferences')
    assert captured['target_formats']==({'CWXML'} if style=='preferences' else {'NATIVE'})
    assert 'use_custom_settings' not in captured
    effective_settings(op,prefs).target_formats.clear()
    assert captured['target_formats']


def test_restore_preferences_on_export_failure():
    prefs=NS(limit_to_selected=False,target_formats={'NATIVE'})
    with pytest.raises(RuntimeError):
        with temporary_settings(prefs,{'limit_to_selected':True,'target_formats':{'CWXML'}}):
            assert prefs.limit_to_selected
            raise RuntimeError('export failed')
    assert prefs.limit_to_selected is False and prefs.target_formats=={'NATIVE'}


def test_missing_setting_fails_before_any_mutation():
    prefs=NS(limit_to_selected=False)
    with pytest.raises(AttributeError):
        with temporary_settings(prefs,{'limit_to_selected':True,'unsupported':True}):
            pytest.fail('must not execute')
    assert prefs.limit_to_selected is False


def test_flat_operator_snapshot_excludes_blender_metadata():
    props=[NS(identifier=name,is_readonly=False,type='STRING')
           for name in ('bl_idname','directory','target_formats','limit_to_selected')]
    operator=NS(bl_rna=NS(properties=props),bl_idname='sollumz.export_assets',
                directory='chosen',target_formats={'NATIVE'},limit_to_selected=True)
    prefs=NS(target_formats={'CWXML'},limit_to_selected=False)
    assert snapshot(operator,prefs)=={'target_formats':{'NATIVE'},'limit_to_selected':True}
