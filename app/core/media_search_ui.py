#!/usr/bin/env python3
"""
A page's end of media search (core/media_search), which itself has no Qt:
the indexer's news brought to the UI thread, Set up's dialog and download,
and searches made off it.

    self.search = MediaSearch(self)
    self.search.changed.connect(lambda: self.emit("search_state", self.search.state()))

state() is what a page shows: whether the model is here (ready), how much Set
up would download, how far a download or the indexing is, whether there's an
ffmpeg to read footage and compressed sound with, and whether it runs on the
graphics card - or could, for one more download (faster_mb). find() embeds what was
typed and looks it up on a thread; only the latest find's answer comes back.

Set up says what it fetches, from where and under which licence before
anything is downloaded, as Ask Buddy's does (pages/manual_chat/settings_panel.py).
"""

from __future__ import annotations

import os
import time

from PySide6.QtCore import QCoreApplication, QObject, QThread, Signal

from core.i18n import tr
from core.media_search import extract, indexer, model
from core.message_dialog import confirm

FFMPEG_CHECK_SECONDS = 30


class _Job(QThread):
    """work() on a thread; (result, error) back on the UI thread."""

    done = Signal(object, object)

    def __init__(self, work):
        super().__init__(QCoreApplication.instance())       # the application's: a page can go while it runs
        self.work = work
        self.finished.connect(self.deleteLater)

    def run(self):
        try:
            result, error = self.work(), None
        except Exception as exc:  # noqa: BLE001 - handed to whoever asked
            result, error = None, exc
        self.done.emit(result, error)


class MediaSearch(QObject):
    changed = Signal()          # set-up or indexing moved on: state() says where to
    _heard = Signal()           # the indexer's news, from its thread

    def __init__(self, parent=None):
        super().__init__(parent)
        self.indexer = indexer.shared()
        self.store = self.indexer.store
        self._setting_up = None             # the download's job, while there is one
        self._setup_text = ""
        self._setup_error = ""
        self._cancel = False
        self._find = 0                      # the latest find's number
        self._jobs = set()
        self._ffmpeg = (0.0, None)
        self._heard.connect(self.changed)
        self._listener = lambda _status: self._tell()
        self.indexer.listen(self._listener)

    def _tell(self):
        try:
            self._heard.emit()
        except RuntimeError:
            pass                            # the page went meanwhile

    def close(self):
        """The page is going: nothing more is heard, a download is stopped."""
        self.indexer.unlisten(self._listener)
        self._cancel = True

    # -------------------------------------------------------------- state --

    def ffmpeg(self) -> str | None:
        at, path = self._ffmpeg
        if time.monotonic() - at > FFMPEG_CHECK_SECONDS or (path and not os.path.isfile(path)):
            path = extract.find_ffmpeg()
            self._ffmpeg = (time.monotonic(), path)
        return path

    def state(self) -> dict:
        ready, why = model.ready()
        extras = model.extras()
        return {
            "supported": model.supported(), "ready": ready, "why": why,
            "download_mb": round(sum(item.size for item in [*model.missing(), *extras]) / 1e6),
            "faster_mb": round(sum(item.size for item in extras) / 1e6) if ready else 0,
            "gpu": model.gpu() if ready else "", "gpu_note": model.gpu_note(),
            "setting_up": self._setting_up is not None, "setup_text": self._setup_text,
            "setup_error": self._setup_error, "ffmpeg": bool(self.ffmpeg()), "index": self.indexer.status(),
        }

    def busy(self) -> str | None:
        """For a page's working_on(): what media search is doing, if anything."""
        if self._setting_up is not None:
            return "Setting up search"
        return "Indexing media" if self.indexer.status()["running"] else None

    # ------------------------------------------------------------- set up --

    def set_up(self, parent) -> None:
        """Asks, then downloads what's missing - and what would make it
        faster, which is all there is to fetch once it's set up. changed
        fires as it goes."""
        items = [*model.missing(), *model.extras()]
        if self._setting_up is not None or not items or not model.supported():
            return
        parts = [tr("Buddy will download:")]
        for item in items:
            # Each part translated on its own: the dialog can't match them joined.
            if item in model.extras():
                parts.append(tr(f"• llama.cpp for the graphics card, {item.mb} MB – runs the model on your graphics card "
                                "instead of the processor, several times faster. MIT licence, from the llama.cpp "
                                "project's GitHub releases, checked against its published checksum. Used only if this "
                                "computer has a graphics card of its own."))
            elif item.label == "llama.cpp":
                scanned = ", and scanned by Windows Defender before it's used." if os.name == "nt" else "."
                parts.append(tr(f"• llama.cpp, {item.mb} MB – runs the model on this computer. MIT licence, from the "
                                "llama.cpp project's GitHub releases (github.com/ggml-org/llama.cpp). Checked against its "
                                f"published checksum{scanned}"))
            elif item.name.startswith("mmproj"):
                parts.append(tr(f"• The part of EmbeddingGemma 2 that reads pictures and sound, {item.mb} MB – from "
                                "huggingface.co/ggml-org, checked against its published checksum. Apache 2.0 licence."))
            else:
                parts.append(tr(f"• EmbeddingGemma 2, {item.mb} MB – Google's embedding model, from "
                                "huggingface.co/ggml-org, checked against its published checksum. Apache 2.0 licence."))
        from pages.manual_chat import local_llama
        parts.append(tr(f"It all goes in {local_llama.ROOT}. Nothing else is installed, and the model only ever runs "
                        "on this computer – your media is never uploaded."))
        title = "Set up search by description" if model.missing() else "Run search on the graphics card"
        if not confirm(parent, title, "\n\n".join(parts), ok="Download and set up"):
            return
        self._cancel, self._setup_error, self._setup_text = False, "", "Starting…"

        def said(_done, _total, text):
            self._setup_text = text
            self._tell()

        job = _Job(lambda: model.install(said, lambda: self._cancel))
        job.done.connect(self._set_up_done)
        self._setting_up = job
        job.start()
        self.changed.emit()

    def cancel_set_up(self) -> None:
        self._cancel = True

    def _set_up_done(self, _note, error):
        from pages.manual_chat import local_llama
        self._setting_up, self._setup_text = None, ""
        if isinstance(error, local_llama.Cancelled):
            self._setup_error = "Set up was cancelled – nothing was kept."
        elif error is not None:
            self._setup_error = str(error) if isinstance(error, local_llama.LlamaError) \
                else f"{type(error).__name__}: {error}"
        self.changed.emit()

    # ----------------------------------------------------------- indexing --

    def index(self, paths, lengths=None, store=None) -> int:
        """Queues these files for the index (`store`; Buddy's own if none is
        given); how many needed it. 0 when the model isn't set up."""
        if not model.ready()[0]:
            return 0
        return self.indexer.add(paths, lengths, store)

    # ------------------------------------------------------------ finding --

    def run(self, work, done) -> None:
        """work() on a thread, then done(result, error) here."""
        job = _Job(work)
        self._jobs.add(job)

        def back(result, error):
            self._jobs.discard(job)
            done(result, error)

        job.done.connect(back)
        job.start()

    def find(self, look, done) -> None:
        """look() on a thread - it embeds and searches - then done(result,
        error) here, unless a later find was started meanwhile."""
        self._find += 1
        number = self._find
        self.run(look, lambda result, error: number == self._find and done(result, error))

    def forget_find(self) -> None:
        """Whatever find is out, its answer isn't wanted any more."""
        self._find += 1
