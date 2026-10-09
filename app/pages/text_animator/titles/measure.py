"""Measuring for the composer with real fonts: Qt's shaping of the face, kerned, as Text+
lays it out (canvas_math - Qt's widths came within 2 px of Resolve's), with the title
fonts Buddy ships loaded first. measure() and metrics() are what composer.compose
takes; the renderer draws with the same Qt fonts, so what's measured is what's drawn.

A face is (family, style), as Text+'s Font and Style inputs name it. The bundled fonts'
names are read from their own name tables - typographic family and style (IDs 16/17),
else the legacy ones (1/2) - never guessed from the file name.
"""

import os
import struct
from functools import lru_cache

from PySide6.QtGui import QFontDatabase, QFontMetricsF

from .. import canvas_math as cm

FONTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))), "assets", "fonts", "titles")

_bundled = {}       # (family, style) -> font file
_font_ids = []      # what Qt registered them as
_loaded = False


def font_names(path):
    """(family, style) from a font file's name table, or None."""
    try:
        with open(path, "rb") as f:
            table = cm._sfnt_tables(f.read())["name"]
        _fmt, count, strings = struct.unpack(">HHH", table[:6])
        found = {}
        for i in range(count):
            platform, _enc, _lang, nid, length, offset = struct.unpack(">HHHHHH", table[6 + 12 * i:18 + 12 * i])
            if platform == 3 and nid in (1, 2, 16, 17) and nid not in found:
                found[nid] = table[strings + offset:strings + offset + length].decode("utf-16-be")
    except (OSError, KeyError, struct.error, UnicodeDecodeError):
        return None
    family, style = found.get(16) or found.get(1), found.get(17) or found.get(2)
    return (family, style) if family and style else None


def bundled_files():
    """Every bundled title font: {(family, style): file}."""
    files = {}
    for folder, _dirs, names in os.walk(FONTS_DIR):
        for name in sorted(names):
            if name.lower().endswith((".ttf", ".otf")):
                path = os.path.join(folder, name)
                face = font_names(path)
                if face:
                    files[face] = path
    return files


def load_bundled():
    """Register the bundled title fonts with Qt, once (needs a QGuiApplication)."""
    global _loaded
    if _loaded:
        return
    _bundled.update(bundled_files())
    for path in _bundled.values():
        _font_ids.append(QFontDatabase.addApplicationFont(path))
    _loaded = True


def unload_bundled():
    """Take the bundled fonts out of Qt again - for tests: where a font is missing (Qt's
    offscreen platform has none of the system's), Qt falls back to a registered one, so
    loading these would change what other tests measure."""
    global _loaded
    for font_id in _font_ids:
        QFontDatabase.removeApplicationFont(font_id)
    _font_ids.clear()
    _bundled.clear()
    for cached in (_advance, _metrics, _ink):
        cached.cache_clear()
    _loaded = False


def font_file(font):
    """The file a face is drawn from: the one Resolve names, else the bundled copy."""
    return cm.font_file(*font) or _bundled.get(tuple(font))


@lru_cache(maxsize=512)
def _advance(text, family, style):
    return QFontMetricsF(cm._measure_font(family, style)).horizontalAdvance(text) / cm._MEASURE_PX


def measure(text, font, size):
    """The advance width of text in font at a letter size of size px."""
    load_bundled()
    return _advance(text, font[0], font[1]) * size


@lru_cache(maxsize=128)
def _metrics(family, style):
    path = font_file((family, style))
    vertical = cm._file_metrics(path) if path else None
    ascent, descent, _gap = vertical or cm.font_vertical_metrics(family, style)
    cap = QFontMetricsF(cm._measure_font(family, style)).capHeight() / cm._MEASURE_PX
    return {"asc": ascent, "desc": descent, "cap": cap or 0.7 * ascent}


@lru_cache(maxsize=1024)
def _ink(text, family, style):
    return cm.glyph_extent(family, text, style)


def ink(text, font):
    """(how far text's ink reaches above its baseline, how far below), in ems."""
    load_bundled()
    return _ink(text, font[0], font[1])


def metrics(font):
    """{"asc", "desc", "cap"} as shares of the letter size - ascent and descent from the
    font file's hhea table, the figures Text+ sizes text by."""
    load_bundled()
    return dict(_metrics(font[0], font[1]))
