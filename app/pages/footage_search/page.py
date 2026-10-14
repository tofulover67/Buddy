#!/usr/bin/env python3
"""
Footage Search - finding a shot in the Media Pool by describing it, and
footage to go with what's being said.

Both rest on media search (core/media_search): a model on this computer that
puts a description beside the frames it describes. The Media Pool's clips are
put in its index when Index is pressed - a frame every couple of seconds, read
from the clips' files with ffmpeg - and found again whenever, in any project
that uses the same files.

  Search    what's typed, as the moments in the footage nearest to it: clip,
            time into it and the frame. Find in Media Pool selects the clip
            in Resolve; Place puts a few seconds from there on the timeline,
            at the playhead.
  B-roll    the latest transcript Subtitles made, line by line, each with the
            three moments nearest what's said. Place puts one over its line.

The index is Buddy's own, shared by every project, unless this project has
been given a folder for its own (the folder button: index_place) - the job's
folder on the media drive, say - where it's one file that goes wherever the
job goes. Which folder is remembered per project, in this tool's settings;
what the project had in the other index is copied over, so nothing is read
again, and on another computer or drive letter the entries are taken over
for the same files where they are there (core/media_search/store.py).

Reads of Resolve go through its reader process (resolve_ext.py); Place is the
only write. A web page (core/web_page.py): the view is web/index.html +
footage.js.

Protocol:
    to the view    state, results, broll, thumb, toast
    from the view  tab, refresh, find, index, stop, setup, cancel_setup,
                   reveal, place, load_transcript, index_place
"""

from __future__ import annotations

import base64
import json
import os
import sqlite3
from collections import OrderedDict

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFileDialog

from core import settings_form as sf
from core import tool_dirs
from core.i18n import tr
from core.media_search import extract
from core.media_search import model as search_model
from core.media_search import store as search_store
from core.media_search_ui import MediaSearch
from core.resolve_reader import ResolveCalls
from core.resolve_worker import ResolveWorker
from core.web_page import WebToolPage

from . import resolve_ext

TABS = ("search", "broll")
FOUND_LIMIT = 40            # moments shown for a description...
FOUND_WITHIN = 0.08         # ... none this much less like it than the closest
PICKS = 3                   # moments suggested for a transcript's line
MAX_LINES = 300             # of a transcript: a feature's worth
MIN_WORDS = 3               # a line shorter than this says too little to find footage for
PLACE_SECONDS = 5.0         # Search's Place: this much of the clip, from the moment found
LINE_SECONDS = (2.0, 10.0)  # B-roll's Place: as long as the line, within these
THUMB_SIDE = 320
THUMBS_KEPT = 240


def transcripts_dir() -> str:
    """Where Subtitles keeps its transcripts (pages/transcribe/jobs.py)."""
    return os.path.join(tool_dirs.buddy_dir(), "transcribe", "transcripts")


