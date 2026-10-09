"""The Font Styling tab's font picker: every face Resolve can draw, as (family, style).

The list is Resolve's own - Fusion's FontManager.GetFontList(), {family: {style: file}} -
once Buddy reaches Resolve (resolve_faces). Each entry is labelled "family style" ("Arial
Regular", "Sitka Text Bold") and sent to a Text+ as exactly that Font and Style, so no
guessing which words of a name are the style. Measured on Studio 21.1 on Windows
(2026-10-05): 421 faces, against 186 in the registry list below - which misses every .ttc
and variable font and Adobe Fonts.

Until Resolve answers, the picker falls back to the computer's own fonts: on macOS every
face with its family and style straight from the OS (_mac_font_face_names); on Windows the
registry's fonts below, the style guessed from the name (split_font_name_and_style).

The Windows fallback - fonts genuinely backed by a .ttf/.otf file on disk:

A font Qt lists may not exist in DaVinci Resolve/Fusion at all (it renders as a black "Font
Not Found" clip). DaVinci Resolve pulls its Font list purely from installed system fonts (it
has no separate bundled font catalog), but Windows can expose a font FAMILY NAME with no real
font file behind it at all - e.g. a "FontSubstitutes" registry alias, or a name registered by
another application without installing an actual .ttf/.otf - which Qt's font enumeration (and
so a plain QFontComboBox) can still list, but Resolve/Fusion (scanning real font files) never
sees. Filtering to the registry's own record of which display name maps to which ACTUAL FILE,
keeping only .ttf/.otf, matches what Resolve/Fusion can actually use.
"""
import re
import sys
from typing import Dict, Iterable, List, Optional, Tuple

# This app's own default font ("Arial Regular"). Centralized here so every place in this
# codebase that falls back to a
# hardcoded default font (subtitle conversion's fallback when no font was specified, the Font
# Styling tab's initial selection, the "Reset to Default" button, the Live Placement Preview's
# own placeholder before real data syncs in) stays in agreement instead of drifting.
DEFAULT_FONT_NAME = "Arial"

# Explicit default Style paired with DEFAULT_FONT_NAME above, so applying "Arial" lands as
# "Arial Regular" rather than "Arial Light"/similar. This app's Media Pool Text+ template can
# carry a residual Style value from whatever was last set on it (e.g. "Light") -
# FusionAnimationEngine.apply_text_style() has an optional Style-PRESERVING mode (so
# switching fonts on a clip a user deliberately Bold/Italic'd directly in Fusion isn't reset),
# but that mode has no way to distinguish "the user chose this on purpose" from "this is just
# whatever the template happened to have" - so ui.py's own Font Styling tab never relies on
# preservation at all. Every font in this app's own picker already encodes its own
# weight/slant directly in its name (e.g. "Arial Bold", "Arial Regular" - see
# build_font_display_map()), so Style is always this exact constant, forced explicitly,
# regardless of what a clip's Style already happens to be - see ui.py's
# _apply_font_style_to_tool().
DEFAULT_FONT_STYLE = "Regular"

# Arial has no Japanese, Chinese, Korean, Thai or Devanagari characters, and
# Text+ doesn't borrow them from another font - such a title shows nothing
# (or boxes). Subtitles in those scripts get the first of these fonts that
# Fusion has (its FontManager - which, unlike the picker's list, includes
# Windows' .ttc fonts such as Yu Gothic), Windows names first, then macOS.
SCRIPT_FONTS = {
    "ja": ("Yu Gothic", "Meiryo", "Noto Sans JP", "MS Gothic", "Hiragino Sans", "Hiragino Kaku Gothic ProN"),
    "zh": ("Microsoft YaHei", "Noto Sans SC", "SimHei", "PingFang SC"),
    "ko": ("Malgun Gothic", "Noto Sans KR", "Apple SD Gothic Neo"),
    "th": ("Leelawadee UI", "Noto Sans Thai", "Tahoma", "Thonburi"),
    "hi": ("Nirmala UI", "Noto Sans Devanagari", "Kohinoor Devanagari"),
}
_KANA = re.compile(r"[\u3040-\u30ff\u31f0-\u31ff]")
_HANGUL = re.compile(r"[\u1100-\u11ff\u3130-\u318f\uac00-\ud7af]")
_HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_THAI = re.compile(r"[\u0e00-\u0e7f]")
_DEVANAGARI = re.compile(r"[\u0900-\u097f]")


def script_of(texts: Iterable[str]) -> str:
    """"ja", "zh", "ko", "th" or "hi" when the texts need a font Arial isn't;
    "" otherwise. Taken over all of them at once: kana anywhere makes a
    kanji-only line Japanese too."""
    joined = "\n".join(t for t in texts if t)
    if _KANA.search(joined):
        return "ja"
    if _HANGUL.search(joined):
        return "ko"
    if _HAN.search(joined):
        return "zh"
    if _THAI.search(joined):
        return "th"
    if _DEVANAGARI.search(joined):
        return "hi"
    return ""


