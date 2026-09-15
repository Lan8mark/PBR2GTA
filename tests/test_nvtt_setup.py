from pbr2gta_blender import nvtt_setup


def test_discovery_prefers_custom_path_and_finds_install_after_missing_path(tmp_path, monkeypatch):
    custom = tmp_path / "custom" / "nvcompress.exe"
    installed = tmp_path / "NVIDIA Corporation/NVIDIA Texture Tools/nvcompress.exe"
    for path in (custom, installed):
        path.parent.mkdir(parents=True)
        path.write_bytes(b"test")
    monkeypatch.setenv("ProgramW6432", str(tmp_path))
    monkeypatch.setattr(nvtt_setup.shutil, "which", lambda _: None)
    assert nvtt_setup.find_nvtt(str(custom)) == custom
    assert nvtt_setup.find_nvtt(str(tmp_path / "missing.exe")) == installed


def test_discovery_rejects_wrong_executable(tmp_path, monkeypatch):
    wrong = tmp_path / "installer.exe"
    wrong.write_bytes(b"test")
    monkeypatch.setattr(nvtt_setup.Path, "is_file", lambda self: self == wrong)
    monkeypatch.setattr(nvtt_setup.shutil, "which", lambda _: None)
    assert nvtt_setup.find_nvtt(str(wrong)) is None
