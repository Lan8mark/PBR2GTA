"""Decode the actual compressed pixels, not just DDS dimensions/flags."""
import struct
from pathlib import Path

import cv2
import numpy as np
import pytest

from pbr2gta_core.dds import NvttCompressor, NORMAL_PROFILE

NVTT = Path(r"C:\Program Files\NVIDIA Corporation\NVIDIA Texture Tools\nvcompress.exe")
pytestmark = pytest.mark.skipif(not NVTT.is_file(), reason="External NVTT required")


def _bc4(block):
    a, b = block[:2]
    table = [float(a), float(b)]
    if a > b:
        table += [((7-i)*a+i*b)/7 for i in range(1, 7)]
    else:
        table += [((5-i)*a+i*b)/5 for i in range(1, 5)] + [0., 255.]
    indices = int.from_bytes(block[2:8], "little")
    return np.array([table[(indices >> (3*i)) & 7] for i in range(16)]).reshape(4, 4)


def _bc3(block):
    c0, c1, indices = struct.unpack_from('<HHI', block, 8)
    def rgb(c):
        return np.array([(c >> 11)*255/31, ((c >> 5)&63)*255/63, (c&31)*255/31])
    a, b = rgb(c0), rgb(c1)
    table = [a, b, (2*a+b)/3, (a+2*b)/3]
    result = np.empty((4, 4, 4))
    result[..., :3] = np.array([table[(indices >> (2*i)) & 3] for i in range(16)]).reshape(4, 4, 3)
    result[..., 3] = _bc4(block[:8])
    return result


def decode_mips(path):
    data = path.read_bytes()
    h, w = struct.unpack_from('<II', data, 12)
    count = struct.unpack_from('<I', data, 28)[0]
    bc5 = data[84:88] == b'BC5U'
    assert bc5 or data[84:88] == b'DXT5'
    offset = 128
    for _ in range(count):
        image = np.empty((((h+3)//4)*4, ((w+3)//4)*4, 2 if bc5 else 4))
        for y in range(0, h, 4):
            for x in range(0, w, 4):
                block = data[offset:offset+16]
                image[y:y+4, x:x+4] = np.stack([_bc4(block[:8]), _bc4(block[8:])], axis=-1) if bc5 else _bc3(block)
                offset += 16
        yield image[:h, :w]
        w, h = max(1, w//2), max(1, h//2)
    assert offset == len(data)


@pytest.mark.parametrize('rgb', [(128,128,255), (204,128,230), (180,180,230)])
@pytest.mark.parametrize('alpha', [None, 64])
def test_constant_normal_direction_survives_every_mip(tmp_path, rgb, alpha):
    pixel = (*rgb[::-1], alpha) if alpha is not None else rgb[::-1]
    source, target = tmp_path/'normal.png', tmp_path/'normal.dds'
    assert cv2.imwrite(str(source), np.full((64,64,len(pixel)), pixel, np.uint8))
    NvttCompressor(NVTT).compress(source, target, NORMAL_PROFILE)
    levels = list(decode_mips(target))
    assert len(levels) == 5 and levels[-1].shape[:2] == (4,4)
    for image in levels:
        expected = (*rgb, alpha) if alpha is not None else rgb[:2]
        assert np.max(np.abs(image - expected)) <= (5 if alpha is not None else 1)


def test_opposite_tilts_average_to_neutral_without_gamma(tmp_path):
    source, target = tmp_path/'normal.png', tmp_path/'normal.dds'
    image = np.empty((64,64,3), np.uint8)
    image[:, ::2] = (230,128,51)
    image[:, 1::2] = (230,128,204)
    assert cv2.imwrite(str(source), image)
    NvttCompressor(NVTT).compress(source, target, NORMAL_PROFILE)
    levels = list(decode_mips(target))
    # Exclude clamp-edge influence at large mips; symmetry must survive.
    for mip in levels[1:]:
        assert abs(float(mip[...,0].mean()) - 127.5) < 2
        assert np.max(np.abs(mip[...,1] - 128)) <= 1
