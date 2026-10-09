"""Dailies: continuous Media Pool playback with saved review drafts.

What reaches the page, and how often, is most of what makes this feel
smooth or not. Each message is sent only when its content changed (_send):
the small "state", the "tape" rows, and "current" - the clip, its draft and
its still. The whole Media Pool catalog goes only to the clip picker, on
request. Every click used to re-send all of it - 232 KB on a real project,
twice over for "current" - and the page rebuilt the whole tape each time.

A clip's still (its first frame) is kept once decoded (STILLS_KEPT), so
going back to a clip, or back to this page, shows it at once; a clip not
seen yet keeps the previous picture faded until its own arrives, rather than
swapping in a message. Opening a clip waits PRIME_DELAY_MS, so arrowing
through a tape opens only the clip it stops on. The clip after the
selected one is always open on the player's second deck (tape_player.py),
so playing on into it - or pressing Next - starts it on the next frame.

Under the player, the view shows either the clip on its own or the whole
source tape laid end to end (the "viewer" setting); either way it is the
page's drawing of the same player - seek_to names a clip and a time in it,
and a clip that isn't open yet is opened and then moved there. The
transcript box beside it is filled per clip by transcripts.py.

J, K and L shuttle as in Resolve (on_shuttle): faster with each L, the
same backwards with J - which runs on through the tape into the end of the
clip before - and K stops. A clip's picture shape (from its first frame,
upright) goes to the page, which gives a tall one the panel's whole height.

Every Resolve call - the once-a-second look at which project and bin are
open, the Media Pool scan, writing the notes - goes through the Resolve
reader's process when there is one (core/resolve_reader.py ResolveCalls). While
the timeline plays, Resolve holds each call until playback stops, and a call
made inside Buddy, even on a worker thread, froze all of Buddy that long -
just from sitting on this page. A call waiting longer than BUSY_AFTER_S
says so on the page.
"""

import os
import uuid
from collections import OrderedDict
from time import monotonic

from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QImage, QKeySequence, QShortcut
from core import settings_form as sf
from core.resolve_reader import ResolveCalls
from core.resolve_worker import ResolveWorker
from core.web_page import WebToolPage
from pages.asset_manager.player import image_data_url
from pages.project_setup import proxy as proxies

from . import model, resolve_ext, transcripts
from .tape_player import TapePlayer
from .video_surface import VideoSurface


POLL_MS = 1000
# Stills held in memory: 50-200 KB of JPEG each, so ~10 MB at most.
STILLS_KEPT = 60
PRIME_DELAY_MS = 150
# Coming back to the page rescans only a catalog older than this. A scan is a
# few hundred Resolve calls, and Resolve's scripting holds Python's GIL for
# each one, so while Resolve is busy with another tool's calls a "background"
# scan stalls this whole UI - 7.7 s once in testing. The location poll still
# rescans at once when the project or the open bin changes; Refresh always does.
SCAN_FRESH_S = 30
# How long a held frame waits for the page to say the next still is on
# screen (still_shown) before it is let go of anyway.
HOLD_MAX_MS = 700
VIEWERS = ("clip", "source")
PRELOAD_AFTER_CUT_MS = 1000
# The fastest L (or J) goes: 1x, 2x, 4x, 8x, 16x.
SHUTTLE_MAX = 16.0
# A Resolve call waiting this long: Resolve is busy (most often, playing).
BUSY_AFTER_S = 3.0
STILL_MS = model.STILL_SECONDS * 1000


