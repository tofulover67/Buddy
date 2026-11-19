#!/usr/bin/env python3
"""
Buddy's languages - one choice, in Settings (the Language dropdown at the
foot of the window), for the whole app.

How text gets translated:

  Web pages (every screen)  Python and the page scripts write English, as
                            they always have. buddy.js receives this
                            language's strings (the "i18n" event, sent by
                            core/web_page.py to every view) and translates
                            the page's text, tooltips and placeholders as
                            they're drawn - so a page needs no changes to
                            be translated. What people type or name
                            themselves (fields, palettes, clips, chat) is
                            never touched: inputs are skipped, and a view
                            marks any other user text translate="no".
  Qt (window titles, tray,  tr(text), from here.
  file pickers, the busy
  overlay)

The strings: core/translations/*.json (one file per part of the app, each
English string -> its text in each language), read into TRANSLATIONS at
import. An English string
may hold {placeholders} for values that change - "Deleted '{name}'"
matches "Deleted 'Sunset'" and keeps the name as it is; a translation
uses the same names, in whatever order its language needs. A name that
starts with t_ marks a value that is itself one of Buddy's words, not the
user's - "Grab stills at the {t_color} markers" - and that value is
translated too; never use it for anything a person named. Keys are
matched with runs of whitespace as one space, so HTML's line breaks don't
matter. A string with no translation stays English.

No Qt needed to translate (translate(), web_strings()) - only the
manager, whose language_changed signal tells open windows to redraw.
"""

import json
import os
import re
import sys

from PySide6.QtCore import QObject, Signal

# Keys: what settings files store. A key that's renamed keeps its old name
# in RENAMED_LANGUAGES, so a saved setting still means the same language.
LANGUAGES = [
    "English",
    "日本語",
    "Español",
    "Deutsch",
    "Français",
    "한국어",
    "中文",
    "العربية",
    "Tiếng Việt",
]
DEFAULT_LANGUAGE = "English"

# Old keys -> the current ones. Korean used to be stored as "한국인"
# ("Korean person") - on Windows until 1.1.3, and Color Palette's own
# language setting, adopted once app-wide, may still hold it; the language
# is 한국어.
RENAMED_LANGUAGES = {"한국인": "한국어"}

# What the dropdown shows where it isn't the key: each language in its
# own name (every key is that now).
LANGUAGE_LABELS = {}

# For the pages' <html lang>, which picks the right CJK glyphs.
LANGUAGE_CODES = {
    "English": "en",
    "日本語": "ja",
    "Español": "es",
    "Deutsch": "de",
    "Français": "fr",
    "한국어": "ko",
    "中文": "zh-Hans",
    "العربية": "ar",
    "Tiếng Việt": "vi",
}

TRANSLATIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "translations")

# Every string is in core/translations/*.json - none here, so a search of
# the code for a piece of text finds the code. Filled in below.
TRANSLATIONS = {}
BASE_FILE = "_base.json"


def _merge_translation_files():
    """core/translations/*.json into TRANSLATIONS, _base.json first. Where
    two files hold the same string the first one read wins, and a later one
    fills in only the languages missing. A file that can't be read is
    skipped - its text stays English - rather than stopping Buddy from
    starting (tests/test_i18n.py reads them all)."""
    if not os.path.isdir(TRANSLATIONS_DIR):
        return
    for name in sorted(os.listdir(TRANSLATIONS_DIR), key=lambda n: (n != BASE_FILE, n)):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(TRANSLATIONS_DIR, name), encoding="utf-8") as fh:
                entries = json.load(fh)
        except (OSError, ValueError) as exc:
            print(f"[i18n] skipped {name}: {exc}", file=sys.stderr)
            continue
        for english, by_language in entries.items():
            if english.startswith("_"):   # "_comment" and the like
                continue
            known = TRANSLATIONS.setdefault(english, {})
            for language, text in by_language.items():
                known.setdefault(language, text)


_merge_translation_files()

# ------------------------------------------------------------- matching --
# buddy.js does the same matching for the pages (translateText there) -
# change one, change both.

_PLACEHOLDER = re.compile(r"\{(\w+)\}")
_ELLIPSIS = "…"


def normalize(text):
    """A key as matched: runs of whitespace as one space, trimmed."""
    return " ".join(str(text).split())


def is_template(key):
    """True for a key with {placeholders} and enough words of its own to
    be recognised - "{count}%" alone would match any number."""
    if not _PLACEHOLDER.search(key):
        return False
    return len(re.findall(r"[^\W\d_]", _PLACEHOLDER.sub("", key))) >= 2


def _compile(key):
    parts, names, pos = [], [], 0
    for m in _PLACEHOLDER.finditer(key):
        parts.append(re.escape(key[pos:m.start()]))
        parts.append("(.+?)")
        names.append(m.group(1))
        pos = m.end()
    parts.append(re.escape(key[pos:]))
    return re.compile("".join(parts), re.S), names


