#!/usr/bin/env python3
"""
Media Relink (a tab on Media Manager) - finds this project's clips a new file to point at: offline
clips from a folder you choose (or a whole drive), or any clips when media
moves to a new drive - from the open bin, the open timeline, the selected
clips or the whole project. A web page (core/web_page.py): the view is
web/index.html + relink.js.

Nothing is relinked until you say so: a folder search only proposes a
match per clip (by filename, case-insensitive - relink_engine.py), shown in
the table, and a clip with several same-named files asks you to pick. The
rules for which clips each mode may touch are in relink_rows.py.

The folder search runs on a worker thread (walking a big network share or
a whole drive can take minutes), with its progress and a Cancel in the
page. It keeps only the filenames the clips could match, and folders it
can't open are left out and counted in the summary rather than ending the
search. The Resolve work - the scan, ReplaceClip and the check after it -
is done in the Resolve reader's process behind the busy overlay
(core/busy_job.py), so Buddy keeps drawing meanwhile; with no reader
process, here on the main thread, never on a worker thread.

A relink is believed only once the Media Pool, read again afterwards, says
so: Resolve has answered ReplaceClip with True and kept the old file.

The page shows a count of what the scan and search found, the proposed
path next to the recorded one, picking among duplicates, search progress
with Cancel, and selecting by checkbox.

Protocol:
    to the view    state, rows, search, log, alert, toast
    from the view  mode, scope, connect, scan, search, cancel_search, pick,
                   browse, relink_selected, relink_all
"""

from __future__ import annotations

import os
import sys
import time

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QFileDialog

from core import busy_job
from core.i18n import tr
from core.resolve_bridge import ResolveConnectionError
from core.resolve_reader import ResolveCalls
from core.web_page import WebToolPage

from . import relink_rows, resolve_ext
from .relink_engine import SearchCancelled, build_file_index, sequence_first_frame, wanted_names
from .relink_rows import MODE_FIX, MODE_RELOCATE, MODES

LOG_LIMIT = 60
PROGRESS_EVERY_SECONDS = 0.2
UNREADABLE_LOGGED = 5     # folders a search couldn't open, named in the Activity log


def _drive_name(root):
    """A whole drive as the summary names it: D: - or a Mac's own disk
    ("/") by the name Finder shows, from its link in /Volumes."""
    if root.rstrip("\\/"):
        return root.rstrip("\\/")
    if sys.platform == "darwin":
        try:
            with os.scandir("/Volumes") as volumes:
                for entry in volumes:
                    if entry.is_symlink() and os.path.realpath(entry.path) == "/":
                        return entry.name
        except OSError:
            pass
    return root


class _SearchWorker(QThread):
    """Indexes a folder's filenames off the UI thread - only the names in
    wanted. Reads the disk only - nothing here touches Resolve or a
    widget."""

    progressed = Signal(int)
    finished_index = Signal(dict, list)    # the index, and the folders it couldn't open
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, folder, wanted, parent=None):
        super().__init__(parent)
        self.folder = folder
        self.wanted = wanted
        self._stop = False
        self._last = 0.0

    def cancel(self):
        self._stop = True

    def _progress(self, seen):
        now = time.monotonic()
        if now - self._last >= PROGRESS_EVERY_SECONDS:
            self._last = now
            self.progressed.emit(seen)

    def run(self):
        unreadable = []
        try:
            index = build_file_index(self.folder, progress=self._progress, should_stop=lambda: self._stop,
                                     wanted=self.wanted, unreadable=unreadable)
        except SearchCancelled:
            self.cancelled.emit()
        except Exception as exc:  # noqa: BLE001 - an unreadable folder, a dropped share
            self.failed.emit(str(exc) or type(exc).__name__)
        else:
            self.finished_index.emit(index, unreadable)


