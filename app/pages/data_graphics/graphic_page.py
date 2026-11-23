#!/usr/bin/env python3
"""
What Data Graphics' two pages (counters/, graphs/) share: the graphic being made is
kept in the page's settings, the open timeline's size and rate and Resolve's fonts are
read (place.read), the view is sent the graphic with its preview - made from the same
clock the comp's keys are (each page's model) - and Add puts it on the timeline behind
the busy overlay (core/busy_job.py, a job named in core/resolve_reader_child.py JOBS).

A page says what it makes:
    MODEL       its model module: clean(raw) -> a whole, valid graphic
    JOB, here   the job's name in JOBS, and the same work done in Buddy itself
    preview()   what the view draws
    WHAT        "counter" / "graph", for what's said

Every change to the graphic goes through change_to(), one undoable step (a run of the
same change - typing in a field, dragging a bar or a curve - within MERGE_S is one
step): undo and redo walk the steps, reset is a step back to how a new one starts
(MODEL.clean({})). The steps last as long as the page; only the graphic is saved.

Protocol (both pages):
    to the view    state, fonts, toast, alert
    from the view  set, part, option, add, refresh, switch_tab, undo, redo, reset
"""

import os
import time

from core import busy_job
from core.resolve_bridge import ResolveConnectionError
from core.resolve_reader import ResolveCalls
from core.resolve_worker import ResolveWorker
from core.web_page import WebToolPage

from . import easing, parts, place, text_look

WRITE_WAIT_S = 3.0
HISTORY = 200               # undo steps kept
MERGE_S = 1.5               # the same change again within this long is the same step
PREVIEW_FRAME, PREVIEW_FPS = (1920, 1080), 24.0     # until a timeline has been read
MAX_PREVIEW_FPS = 60.0
FALLBACK_FONTS = {"Arial": ["Regular", "Bold", "Italic", "Bold Italic", "Black"]}
WEIGHTS = (("thin", 100), ("hairline", 100), ("extralight", 200), ("ultralight", 200), ("semilight", 350),
           ("demilight", 350), ("light", 300), ("medium", 500), ("semibold", 600), ("demibold", 600),
           ("demi", 600), ("extrabold", 800), ("ultrabold", 800), ("heavy", 800), ("black", 900), ("bold", 700))


def css_face(family, style):
    """How the preview asks the browser for a Text+ face: {"family", "weight", "italic"}."""
    name = str(style or "").lower().replace(" ", "").replace("-", "")
    weight = next((w for word, w in WEIGHTS if word in name), 400)
    return {"family": str(family or "Arial"), "weight": weight, "italic": "italic" in name or "oblique" in name}


def face_metrics(family, style):
    """{"em", "baseline"} of a face as Text+ draws it, or {} when it can't be measured
    here (text_look.clean_metrics has what stands in)."""
    try:
        from pages.text_animator import canvas_math
        return {"em": float(canvas_math._px_per_size(family, style)),
                "baseline": float(canvas_math.baseline(family, style))}
    except Exception:
        return {}


