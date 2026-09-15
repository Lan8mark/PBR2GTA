"""Bundled translations only: the installed add-on never calls a translator."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
import json
from pathlib import Path

_language = ContextVar("shader_guide_language", default="RU")


@lru_cache(maxsize=1)
def english_catalog():
    return json.loads(Path(__file__).with_name("shader_guide_en.json").read_text(encoding="utf-8"))


def translate(text):
    if _language.get() != "EN" or not any(0x400 <= ord(c) <= 0x4ff for c in text):
        return text
    return english_catalog().get(text, "Description unavailable for this shader variant.")


@contextmanager
def language_scope(language):
    token = _language.set(language)
    try:
        yield
    finally:
        _language.reset(token)


class LocalizedLayout:
    def __init__(self, layout):
        self._layout = layout

    def __setattr__(self, name, value):
        if name == "_layout":
            object.__setattr__(self, name, value)
        else:
            setattr(self._layout, name, value)

    def __getattr__(self, name):
        attribute = getattr(self._layout, name)
        if name in {"row", "column", "box", "split", "grid_flow"}:
            return lambda *args, **kwargs: LocalizedLayout(attribute(*args, **kwargs))
        if name == "label":
            def label(*args, **kwargs):
                if "text" in kwargs:
                    kwargs["text"] = translate(kwargs["text"])
                return attribute(*args, **kwargs)
            return label
        return attribute
