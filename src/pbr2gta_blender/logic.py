from __future__ import annotations

import re
from pathlib import Path

_KNOWN_SUFFIXES = (
    "_base_color", "_basecolor", "_diffuse", "_albedo", "_color",
    "_metallic", "_metalness", "_roughness", "_specular", "_glossiness",
    "_gloss", "_normal", "_normals", "_nrm",
)


def derive_stem(path: str, fallback: str = "material") -> str:
    stem = Path(path).stem
    lowered = stem.casefold()
    for suffix in _KNOWN_SUFFIXES:
        if lowered.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", stem).strip("._-")
    return stem or fallback


def normalize_output_name(value: str, fallback: str) -> str:
    candidate = (value or fallback).strip()
    if not candidate.casefold().endswith(".dds"):
        candidate += ".dds"
    if Path(candidate).name != candidate or candidate in {".dds", "..dds"}:
        raise ValueError("Output names must be plain DDS filenames.")
    return candidate


def assert_unique_names(names: list[str]) -> None:
    seen: set[str] = set()
    for name in names:
        folded = name.casefold()
        if folded in seen:
            raise ValueError(f"Duplicate output texture name: {name}")
        seen.add(folded)

