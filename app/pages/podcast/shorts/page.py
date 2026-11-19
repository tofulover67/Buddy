#!/usr/bin/env python3
"""
Shorts - one of the Podcast tab's sub-tabs (see pages/podcast/page.py): the
clips selected on the open timeline, made into a new timeline of their own
- a moment of the podcast as a short for YouTube Shorts or Instagram.

The view (web/) shows what's selected in Resolve as it changes - the
sections it falls into, how long they run together - and the short's name
and shape. Make duplicates the timeline and keeps only the selection in the
copy, its sections moved up against each other (resolve_ext.build): every
clip keeps its grade, zoom and titles, and the open timeline isn't touched.

Protocol:
    to the view    state, made, toast, alert
    from the view  refresh, option, build, back, switch_tab
"""

import os

from PySide6.QtCore import QTimer

from core import busy_job
from core.resolve_bridge import ResolveConnectionError
from core.resolve_reader import ResolveCalls
from core.resolve_worker import ResolveWorker
from core.web_page import WebToolPage

from . import model as M
from . import resolve_ext

READ_MS = 1000          # what's selected in Resolve, while this tab is showing
WRITE_WAIT_S = 3.0
MADE_KEPT = 12
DEFAULTS = {"format": "vertical", "separate": False, "open_new": True}
TOGGLES = ("separate", "open_new")
OLD_RESOLVE = "Reading what's selected on the timeline needs DaVinci Resolve 21.0.4 or later."


