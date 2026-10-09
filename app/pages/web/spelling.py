#!/usr/bin/env python3
"""
Spell check in what's typed on pages (Settings > Tools > Web > Check
spelling in). Chromium's own spell checker, with the dictionary Chrome uses
for the language: fetched from Chrome's dictionary server the first time
the language is chosen - as Chrome does - and kept in
~/.buddy/web-dictionaries (Qt WebEngine is told to look there before it
starts: by core/web_flags.py on Windows, by main.py on macOS). A file that
isn't a dictionary (BDIC_MAGIC) is never kept.
"""

import os
import sys

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

if sys.platform != "darwin":        # core/web_flags.py is Windows' web engine switches
    from core import web_flags

# Chrome's dictionary server; a file is the language's versioned name, lowercase.
SERVER = "https://redirector.gvt1.com/edgedl/chrome/dict/"
BDIC_MAGIC = b"BDic"
MAX_BYTES = 8 * 1024 * 1024
# Language -> (what Settings calls it, Chrome's file for it).
LANGUAGES = {
    "en-US": ("English (US)", "en-US-10-1"), "en-GB": ("English (UK)", "en-GB-10-1"),
    "en-CA": ("English (Canada)", "en-CA-10-1"), "en-AU": ("English (Australia)", "en-AU-10-1"),
    "de-DE": ("German", "de-DE-3-0"), "fr-FR": ("French", "fr-FR-3-0"), "es-ES": ("Spanish", "es-ES-3-0"),
    "it-IT": ("Italian", "it-IT-3-0"), "pt-BR": ("Portuguese (Brazil)", "pt-BR-3-0"),
    "pt-PT": ("Portuguese (Portugal)", "pt-PT-3-0"), "nl-NL": ("Dutch", "nl-NL-3-0"),
    "sv-SE": ("Swedish", "sv-SE-3-0"), "da-DK": ("Danish", "da-DK-3-0"), "nb-NO": ("Norwegian", "nb-NO-3-0"),
    "pl-PL": ("Polish", "pl-PL-3-0"), "ru-RU": ("Russian", "ru-RU-3-0"), "tr-TR": ("Turkish", "tr-TR-4-0"),
    "uk-UA": ("Ukrainian", "uk-UA-4-0"), "ko": ("Korean", "ko-3-0"), "vi-VN": ("Vietnamese", "vi-VN-3-0"),
}
OFF = "off"
if sys.platform == "darwin":
    DICTIONARIES_DIR = os.path.join(os.path.expanduser("~"), ".buddy", "web-dictionaries")


def folder():
    if sys.platform == "darwin":
        return os.environ.get("QTWEBENGINE_DICTIONARIES_PATH") or DICTIONARIES_DIR
    else:
        return os.environ.get("QTWEBENGINE_DICTIONARIES_PATH") or web_flags.dictionaries_dir()


def path_for(language):
    return os.path.join(folder(), f"{language}.bdic")


def have(language):
    return os.path.isfile(path_for(language))


def url_for(language):
    return SERVER + LANGUAGES[language][1].lower() + ".bdic"


def is_dictionary(data):
    return bytes(data[:4]) == BDIC_MAGIC


class Fetcher(QObject):
    """Fetches a language's dictionary: done(language, error) - "" if it's
    in place."""

    done = Signal(str, str)

    def __init__(self, parent=None, server=None):
        super().__init__(parent)
        self.server = server                  # tests: a local server in place of Chrome's
        self._net = None
        self._busy = {}                       # language -> reply

    def fetch(self, language):
        if language not in LANGUAGES or language in self._busy:
            return
        if self._net is None:
            self._net = QNetworkAccessManager(self)
        url = (self.server + LANGUAGES[language][1].lower() + ".bdic") if self.server else url_for(language)
        request = QNetworkRequest(QUrl(url))
        request.setAttribute(QNetworkRequest.RedirectPolicyAttribute,
                             QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
        reply = self._net.get(request)
        self._busy[language] = reply
        reply.finished.connect(lambda: self._finished(language, reply))

    def busy(self, language):
        return language in self._busy

    def _finished(self, language, reply):
        self._busy.pop(language, None)
        error = ""
        if reply.error() != QNetworkReply.NetworkError.NoError:
            error = reply.errorString() or "The download didn't work."
        else:
            data = bytes(reply.readAll())
            if not is_dictionary(data) or len(data) > MAX_BYTES:
                error = "What came back wasn't a dictionary."
            else:
                try:
                    os.makedirs(folder(), exist_ok=True)
                    temp = path_for(language) + ".part"
                    with open(temp, "wb") as fh:
                        fh.write(data)
                    os.replace(temp, path_for(language))
                except OSError as exc:
                    error = str(exc)
        reply.deleteLater()
        self.done.emit(language, error)
