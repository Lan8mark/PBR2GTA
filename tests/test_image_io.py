from pathlib import Path

import cv2
import numpy as np

from pbr2gta_core.image_io import read_image


def test_read_image_supports_unicode_windows_paths(tmp_path: Path) -> None:
    directory = tmp_path / "Рабочий стол"
    directory.mkdir()
    path = directory / "текстура.png"
    expected = np.full((7, 9, 4), (10, 20, 30, 255), dtype=np.uint8)
    encoded_ok, encoded = cv2.imencode(".png", expected)
    assert encoded_ok
    encoded.tofile(path)

    actual = read_image(path)

    assert actual is not None
    assert np.array_equal(actual, expected)


def test_read_image_rejects_invalid_payload(tmp_path: Path) -> None:
    path = tmp_path / "broken.png"
    path.write_bytes(b"not a png")
    assert read_image(path) is None
