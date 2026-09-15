from pbr2gta_core.profiles import (
    GENERIC_DROP_IN_SHADERS,
    GENERIC_MASK_REQUIRED_SHADERS,
    WEAPON_SHADERS,
    classify_shader,
)


def test_profile_registry_is_complete_and_unique() -> None:
    selectors = [
        *WEAPON_SHADERS,
        *GENERIC_DROP_IN_SHADERS,
        *GENERIC_MASK_REQUIRED_SHADERS,
    ]
    assert len(WEAPON_SHADERS) == 5
    assert len(GENERIC_DROP_IN_SHADERS) == 47
    assert len(GENERIC_MASK_REQUIRED_SHADERS) == 5
    assert len(selectors) == len(set(selectors)) == 57


def test_classifier_handles_paths_case_and_required_mask() -> None:
    weapon = classify_shader(r"common\shaders\WEAPON_NORMAL_SPEC_PALETTE.SPS")
    generic = classify_shader("spec_twiddle_tnt")
    unknown = classify_shader("not_supported.sps")
    assert weapon.status == "supported" and weapon.output_mode == "weapon"
    assert generic.status == "supported" and generic.output_mode == "default"
    assert generic.requires_spec_map_int_mask
    assert unknown.status == "unsupported"