def font_for_texts(texts: Iterable[str], available=None) -> str:
    """A font that has the texts' characters: "" when the default will do.
    available: the font families Fusion has (None if unknown - then the
    first candidate)."""
    candidates = SCRIPT_FONTS.get(script_of(texts), ())
    if not candidates:
        return ""
    if available is None:
        return candidates[0]
    return next((name for name in candidates if name in available), "")

# Windows registers each installed font face as one value under this key: value name is the
# display name shown in Windows' own Fonts control panel (e.g. "Arial Bold (TrueType)"), value
# data is the backing file (a bare filename for fonts under %WINDIR%\Fonts, or a full path for
# a font installed for the current user only, e.g. via Windows 10+'s per-user font install).
_MACHINE_FONTS_KEY = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"
_USER_FONTS_KEY = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"

# Suffixes Windows appends to the display name to indicate the font technology - stripped so
# the name matches what's actually typed into Fusion's "Font" input (a plain family/face
# string, with the style in a separate "Style" input - see
# FusionAnimationEngine.apply_text_style()'s docstring).
_DISPLAY_NAME_SUFFIXES = (
    " (TrueType)",
    " (OpenType)",
    " (TrueType, OpenType)",
    " (OpenType, TrueType)",
)


def _clean_font_display_name(display_name: str) -> str:
    """Strips a known Windows font-technology suffix off a registry display name. Pure/
    testable independently of the registry itself - see _MACHINE_FONTS_KEY's docstring for
    why this stripping is needed."""
    for suffix in _DISPLAY_NAME_SUFFIXES:
        if display_name.endswith(suffix):
            return display_name[: -len(suffix)]
    return display_name


# Weight/slant words: a font name ending in them has them as its style
# (split_font_name_and_style - the fallback picker's guess; Resolve's own list needs none),
# and the live preview draws a name's Bold/Italic (parse_bold_italic_from_name). Whole words,
# case-insensitive: a substring check would flag "Blackadder" or "Boldonse". A guess, not a
# guarantee - "Arial Black" could be its own family or Arial's Black style.
_STYLE_KEYWORDS = {
    "thin", "extralight", "ultralight", "light", "regular", "normal", "book",
    "medium", "semibold", "demibold", "bold", "extrabold", "ultrabold", "heavy",
    "black", "italic", "oblique",
}

_WORD_PATTERN = re.compile(r"[A-Za-z]+")


def parse_bold_italic_from_name(name: str) -> Tuple[bool, bool]:
    """Detects whether a font's own name already spells out Bold and/or Italic, as whole
    words - used by the Font Styling tab's Live Preview (style_preview.py) to apply
    QFont.setBold()/setItalic() explicitly. Needed because Qt's own font matching often can't
    resolve a compound name like "Arial Bold" as a distinct family the way Windows/Fusion's
    own font list does (Qt typically only indexes the base "Arial" family, with Bold/Italic as
    separate style flags within it) - without this, the preview silently falls back to a
    plain/regular rendering regardless of which named variant was actually selected."""
    words = {word.lower() for word in _WORD_PATTERN.findall(name)}
    return ("bold" in words, "italic" in words)


def _is_real_font_file(filename: str) -> bool:
    """True only for a genuine outline font file - .ttf/.otf. Deliberately excludes .ttc
    (TrueType Collection, bundles several faces in one file) and .fon (legacy bitmap
    fonts)."""
    lowered = filename.lower()
    return lowered.endswith(".ttf") or lowered.endswith(".otf")


# macOS: {font name as listed: (Fusion Font, Fusion Style)} for every face listed by
# _mac_font_face_names(). There the family and style come straight from the OS, so
# split_font_name_and_style() uses them as-is rather than guessing the split from the name -
# a guess gets "Helvetica Neue Condensed Bold" wrong, and sends "Futura" (whose faces are
# Medium, Bold, ...) as a "Regular" it doesn't have.
_KNOWN_FACES: Dict[str, Tuple[str, str]] = {}


def _mac_font_face_names() -> List[str]:
    """Every installed face, as "Family Style" - or just "Family" for its Regular face, the
    way Windows lists them. Needs a QApplication (QFontDatabase)."""
    from PySide6.QtGui import QFontDatabase

    _KNOWN_FACES.clear()
    for family in QFontDatabase.families():
        if family.startswith("."):   # hidden system UI fonts, e.g. ".AppleSystemUIFont"
            continue
        for style in QFontDatabase.styles(family):
            name = family if style == "Regular" else f"{family} {style}"
            _KNOWN_FACES[name] = (family, style)
    return sorted(_KNOWN_FACES, key=str.casefold)


