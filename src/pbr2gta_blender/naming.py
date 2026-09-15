"""Deterministic DDS names, independent of selection and conversion order."""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict


def clean_token(value: str, limit: int = 80) -> str:
    value = re.sub(r"[^a-z0-9_.-]", "_", value.lower())
    value = re.sub(r"[_.-]{2,}", "_", value)
    return value[:limit].strip("._-")


def allocate_names(entries, reserved=()):
    """entries: (material UUID, material name, sampler, suffix).

    Return {(UUID, sampler): filename}; retained textures reserve their names.
    Collision suffixes expand deterministically, including adversarial prefixes.
    """
    records = {}
    for identifier, name, slot, suffix in entries:
        key = (identifier, slot)
        token = identifier.replace("-", "").lower()
        if not re.fullmatch(r"[a-z0-9]+", token):
            token = hashlib.sha256(identifier.encode()).hexdigest()
        records[key] = (clean_token(name) or f"material_{token[:8]}",
                        clean_token(suffix) or "texture", token)
    occupied = {str(name).casefold() for name in reserved}
    lengths = {key: 0 for key in records}
    for _ in range(128):
        groups = defaultdict(list)
        result = {}
        for key, (base, suffix, token) in records.items():
            length = lengths[key]
            # The extra hash separates distinct samplers that normalize alike.
            extended = token + hashlib.sha256(repr(key).encode()).hexdigest()
            discriminator = "_" + extended[:length] if length else ""
            filename = f"{base}{discriminator}_{suffix}.dds"
            result[key] = filename
            groups[filename.casefold()].append(key)
        conflicts = {key for name, keys in groups.items()
                     if name in occupied or len(keys) > 1 for key in keys}
        if not conflicts:
            return result
        for key in conflicts:
            lengths[key] = 8 if lengths[key] == 0 else lengths[key] + 1
    raise ValueError("Cannot allocate unique DDS names for these materials.")
