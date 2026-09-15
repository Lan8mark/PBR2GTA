from __future__ import annotations

from pathlib import Path

import bpy.utils.previews

_collection = None
_EXPECTED_SIZES = {
    "pbr2gta": (64, 64),
    "mirror": (256, 256),
    "matte": (256, 256),
}


def register() -> None:
    global _collection
    if _collection is not None:
        return
    assets = Path(__file__).resolve().parent / "assets"
    _collection = bpy.utils.previews.new()
    _collection.load("pbr2gta", str(assets / "branding" / "pbr2gta.png"), "IMAGE")
    for name in ("mirror", "matte"):
        _collection.load(name, str(assets / "fresnel" / f"{name}.png"), "IMAGE")


def icon(name: str) -> int:
    if _collection is None or name not in _collection:
        return 0
    return _collection[name].icon_id


def is_loaded(name: str) -> bool:
    return (
        _collection is not None
        and name in _collection
        and tuple(_collection[name].image_size) == _EXPECTED_SIZES.get(name)
    )


def unregister() -> None:
    global _collection
    if _collection is not None:
        bpy.utils.previews.remove(_collection)
        _collection = None
