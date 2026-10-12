#!/usr/bin/env python3
"""
The one thread that fills the media search index.

Tools hand it files (add) and the index they go in - Buddy's own, or a
project's (store.py); it works through them one at a time - what's already
in that index as the file is now is passed over - and tells whoever listens
how far it is. One thread for every tool, so the model is never run
twice at once; it ends when the queue is empty and starts again with the
next add.

A file that can't be read is noted in the index with why, and not tried
again until it changes. One that needs ffmpeg when there's none is only
counted (needs_ffmpeg): it's tried again once there is. If the model itself
stops - not set up, llama.cpp gone - the queue is dropped and status()
says why.

Listeners are called on this thread: a page passes what it hears to its own
thread itself (core/media_search_ui.py).
"""

from __future__ import annotations

import os
import sqlite3
import threading

from . import extract, model
from .store import KIND_FRAME, KIND_IMAGE, KIND_SOUND, Piece, Store, key

FRAMES_A_STEP = model.BATCH


class Indexer:
    def __init__(self, store: Store, embedder=model, ffmpeg=extract.find_ffmpeg):
        self.store = store
        self._model = embedder
        self._find_ffmpeg = ffmpeg
        self._lock = threading.Lock()
        self._queue = []            # (path, the index it goes in) still to do, oldest first
        self._lengths = {}          # path -> seconds, where whoever added it knew
        self._thread = None
        self._stop = False
        self._listeners = []
        self._status = self._idle()

    @staticmethod
    def _idle() -> dict:
        return {"running": False, "done": 0, "total": 0, "current": "", "part": 0.0, "failed": 0,
                "needs_ffmpeg": 0, "error": ""}

    # ------------------------------------------------------------ asking --

    def listen(self, callback) -> None:
        """callback(status) whenever status() changes - on the indexer's thread."""
        self._listeners.append(callback)

    def unlisten(self, callback) -> None:
        if callback in self._listeners:
            self._listeners.remove(callback)

    def status(self) -> dict:
        """running, done and total (files, this run), current (the file's
        name), part (how far through it, 0-1), failed and needs_ffmpeg (files,
        this run), error (why the run stopped early, or "")."""
        with self._lock:
            return dict(self._status)

    def add(self, paths, lengths=None, store: Store | None = None) -> int:
        """Queues those of `paths` the index (`store`; Buddy's own if none
        is given) hasn't got as they are now and starts on them. lengths:
        {path: seconds} where known - saves asking ffmpeg. Returns how many
        were queued."""
        store = store or self.store
        state = store.state(paths)
        fresh = [p for p in dict.fromkeys(str(p) for p in paths)
                 if state.get(key(p)) == "new" and extract.kind_of(p)]
        with self._lock:
            waiting = {(p, id(s)) for p, s in self._queue}
            fresh = [p for p in fresh if (p, id(store)) not in waiting]
            self._queue += [(p, store) for p in fresh]
            self._lengths.update({str(p): s for p, s in (lengths or {}).items() if s})
            if not fresh:
                return 0
            if not self._status["running"]:
                self._status = dict(self._idle(), running=True)
            self._status["total"] += len(fresh)
            self._stop = False
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="media-search-index", daemon=True)
                self._thread.start()
        self._tell()
        return len(fresh)

    def stop(self, wait: float = 0.0) -> None:
        """Drops the queue; the file being read is finished with first."""
        with self._lock:
            self._stop = True
            self._queue.clear()
            thread = self._thread
        if wait and thread is not None and thread is not threading.current_thread():
            thread.join(wait)

    # ----------------------------------------------------------- working --

    def _tell(self):
        status = self.status()
        for callback in list(self._listeners):
            try:
                callback(status)
            except Exception:  # noqa: BLE001 - one listener's trouble isn't the others'
                pass

    def _set(self, **changes):
        with self._lock:
            self._status.update(changes)
        self._tell()

    def _run(self):
        ffmpeg = self._find_ffmpeg()
        while True:
            with self._lock:
                if self._stop or not self._queue:
                    self._status.update(running=False, current="", part=0.0)
                    break
                path, store = self._queue.pop(0)
                seconds = self._lengths.pop(path, 0.0)
            self._set(current=os.path.basename(path), part=0.0)
            try:
                outcome = self._one(path, seconds, ffmpeg, store)
            except (model.SearchError, sqlite3.Error, OSError) as exc:
                # The model stopped, or the index can't be written (its drive unplugged): nothing more can go in.
                why = str(exc) if isinstance(exc, model.SearchError) else f"Couldn't write the index in {store.path.parent}: {exc}"
                with self._lock:
                    self._queue.clear()
                    self._status.update(running=False, current="", part=0.0, error=why)
                break
            with self._lock:
                self._status["done"] += 1
                if outcome:
                    self._status[outcome] += 1
        self._tell()

    def _one(self, path, seconds, ffmpeg, store) -> str:
        """Embeds one file into its index: "" when it's in, "failed" when it
        couldn't be read (noted in the index), "needs_ffmpeg" when it has to
        wait for one. SearchError when the model stops."""
        kind = extract.kind_of(path)
        pieces = []
        try:
            if kind == extract.IMAGE:
                vector, = self._model.embed_pictures([extract.picture(path)])
                pieces.append(Piece(KIND_IMAGE, vector))
            elif kind == extract.VIDEO:
                seconds = seconds or (extract.duration(ffmpeg, path) if ffmpeg else 0.0)
                self._batches(extract.frames(ffmpeg, path, seconds), self._model.embed_pictures, KIND_FRAME, seconds,
                              pieces)
            else:
                seconds = seconds or (extract.duration(ffmpeg, path) if ffmpeg else 0.0)
                self._batches(extract.sounds(ffmpeg, path, seconds), self._model.embed_sounds, KIND_SOUND, seconds,
                              pieces)
        except extract.NeedsFfmpeg:
            return "needs_ffmpeg"
        except extract.Unreadable as exc:
            store.put(path, kind, [], seconds, problem=str(exc))
            return "failed"
        if self._stop:
            return ""                   # stopped halfway through it: nothing kept, done whole next time
        if not pieces:
            store.put(path, kind, [], seconds, problem="There's nothing in it to find." if kind != extract.AUDIO
                      else "It's silent.")
            return "failed"
        store.put(path, kind, pieces, seconds)
        return ""

    def _batches(self, parts, embed, piece_kind, seconds, pieces):
        """Embeds (start, span, data) parts a few at a time into `pieces`,
        saying how far through the file it is."""
        batch = []

        def flush():
            for (start, span, _data), vector in zip(batch, embed([data for _s, _sp, data in batch])):
                pieces.append(Piece(piece_kind, vector, start, span))
            if seconds > 0 and batch:
                self._set(part=min(1.0, (batch[-1][0] + batch[-1][1]) / seconds))
            batch.clear()

        try:
            for part in parts:
                if self._stop:
                    return
                batch.append(part)
                if len(batch) >= FRAMES_A_STEP:
                    flush()
            if batch and not self._stop:
                flush()
        finally:
            parts.close()               # stopped halfway: its ffmpeg isn't left running


_SHARED = None
_SHARED_LOCK = threading.Lock()


def shared() -> Indexer:
    """The indexer every tool uses, on the index in ~/.buddy/media_search."""
    global _SHARED
    with _SHARED_LOCK:
        if _SHARED is None:
            _SHARED = Indexer(Store(model=model.NAME))
        return _SHARED