def latest_transcript(folder: str | None = None):
    """(name, lines) of the newest transcript Subtitles made - lines as
    [{"start", "end", "text"}], seconds from the timeline's first frame -
    or None when there's none to read."""
    folder = folder or transcripts_dir()
    try:
        files = sorted((os.path.join(folder, f) for f in os.listdir(folder) if f.lower().endswith(".json")),
                       key=os.path.getmtime, reverse=True)
    except OSError:
        return None
    for path in files:
        try:
            with open(path, encoding="utf-8") as f:
                segments = json.load(f).get("segments")
        except (OSError, ValueError, AttributeError):
            continue
        lines = []
        for segment in segments if isinstance(segments, list) else []:
            try:
                text = " ".join(str(segment["text"]).split())
                start, end = float(segment["start"]), float(segment["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if len(text.split()) >= MIN_WORDS and end > start:
                lines.append({"start": start, "end": end, "text": text})
        if lines:
            return os.path.splitext(os.path.basename(path))[0], lines[:MAX_LINES]
    return None


def clock(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    return f"{hours}:{rest // 60:02d}:{rest % 60:02d}" if hours else f"{rest // 60}:{rest % 60:02d}"


class FootageSettingsMixin:
    """Footage Search's section of the Settings window (core/settings_form.py)."""

    def settings_fields(self):
        card = search_model.gpu()
        fields = [
            sf.heading("Footage Search"),
            sf.check("search_use_gpu", "Use the graphics card for search by description", search_model.use_gpu(),
                     hint_text="Several times faster than the processor, on a computer with a graphics card of its "
                               "own. Switch it off if Resolve needs all of the card's memory. Asset Manager's search "
                               "follows this too."),
            sf.info("Running on", card, raw=True) if card else None,
        ]
        return [f for f in fields if f]

    def on_setting(self, key, value, ui):
        if key == "search_use_gpu":
            search_model.set_use_gpu(bool(value))
            self._push()


class FootageSearchPage(FootageSettingsMixin, WebToolPage):
    tool_id = "footage_search"
    display_name = "Footage Search"
    category = "Media & Assets"
    web_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")

    _thumbed = Signal(str, str)         # a thumbnail's key and data URL, from the thread that made it

    def build_state(self):
        self.settings = self.host.tool_settings(self.tool_id)       # index_folders: {project id: {"folder", "name"}}
        self._stores = {}               # a project's own index, by the folder it's in
        self.tab = "search"
        self.catalog = None             # the latest scan (resolve_ext.scan), or None
        self.problem = ""
        self.loading = False
        self.query = ""
        self.results = None             # moments found for the query; None before a search
        self.finding = False
        self.transcript = None          # {"name", "lines": [...]} with each line's "picks" once suggested
        self.suggesting = False
        self._thumbs = OrderedDict()    # thumbnail key -> data URL, the latest THUMBS_KEPT
        self._thumb_run = 0             # the thumbnails being made are this run's; an older run stops
        self._paths_cache = None
        self._counted = None            # (clips in the index, waiting, unreadable), until either changes
        self._was_indexing = False
        self._worker = ResolveWorker(self)      # Resolve calls when there's no reader process
        self._calls = ResolveCalls(self, self._worker)
        self._thumbed.connect(self._on_thumb)
        self.search = MediaSearch(self)
        self.search.changed.connect(self._search_changed)

    def web_ready(self):
        self._push()
        self._push_results()
        self._push_broll()

    def on_shown(self):
        self.on_refresh(None, quiet=True)

    def on_connection_changed(self, connected):
        if connected and self.isVisible():
            self.on_refresh(None, quiet=True)
        elif not connected:
            self.catalog = None
            self.problem = "Connect to Resolve to search its Media Pool."
            self._push()

    def on_app_quitting(self):
        self._thumb_run += 1
        self.search.close()
        self.search.indexer.stop()

    def working_on(self):
        return self.search.busy()

    # --------------------------------------------------------------- view --

    def _clips(self):
        return self.catalog["clips"] if self.catalog else []

    def _paths(self):
        """{the index's name for a file: its clip} for clips whose file is there."""
        if self._paths_cache is None:
            self._paths_cache = {}
            for clip in self._clips():
                if os.path.exists(clip["path"]):
                    self._paths_cache.setdefault(search_store.key(clip["path"]), clip)
        return self._paths_cache

    def _folder(self):
        """The folder this project's own index is kept in - "" when it's in Buddy's."""
        entry = (self.settings.get("index_folders") or {}).get((self.catalog or {}).get("project_id"))
        return str(entry.get("folder") or "") if isinstance(entry, dict) else ""

    def _trouble(self):
        """Why this project's index can't be used now, or ""."""
        folder = self._folder()
        if folder and not os.path.isdir(folder):
            return f"This project's index is kept in {folder}, which isn't there now."
        return ""

    def _store(self):
        """The index this project's clips are in: its own, or Buddy's."""
        folder = self._folder()
        if not folder:
            return self.search.store
        if folder not in self._stores:
            self._stores[folder] = search_store.Store(os.path.join(folder, search_store.INDEX_NAME),
                                                      model=search_model.NAME, shared=True)
        return self._stores[folder]

    def _counts(self, fresh=False):
        """(clips in the index, clips still to go in, clips it couldn't
        read). Asking looks at every file, so the answer is kept until the
        Media Pool is read again or the index stops filling (fresh)."""
        if self._trouble():
            return 0, 0, 0
        if self._counted is None or fresh:
            try:
                states = list(self._store().state(list(self._paths())).values())
            except (sqlite3.Error, OSError):
                return 0, 0, 0
            self._counted = (states.count("done"), states.count("new"), states.count("failed"))
        return self._counted

    def _push(self):
        state = self.search.state()
        indexed, waiting, failed = self._counts() if self.catalog and state["ready"] else (0, 0, 0)
        catalog = self.catalog or {}
        self.emit("state", {
            "tab": self.tab, "problem": self.problem, "loading": self.loading,
            "project": catalog.get("project_name", ""), "timeline": catalog.get("timeline"),
            "clips": len(self._clips()), "offline": len(self._clips()) - len(self._paths()) if self.catalog else 0,
            "other": catalog.get("other", 0), "indexed": indexed, "waiting": waiting, "failed": failed,
            "search": state, "index_folder": self._folder(), "index_trouble": self._trouble(),
        })

    def _row(self, hit):
        """A moment found, as the view shows it."""
        clip = self._paths().get(hit.path)
        if clip is None:
            return None
        still = clip["type"] == resolve_ext.IMAGE
        at = 0.0 if still else hit.start            # the moment the frame that was found is from
        key = f"{clip['id']}@{at:.2f}"
        return {"key": key, "clip": clip["id"], "name": clip["name"], "bin": clip["bin"], "still": still,
                "at": at, "time": "" if still else clock(at), "score": round(hit.score, 3),
                "thumb": self._thumbs.get(key)}

    def _push_results(self):
        self.emit("results", {"query": self.query, "finding": self.finding, "rows": self.results})

    def _push_broll(self):
        t = self.transcript
        timeline = (self.catalog or {}).get("timeline")
        self.emit("broll", {
            "suggesting": self.suggesting, "name": t["name"] if t else "", "timeline": timeline["name"] if timeline else "",
            "lines": [{"i": i, "time": clock(line["start"]), "start": line["start"],
                       "seconds": min(max(line["end"] - line["start"], LINE_SECONDS[0]), LINE_SECONDS[1]),
                       "text": line["text"], "picks": line.get("picks")}
                      for i, line in enumerate(t["lines"])] if t else None,
        })

    def on_tab(self, payload):
        tab = (payload or {}).get("tab")
        if tab in TABS:
            self.tab = tab
            self._push()
            if tab == "broll" and self.transcript is None:
                self.on_load_transcript(None)

    # ------------------------------------------------------------ Resolve --

    def on_refresh(self, _payload, quiet=False):
        controller = self.host.controller if getattr(self.host, "connected", False) else None
        if controller is None:
            self.catalog = None
            self.problem = "Connect to Resolve to search its Media Pool."
            self._push()
            return
        self.problem = ""
        self.loading = not quiet or self.catalog is None
        self._push()
        if not self._calls.call("footage.scan", {}, lambda: resolve_ext.scan(controller), self._on_scan):
            self.loading = False
            self._push()

    def _on_scan(self, result, error):
        self.loading = False
        self._paths_cache = None
        if error is not None:
            self.catalog = None
            self.problem = f"Could not read the Media Pool: {error}"
        else:
            self.catalog = result
            self._counted = None
            if self._folder() and not self._trouble():
                try:        # an index that came from another computer or drive letter: its entries, for the files here
                    self._store().adopt(list(self._paths()))
                except (sqlite3.Error, OSError):
                    pass
        self._push()

    # ------------------------------------------------- where the index is --

    def on_index_place(self, payload):
        """This project's index into a folder of the person's, or back into
        Buddy's own. What the project has in the index it leaves is copied
        to the one it goes to: nothing is read again."""
        where = (payload or {}).get("where")
        if not self.catalog or where not in ("buddy", "folder"):
            return
        if self.search.indexer.status()["running"]:
            self.emit("toast", {"text": "Stop indexing first."})
            return
        old_folder = self._folder()
        if where == "folder":
            folder = QFileDialog.getExistingDirectory(self, tr("Keep this project's index in…"), old_folder)
            if not folder:
                return
            folder = os.path.normpath(folder)
        else:
            folder = ""
        if folder == old_folder:
            return
        old = None if self._trouble() else self._store()
        folders = dict(self.settings.get("index_folders") or {})
        if folder:
            folders[self.catalog["project_id"]] = {"folder": folder, "name": self.catalog["project_name"]}
        else:
            folders.pop(self.catalog["project_id"], None)
        self.settings["index_folders"] = folders
        self.settings.save()
        self._counted = None
        try:
            new = self._store()
            new.adopt(list(self._paths()))
            if old is not None:
                new.take_from(old, list(self._paths()))
        except (sqlite3.Error, OSError) as exc:
            self.emit("toast", {"text": f"Couldn't use that folder for the index: {exc}"})
            folders = dict(folders)
            folders.pop(self.catalog["project_id"], None)
            if old_folder:
                folders[self.catalog["project_id"]] = {"folder": old_folder, "name": self.catalog["project_name"]}
            self.settings["index_folders"] = folders
            self.settings.save()
            self._counted = None
        else:
            self.emit("toast", {"text": f"This project's index is now kept in {folder}." if folder
                                else "This project's index is now kept in Buddy's folder."})
        self._push()
        if self.query:
            self.on_find({"query": self.query})

    def on_reveal(self, payload):
        clip_id = (payload or {}).get("clip")
        if not self._connected_for(clip_id):
            return
        project_id, controller = self.catalog["project_id"], self.host.controller
        if not self._calls.call("footage.reveal", {"expected_project_id": project_id, "clip_id": clip_id},
                                lambda: resolve_ext.reveal(controller, project_id, clip_id), self._on_revealed):
            self._busy_toast()

    def _on_revealed(self, bin_name, error):
        if error is not None:
            self.emit("toast", {"text": f"Couldn't find it in the Media Pool: {error}"})
        else:
            self.emit("toast", {"text": f"Selected in the Media Pool, in {bin_name}." if bin_name
                                else "Selected in the Media Pool."})

    def on_place(self, payload):
        """Place: a stretch of a clip onto the timeline - at the playhead, or
        (B-roll) over the transcript line it was suggested for."""
        payload = payload or {}
        clip_id = payload.get("clip")
        if not self._connected_for(clip_id):
            return
        try:
            at = max(0.0, float(payload.get("at") or 0))
            line = payload.get("line")
            line = self.transcript["lines"][int(line)] if line is not None and self.transcript else None
        except (TypeError, ValueError, IndexError):
            return
        seconds = min(max(line["end"] - line["start"], LINE_SECONDS[0]), LINE_SECONDS[1]) if line else PLACE_SECONDS
        args = {"expected_project_id": self.catalog["project_id"], "clip_id": clip_id, "source_start": at,
                "seconds": seconds, "record_seconds": line["start"] if line else None}
        controller = self.host.controller
        if not self._calls.call("footage.place", args, lambda: resolve_ext.place(controller, **args), self._on_placed):
            self._busy_toast()

    def _on_placed(self, result, error):
        if error is not None:
            self.emit("toast", {"text": f"Couldn't place it: {error}"})
        else:
            self.achieve("placed", 1)
            self.emit("toast", {"text": f"Placed on V{result['track']}."})

    def _connected_for(self, clip_id):
        if self.catalog is None or not any(c["id"] == clip_id for c in self._clips()):
            return False
        if not getattr(self.host, "connected", False):
            self.emit("toast", {"text": "Connect Buddy to Resolve first."})
            return False
        return True

    def _busy_toast(self):
        self.emit("toast", {"text": "Resolve is busy with another of Buddy's requests – try again in a moment."})

    # ----------------------------------------------------------- indexing --

    def on_setup(self, _payload):
        self.search.set_up(self)

    def on_cancel_setup(self, _payload):
        self.search.cancel_set_up()

    def on_index(self, _payload):
        if self._trouble():
            return
        clips = self._paths()
        try:
            self.search.index([c["path"] for c in clips.values()],
                              {c["path"]: c["seconds"] for c in clips.values() if c["type"] == resolve_ext.VIDEO},
                              self._store())
        except (sqlite3.Error, OSError) as exc:
            self.emit("toast", {"text": f"Couldn't open the index: {exc}"})
        self._push()

    def on_stop(self, _payload):
        self.search.indexer.stop()

    def _search_changed(self):
        indexing = self.search.indexer.status()["running"]
        ended = self._was_indexing and not indexing
        self._was_indexing = indexing
        if ended:
            self._counts(fresh=True)
            if self.query:
                self.on_find({"query": self.query})         # more can be found now
        self._push()

    # ------------------------------------------------------------ finding --

    def on_find(self, payload):
        self.query = " ".join(str((payload or {}).get("query", "")).split())[:300]
        if not self.query or not self.search.state()["ready"] or not self.catalog or self._trouble():
            self.search.forget_find()
            self.results, self.finding = None, False
            self._push_results()
            return
        query, paths, store = self.query, list(self._paths()), self._store()

        def look():
            vector, = search_model.embed_texts([query])
            return store.moments(vector, paths=paths, limit=FOUND_LIMIT)

        def done(hits, error):
            self.finding = False
            if error is not None:
                self.emit("toast", {"text": str(error)})
                hits = []
            rows = [self._row(h) for h in hits if h.score >= hits[0].score - FOUND_WITHIN]
            self.results = [r for r in rows if r]
            self._push_results()
            self._make_thumbs(self.results)

        self.finding = True
        self.search.find(look, done)
        self._push_results()

    def on_load_transcript(self, _payload):
        """B-roll: the newest transcript, then each line's nearest moments."""
        if self.suggesting:
            return
        found = latest_transcript()
        self.transcript = {"name": found[0], "lines": found[1]} if found else None
        self._push_broll()
        if not self.transcript or not self.search.state()["ready"] or not self.catalog or self._trouble():
            return
        lines, paths, store = self.transcript["lines"], list(self._paths()), self._store()

        def look():
            vectors = search_model.embed_texts([line["text"] for line in lines])
            return [store.moments(vector, paths=paths, limit=PICKS) for vector in vectors]

        def done(found_for, error):
            self.suggesting = False
            if error is not None:
                self.emit("toast", {"text": str(error)})
            elif self.transcript is not None and self.transcript["lines"] is lines:
                for line, hits in zip(lines, found_for):
                    line["picks"] = [r for r in (self._row(h) for h in hits) if r]
                self._make_thumbs([pick for line in lines for pick in line["picks"]])
            self._push_broll()

        self.suggesting = True
        self.search.run(look, done)
        self._push_broll()

    # --------------------------------------------------------- thumbnails --

    def _make_thumbs(self, rows):
        """The frames of these moments, made on a thread and sent as each is
        ready. A later call takes over: what an older one hadn't made yet,
        nobody's looking at any more."""
        self._thumb_run += 1
        run = self._thumb_run
        clips = {c["id"]: c for c in self._clips()}
        wanted = [(r["key"], clips[r["clip"]], r["at"]) for r in rows if not r["thumb"] and r["clip"] in clips]
        ffmpeg = self.search.ffmpeg()
        if not wanted:
            return

        def work():
            for key, clip, at in wanted:
                if run != self._thumb_run:
                    return
                try:
                    data = extract.picture(clip["path"]) if clip["type"] == resolve_ext.IMAGE \
                        else (extract.frame(ffmpeg, clip["path"], at, THUMB_SIDE) if ffmpeg else None)
                except extract.Unreadable:
                    data = None
                if data:
                    try:
                        self._thumbed.emit(key, "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii"))
                    except RuntimeError:
                        return              # the page went meanwhile

        self.search.run(work, lambda _result, _error: None)

    def _on_thumb(self, key, src):
        self._thumbs[key] = src
        while len(self._thumbs) > THUMBS_KEPT:
            self._thumbs.popitem(last=False)
        for row in self.results or []:
            if row["key"] == key:
                row["thumb"] = src
        for line in (self.transcript or {}).get("lines", []):
            for pick in line.get("picks") or []:
                if pick["key"] == key:
                    pick["thumb"] = src
        self.emit("thumb", {"key": key, "src": src})
