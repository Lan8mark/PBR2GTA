from pbr2gta_blender.guide_localization import (
    LocalizedLayout, english_catalog, language_scope, translate,
)


def test_language_scope_restores_russian_and_keeps_shader_identifiers():
    text = "Не используйте цвет.A как opacity: shipped branch заменяет смешивание alpha на Spec.G²"
    assert translate(text) == text
    with language_scope("EN"):
        assert translate(text).startswith("Do not use color.A as opacity")
        assert "Spec.G²" in translate(text)
        assert translate("DiffuseSampler") == "DiffuseSampler"
        with language_scope("RU"):
            assert translate(text) == text
    assert translate(text) == text


def test_bundled_english_catalog_has_no_russian_or_empty_descriptions():
    catalog = english_catalog()
    assert len(catalog) >= 1714
    for source, english in catalog.items():
        assert source and english.strip()
        assert not any(0x400 <= ord(c) <= 0x4ff for c in english), source


def test_layout_translates_nested_labels_and_forwards_settings():
    class Layout:
        def row(self):
            return self

        def label(self, text):
            self.text = text

    native = Layout()
    layout = LocalizedLayout(native)
    with language_scope("EN"):
        row = layout.row()
        row.label(text="ВАЖНО")
        row.enabled = False
    assert native.text == "IMPORTANT"
    assert native.enabled is False


def test_technical_terms_and_numeric_information_are_preserved():
    import re
    from collections import Counter
    catalog = english_catalog()
    assert catalog["Блик  ·  SpecSampler"] == "Specular · SpecSampler"
    assert catalog["ВЕРТЕКСНЫЙ ЦВЕТ"] == "VERTEX COLOR"
    assert catalog["Цвет 1 · Colour0"] == "Color 1 · Colour0"
    assert "cutout" in catalog["Прозрачность. В вырезание задаёт вырезание"]
    numbers = r"(?<![A-Za-z_])\d+(?:\.\d+)?"
    for source, english in catalog.items():
        assert not Counter(re.findall(numbers, source)) - Counter(re.findall(numbers, english)), source
        assert not re.search(r"\b(Blizzard|foreheads?|Micrometal|spectre|binit|Blik)\b", english), source