def get_installed_font_family_names() -> List[str]:
    """Returns installed font names, sorted case-insensitively. On Windows, the ones actually
    backed by a .ttf/.otf file, from the registry; on macOS, every face (see
    _mac_font_face_names()). Elsewhere an empty list, so callers fall back to a plain OS-level
    font list (see page.py's font picker, which falls back to QFontDatabase.families() when
    this returns empty)."""
    if sys.platform == "darwin":
        return _mac_font_face_names()
    if sys.platform != "win32":
        return []

    import winreg

    names = set()
    for hive, subkey in (
        (winreg.HKEY_LOCAL_MACHINE, _MACHINE_FONTS_KEY),
        (winreg.HKEY_CURRENT_USER, _USER_FONTS_KEY),
    ):
        try:
            key = winreg.OpenKey(hive, subkey)
        except OSError:
            continue
        try:
            index = 0
            while True:
                try:
                    display_name, filename, _value_type = winreg.EnumValue(key, index)
                except OSError:
                    break
                index += 1
                if not isinstance(display_name, str) or not isinstance(filename, str):
                    continue
                if not _is_real_font_file(filename):
                    continue
                names.add(_clean_font_display_name(display_name).strip())
        finally:
            winreg.CloseKey(key)

    return sorted(names, key=str.casefold)


def face_label(family: str, style: str) -> str:
    """A face as the picker shows it: "Arial Regular", "Sitka Text Bold"."""
    return f"{family} {style or DEFAULT_FONT_STYLE}"


def resolve_faces(font_list) -> Dict[str, Tuple[str, str]]:
    """{label: (family, style)} for every face in Fusion's FontManager.GetFontList()
    ({family: {style: file}}) - {} if it isn't one."""
    faces: Dict[str, Tuple[str, str]] = {}
    if not isinstance(font_list, dict):
        return faces
    for family, styles in font_list.items():
        if not isinstance(styles, dict):
            continue
        for style in styles:
            faces.setdefault(face_label(str(family), str(style)), (str(family), str(style)))
    return faces


def guessed_faces(names: Iterable[str]) -> Dict[str, Tuple[str, str]]:
    """{label: (family, style)} for font names with the style written in (the registry's
    "Arial Bold") - the style guessed from the name's last words."""
    faces: Dict[str, Tuple[str, str]] = {}
    for name in names:
        family, style = split_font_name_and_style(name)
        faces.setdefault(face_label(family, style), (family, style))
    return faces


def find_face(faces: Dict[str, Tuple[str, str]], name: str) -> Optional[Tuple[str, str]]:
    """The face a picker label or an older Buddy's saved font name ("Segoe UI Semilight",
    "Franklin Gothic Medium") stands for in `faces`, or None."""
    name = (name or "").strip()
    found = faces.get(name) or faces.get(face_label(name, DEFAULT_FONT_STYLE))
    if found is None:               # the registry's "SemiLight", Resolve's "Semilight"
        wanted = {name.casefold(), face_label(name, DEFAULT_FONT_STYLE).casefold()}
        found = next((face for label, face in faces.items() if label.casefold() in wanted), None)
    return found


def qfont_for(name: str):
    """A QFont for a name from the Font picker. On macOS that's the face's exact family and
    style (Qt can't resolve "Futura Medium" as a family name, and would draw a fallback);
    elsewhere just QFont(name), bold/italic being set by the caller (see
    parse_bold_italic_from_name()). Once a style name is set, Qt matches on it rather than on
    those bold/italic flags."""
    from PySide6.QtGui import QFont

    known = _KNOWN_FACES.get(name)
    if not known:
        return QFont(name)
    font = QFont(known[0])
    font.setStyleName(known[1])
    return font


def regular_style_for(family: str) -> str:
    """DEFAULT_FONT_STYLE ("Regular") unless the family has no style by that name - then its
    upright style nearest normal weight, e.g. "W3" for macOS's Hiragino Sans (W0-W9 only),
    which Fusion would otherwise show as "Font Not Found". Needs a QApplication."""
    from PySide6.QtGui import QFont, QFontDatabase

    styles = QFontDatabase.styles(family)
    if not styles or DEFAULT_FONT_STYLE in styles:
        return DEFAULT_FONT_STYLE
    upright = [st for st in styles if not QFontDatabase.italic(family, st)] or styles
    normal = QFont.Weight.Normal.value
    return min(upright, key=lambda st: abs(QFontDatabase.weight(family, st) - normal))


def split_font_name_and_style(full_name: str) -> Tuple[str, str]:
    """(family, style) for a name with the style written in: a listed macOS face's own
    (_KNOWN_FACES), else a guess - its trailing weight/slant words (_STYLE_KEYWORDS) are the
    style, else "Regular"."""
    if full_name in _KNOWN_FACES:
        return _KNOWN_FACES[full_name]
    words = full_name.split()
    style_words = []
    while words and words[-1].lower() in _STYLE_KEYWORDS:
        style_words.insert(0, words.pop())
    if not words:
        return full_name, "Regular"
    family = " ".join(words)
    style = " ".join(style_words) if style_words else "Regular"
    return family, style
