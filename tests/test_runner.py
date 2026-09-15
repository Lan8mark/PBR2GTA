from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from pbr2gta_core.runner import RequestError, run_request

NVTT = Path(r"C:\Program Files\NVIDIA Corporation\NVIDIA Texture Tools\nvcompress.exe")


def _write(path: Path, value: np.ndarray) -> str:
    assert cv2.imwrite(str(path), value)
    return str(path)


def _sources(tmp_path: Path, workflow: str, *, sixteen_bit: bool = False) -> dict[str, str]:
    dtype = np.uint16 if sixteen_bit else np.uint8
    maximum = np.iinfo(dtype).max
    color = np.empty((16, 16, 4), dtype=dtype)
    color[..., :3] = maximum // 2
    color[..., 3] = maximum
    scalar = np.linspace(0, maximum, 256, dtype=dtype).reshape(16, 16)
    normal = np.empty((16, 16, 4), dtype=dtype)
    normal[..., 0] = maximum
    normal[..., 1] = maximum // 2
    normal[..., 2] = maximum // 2
    normal[..., 3] = maximum
    if workflow == "metal_rough":
        return {
            "base_color": _write(tmp_path / "base.png", color),
            "metallic": _write(tmp_path / "metal.png", scalar),
            "roughness": _write(tmp_path / "rough.png", scalar),
            "normal": _write(tmp_path / "normal.png", normal),
        }
    color8 = np.rint(color.astype(np.float64) * (255.0 / maximum)).astype(np.uint8)
    scalar8 = np.rint(scalar.astype(np.float64) * (255.0 / maximum)).astype(np.uint8)
    return {
        "diffuse": _write(tmp_path / "diffuse.png", color8),
        "specular": _write(tmp_path / "spec.png", scalar8),
        "gloss": _write(tmp_path / "gloss.png", scalar),
        "normal": _write(tmp_path / "normal.png", normal),
    }


def _request(tmp_path: Path, workflow: str, shader: str, *, sixteen_bit: bool = False) -> dict:
    return {
        "schema": "pbr2gta.convert.v1",
        "nvcompress_path": str(NVTT),
        "cache_dir": str(tmp_path / "cache"),
        "output_dir": str(tmp_path / "output"),
        "materials": [
            {
                "id": "material-id",
                "workflow": workflow,
                "shader": shader,
                "stem": "sample",
                "surface": 0.45,
                "sources": _sources(tmp_path, workflow, sixteen_bit=sixteen_bit),
            }
        ],
    }


@pytest.mark.skipif(not NVTT.is_file(), reason="NVTT 3.2.5 is not installed")
@pytest.mark.parametrize(
    ("workflow", "shader", "expected_spec"),
    [
        ("metal_rough", "weapon_normal_spec_palette.sps", "DXT1"),
        ("spec_gloss", "normal_spec.sps", "DXT5"),
    ],
)
def test_real_conversion_and_cache(
    tmp_path: Path, workflow: str, shader: str, expected_spec: str
) -> None:
    request = _request(tmp_path, workflow, shader, sixteen_bit=True)
    first = run_request(request)
    second = run_request(request)
    assert first["materials"][0]["cache_hit"] is False
    assert second["materials"][0]["cache_hit"] is True
    records = {item["role"]: item for item in first["materials"][0]["files"]}
    assert records["specular"]["fourcc"] == expected_spec
    assert records["normal"]["fourcc"] == "BC5U"
    assert records["normal"]["mip_count"] == 3
    assert all(Path(item["path"]).is_file() for item in records.values())


@pytest.mark.skipif(not NVTT.is_file(), reason="NVTT 3.2.5 is not installed")
def test_mask_required_shader_returns_allowlisted_patch(tmp_path: Path) -> None:
    result = run_request(_request(tmp_path, "spec_gloss", "spec_twiddle_tnt.sps"))
    patch = result["materials"][0]["parameter_patch"]
    assert patch == {
        "SpecularIntensityMult": [0.3],
        "SpecularFalloffMult": [200.0],
        "SpecularFresnel": [0.45],
        "specMapIntMask": [1.0, 0.0, 0.0, 0.0],
    }


