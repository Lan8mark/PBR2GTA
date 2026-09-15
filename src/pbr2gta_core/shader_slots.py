from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

MANIFEST_PATH = Path(__file__).with_name("runtime_shader_slots.json")


def normalize_selector(value: str) -> str:
    selector = value.replace("\\", "/").rsplit("/", 1)[-1].casefold()
    return selector if selector.endswith(".sps") else f"{selector}.sps"


@lru_cache(maxsize=1)
def manifest() -> dict[str, Any]:
    document = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if document.get("schema") != "pbr2gta.runtime-shader-slots.v1":
        raise RuntimeError("Unsupported PBR2GTA runtime shader manifest.")
    return document


def shader_definition(selector: str) -> dict[str, Any] | None:
    return manifest()["shaders"].get(normalize_selector(selector))


def slot_definition(selector: str, slot_name: str) -> dict[str, Any] | None:
    shader = shader_definition(selector)
    if shader is None:
        return None
    folded = slot_name.casefold()
    return next((slot for slot in shader["slots"] if slot["name"].casefold() == folded), None)


def transport_profile(profile_id: str) -> dict[str, Any]:
    try:
        return manifest()["profiles"][profile_id]
    except KeyError as exc:
        raise RuntimeError(f"Unknown shader transport profile: {profile_id}") from exc


def diffuse_uses_alpha(selector: str, slot_name: str = "DiffuseSampler") -> bool:
    slot = slot_definition(selector, slot_name)
    return bool(slot and slot["transport_profile_id"] == "DIFFUSE_RGBA_AUTO")


def known_selector(selector: str) -> bool:
    return shader_definition(selector) is not None


__all__ = [
    "diffuse_uses_alpha",
    "known_selector",
    "manifest",
    "normalize_selector",
    "shader_definition",
    "slot_definition",
    "transport_profile",
]