class DailiesPage(WebToolPage):
    tool_id = "dailies"
    display_name = "Dailies"
    category = "Editing Tools"
    web_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")

    def build_state(self):
        self.setFocusPolicy(Qt.ClickFocus)
        self.settings = self.host.tool_settings(self.tool_id, {"projects": {}, "viewer": "clip",
                                                                "transcribe": True, "sub_bins": False,
                                                                "proxies": True, "language": "",
                                                                "mixed_languages": []})
        self._proxy = False         # the clip on screen plays from its proxy file (_playback_path)
        self.catalog = None
        self.source_id = "@all"
        self.current_id = None
        self.media_filter = "All"
        self.auto_play = False
        # Playing speed: 1 at Space, doubled by each L (or J, backwards).
        self.rate = 1.0
        self._shapes = {}           # clip id -> its picture's [width, height], upright
        self.ready_id = None
        self._opened_id = None      # the clip the player was last asked to open
        self._stills = OrderedDict()
        self._sent = {}
        self._scanned_at = None
        self._pending_seek = None   # (clip id, ms) to move to once that clip is open
        self._transcripts = {}      # clip id -> {"status", "segments", "message"}
        self._transcript_job = None
        self.skipped = set()
        self.finished = False
        self.problem = ""
        self.loading = False
        self.saving = False
        self._refresh_pending = False
        self._commit_pending = False
        self._worker = ResolveWorker(self)      # Resolve calls when there's no reader process
        self._calls = ResolveCalls(self, self._worker)
        self.resolve_busy = False
        self._location_poll = QTimer(self)
        self._location_poll.setInterval(POLL_MS)
        self._location_poll.timeout.connect(self._poll_location)
        self._still_timer = QTimer(self, singleShot=True)
        self._still_timer.timeout.connect(self._still_done)
        # A still has no player to report where it is, so this does - the
        # tape's playhead moves across a still like across anything else,
        # and can be moved on it. Running while _still_clock is: from
        # _still_from ms at _still_started (monotonic s), at _still_rate.
        self._still_clock = QTimer(self)
        self._still_clock.setInterval(200)
        self._still_clock.timeout.connect(self._on_still_tick)
        self._still_started = 0.0
        self._still_from = 0.0
        self._still_rate = 1.0
        self._prime_timer = QTimer(self, singleShot=True)
        self._prime_timer.timeout.connect(self._prime_current)
        self._video_rect = QRect()
        self._surface = VideoSurface(self)
        self._video_widget = self._surface.widget
        # The clip whose playback the surface is drawing, and whether it is
        # holding the previous clip's last frame while the next one opens.
        self._surface_clip = None
        self._surface_hold = False
        self._hold_timer = QTimer(self, singleShot=True)
        self._hold_timer.timeout.connect(self._release_hold)
        self._surface.drawn.connect(self._on_surface_frame)
        self.preview = TapePlayer(self, self._surface.output, self._surface.drawn)
        self._last_space_at = 0.0
        self._space_shortcut = QShortcut(QKeySequence(Qt.Key_Space), self)
        self._space_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self._space_shortcut.setAutoRepeat(False)
        self._space_shortcut.activated.connect(self._on_space)
        self.preview.still.connect(self._on_frame)
        self.preview.ready.connect(self._on_ready)
        self.preview.position.connect(self._on_position)
        self.preview.failed.connect(self._on_failed)
        self.preview.ended.connect(self._on_ended)
        self.preview.at_start.connect(self._on_at_start)
        self.preview.shape.connect(self._on_shape)

    def web_ready(self):
        self._sent.clear()      # a fresh page has none of it
        self._push()

    def on_shown(self):
        self._location_poll.start()
        self._push()
        if self._scanned_at is None or monotonic() - self._scanned_at > SCAN_FRESH_S:
            self.on_refresh(None, quiet=True)
        elif self.current_id is not None:
            self._show_selected()     # the player let go of it while hidden

    def on_connection_changed(self, connected):
        if connected and self.isVisible():
            self.on_refresh(None, quiet=True)
        elif not connected:
            self._refresh_pending = False
            self._commit_pending = False
            self._stop()
            self.catalog = self._scanned_at = None
            self.problem = "Connect to Resolve to review Media Pool clips."
            self._push()

    def hideEvent(self, event):
        self._location_poll.stop()
        self._stop()
        self.preview.stop_all()     # let go of both decks' files too
        self._release_hold()
        super().hideEvent(event)

    def on_app_quitting(self):
        self._location_poll.stop()
        self._still_timer.stop()
        self._still_clock.stop()
        job = self._transcript_job
        self._cancel_transcript()
        if job is not None:
            job.wait(5000)      # a QThread destroyed while running crashes the exit
        self.preview.shutdown()

    def on_space_shortcut(self, payload):
        self._space_shortcut.setEnabled(bool((payload or {}).get("enabled")))

    def on_space_play(self, _payload):
        self._on_space(from_web=True)

    def _on_space(self, from_web=False):
        # WebEngine and Qt can both report the same press; toggle only once.
        now = monotonic()
        if (not self.isVisible() or (not from_web and not self._space_shortcut.isEnabled())
                or now - self._last_space_at < 0.15):
            return
        self._last_space_at = now
        self.on_play(None)

    def _resolve_waiting(self):
        return self._calls.busy()

    def _poll_location(self):
        busy = self._calls.waiting_for() > BUSY_AFTER_S
        if busy != self.resolve_busy:
            self.resolve_busy = busy
            self._push_state()
        if (not self.isVisible() or not getattr(self.host, "connected", False)
                or self.loading or self.saving or self._resolve_waiting()):
            return
        controller = self.host.controller
        self._calls.call("dailies.location", {}, lambda: resolve_ext.current_location(controller), self._on_location)

    def _on_location(self, location, error):
        if self.resolve_busy:
            self.resolve_busy = False
            self._push_state()
        if not self.isVisible() or not getattr(self.host, "connected", False):
            return
        # From the reader's process the pair comes back a JSON list.
        if self._refresh_pending or (error is None and (self.catalog is None or tuple(location or ()) !=
                (self.catalog["project_id"], self.catalog["current_bin_id"]))):
            self._refresh_pending = False
            self.on_refresh(None, quiet=True)
        elif self._commit_pending:
            self._commit_pending = False
            self.on_commit(None)

    def _project_data(self):
        if self.catalog is None:
            return None
        projects = self.settings.get("projects")
        if not isinstance(projects, dict):
            projects = {}
            self.settings["projects"] = projects
        return projects.setdefault(self.catalog["project_id"],
                                   {"sources": [], "drafts": {}, "active": "@all", "filter": "All"})

    def _sources(self):
        data = self._project_data()
        return data["sources"] if data else []

    def _drafts(self):
        data = self._project_data()
        return data["drafts"] if data else {}

    def _bin_ids(self):
        """The current bin's clips - and its sub-bins' too, with that box ticked."""
        catalog = self.catalog
        if self.settings.get("sub_bins"):
            return catalog.get("current_tree_ids", catalog["current_ids"])
        return catalog["current_ids"]

    def _tape_ids(self):
        if self.catalog is None:
            return []
        clips = self.catalog["clips"]
        if self.source_id == "@all":
            ids = list(clips)
        elif self.source_id == "@bin":
            ids = self._bin_ids()
        else:
            source = next((s for s in self._sources() if s["id"] == self.source_id), None)
            ids = source["ids"] if source else []
        return [clip_id for clip_id in ids if clip_id in clips and
                (self.media_filter == "All" or clips[clip_id]["type"] == self.media_filter)]

    def _send(self, name, payload):
        """Emits `payload` unless it is exactly what the page already has."""
        if self._sent.get(name) == payload:
            return
        self._sent[name] = payload
        self.emit(name, payload)

    def _push(self):
        self._push_state()
        self._push_tape()
        self._push_current()

    def _pending_count(self):
        catalog = self.catalog
        return sum(bool(d and not d.get("applied")) for clip_id, d in self._drafts().items()
                   if catalog and clip_id in catalog["clips"])

    def _push_state(self):
        catalog = self.catalog
        self._send("state", {
            "project": catalog["project_name"] if catalog else "", "project_id": catalog["project_id"] if catalog else "",
            "current_bin": catalog["current_bin"] if catalog else "",
            "problem": self.problem, "loading": self.loading, "saving": self.saving,
            "source": self.source_id, "filter": self.media_filter,
            "sources": [{"id": s["id"], "name": s["name"], "count": len(s["ids"])} for s in self._sources()],
            "clip_count": len(catalog["clips"]) if catalog else 0,
            "current": self.current_id, "pending": self._pending_count(),
            "auto_play": self.auto_play, "viewer": self.settings.get("viewer", "clip"),
            "sub_bins": bool(self.settings.get("sub_bins")), "rate": self.rate,
            "proxies": bool(self.settings.get("proxies", True)), "proxy": self._proxy,
            "resolve_busy": self.resolve_busy,
        })

    def _push_tape(self):
        catalog = self.catalog
        drafts = self._drafts()
        rows = []
        if catalog:
            for clip_id in self._tape_ids():
                row = catalog["clips"][clip_id]
                draft = drafts.get(clip_id)
                color = (draft or {}).get("color")
                rows.append({**{key: row[key] for key in ("id", "name", "bin", "type", "duration")},
                             "seconds": row.get("seconds", model.STILL_SECONDS),
                             "color": row["metadata"].get("Clip Color", "") if color is None else color,
                             "draft": bool(draft), "pending": bool(draft and not draft.get("applied"))})
        self._send("tape", {"clips": rows, "loading": self.loading})

    def on_catalog(self, _payload):
        """The whole Media Pool, for the clip picker - only when it opens."""
        clips = self.catalog["clips"].values() if self.catalog else ()
        self.emit("catalog", [{key: clip[key] for key in ("id", "name", "bin", "type")} for clip in clips])

    def _push_current(self):
        row = self.catalog["clips"].get(self.current_id) if self.catalog and self.current_id else None
        draft = self._drafts().get(self.current_id, {}) if row else {}
        self._send("current", {"clip": row, "draft": draft,
                               "frame": self._stills.get(self.current_id, "") if row else "",
                               "shape": self._shapes.get(self.current_id) if row else None})
        self._sync_video_widget()

    def _remember_still(self, clip_id, src):
        self._stills[clip_id] = src
        self._stills.move_to_end(clip_id)
        while len(self._stills) > STILLS_KEPT:
            self._stills.popitem(last=False)
        sent = self._sent.get("current")
        if sent and sent["clip"] and sent["clip"]["id"] == clip_id:
            # The page is told by "frame"; "current" need not carry it again.
            self._sent["current"] = {**sent, "frame": src}

    def on_stage_geometry(self, payload):
        """Place the native video surface over the web page's video stage."""
        try:
            x = int((payload or {})["x"])
            y = int(payload["y"])
            width = int(payload["width"])
            height = int(payload["height"])
            stage = payload.get("stage") or payload
            sx, sy = int(stage["x"]), int(stage["y"])
            sw, sh = max(0, int(stage["width"])), max(0, int(stage["height"]))
        except (KeyError, TypeError, ValueError):
            return
        # Crop the native surface to the visible workspace without shrinking
        # the video itself when part of its stage scrolls out of view: the
        # widget covers the visible part, the picture inside it is shifted
        # (video_surface.py - a mask is ignored on the stacked-on-top widget).
        origin = self.view.mapTo(self, QPoint(sx, sy))
        clip = QRect(x - sx, y - sy, max(0, width), max(0, height)).intersected(QRect(0, 0, sw, sh))
        if clip.isEmpty():
            self._video_rect = QRect()
        elif self._surface.covers_whole_stage():
            self._video_rect = QRect(origin.x(), origin.y(), sw, sh)
        else:
            self._video_rect = clip.translated(origin)
        if not clip.isEmpty():
            self._surface.crop(sw, sh, clip)
        self._sync_video_widget()

    def _sync_video_widget(self):
        row = self.catalog["clips"].get(self.current_id) if self.catalog and self.current_id else None
        drawing = row is not None and row["type"] == "Video" and self._surface_clip == self.current_id
        if not self.isVisible() or self._video_rect.isEmpty() or not (drawing or self._surface_hold):
            self._video_widget.hide()
            return
        self._video_widget.setGeometry(self._video_rect)
        self._video_widget.show()
        self._video_widget.raise_()

    def _leave_clip(self):
        """Before the clip on screen changes: a surface showing playback keeps
        its last frame up (faded, unless playing on) until the next clip has a
        picture of its own - no gap for the page to flash through."""
        if self._video_widget.isVisible() and self._surface_clip is not None:
            self._surface_hold = True
            self._surface.set_dim(not self.auto_play)
        self._surface_clip = None

    def _release_hold(self):
        self._hold_timer.stop()
        if self._surface_hold:
            self._surface_hold = False
            self._surface.set_dim(False)
        self._sync_video_widget()

    def _release_hold_once_shown(self):
        """Lets go of a held frame once the page has the new still up (it
        sends still_shown after decoding it). Letting go when the still was
        merely sent showed the old picture for a frame in between.

        Not while the tape plays on: then the next clip's own playback
        takes over the surface (_on_surface_frame), and dropping to the still
        in between would be a flicker at every cut."""
        if self.auto_play:
            return
        if self._surface_hold and not self._hold_timer.isActive():
            self._hold_timer.start(HOLD_MAX_MS)

    def on_still_shown(self, payload):
        if (payload or {}).get("id") == self.current_id:
            self._release_hold()

    def _on_surface_frame(self, frame):
        # Every frame of playback lands here, so this returns early on all
        # but the first one of a clip.
        if (self._surface_clip == self.current_id and not self._surface_hold) or not frame.isValid():
            return
        if self.current_id is not None and self.ready_id == self.current_id:
            self._surface_clip = self.current_id
            self._surface_hold = False
            self._surface.set_dim(False)
            self._sync_video_widget()

    def _show_selected(self, open_now=False):
        """Update the page, then open the selected clip - straight away when
        playing on, otherwise once the selection has settled."""
        self._push()
        self._want_transcript()
        if self.current_id is None or not self.isVisible():
            return
        row = self.catalog["clips"].get(self.current_id) if self.catalog else None
        if row is not None and row["type"] != "Video":
            self._release_hold()
        elif self.current_id in self._stills:
            self._release_hold_once_shown()
        if open_now:
            self._prime_timer.stop()
            self._open_current()
        else:
            self._prime_timer.start(PRIME_DELAY_MS)

    def _prime_current(self):
        if self.isVisible() and self.current_id is not None and self._opened_id != self.current_id:
            self._open_current()

    def on_refresh(self, _payload, quiet=False):
        """Rescans the Media Pool. `quiet` (coming back to the page, the bin
        changing in Resolve) leaves what is on screen alone while it runs;
        only the page's own Refresh button shows it reading."""
        controller = self.host.controller if getattr(self.host, "connected", False) else None
        if controller is None:
            self.problem = "Connect to Resolve to review Media Pool clips."
            self._push()
            return
        if self._resolve_waiting():
            self._refresh_pending = True
            return
        self._refresh_pending = False
        self.loading = not quiet or self.catalog is None
        self.problem = ""
        self._push()
        self._calls.call("dailies.scan", {}, lambda: resolve_ext.scan_rows(controller), self._on_scan)

    def _on_scan(self, result, error):
        self.loading = self.resolve_busy = False
        if error is not None:
            self._stop()
            self.catalog = self._scanned_at = None
            self.current_id = None
            self.problem = f"Could not read the Media Pool: {error}"
            self._push()
            self._commit_pending = False
            if self._refresh_pending:
                self._refresh_pending = False
                self.on_refresh(None, quiet=True)
            return
        old_project = self.catalog["project_id"] if self.catalog else None
        self.catalog = result
        self._scanned_at = monotonic()
        data = self._project_data()
        if old_project != result["project_id"]:
            self._stop()
            self.skipped.clear()
            self.finished = False
            self.source_id = data.get("active", "@all")
            self.media_filter = data.get("filter", "All")
            self.current_id = None
        if self.source_id not in ("@all", "@bin") and not any(s["id"] == self.source_id for s in self._sources()):
            self.source_id = "@all"
        if self.media_filter not in ("All", "Video", "Audio", "Image", "Other"):
            self.media_filter = "All"
        if self.current_id not in self._tape_ids():
            self._stop()
            self.finished = False
            self.current_id = self._tape_ids()[0] if self._tape_ids() else None
        # A rescan that changed nothing about the selected clip leaves the
        # player alone: reopening it is what used to blank the picture every
        # time this page came back into view.
        if self.auto_play or self._opened_id == self.current_id:
            self._push()
        else:
            self._show_selected()
        if self._refresh_pending:
            self._refresh_pending = False
            self.on_refresh(None, quiet=True)
            return
        if self._commit_pending:
            self._commit_pending = False
            self.on_commit(None)

    def on_select_source(self, payload):
        if self.catalog is None:
            return
        source_id = str((payload or {}).get("id") or "")
        if source_id not in ("@all", "@bin") and not any(s["id"] == source_id for s in self._sources()):
            return
        self._stop()
        self.skipped.clear()
        self.finished = False
        self.source_id = source_id
        self.current_id = self._tape_ids()[0] if self._tape_ids() else None
        self._project_data()["active"] = source_id
        self.settings.save()
        self._show_selected()

    def on_filter(self, payload):
        if self.catalog is None:
            return
        value = str((payload or {}).get("type") or "")
        if value not in ("All", "Video", "Audio", "Image", "Other"):
            return
        self._stop()
        self.skipped.clear()
        self.finished = False
        self.media_filter = value
        if self.current_id not in self._tape_ids():
            self.current_id = self._tape_ids()[0] if self._tape_ids() else None
        self._project_data()["filter"] = value
        self.settings.save()
        self._show_selected()

    def _playback_path(self, row):
        """(the file to play, whether it's the clip's proxy). With Play proxies on,
        a video clip plays from its proxy when one is beside the source - Project
        Setup's or Resolve's own (proxy.known_proxy): half-size H.264 the player
        decodes on the graphics card, where the camera's 10-bit 4:2:2 HEVC or
        ProRes it can't (measured: 1.6x at best asked for 4x, the proxy 4x)."""
        path = row.get("path") or ""
        if row.get("type") == "Video" and path and self.settings.get("proxies", True):
            try:
                found = proxies.known_proxy({"path": path})
            except Exception:  # noqa: BLE001 - a lookup that fails plays the original
                found = ""
            if found:
                return found, True
        return path, False

    def settings_fields(self):
        return [
            sf.heading("Dailies"),
            sf.check("proxies", "Play proxies when a clip has one", self.settings.get("proxies", True),
                     tooltip="A clip plays from its proxy file when one is beside the source - Project Setup's, "
                             "or Resolve's own. Smaller, and the graphics card decodes it, so J, K and L run at "
                             "speed on footage it otherwise can't (10-bit 4:2:2 HEVC, ProRes). A Proxy note by "
                             "the clock says when it's playing one."),
        ]

    def on_setting(self, key, value, ui):
        if key == "proxies":
            self._set_proxies(bool(value))

    def _set_proxies(self, on):
        """Play proxies: the clip on screen is opened again from the other file,
        where it was, when it has a proxy to switch between."""
        if on == bool(self.settings.get("proxies", True)):
            return
        self.settings["proxies"] = on
        self.settings.save()
        row = self.catalog["clips"].get(self.current_id) if self.catalog and self.current_id else None
        if row is not None and row.get("type") == "Video" and self._playback_path(row)[1] != self._proxy:
            self._pending_seek = (self.current_id, self.preview.position_ms())
            self._open_current()
        self._push_state()

    def on_sub_bins(self, payload):
        """The Current Bin tape with its sub-bins' clips, or without. The clip
        on screen stays if it's still on the tape."""
        on = bool((payload or {}).get("on"))
        if on == bool(self.settings.get("sub_bins")):
            return
        self.settings["sub_bins"] = on
        self.settings.save()
        if self.catalog is None or self.source_id != "@bin":
            self._push_state()
            return
        self.skipped.clear()
        self.finished = False
        if self.current_id not in self._tape_ids():
            self._stop()
            self.current_id = self._tape_ids()[0] if self._tape_ids() else None
            self._show_selected()
        else:
            self._push()

    def on_create_source(self, payload):
        if self.catalog is None:
            return
        payload = payload or {}
        name = str(payload.get("name") or "").strip()[:80]
        scope = payload.get("scope")
        if not name:
            self.emit("alert", {"title": "Name this source", "text": "Enter a source name first."})
            return
        if scope == "all":
            ids = list(self.catalog["clips"])
        elif scope == "bin":
            ids = self._bin_ids()[:]
        elif scope == "selected":
            ids = [str(i) for i in payload.get("ids", []) if str(i) in self.catalog["clips"]]
        else:
            return
        ids = list(dict.fromkeys(ids))
        if not ids:
            self.emit("alert", {"title": "Empty source", "text": "Choose at least one Media Pool clip."})
            return
        source = {"id": uuid.uuid4().hex, "name": name, "ids": ids}
        self._sources().append(source)
        self.settings.save()
        self.on_select_source({"id": source["id"]})

    def on_rename_source(self, payload):
        source = next((s for s in self._sources() if s["id"] == (payload or {}).get("id")), None)
        name = str((payload or {}).get("name") or "").strip()[:80]
        if source and name:
            source["name"] = name
            self.settings.save()
            self._push()

    def on_delete_source(self, payload):
        source_id = (payload or {}).get("id")
        data = self._project_data()
        if not data or not any(s["id"] == source_id for s in data["sources"]):
            return
        data["sources"] = [s for s in data["sources"] if s["id"] != source_id]
        self.settings.save()
        if self.source_id == source_id:
            self.on_select_source({"id": "@all"})
        else:
            self._push()

    def on_add_clips(self, payload):
        source = next((s for s in self._sources() if s["id"] == self.source_id), None)
        if source is None or self.catalog is None:
            return
        for clip_id in (payload or {}).get("ids", []):
            if clip_id in self.catalog["clips"] and clip_id not in source["ids"]:
                source["ids"].append(clip_id)
        self.settings.save()
        if self.current_id is None and self._tape_ids():
            # Clips added to an empty tape: the first is up, ready to play.
            self.finished = False
            self.current_id = self._tape_ids()[0]
            self._show_selected()
        else:
            self._push()

    def on_remove_clip(self, payload):
        source = next((s for s in self._sources() if s["id"] == self.source_id), None)
        if source is None:
            return
        clip_id = (payload or {}).get("id")
        removed_current = self.current_id == clip_id
        source["ids"] = [i for i in source["ids"] if i != clip_id]
        if removed_current:
            self._stop()
            self.current_id = self._tape_ids()[0] if self._tape_ids() else None
        self.settings.save()
        if removed_current:
            self._show_selected()
        else:
            self._push()

    def on_find_in_pool(self, payload):
        """A clip's right-click "Find in Media Pool": its bin opened in
        Resolve's Media Pool, the clip selected there."""
        clip_id = (payload or {}).get("id")
        if self.catalog is None or clip_id not in self.catalog["clips"]:
            return
        if not getattr(self.host, "connected", False):
            self.emit("toast", {"text": "Connect Buddy to Resolve first."})
            return
        project_id, controller = self.catalog["project_id"], self.host.controller
        if not self._calls.call("dailies.reveal", {"expected_project_id": project_id, "clip_id": clip_id},
                                lambda: resolve_ext.reveal_by_id(controller, project_id, clip_id),
                                self._on_revealed):
            self.emit("toast", {"text": "Resolve is busy with another of Buddy's requests – try again in a moment."})

    def _on_revealed(self, bin_name, error):
        if error is not None:
            self.emit("toast", {"text": f"Couldn't find it in the Media Pool: {error}"})
        else:
            self.emit("toast", {"text": f"Selected in the Media Pool, in {bin_name}." if bin_name
                                else "Selected in the Media Pool."})

    def on_pick_clip(self, payload):
        clip_id = (payload or {}).get("id")
        if clip_id not in self._tape_ids():
            return
        self._stop()
        self.finished = False
        self.current_id = clip_id
        self._show_selected()

    def _stop(self):
        self._leave_clip()
        self.auto_play = False
        self.ready_id = None
        self._opened_id = None
        self._pending_seek = None
        self._still_timer.stop()
        self._still_clock.stop()
        self._still_from = 0.0
        self._prime_timer.stop()
        self.preview.stop()
        self._sync_video_widget()

    def _open_current(self):
        row = self.catalog["clips"].get(self.current_id) if self.catalog else None
        if row is None:
            return
        self.ready_id = None
        self._opened_id = self.current_id
        self._push_current()
        path = row["path"]
        if not path or not os.path.isfile(path):
            self._on_failed(self.current_id, "The source file is unavailable on this computer.")
            return
        if row["type"] in ("Video", "Audio"):
            was, (path, self._proxy) = self._proxy, self._playback_path(row)
            self.preview.open(self.current_id, path, row["type"], play=self.auto_play)
            if self._proxy != was:
                self._push_state()          # the Proxy note by the clock
            self._preload_next()
        elif row["type"] == "Image":
            if self.current_id not in self._stills:
                image = QImage(path)
                if image.isNull():
                    self._on_failed(self.current_id, "This image format could not be previewed.")
                    return
                self._on_frame(self.current_id, image_data_url(image, max_side=1280))
            self.ready_id = self.current_id
            seek, self._pending_seek = self._pending_seek, None
            if seek is not None and seek[0] == self.current_id:
                self._seek_still(seek[1])
            self._preload_next()
            if self.auto_play:
                self._start_still()
        else:
            self._on_failed(self.current_id, "This media type cannot be previewed here.")

    def on_play(self, _payload):
        """Space: play at normal speed, or pause."""
        if self.current_id is None:
            return
        if self.auto_play:
            self._pause()
        else:
            self._play(1.0)

    def on_shuttle(self, payload):
        """J, K and L, as in Resolve: L plays, and each L after that doubles
        the speed (to SHUTTLE_MAX); J the same, backwards; K stops. Backwards
        runs on through the tape into the clip before (_reverse_into_previous)."""
        way = (payload or {}).get("way")
        if way not in (-1, 0, 1) or self.current_id is None:
            return
        if way == 0:
            if self.auto_play:
                self._pause()
            return
        if not self.auto_play:
            self._play(float(way))
            return
        same_way = (self.rate > 0) == (way > 0)
        self.rate = way * min(abs(self.rate) * 2, SHUTTLE_MAX) if same_way else float(way)
        self.preview.set_rate(self.rate)
        row = self.catalog["clips"].get(self.current_id) if self.catalog else None
        if row is not None and row["type"] not in ("Video", "Audio") and self._still_timer.isActive():
            self._start_still()     # a still runs its time again, at this speed
        self._push_state()

    def on_step(self, payload):
        """K held with J or L: one frame back or on, paused."""
        try:
            frames = int((payload or {}).get("frames", 0))
        except (TypeError, ValueError):
            return
        if not frames or self.current_id is None or self.ready_id != self.current_id:
            return
        if self.auto_play:
            self._pause()
        self.preview.step(frames, self.current_id)

    def _pause(self):
        self.auto_play = False
        self.rate = 1.0
        self._still_from = self._still_position()
        self._still_timer.stop()
        self._still_clock.stop()
        self.preview.pause()
        self.preview.set_rate(1.0)
        self._push_state()

    def _play(self, rate):
        if self.finished and self._tape_ids():
            # At the tape's end: forwards starts it over, backwards plays
            # back from the end of its last clip.
            self.finished = False
            if rate > 0:
                self._stop()
                self.skipped.clear()
                self.current_id = self._tape_ids()[0]
            else:
                self._pending_seek = (self.current_id, self._end_ms(self.current_id))
        self.rate = rate
        self.preview.set_rate(rate)
        self.auto_play = True
        self._push()
        if self.ready_id == self.current_id:
            row = self.catalog["clips"][self.current_id]
            if row["type"] in ("Video", "Audio"):
                self.preview.play()
            else:
                self._start_still()
        else:
            self._prime_timer.stop()
            self._open_current()

    def _end_ms(self, clip_id):
        """Where a clip's last frame is, near enough: seek_ms keeps it inside
        the file's own length."""
        row = self.catalog["clips"][clip_id]
        return max(0.0, float(row.get("seconds") or 0) * 1000 - 1)

    def _on_at_start(self, clip_id):
        if clip_id == self.current_id and self.auto_play and self.rate < 0:
            self._reverse_into_previous()

    def _reverse_into_previous(self):
        """Playing backwards past a clip's start: on into the end of the
        clip before it, at the same speed - or a stop at the tape's start."""
        tape = self._tape_ids()
        if self.current_id not in tape:
            return
        index = tape.index(self.current_id)
        if index == 0:
            self._pause()
            return
        rate = self.rate
        self.finished = False
        self._stop()
        self.current_id = tape[index - 1]
        self.auto_play, self.rate = True, rate
        self._pending_seek = (self.current_id, self._end_ms(self.current_id))
        self._show_selected(open_now=True)

    def _still_done(self):
        """A still has had its time (or a clip that couldn't play its moment)."""
        if self.auto_play and self.rate < 0:
            self._reverse_into_previous()
        else:
            self._advance()

    def _preload_next(self):
        """The clip after this one, open and waiting on the second deck."""
        tape = self._tape_ids()
        if self.current_id not in tape:
            return
        following = tape[tape.index(self.current_id) + 1:tape.index(self.current_id) + 2]
        for clip_id in following:
            row = self.catalog["clips"][clip_id]
            if row["type"] in ("Video", "Audio") and row["path"] and os.path.isfile(row["path"]):
                self.preview.preload(clip_id, self._playback_path(row)[0], row["type"])

    def _still_position(self):
        """Where the tape is in the still on screen, ms."""
        if not self._still_clock.isActive():
            return self._still_from
        moved = (monotonic() - self._still_started) * 1000 * self._still_rate
        return min(max(self._still_from + moved, 0.0), STILL_MS)

    def _start_still(self):
        """Runs the still from where it is, at the set speed (backwards too) -
        what's left of its time, then the clip after (or before) it."""
        self._still_from = self._still_position()
        self._still_rate = self.rate
        self._still_started = monotonic()
        left = STILL_MS - self._still_from if self.rate > 0 else self._still_from
        self._still_timer.start(max(0, int(left / abs(self.rate))))
        self._still_clock.start()
        self._emit_still_position()

    def _seek_still(self, ms):
        """Moves the playhead on a still: running, it runs on from there."""
        running = self._still_clock.isActive()
        self._still_clock.stop()
        self._still_from = min(max(float(ms), 0.0), STILL_MS)
        if running:
            self._start_still()
        else:
            self._emit_still_position()

    def _emit_still_position(self):
        self.emit("position", {"position": int(self._still_position()), "duration": int(STILL_MS)})

    def _on_still_tick(self):
        if not self._still_timer.isActive():
            self._still_clock.stop()
            return
        self._emit_still_position()

    def on_viewer(self, payload):
        viewer = (payload or {}).get("viewer")
        if viewer in VIEWERS and viewer != self.settings.get("viewer"):
            self.settings["viewer"] = viewer
            self.settings.save()
            self._push_state()

    def on_seek_to(self, payload):
        """Moves to `ms` into clip `id` - on the tape (the source viewer) that
        can be any clip, which is opened first and moved to once it's ready.
        Playing carries on playing; paused shows that frame."""
        payload = payload or {}
        clip_id = str(payload.get("id") or "")
        try:
            ms = max(0.0, float(payload.get("ms", 0)))
        except (TypeError, ValueError):
            return
        if clip_id not in self._tape_ids():
            return
        if clip_id == self.current_id:
            row = self.catalog["clips"][clip_id]
            if self.ready_id == clip_id and row["type"] not in ("Video", "Audio"):
                self._pending_seek = None
                self._seek_still(ms)
            elif self.ready_id == clip_id:
                self._pending_seek = None
                self.preview.seek_ms(ms, clip_id)
            else:
                self._pending_seek = (clip_id, ms)
                if self._opened_id != clip_id:
                    self._prime_timer.stop()
                    self._open_current()
            return
        playing = self.auto_play
        self._stop()
        self.finished = False
        self.current_id = clip_id
        self.auto_play = playing
        self._pending_seek = (clip_id, ms) if ms > 0 else None
        self._show_selected(open_now=True)

    # --------------------------------------------------------- transcript --

    def _push_transcript(self):
        row = self.catalog["clips"].get(self.current_id) if self.catalog and self.current_id else None
        entry = self._transcripts.get(self.current_id) or {}
        if row is not None and row["type"] not in ("Video", "Audio"):
            entry = {"status": "none", "message": "Nothing to transcribe in a still."}
        self._send("transcript", {"id": self.current_id if row else None,
                                  "on": bool(self.settings.get("transcribe", True)),
                                  "status": entry.get("status", "idle"),
                                  "segments": list(entry.get("segments", [])),
                                  "message": entry.get("message", ""),
                                  "language": self._language_choice(),
                                  "languages": [{"value": v, "label": l} for v, l in self._language_choices()],
                                  "mixed": self._mixed_languages(), "spoken": self._spoken(),
                                  "heard": transcripts.language_name(entry.get("language", ""))})

    def _language_choice(self):
        """The transcript box's language: "" to detect, a code, or MIXED."""
        return str(self.settings.get("language") or "")

    def _mixed_languages(self):
        """Dailies' own mixed set - the Transcribe tool's, until one is ticked here."""
        own = transcripts.valid(self.settings.get("mixed_languages") or [])
        if own:
            return own
        try:
            return transcripts.mixed_languages()
        except Exception:  # noqa: BLE001 - no Transcribe tool here
            return []

    def _language_choices(self):
        try:
            return transcripts.choices(self._mixed_languages())
        except Exception:  # noqa: BLE001 - no Transcribe tool here: detection only
            return [("", "Auto-detect")]

    def _spoken(self):
        try:
            return transcripts.spoken()
        except Exception:  # noqa: BLE001
            return []

    def on_transcript_language(self, payload):
        """The transcript box's language menu: the clip is heard again in it
        (a transcript heard in another language is kept on disk, not here)."""
        choice = str((payload or {}).get("language") or "")
        if choice not in dict(self._language_choices()) or choice == self._language_choice():
            return
        if choice == transcripts.MIXED and len(self._mixed_languages()) < 2:
            return                      # the page opens the picker first (on_transcript_mixed)
        self._hear_in(choice)

    def on_transcript_mixed(self, payload):
        """The picker's languages: two or more, heard from now on."""
        codes = transcripts.valid((payload or {}).get("codes") or [])
        if len(codes) < 2:
            return
        self.settings["mixed_languages"] = codes
        self._hear_in(transcripts.MIXED, force=True)

    def _hear_in(self, choice, force=False):
        if not force and choice == self._language_choice():
            return
        self.settings["language"] = choice
        self.settings.save()
        self._cancel_transcript()
        self._transcripts = {}
        self._want_transcript()

    def _want_transcript(self):
        """The selected clip's transcript: from memory, from disk, or started."""
        self._push_transcript()
        clip_id = self.current_id
        row = self.catalog["clips"].get(clip_id) if self.catalog and clip_id else None
        if row is None or row["type"] not in ("Video", "Audio") or not self.settings.get("transcribe", True):
            return
        if self._transcripts.get(clip_id, {}).get("status") in ("done", "running", "error"):
            return
        self._start_transcript(clip_id)

    def _start_transcript(self, clip_id, quiet=False):
        """Starts transcribing clip_id - unless it's on disk already. `quiet`
        (reading ahead) never cancels the clip being done now, and never
        reports why transcription can't run."""
        row = self.catalog["clips"][clip_id]
        path = row["path"]
        if not path or not os.path.isfile(path):
            if not quiet:
                self._transcripts[clip_id] = {"status": "error", "segments": [],
                                              "message": "The source file is unavailable on this computer."}
                self._push_transcript()
            return False
        languages, language = transcripts.heard(self._language_choice(), self._mixed_languages())
        saved = transcripts.cached(path, languages=languages, language=language)
        if saved is not None:
            self._transcripts[clip_id] = {"status": "done", "segments": saved["segments"], "message": "",
                                          "language": saved.get("language", "")}
            if clip_id == self.current_id:
                self._push_transcript()
            return False
        job = self._transcript_job
        if job is not None:
            if job.clip_id == clip_id or quiet:
                return False
            self._cancel_transcript()    # for a clip that's no longer on screen
        plan, why = transcripts.engine_plan(languages, language)
        if plan is None:
            if not quiet:
                # Not remembered: installing the engine fixes it without a restart.
                self._send("transcript", {"id": clip_id, "on": True, "status": "unavailable",
                                          "segments": [], "message": why})
            return False
        job = transcripts.TranscriptJob(clip_id, path, plan, self, languages=languages, language=language)
        job.segment.connect(self._on_transcript_segment)
        job.done.connect(self._on_transcript_done)
        job.failed.connect(self._on_transcript_failed)
        job.finished.connect(lambda job=job: self._on_transcript_ended(job))
        self._transcript_job = job
        self._transcripts[clip_id] = {"status": "running", "segments": [], "message": ""}
        job.start()
        if clip_id == self.current_id:
            self._push_transcript()
        return True

    def _cancel_transcript(self):
        job, self._transcript_job = self._transcript_job, None
        if job is None:
            return
        job.cancel()
        if self._transcripts.get(job.clip_id, {}).get("status") == "running":
            del self._transcripts[job.clip_id]      # half a transcript isn't one

    def _on_transcript_segment(self, clip_id, segment):
        entry = self._transcripts.get(clip_id)
        if entry is None or entry["status"] != "running":
            return
        entry["segments"].append(segment)
        if clip_id == self.current_id:
            self._sent.pop("transcript", None)   # the page's copy has moved on
            self.emit("transcript_segment", {"id": clip_id, "segment": segment})

    def _on_transcript_done(self, clip_id, transcript):
        self._transcripts[clip_id] = {"status": "done", "segments": transcript["segments"], "message": "",
                                      "language": transcript.get("language", "")}
        if clip_id == self.current_id:
            self._push_transcript()

    def _on_transcript_failed(self, clip_id, message):
        self._transcripts[clip_id] = {"status": "error", "segments": [], "message": message}
        if clip_id == self.current_id:
            self._push_transcript()

    def _on_transcript_ended(self, job):
        if self._transcript_job is job:
            self._transcript_job = None
        job.deleteLater()
        # Then the clip after this one, so it's ready when the tape gets there.
        if not self.settings.get("transcribe", True) or self._transcript_job is not None:
            return
        tape = self._tape_ids()
        if self.current_id in tape:
            for clip_id in tape[tape.index(self.current_id):tape.index(self.current_id) + 2]:
                row = self.catalog["clips"][clip_id]
                if row["type"] in ("Video", "Audio") and clip_id not in self._transcripts:
                    if self._start_transcript(clip_id, quiet=True):
                        return

    def on_transcribe(self, payload):
        on = bool((payload or {}).get("on"))
        self.settings["transcribe"] = on
        self.settings.save()
        if not on:
            self._cancel_transcript()
        else:
            # Turning it back on tries again what couldn't be done before.
            self._transcripts = {clip_id: entry for clip_id, entry in self._transcripts.items()
                                 if entry.get("status") != "error"}
        self._want_transcript()

    def on_copy_transcript(self, _payload):
        """The transcript's text, into the review log."""
        entry = self._transcripts.get(self.current_id) or {}
        text = " ".join((s.get("text") or "").strip() for s in entry.get("segments", [])).strip()
        if text:
            self.emit("transcript_text", {"id": self.current_id, "text": text})

    def _advance(self):
        tape = self._tape_ids()
        if self.current_id not in tape:
            return
        index = tape.index(self.current_id) + 1
        if index >= len(tape):
            completed_playback = self.auto_play
            self._stop()
            self.finished = True
            self.emit("finished", {"count": len(tape), "skipped": len(self.skipped.intersection(tape)),
                                   "applying": completed_playback})
            self._push()
            return
        playing = self.auto_play
        self.finished = False
        self._stop()
        self.current_id = tape[index]
        self.auto_play = playing
        self._show_selected(open_now=playing)

    def on_next(self, _payload):
        self._advance()

    def on_previous(self, _payload):
        tape = self._tape_ids()
        if self.current_id not in tape:
            return
        index = max(0, tape.index(self.current_id) - 1)
        playing = self.auto_play
        self.finished = False
        self._stop()
        self.current_id = tape[index]
        self.auto_play = playing
        self._show_selected(open_now=playing)

    def on_volume(self, payload):
        try:
            self.preview.set_volume(float((payload or {}).get("value", 1)))
        except (TypeError, ValueError):
            pass

    def _on_frame(self, clip_id, src):
        known = self._stills.get(clip_id) == src
        self._remember_still(clip_id, src)
        if clip_id == self.current_id:
            if not known:   # reopening a clip decodes the same still again
                self.emit("frame", {"id": clip_id, "src": src})
            # The page has this clip's own picture now; let go of the last one.
            self._release_hold_once_shown()

    def _on_shape(self, clip_id, width, height):
        """A clip's picture size, upright, from its first frame - the page
        lays a tall picture out beside the controls rather than over them."""
        shape = [int(width), int(height)]
        if self._shapes.get(clip_id) == shape:
            return
        self._shapes[clip_id] = shape
        sent = self._sent.get("current")
        if sent and sent["clip"] and sent["clip"]["id"] == clip_id:
            self._sent["current"] = {**sent, "shape": shape}
            self.emit("shape", {"id": clip_id, "shape": shape})

    def _on_ready(self, clip_id, _kind):
        if clip_id == self.current_id:
            self.ready_id = clip_id
            seek, self._pending_seek = self._pending_seek, None
            if seek is not None and seek[0] == clip_id:
                self.preview.seek_ms(seek[1], clip_id)
            if self.auto_play and not self.preview.is_playing():
                self.preview.play()

    def _on_position(self, position, duration):
        if self.ready_id == self.current_id and self.current_id is not None:
            self.emit("position", {"position": position, "duration": duration})

    def _on_failed(self, clip_id, message):
        if clip_id == self.current_id:
            self._release_hold()
            self.emit("failed", {"id": clip_id, "text": message})
            if self.auto_play:
                self.skipped.add(clip_id)
                self._still_timer.start(2500)

    def _on_ended(self, clip_id, following):
        """A clip played to its end. `following` is the clip the player has
        already gone on into - the next on the tape, waiting on its second
        deck - in which case only the page catches up (_arrive)."""
        if clip_id != self.current_id or not self.auto_play:
            return
        tape = self._tape_ids()
        if (following and clip_id in tape and following in tape
                and tape.index(following) == tape.index(clip_id) + 1):
            self._arrive(following)
            return
        if following:
            self.preview.stop()     # it went on into something the tape doesn't
        self._advance()

    def _arrive(self, clip_id):
        """The player is already playing clip_id: move the page onto it
        without touching the player, and without taking the surface away."""
        self.finished = False
        self._pending_seek = None
        self._still_timer.stop()
        self._prime_timer.stop()
        self.current_id = self.ready_id = self._opened_id = self._surface_clip = clip_id
        self._surface_hold = False
        self._push()
        self._want_transcript()
        # Not straight away: opening the clip after this one decodes a 4K
        # frame too, and doing it while this one spins up made the first
        # quarter-second after every cut uneven.
        QTimer.singleShot(PRELOAD_AFTER_CUT_MS, lambda c=clip_id: c == self.current_id and self._preload_next())

    def on_edit(self, payload):
        if self.catalog is None:
            return
        payload = payload or {}
        clip_id = str(payload.get("id") or "")
        if clip_id not in self.catalog["clips"]:
            return
        old = self._drafts().get(clip_id, {})
        fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else old.get("fields", {})
        draft = {"log": str(payload.get("log", old.get("log", "")))[:20000],
                 "log_edited": bool(payload.get("log_edited", old.get("log_edited", False))),
                 "fields": {key: str(value)[:500] for key, value in fields.items()
                            if key in model.EDITABLE and isinstance(value, str)},
                 "tag": payload.get("tag", old.get("tag")),
                 "color": payload.get("color", old.get("color")), "applied": False}
        if draft["tag"] is not None and str(draft["tag"]) not in model.TAGS:
            return
        if draft["color"] is not None and draft["color"] not in ("", *model.COLORS):
            return
        self._drafts()[clip_id] = draft
        self.settings.save()
        self.emit("draft_saved", {"id": clip_id, "pending": self._pending_count()})
        self._push_tape()       # its colour, on the tape and the strip

    def on_commit(self, _payload):
        if self.catalog is None or self.saving:
            return
        if self._resolve_waiting():
            self._commit_pending = True
            return
        pending = {clip_id: dict(draft) for clip_id, draft in self._drafts().items()
                   if not draft.get("applied") and clip_id in self.catalog["clips"]}
        if not pending:
            self.emit("toast", {"text": "No metadata changes to apply."})
            return
        self.saving = True
        self._push()
        project_id = self.catalog["project_id"]
        controller = self.host.controller
        self._calls.call("dailies.apply", {"expected_project_id": project_id, "drafts": pending},
                   lambda: resolve_ext.apply_by_id(controller, project_id, pending),
                   lambda result, error: self._on_commit_done(project_id, pending, result, error))

    def _on_commit_done(self, project_id, pending, result, error):
        self.saving = self.resolve_busy = False
        if error is not None:
            self.emit("alert", {"title": "Could not apply dailies notes", "text": str(error)})
        elif self.catalog and self.catalog["project_id"] == project_id:
            saved, failures, written = result
            for clip_id in saved:
                if self._drafts().get(clip_id) == pending[clip_id]:
                    self._drafts()[clip_id]["applied"] = True
                if clip_id in written and clip_id in self.catalog["clips"]:
                    self.catalog["clips"][clip_id]["metadata"].update(written[clip_id])
            self.settings.save()
            if failures:
                self.emit("alert", {"title": "Some notes could not be applied", "text": "\n".join(failures[:30])})
            self.emit("toast", {"text": f"Applied metadata to {len(written)} clip{'s' if len(written) != 1 else ''}."
                                 if written else "No metadata changes were needed."})
        self._push()
        if self._refresh_pending:
            self._refresh_pending = False
            self.on_refresh(None, quiet=True)