@pytest.mark.skipif(not NVTT.is_file(), reason="NVTT 3.2.5 is not installed")
def test_cache_hit_refreshes_surface_patch_without_recompression(tmp_path: Path) -> None:
    request = _request(tmp_path, "metal_rough", "weapon_normal_spec_palette.sps")
    first = run_request(request)
    request["materials"][0]["surface"] = 0.73
    second = run_request(request)
    assert first["materials"][0]["parameter_patch"]["SpecularFresnel"] == [0.45]
    assert second["materials"][0]["cache_hit"] is True
    assert second["materials"][0]["parameter_patch"]["SpecularFresnel"] == [0.73]


def test_legacy_surface_preset_remains_supported(tmp_path: Path) -> None:
    request = _request(tmp_path, "metal_rough", "weapon_normal_spec_palette.sps")
    request["materials"][0].pop("surface")
    request["materials"][0]["fresnel_preset"] = "matte"
    result = run_request(request)
    assert result["materials"][0]["parameter_patch"]["SpecularFresnel"] == [0.95]


def test_unknown_shader_is_rejected_before_conversion(tmp_path: Path) -> None:
    with pytest.raises(RequestError, match="Unknown Sollumz shader"):
        run_request(_request(tmp_path, "metal_rough", "unknown.sps"))


@pytest.mark.skipif(not NVTT.is_file(), reason="NVTT is not installed")
def test_renamed_outputs_do_not_reuse_old_cached_filenames(tmp_path: Path) -> None:
    request = _request(tmp_path, "metal_rough", "normal_spec.sps")
    material = request["materials"][0]
    material["output_names"] = {role: f"chain_{suffix}.dds" for role,suffix in
                                [("diffuse","d"),("specular","s"),("normal","n")]}
    first = run_request(request)
    repeated = run_request(request)
    assert repeated["materials"][0]["cache_hit"]
    material["output_names"] = {role:name.replace("chain_","renamed_")
                                for role,name in material["output_names"].items()}
    renamed = run_request(request)
    assert not renamed["materials"][0]["cache_hit"]
    for old,new in zip(first["materials"][0]["files"],renamed["materials"][0]["files"]):
        assert new["name"].startswith("renamed_")
        assert Path(old["path"]).read_bytes()==Path(new["path"]).read_bytes()


def test_case_insensitive_output_collision_is_rejected(tmp_path: Path) -> None:
    request = _request(tmp_path, "metal_rough", "weapon_normal_spec_palette.sps")
    request["materials"][0]["output_names"] = {
        "diffuse": "Same.dds",
        "specular": "same.DDS",
        "normal": "normal.dds",
    }
    with pytest.raises(RequestError, match="Duplicate output"):
        run_request(request)


@pytest.mark.skipif(not NVTT.is_file(), reason="NVTT 3.2.5 is not installed")
def test_metal_rough_uses_independent_diffuse_spec_and_normal_grids(tmp_path: Path) -> None:
    request = _request(tmp_path, "metal_rough", "weapon_normal_spec_palette.sps")
    sources = request["materials"][0]["sources"]
    base = np.full((32, 48, 4), 180, np.uint8)
    base[..., 3] = 255
    normal = np.full((8, 12, 3), 128, np.uint8)
    normal[..., 0] = 255
    sources["base_color"] = _write(tmp_path / "large_base.png", base)
    sources["normal"] = _write(tmp_path / "small_normal.png", normal)

    result = run_request(request)
    records = {item["role"]: item for item in result["materials"][0]["files"]}
    assert (records["diffuse"]["width"], records["diffuse"]["height"]) == (48, 32)
    assert (records["specular"]["width"], records["specular"]["height"]) == (16, 16)
    assert (records["normal"]["width"], records["normal"]["height"]) == (12, 8)


