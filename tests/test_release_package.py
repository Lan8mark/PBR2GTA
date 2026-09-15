import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("release_package", Path(__file__).parents[1] / "scripts/release_package.py")
packaging = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packaging)


def licensed_stage(tmp_path):
    for name in ("python/LICENSE.txt", "numpy/LICENSE.txt", "opencv-python-headless/LICENSE.txt",
                 "opencv-python-headless/LICENSE-3RD-PARTY.txt"):
        path = tmp_path / "third_party_licenses" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("test notice\n" * 20)
    return tmp_path


def test_missing_notice_stops_release(tmp_path):
    stage = licensed_stage(tmp_path)
    (stage / "third_party_licenses/python/LICENSE.txt").unlink()
    with pytest.raises(ValueError, match="Missing release license: python"):
        packaging.validate(stage)


def test_private_provenance_stops_release_but_upstream_examples_do_not(tmp_path):
    stage = licensed_stage(tmp_path)
    third_party = stage / "core/_internal/example.txt"
    third_party.parent.mkdir(parents=True)
    third_party.write_text(r"C:\Users\example\upstream")
    guide = stage / "shader_guide_data.json"
    guide.write_text('{"locator":"source://Shaders.xml"}')
    packaging.validate(stage)
    guide.write_text(r'{"locator":"C:\\Users\\private\\Shaders.xml"}')
    with pytest.raises(ValueError, match="Local machine path"):
        packaging.validate(stage)


def test_development_files_cannot_enter_release(tmp_path):
    stage = licensed_stage(tmp_path)
    report = stage / "reports/debug.json"
    report.parent.mkdir()
    report.write_text('{}')
    with pytest.raises(ValueError, match="Development file"):
        packaging.validate(stage)