class _Catalog:
    """One language's strings, ready to look up."""

    def __init__(self, language):
        self.exact = {}
        templates = []
        for english, by_language in TRANSLATIONS.items():
            text = by_language.get(language)
            if not text:
                continue
            key = normalize(english)
            self.exact[key] = text
            if is_template(key):
                pattern, names = _compile(key)
                templates.append((len(_PLACEHOLDER.sub("", key)), pattern, names, text))
        # The most specific first: "Deleted folder '{name}'" before "Deleted {what}".
        templates.sort(key=lambda t: -t[0])
        self.templates = [t[1:] for t in templates]

    def _exact(self, key):
        hit = self.exact.get(key)
        if hit is not None:
            return hit
        if "..." in key or _ELLIPSIS in key:
            hit = self.exact.get(key.replace("...", _ELLIPSIS)) or self.exact.get(key.replace(_ELLIPSIS, "..."))
            if hit is not None:
                return hit
        # "Name:" when only "Name" is known, and the other way round; the
        # same for a trailing "…" (a menu item that opens a dialog).
        for tail in (":", _ELLIPSIS):
            if key.endswith(tail) and len(key) > len(tail):
                hit = self.exact.get(key[:-len(tail)].rstrip())
                if hit is not None:
                    return hit + tail
        if key + ":" in self.exact:
            return self.exact[key + ":"].rstrip(":：").rstrip()
        return None

    def lookup(self, key):
        hit = self._exact(key)
        if hit is not None:
            return hit
        for pattern, names, text in self.templates:
            m = pattern.fullmatch(key)
            if m:
                values = dict(zip(names, m.groups()))
                for name in names:
                    if name.startswith("t_"):
                        values[name] = self._exact(normalize(values[name])) or values[name]
                return _PLACEHOLDER.sub(lambda p: values.get(p.group(1), p.group(0)), text)
        return None


_catalogs = {}


def _catalog(language):
    if language not in _catalogs:
        _catalogs[language] = _Catalog(language)
    return _catalogs[language]


def translate(text, language):
    """`text` in `language`, or `text` itself when there's no translation.
    Whitespace around it is kept."""
    if not text or not isinstance(text, str) or language == DEFAULT_LANGUAGE or language not in LANGUAGES:
        return text
    key = normalize(text)
    if not key:
        return text
    hit = _catalog(language).lookup(key)
    if hit is None:
        return text
    lead = text[:len(text) - len(text.lstrip())]
    trail = text[len(text.rstrip()):]
    return lead + hit + trail


def translate_filter(file_filter, language):
    """A file picker's filter ("Images (*.png *.jpg);;All files (*)") with
    each name translated and the patterns left alone."""
    out = []
    for part in file_filter.split(";;"):
        name, sep, patterns = part.partition(" (")
        out.append(translate(name, language) + sep + patterns)
    return ";;".join(out)


def web_strings(language):
    """What buddy.js needs to translate a page: {english: translation}
    (keys normalized), or None for English."""
    if language == DEFAULT_LANGUAGE or language not in LANGUAGES:
        return None
    return _catalog(language).exact


def canonical_language(language):
    """A stored language key as it is now: an old name (RENAMED_LANGUAGES)
    becomes the current one; anything else comes back unchanged."""
    return RENAMED_LANGUAGES.get(language, language)


def language_label(language):
    return LANGUAGE_LABELS.get(language, language)


# ------------------------------------------------------------- manager --

class I18nManager(QObject):
    """The language in use. language_changed tells open windows to redraw
    (core/web_page.py sends every view its new strings)."""

    language_changed = Signal(str)

    def __init__(self, current_language=DEFAULT_LANGUAGE):
        super().__init__()
        current_language = canonical_language(current_language)
        self._language = current_language if current_language in LANGUAGES else DEFAULT_LANGUAGE

    @property
    def language(self):
        return self._language

    @language.setter
    def language(self, lang_name):
        lang_name = canonical_language(lang_name)
        if lang_name in LANGUAGES and lang_name != self._language:
            self._language = lang_name
            self.language_changed.emit(self._language)

    def tr(self, text):
        return translate(text, self._language)


_global_i18n = None


def get_i18n():
    global _global_i18n
    if _global_i18n is None:
        _global_i18n = I18nManager()
    return _global_i18n


def current_language():
    return get_i18n().language


def tr(text):
    """`text` in the language chosen in Settings."""
    return get_i18n().tr(text)


def tr_filter(file_filter):
    return translate_filter(file_filter, get_i18n().language)


def format_when(when, english_format, style="date"):
    """A datetime written for the language chosen in Settings - month and
    weekday names can't go through tr(). English keeps english_format (a
    strftime pattern); other languages use their own convention: style
    "long" (weekday, day, month, year), "date" or "datetime" (short)."""
    language = get_i18n().language
    if language == DEFAULT_LANGUAGE or language not in LANGUAGE_CODES:
        return when.strftime(english_format)
    from PySide6.QtCore import QDate, QDateTime, QLocale, QTime
    locale = QLocale(LANGUAGE_CODES[language])
    date = QDate(when.year, when.month, when.day)
    if style == "datetime":
        return locale.toString(QDateTime(date, QTime(when.hour, when.minute)), QLocale.FormatType.ShortFormat)
    return locale.toString(date, QLocale.FormatType.LongFormat if style == "long" else QLocale.FormatType.ShortFormat)
