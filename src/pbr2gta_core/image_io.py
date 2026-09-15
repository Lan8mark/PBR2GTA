from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from numpy.typing import NDArray


def read_image(path: Path, flags: int = cv2.IMREAD_UNCHANGED) -> NDArray | None:
    """Decode an image without passing a Unicode Windows path to OpenCV."""
    try:
        encoded = np.fromfile(Path(path), dtype=np.uint8)
    except OSError:
        return None
    if encoded.size == 0:
        return None
    try:
        return cv2.imdecode(encoded, flags)
    except cv2.error:
        return None
