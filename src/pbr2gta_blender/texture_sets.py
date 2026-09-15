"""Filename matching shared with the web batch convention (dictionary v3).

Pure Python: no Blender dependency. The selected PNG identifies a set, never
the active material's name. Ambiguous roles are errors, not first-file wins.
"""
from pathlib import Path
import json
import unicodedata

DICTIONARY = json.loads(Path(__file__).with_name('material_naming.json').read_text(encoding='utf-8'))
SEPARATORS = '_.- '


def suffix_match(stem, aliases):
    for alias in sorted(aliases, key=len, reverse=True):
        if stem.casefold().endswith(alias.casefold()):
            start = len(stem) - len(alias)
            if start == 0 or stem[start - 1] in SEPARATORS:
                return stem[:start].rstrip(SEPARATORS)
    return None


def classify(path, workflow):
    path = Path(path)
    if path.suffix.casefold() != '.png':
        return None
    stem = unicodedata.normalize('NFC', path.stem)
    if suffix_match(stem, DICTIONARY['generated']) is not None:
        return None
    for role, aliases in DICTIONARY['auxiliary'].items():
        prefix = suffix_match(stem, aliases)
        if prefix is not None:
            return ((prefix or path.parent.name).casefold(), role) if role == 'normal' else None
    pipeline = {'metal_rough': 'pbr2gta', 'spec_gloss': 'spec2gta'}[workflow]
    for role, aliases in DICTIONARY['pipelines'][pipeline].items():
        prefix = suffix_match(stem, aliases)
        if prefix is not None:
            return ((prefix or path.parent.name).casefold(), role)
    return None


def detect_set(anchor, workflow):
    anchor = Path(anchor)
    if not anchor.is_file():
        raise ValueError('Select an existing PNG from the texture set.')
    identified = classify(anchor, workflow)
    if identified is None:
        raise ValueError('Filename not recognized. Select a Base Color, Metallic, Roughness, Diffuse, Specular, Gloss or Normal PNG.')
    roles = {}
    for path in sorted(anchor.parent.iterdir()):
        match = classify(path, workflow) if path.is_file() else None
        if match and match[0] == identified[0]:
            roles.setdefault(match[1], []).append(path)
    conflicts = [f"{role}: {', '.join(p.name for p in paths)}" for role, paths in roles.items() if len(paths) > 1]
    if conflicts:
        raise ValueError('Ambiguous texture set — ' + '; '.join(conflicts))
    required = ('base_color', 'metallic', 'roughness') if workflow == 'metal_rough' else ('diffuse', 'specular', 'gloss')
    missing = [role for role in required if role not in roles]
    if missing:
        raise ValueError('Incomplete texture set; missing: ' + ', '.join(missing))
    return {role: paths[0] for role, paths in roles.items()}