def test_only_the_coupled_pair_must_match_dimensions(tmp_path: Path) -> None:
    request = _request(tmp_path, "metal_rough", "weapon_normal_spec_palette.sps")
    sources = request["materials"][0]["sources"]
    sources["roughness"] = _write(tmp_path / "wrong_rough.png", np.zeros((8, 8), np.uint8))
    with pytest.raises(RequestError, match="metallic and roughness"):
        run_request(request)


@pytest.mark.skipif(not NVTT.is_file(), reason="NVTT 3.2.5 is not installed")
def test_shader_without_normal_slot_does_not_require_or_emit_normal(tmp_path: Path) -> None:
    request = _request(tmp_path, "metal_rough", "spec.sps")
    request["materials"][0]["sources"].pop("normal")
    request["materials"][0]["outputs"] = ["diffuse", "specular"]

    result = run_request(request)
    roles = {item["role"] for item in result["materials"][0]["files"]}
    assert roles == {"diffuse", "specular"}


@pytest.mark.skipif(not NVTT.is_file(), reason="NVTT 3.2.5 is not installed")
@pytest.mark.parametrize(("alpha", "expected"), [(255, "DXT1"), (128, "DXT5")])
def test_diffuse_alpha_requires_both_shader_semantics_and_source_data(
    tmp_path: Path, alpha: int, expected: str
) -> None:
    request = _request(tmp_path, "spec_gloss", "normal_spec_alpha.sps")
    diffuse = np.full((16, 16, 4), 180, np.uint8)
    diffuse[..., 3] = alpha
    request["materials"][0]["sources"]["diffuse"] = _write(
        tmp_path / f"diffuse_{alpha}.png", diffuse
    )
    result = run_request(request)
    records = {item["role"]: item for item in result["materials"][0]["files"]}
    assert records["diffuse"]["fourcc"] == expected


@pytest.mark.skipif(not NVTT.is_file(), reason="NVTT 3.2.5 is not installed")
def test_masked_normal_is_emitted_as_dxt5(tmp_path: Path) -> None:
    request = _request(tmp_path, "metal_rough", "weapon_normal_spec_palette.sps")
    normal = np.full((16, 16, 4), 128, np.uint8)
    normal[..., 0] = 255
    normal[..., 3] = 64
    request["materials"][0]["sources"]["normal"] = _write(
        tmp_path / "masked_normal.png", normal
    )
    result = run_request(request)
    records = {item["role"]: item for item in result["materials"][0]["files"]}
    assert records["normal"]["fourcc"] == "DXT5"


@pytest.mark.skipif(not NVTT.is_file(), reason="NVTT 3.2.5 is not installed")
def test_direct_slot_uses_shader_channel_contract(tmp_path: Path) -> None:
    rgba = np.full((16, 16, 4), 127, np.uint8)
    rgba[..., 3] = 12
    source = _write(tmp_path / "hf.png", rgba)
    request = {
        "schema": "pbr2gta.convert.v1",
        "nvcompress_path": str(NVTT),
        "cache_dir": str(tmp_path / "cache"),
        "output_dir": str(tmp_path / "output"),
        "materials": [
            {
                "id": "direct",
                "name": "Direct",
                "shader": "grass_fur_mask.sps",
                "stem": "grass",
                "slots": [
                    {
                        "slot": "DiffuseHfSampler",
                        "source": source,
                        "output_name": "grass_hf.dds",
                    }
                ],
            }
        ],
    }
    result = run_request(request)
    record = result["materials"][0]["files"][0]
    assert record["slot"] == "DiffuseHfSampler"
    assert record["fourcc"] == "DXT1"


def test_deferred_special_spec_cannot_be_overwritten(tmp_path: Path) -> None:
    source = _write(tmp_path / "spec.png", np.zeros((8, 8, 4), np.uint8))
    request = {
        "schema": "pbr2gta.convert.v1",
        "nvcompress_path": str(NVTT),
        "cache_dir": str(tmp_path / "cache"),
        "output_dir": str(tmp_path / "output"),
        "materials": [
            {
                "shader": "ped.sps",
                "slots": [{"slot": "SpecSampler", "source": source}],
            }
        ],
    }
    with pytest.raises(RequestError, match="not a writable"):
        run_request(request)
