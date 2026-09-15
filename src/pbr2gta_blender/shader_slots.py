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


def transport_profile(profile_id: str) -> dict[str, Any]:
    try:
        return manifest()["profiles"][profile_id]
    except KeyError as exc:
        raise RuntimeError(f"Unknown shader transport profile: {profile_id}") from exc


__all__ = ["manifest", "normalize_selector", "shader_definition", "transport_profile"]