class MediaRelinkPage(WebToolPage):
    tool_id = "media_relink"
    display_name = "Media Relink"
    category = "Media & Assets"         # a tab on Media Manager (pages/media_manager)
    web_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")

    def on_switch_tab(self, payload):
        self._media_manager.show_tab((payload or {}).get("tab"))

    def build_state(self):
        self.mode = MODE_FIX
        self._lists = {m: {"rows": [], "skipped": 0, "scanned": False, "summary": "", "generation": 0,
                           "project_id": None, "scope": resolve_ext.SCOPE_PROJECT}
                       for m in MODES}
        self._log = []
        self._worker = None
        self._search = None   # {"mode", "folder", "seen", "stopping"}
        # The scan and the relink, in the Resolve reader's process.
        self._calls = ResolveCalls(self)

    def working_on(self):
        return "Searching for media" if self._search is not None else None

    def web_ready(self):
        self.emit("log", self._log)
        self._push_state()
        for mode in MODES:
            self._push_rows(mode)
        self.emit("search", self._search)

    def on_app_quitting(self):
        worker = self._worker
        if worker is not None and worker.isRunning():
            worker.cancel()
            worker.wait(3000)

    def _dispatch(self, name, data):
        # While a search runs, the lists it will write into stay as they are.
        if self._search and name not in ("mode", "cancel_search"):
            return
        super()._dispatch(name, data)

    # --------------------------------------------------------------- view --

    def _connected(self):
        return getattr(self.host, "connected", False) and self.host.controller is not None

    def _push_state(self):
        self.emit("state", {"mode": self.mode, "scope": self._lists[self.mode]["scope"],
                            "connected": self._connected(), "busy": bool(self._search)})

    def _push_rows(self, mode):
        data = self._lists[mode]
        self.emit("rows", {
            "mode": mode,
            "generation": data["generation"],
            "scanned": data["scanned"],
            "summary": data["summary"],
            "summary_parts": data.get("summary_parts") or ([data["summary"]] if data["summary"] else []),
            "counts": relink_rows.counts(data["rows"]),
            "rows": [relink_rows.view(r) for r in data["rows"]],
        })

    def _add_log(self, text, kind="info"):
        self._log.append({"time": time.strftime("%H:%M"), "text": text, "kind": kind})
        del self._log[:-LOG_LIMIT]
        self.emit("log", self._log)

    def _summary(self, mode, parts, kind="info"):
        """parts: whole sentences, each translated on its own."""
        self._lists[mode]["summary"] = " ".join(parts)
        self._lists[mode]["summary_parts"] = parts
        self._log.append({"time": time.strftime("%H:%M"), "text": " ".join(parts), "parts": parts, "kind": kind})
        del self._log[:-LOG_LIMIT]
        self.emit("log", self._log)

    def _rows_for(self, mode, ids):
        wanted = {int(i) for i in ids or [] if str(i).isdigit()}
        return [r for r in self._lists[mode]["rows"] if r["id"] in wanted]

    @staticmethod
    def _project_id(controller):
        return resolve_ext.current_project_id(controller)

    def _check_scanned_project(self, mode, controller):
        scanned_id = self._lists[mode]["project_id"]
        if scanned_id and scanned_id != self._project_id(controller):
            self._alert("Project changed", "Scan the current Resolve project before searching or relinking.")
            return False
        return True

    # ------------------------------------------------------------ actions --

    def on_mode(self, payload):
        mode = (payload or {}).get("mode")
        if mode in MODES:
            self.mode = mode
            self._push_state()

    def on_connect(self, _payload):
        try:
            self.host.ensure_connected()
        except ResolveConnectionError:
            pass
        self._push_state()

    def on_scope(self, payload):
        scope = (payload or {}).get("scope")
        data = self._lists[self.mode]
        if self._search or scope not in resolve_ext.SCOPES or scope == data["scope"]:
            return
        # Never let matches or selections from the old scope be relinked.
        data.update(scope=scope, rows=[], skipped=0, scanned=False, summary="",
                    summary_parts=[], project_id=None, generation=data["generation"] + 1)
        self._push_state()
        self._push_rows(self.mode)
        self.on_scan(None)

    def on_scan(self, _payload):
        mode = self.mode
        try:
            controller = self.host.ensure_connected()
        except ResolveConnectionError:
            self._push_state()
            return
        scope = self._lists[mode]["scope"]
        # In the Resolve reader's process: a project's clips are a few calls
        # each, and Buddy keeps drawing meanwhile (core/busy_job.py).
        busy_job.run(self.host, self._calls, "relink.scan", {"scope": scope},
                     lambda: resolve_ext.scan_rows(controller, scope),
                     lambda result, error: self._on_scanned(mode, result, error),
                     "Reading the timeline…" if scope == resolve_ext.SCOPE_TIMELINE else "Scanning the Media Pool…")

    def _on_scanned(self, mode, result, error):
        self._push_state()
        if error is None and result.get("nothing"):
            title, text = result["nothing"]
            self._alert(title, text)
            return
        if error is not None:      # no project, or the connection dropped
            self._add_log(f"Couldn't scan the Media Pool: {error}", "error")
            return
        rows, skipped = result["rows"], result["skipped"]
        data = self._lists[mode]
        data.update(rows=rows, skipped=skipped, scanned=True, generation=data["generation"] + 1,
                    project_id=result["project_id"])
        c = relink_rows.counts(rows)
        parts = [f"Scanned 1 clip – {c['offline']} offline." if c["total"] == 1
                 else f"Scanned {c['total']} clips – {c['offline']} offline."]
        if skipped:
            parts.append(f"{skipped} had no single file to check (generated media) and were left out.")
        self._summary(mode, parts, "success" if not c["offline"] or mode == MODE_RELOCATE else "warn")
        self._push_rows(mode)

    def on_search(self, _payload):
        mode = self.mode
        if not self._lists[mode]["rows"]:
            self._alert("Scan first", "Scan the project first, so there are clips to find files for.")
            return
        try:
            controller = self.host.ensure_connected()
        except ResolveConnectionError:
            self._push_state()
            return
        if not self._check_scanned_project(mode, controller):
            return
        folder = QFileDialog.getExistingDirectory(self, tr("Choose a folder – or a whole drive – to search"))
        if not folder:
            return
        folder = os.path.normpath(folder)
        self._search = {"mode": mode, "folder": folder, "seen": 0, "stopping": False}
        wanted = wanted_names(r["old_path"] for r in self._lists[mode]["rows"])
        self._worker = _SearchWorker(folder, wanted, self)
        self._worker.progressed.connect(self._on_search_progress)
        self._worker.finished_index.connect(self._on_search_done)
        self._worker.failed.connect(self._on_search_failed)
        self._worker.cancelled.connect(self._on_search_cancelled)
        self._worker.start()
        self.emit("search", self._search)
        self._push_state()

    def on_cancel_search(self, _payload):
        if self._worker is not None and self._search:
            self._worker.cancel()
            self._search["stopping"] = True
            self.emit("search", self._search)

    def _on_search_progress(self, seen):
        if self._search:
            self._search["seen"] = seen
            self.emit("search", self._search)

    def _end_search(self):
        search, self._search = self._search, None
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.wait(1000)
            worker.deleteLater()
        self.emit("search", None)
        self._push_state()
        return search

    def _on_search_done(self, index, unreadable):
        search = self._end_search()
        mode = search["mode"]
        matched, ambiguous = relink_rows.apply_search(self._lists[mode]["rows"], index, mode)
        folder = os.path.basename(search["folder"])
        if folder:
            parts = [f"Searched {folder}: 1 match found." if matched == 1
                     else f"Searched {folder}: {matched} matches found."]
        else:   # a whole drive: D:\, or a Mac's own disk
            drive = _drive_name(search["folder"])
            parts = [f"Searched all of {drive} – 1 match found." if matched == 1
                     else f"Searched all of {drive} – {matched} matches found."]
        if ambiguous:
            parts.append(f"{ambiguous} with more than one file of that name (pick which).")
        if unreadable:
            # No permission mostly - but also a folder deleted mid-search, or a
            # path too long for Windows - so no reason is given.
            parts.append("Couldn't open 1 folder, so it wasn't searched." if len(unreadable) == 1
                         else f"Couldn't open {len(unreadable)} folders, so they weren't searched.")
            shown = "; ".join(unreadable[:UNREADABLE_LOGGED])
            more = len(unreadable) - UNREADABLE_LOGGED
            self._add_log(f"Folders that couldn't be opened: {shown} (and {more} more)" if more > 0
                          else f"Folders that couldn't be opened: {shown}", "warn")
        self._summary(mode, parts, "success" if matched or ambiguous else "warn")
        self._push_rows(mode)

    def _on_search_failed(self, message):
        self._end_search()
        self._add_log(f"Couldn't search that folder: {message}", "error")

    def _on_search_cancelled(self):
        self._end_search()
        self._add_log("Search cancelled – nothing was changed.", "warn")

    def on_pick(self, payload):
        payload = payload or {}
        rows = self._rows_for(self.mode, [payload.get("id")])
        path = payload.get("path")
        if not rows or path not in rows[0]["candidates"]:
            return
        relink_rows.pick(rows[0], path)
        self._push_rows(self.mode)

    def on_browse(self, payload):
        rows = self._rows_for(self.mode, [(payload or {}).get("id")])
        if not rows:
            return
        row = rows[0]
        start = os.path.dirname(row["old_path"])
        path, _filter = QFileDialog.getOpenFileName(self, tr("Choose the file for {name}").format(name=row["name"]),
                                                    start if os.path.isdir(start) else "")
        if path:
            relink_rows.pick(row, os.path.normpath(path))
            self._push_rows(self.mode)

    def on_relink_selected(self, payload):
        rows = self._rows_for(self.mode, (payload or {}).get("ids"))
        if not rows:
            self._alert("Select clips first", "Tick the clips you want to relink – or use Select all.")
            return
        self._relink(self.mode, rows)

    def on_relink_all(self, _payload):
        # Only in Fix mode: every clip it can touch is offline anyway.
        if self.mode != MODE_FIX:
            return
        rows = relink_rows.matched_rows(self._lists[MODE_FIX]["rows"])
        if not rows:
            self._add_log("Nothing to relink yet – search a folder for matches first.", "warn")
            return
        self._relink(MODE_FIX, rows)

    def _relink(self, mode, rows):
        try:
            controller = self.host.ensure_connected()
        except ResolveConnectionError:
            self._push_state()
            return
        if not self._check_scanned_project(mode, controller):
            return
        # Resolve's scripting won't relink an image sequence (ReplaceClip
        # refuses it; RelinkClips says yes and changes nothing), so those
        # are left for Resolve's own Relink.
        is_sequence = lambda row: row["resolved"] and sequence_first_frame(row["old_path"])
        sequences = sum(1 for r in rows if is_sequence(r))
        rows = [r for r in rows if not is_sequence(r)]
        # In the Resolve reader's process, like the scan (core/busy_job.py):
        # the rows go as plain data, and come back as relink() left them.
        args = {"project_id": self._lists[mode]["project_id"], "rows": [dict(r) for r in rows]}
        busy_job.run(self.host, self._calls, "relink.replace", args,
                     lambda: resolve_ext.relink_by_id(controller, **args),
                     lambda result, error: self._on_relinked(mode, rows, sequences, controller, result, error),
                     "Relinking 1 clip…" if len(rows) == 1 else f"Relinking {len(rows)} clips…")

    def _on_relinked(self, mode, rows, sequences, controller, result, error):
        if error is not None:      # the reader's process stopped mid-way, or the project closed
            self._summary(mode, [str(error)], "error")
            self._push_rows(mode)
            return
        relinked, skipped, failed = result["relinked"], result["skipped"], result["failed"]
        answered = {r["id"]: r for r in result["rows"]}
        for row in rows:
            row.update(answered.get(row["id"], {}))
        if not relinked:
            return self._relinked(mode, sequences, relinked, skipped, failed, 0, None)
        ids = [r["uid"] for r in rows if r.get("before")]
        busy_job.run(self.host, self._calls, "relink.check", {"ids": ids},
                     lambda: resolve_ext.current_paths(controller, ids),
                     lambda paths, error: self._on_checked(mode, rows, sequences, (relinked, skipped, failed),
                                                           paths, error),
                     "Checking the relinked clips…")

    def _on_checked(self, mode, rows, sequences, counts, paths, error):
        relinked, skipped, failed = counts
        # An error: the connection dropped mid-check - taken on Resolve's word.
        unchanged = relink_rows.confirm(rows, paths if error is None else {})
        self._relinked(mode, sequences, relinked - unchanged, skipped, failed, unchanged, error)

    def _relinked(self, mode, sequences, relinked, skipped, failed, unchanged, error):
        if relinked > 0:
            self.achieve("relinked", relinked)
        parts = ["Relinked 1 clip." if relinked == 1 else f"Relinked {relinked} clips."]
        if skipped:
            parts.append(f"{skipped} had no file chosen yet and were skipped.")
        if failed:
            parts.append(f"Resolve refused {failed} (a different kind of media, or no access to the file).")
        if sequences:
            parts.append(f"Resolve won't let Buddy relink image sequences ({sequences}) – "
                         "relink those in Resolve: right-click them › Relink Selected Clips.")
        if unchanged:
            parts.append(f"Resolve accepted {unchanged} more but kept their old files, "
                         "so they're still listed – try them again.")
        if error is not None:
            parts.append(f"Couldn't read the Media Pool back to check them: {error}")
        self._summary(mode, parts, "error" if failed or unchanged else "success" if relinked else "warn")
        if relinked:
            self.emit("toast", {"text": "Relinked 1 clip" if relinked == 1 else f"Relinked {relinked} clips"})
        self._push_rows(mode)

    def _alert(self, title, text):
        self.emit("alert", {"title": title, "text": text})