class ShortsPage(WebToolPage):
    tool_id = "podcast_shorts"
    display_name = "Shorts"
    category = "Podcast"
    web_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")

    def on_switch_tab(self, payload):
        self._podcast.show_tab((payload or {}).get("tab"))

    # ------------------------------------------------------------ state --

    def build_state(self):
        self.settings = self.host.tool_settings(self.tool_id, dict(DEFAULTS))
        self.timeline = None            # resolve_ext.read()'s, or None
        self.problem = "Connecting…"
        self.clips = []                 # model.clean() of what's selected
        self.made = []                  # this session's shorts, newest first
        self.source = None              # {"id", "name"}: the timeline they were made from
        self.building = False
        self._sent = None
        self._worker = ResolveWorker(self)
        self._calls = ResolveCalls(self, self._worker)
        self._read_timer = QTimer(self, interval=READ_MS)
        self._read_timer.timeout.connect(self._tick)
        self._read_timer.start()

    def web_ready(self):
        self._sent = None
        self._push_state()
        self._push_made()

    def on_shown(self):
        self._read(connect=True)

    def on_app_quitting(self):
        self._read_timer.stop()

    def on_connection_changed(self, connected):
        if connected and self.isVisible():
            self._read(connect=False)

    def working_on(self):
        return "Making a short" if self.building else None

    def _state(self):
        tl = self.timeline
        fps = float(tl["fps"]) if tl else 24.0
        parts = M.sections(self.clips)
        seconds = M.length(parts) / fps
        return {
            "connected": tl is not None,
            "problem": self.problem if tl is None else ("" if tl["can_select"] else OLD_RESOLVE),
            "timeline": tl["name"] if tl else "",
            "size": tl["size"] if tl else [0, 0],
            "clips": len(self.clips),
            "sections": [{"start": s["start"] / fps, "end": s["end"] / fps, "clips": len(s["clips"]),
                          "tracks": M.tracks(s["clips"])} for s in parts],
            "seconds": seconds,
            "over": seconds > M.LIMIT_S,
            "longest_over": any((s["end"] - s["start"]) / fps > M.LIMIT_S for s in parts),
            "format": self.settings.get("format") if self.settings.get("format") in M.FORMATS else "vertical",
            "formats": [{"value": key, "label": label, "size": [w, h] if w else None}
                        for key, (label, w, h) in M.FORMATS.items()],
            "separate": bool(self.settings.get("separate")),
            "open_new": bool(self.settings.get("open_new")),
            "default_name": M.default_name(tl["name"]) if tl else "",
            "building": self.building,
        }

    def _push_state(self):
        state = self._state()
        if state != self._sent:
            self._sent = state
            self.emit("state", state)

    def _push_made(self):
        on_source = bool(self.source and self.timeline and self.timeline["id"] == self.source["id"])
        self.emit("made", {"shorts": self.made, "source": self.source["name"] if self.source else "",
                           "can_go_back": bool(self.source) and not on_source})

    # ----------------------------------------------------------- Resolve --

    def _controller(self, connect):
        if connect:
            try:
                return self.host.ensure_connected()
            except ResolveConnectionError as exc:
                self.problem = str(exc)
                return None
        return self.host.controller if getattr(self.host, "connected", False) else None

    def _tick(self):
        if self.isVisible() and not self.building:
            self._read(connect=False)

    def _read(self, connect=False):
        controller = self._controller(connect)
        if controller is None:
            return self._lost(self.problem if connect else "Not connected to Resolve.")
        if self._calls.busy():
            return
        self._calls.call("podshorts.read", {}, lambda: resolve_ext.read(controller), self._on_read)

    def _lost(self, problem):
        self.timeline, self.problem, self.clips = None, problem, []
        self._push_state()

    def _on_read(self, result, error):
        if error is not None:
            return self._lost(str(error) if isinstance(error, ResolveConnectionError)
                              else f"Couldn't read the timeline: {error}")
        switched = self.timeline is None or self.timeline["id"] != result["id"]
        self.timeline, self.problem = result, ""
        self.clips = M.clean(result.get("selection"))
        self._push_state()
        if switched:
            self._push_made()

    # ----------------------------------------------------------- actions --

    def on_refresh(self, _payload=None):
        self._read(connect=True)

    def on_option(self, payload):
        key, value = (payload or {}).get("key"), (payload or {}).get("value")
        if key == "format" and value in M.FORMATS:
            self.settings[key] = value
        elif key in TOGGLES:
            self.settings[key] = bool(value)
        else:
            return
        self.settings.save()
        self._push_state()

    def on_build(self, payload=None):
        if self.building or self.timeline is None:
            return
        if not self.clips:
            return self.emit("toast", {"text": "Select the clips for the short on Resolve's timeline first."})
        controller = self._controller(True)
        if controller is None:
            return self.emit("alert", {"title": "Not connected", "text": self.problem})
        if not self._calls.wait_idle(WRITE_WAIT_S):
            return self.emit("toast", {"text": "Resolve is still busy – try again in a moment."})
        state = self._state()
        several = state["separate"] and len(state["sections"]) > 1
        args = {"clips": [list(c) for c in self.clips], "timeline_id": self.timeline["id"],
                "name": M.clean_name((payload or {}).get("name")), "fmt": state["format"],
                "separate": several, "open_new": state["open_new"]}
        self.building = True
        self._push_state()
        busy_job.run(self.host, self._calls, "podshorts.build", args,
                     lambda: resolve_ext.build_here(controller, **args), self._on_built,
                     "Making the shorts…" if several else "Making the short…")

    def _on_built(self, result, error):
        self.building = False
        self._push_state()
        if error is not None:      # said, and nothing in the open timeline changed
            return self.emit("alert", {"title": "Couldn't make the short", "text": str(error)})
        fps = float(self.timeline["fps"]) if self.timeline else 24.0
        rows = [{"name": m["name"], "seconds": m["frames"] / fps, "clips": m["clips"]} for m in result["made"]]
        self.made = (rows[::-1] + self.made)[:MADE_KEPT]
        self.source = result["source"]
        self._push_made()
        if len(rows) == 1:
            text = (f"“{rows[0]['name']}” is open." if result.get("opened")
                    else f"“{rows[0]['name']}” is in the Media Pool.") + " Your timeline is as it was."
        else:
            text = f"{len(rows)} shorts made. Your timeline is as it was."
        if result["warnings"]:
            self.emit("alert", {"title": "Short made" if len(rows) == 1 else "Shorts made",
                                "text": text + "\n\n" + "\n".join(result["warnings"])})
        else:
            self.emit("toast", {"text": text})
        self._read(connect=False)

    def on_back(self, _payload=None):
        """Back to the timeline the shorts were made from, to pick the next one."""
        if self.source is None or self.building:
            return
        controller = self._controller(True)
        if controller is None:
            return self.emit("alert", {"title": "Not connected", "text": self.problem})
        tid = self.source["id"]

        def done(_result, error):
            if error is not None:
                return self.emit("toast", {"text": str(error), "kind": "error"})
            self._read(connect=False)

        self._calls.wait_idle(WRITE_WAIT_S)
        if not self._calls.call("podshorts.open", {"timeline_id": tid},
                                lambda: resolve_ext.open_timeline(controller, tid), done):
            self.emit("toast", {"text": "Resolve is still busy – try again in a moment."})