class GraphicPage(WebToolPage):
    category = "Editing Tools"
    MODEL = None
    JOB = ""
    WHAT = "graphic"
    ADDING = "Adding…"

    def on_switch_tab(self, payload):
        self._graphics.show_tab((payload or {}).get("tab"))

    # ------------------------------------------------------------ state --

    def build_state(self):
        self.settings = self.host.tool_settings(self.tool_id, {"spec": {}, "replace": True})
        self.spec = self.MODEL.clean(self.settings.get("spec"))
        self.timeline = ""              # the open timeline's name, "" when there's none
        self.frame, self.fps = PREVIEW_FRAME, PREVIEW_FPS
        self.fonts = {}                 # {family: [styles]} - Resolve's, once read
        self.problem = ""
        self.adding = False
        self.undo_steps, self.redo_steps = [], []       # the graphic as it was before each step / after each undone one
        self._last_change, self._last_at = None, 0.0
        self._worker = ResolveWorker(self)
        self._calls = ResolveCalls(self, self._worker)

    def web_ready(self):
        self._push_fonts()
        self._push_state()

    def on_shown(self):
        self._read(connect=True)

    def on_connection_changed(self, connected):
        if connected and self.isVisible():
            self._read(connect=False)
        elif not connected:
            self.timeline, self.problem = "", "Not connected to Resolve."
            self._push_state()

    def working_on(self):
        return f"Adding a {self.WHAT}" if self.adding else None

    def metrics(self):
        return text_look.clean_metrics(face_metrics(self.spec["font"], self.spec["style"]))

    def preview_fps(self):
        return min(MAX_PREVIEW_FPS, max(1.0, float(self.fps)))

    def preview(self):
        raise NotImplementedError

    def arrivals(self, timings, fps, total):
        """How the parts that arrive do, for the view: {part id: [[alpha, dy in letter
        sizes, scale] per frame until it has arrived]} - only the parts that do."""
        out = {}
        for part, timing in timings.items():
            states = parts.states(timing, fps, total)
            if states:
                out[part] = [list(state) for state in states]
        return out

    def _look(self):
        return {"colour": self.spec["colour"], "outline": self.spec["outline"],
                "outline_colour": self.spec["outline_colour"], "shadow": self.spec["shadow"],
                "outline_w": text_look.OUTLINE_W, "shadow_dist": text_look.SHADOW_DIST,
                "face": css_face(self.spec["font"], self.spec["style"])}

    def _state(self):
        return {"connected": bool(self.timeline), "timeline": self.timeline, "problem": self.problem,
                "frame": list(self.frame), "fps": self.fps, "spec": self.spec, "look": self._look(),
                "preview": self.preview(), "curve_range": [easing.Y_MIN, easing.Y_MAX],
                "lanes": self.MODEL.lanes(self.spec, self.frame), "cap": text_look.CAP, "min_length": parts.MIN_LENGTH,
                "dock": bool(self.settings.get("dock", True)), "replace": bool(self.settings.get("replace", True)),
                "adding": self.adding, "can_undo": bool(self.undo_steps), "can_redo": bool(self.redo_steps),
                "is_template": self.spec == self.MODEL.clean({})}

    def _push_state(self):
        self.emit("state", self._state())

    def _push_fonts(self):
        fonts = dict(self.fonts or FALLBACK_FONTS)
        # The face in use is always in the list, even one this Resolve doesn't have.
        styles = fonts.setdefault(self.spec["font"], [self.spec["style"]])
        if self.spec["style"] not in styles:
            fonts[self.spec["font"]] = styles + [self.spec["style"]]
        self.emit("fonts", {"fonts": fonts})

    def _save(self):
        self.settings["spec"] = self.spec
        self.settings.save()

    # ----------------------------------------------------------- Resolve --

    def _controller(self, connect):
        if connect:
            try:
                return self.host.ensure_connected()
            except ResolveConnectionError as exc:
                self.problem = str(exc)
                return None
        return self.host.controller if getattr(self.host, "connected", False) else None

    def _read(self, connect=False):
        controller = self._controller(connect)
        if controller is None:
            self.timeline = ""
            self.problem = self.problem if connect else "Not connected to Resolve."
            return self._push_state()
        if self._calls.busy():
            return
        args = {"fonts": not self.fonts}
        self._calls.call("data_graphics.read", args, lambda: place.read(controller, **args), self._on_read)

    def _on_read(self, result, error):
        if error is not None:
            self.timeline = ""
            self.problem = (str(error) if isinstance(error, ResolveConnectionError)
                            else f"Couldn't read the timeline: {error}")
            return self._push_state()
        self.timeline = result.get("timeline") or ""
        self.problem = "" if self.timeline else "Open a timeline in Resolve to add this to."
        if self.timeline:
            self.frame, self.fps = (int(result["frame"][0]), int(result["frame"][1])), float(result["fps"])
        if result.get("fonts"):
            self.fonts = result["fonts"]
            self._push_fonts()
        self._push_state()

    # ----------------------------------------------------------- actions --

    def on_refresh(self, _payload=None):
        self._read(connect=True)

    def change(self, key, value):
        """One field of the graphic changed in the view. A font new to the page takes a
        style it has."""
        raw = {**self.spec, key: value}
        if key == "font":
            styles = (self.fonts or FALLBACK_FONTS).get(str(value)) or []
            if styles and raw.get("style") not in styles:
                raw["style"] = next((s for s in ("Bold", "Regular") if s in styles), styles[0])
        return raw

    def change_to(self, spec, merge=None):
        """The graphic becomes `spec` (already clean), as one step Undo can take back.
        merge: what kind of change this is ("set:title", a part dragged...) - the same
        kind again within MERGE_S joins the step before (a word typed, a bar dragged is
        one step, not one a letter or a pixel). The view is sent the state either way,
        so a field being typed in is tidied."""
        now = time.monotonic()
        if spec != self.spec:
            if not (merge and merge == self._last_change and now - self._last_at < MERGE_S and self.undo_steps):
                self.undo_steps.append(self.spec)
                del self.undo_steps[:-HISTORY]
            self.redo_steps.clear()
            self._last_change, self._last_at = merge, now
            self.spec = spec
            self._save()
        self._push_state()

    def on_set(self, payload):
        key = (payload or {}).get("key")
        if key not in self.spec or self.adding:
            return
        self.change_to(self.MODEL.clean(self.change(key, (payload or {}).get("value"))), f"set:{key}")

    def on_part(self, payload):
        """One thing about a part's timing changed in the dock: its "start" or "length"
        (its bar dragged, or typed), its "curve" (dragged) or "easing" (a named curve),
        the "kind" of its arriving - or "reset": it follows the graphic again."""
        payload = payload or {}
        if self.adding:
            return
        spec = self.MODEL.with_part(self.spec, payload.get("id"), payload.get("key"), payload.get("value"), self.frame)
        if spec is None:
            return
        self.change_to(spec, f"part:{payload.get('id')}:{payload.get('key')}")

    def _step(self, back, forth):
        if self.adding or not back:
            return
        forth.append(self.spec)
        self.spec = back.pop()
        self._last_change = None                # what comes next is a step of its own
        self._save()
        self._push_state()

    def on_undo(self, _payload=None):
        """The graphic as it was before the last step (Ctrl+Z)."""
        self._step(self.undo_steps, self.redo_steps)

    def on_redo(self, _payload=None):
        """The step Undo took back, again (Ctrl+Y, Ctrl+Shift+Z)."""
        self._step(self.redo_steps, self.undo_steps)

    def on_reset(self, _payload=None):
        """Back to the template: everything as a new one starts - one step, so Undo
        brings back what there was."""
        if not self.adding:
            self.change_to(self.MODEL.clean({}))

    def on_option(self, payload):
        """"replace": Buddy's earlier graphic on the spot is replaced; "dock": the timing dock is open."""
        if (payload or {}).get("key") in ("replace", "dock"):
            self.settings[payload["key"]] = bool(payload.get("value"))
            self.settings.save()
            self._push_state()

    def ready_to_add(self):
        """"" when the graphic can be added, else what to say instead."""
        return ""

    def on_add(self, _payload=None):
        if self.adding:
            return
        problem = self.ready_to_add()
        if problem:
            return self.emit("toast", {"text": problem})
        controller = self._controller(True)
        if controller is None:
            return self.emit("alert", {"title": "Not connected", "text": self.problem})
        if not self._calls.wait_idle(WRITE_WAIT_S):
            return self.emit("toast", {"text": "Resolve is still busy – try again in a moment."})
        args = {"spec": self.spec, "metrics": self.metrics(), "replace": bool(self.settings.get("replace", True))}
        self.adding = True
        self._push_state()
        busy_job.run(self.host, self._calls, self.JOB, args, lambda: self.here(controller, **args),
                     self._on_added, self.ADDING)

    def here(self, controller, **args):
        raise NotImplementedError

    def _on_added(self, result, error):
        self.adding = False
        if error is not None:      # said, and nothing on the timeline changed
            self._push_state()
            return self.emit("alert", {"title": f"Couldn't add the {self.WHAT}", "text": str(error)})
        self.timeline = result.get("timeline") or self.timeline
        self.frame, self.fps = (int(result["frame"][0]), int(result["frame"][1])), float(result["fps"])
        self.problem = ""
        self._push_state()
        self.emit("toast", {"text": self.added_text(result)})

    def added_text(self, result):
        raise NotImplementedError


def web_dir(page_file):
    return os.path.join(os.path.dirname(os.path.abspath(page_file)), "web")
