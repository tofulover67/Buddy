#!/usr/bin/env python3
"""
Animation - the page for animating things in Resolve. Its top row of tabs is
the kind of animation: Previews, then Text+.

Previews shows every motion preset (motion.py, motion_presets.json) playing
in a little viewer, grouped by pack, and puts the chosen one on the clips
selected in Resolve's timeline - stills, video, Text+ and other titles -
through their Fusion comps (motion_resolve.py). The clip's Inspector framing
goes into the comp with it (framing.py), so the move is never cut off at the
frame's edge; re-frame in the Inspector and Update framing folds it in.
Remove takes the preset off and puts the framing back.

Animate by plays the preset line by line, word by word or letter by letter
on Text+ clips, a stagger apart in the order chosen (units.py); other clips
move as a whole, as with Clip.

Editor opens any preset as keys on a spline editor (motion.keys_of) with a
viewer that plays exactly what's drawn; the edit can go straight on the
selected clips, or be saved as a preset of the person's own
(motion.from_keys) - those are the Saved pack, at the foot of Previews.
Favorites is every preset that's been hearted, with Previews' apply card.
Each pack on Previews folds away; at first only the top one is open, and
which are open is kept (open_packs).

Presets puts a word highlight on Text+ captions (highlight.py,
highlight_resolve.py): the word being said gets a box and a colour and
pops, timed by the word timings Transcribe keeps for every transcript -
evenly where no transcript matches. With one caption selected, its words
can be timed by hand on a strip (dragged, or set to Resolve's playhead);
that timing stays with the clip. The other Text+ tools (font styling,
placement, word layouts) are tabs on Subtitles, right after the Subtitle
Conversion that makes the Text+ (see text_plus.py, which that page hosts).

Zoom plays the one video clip or still selected in Resolve right here
(zoom_player.py: its own stretch of its file), with its zooms on a track
under it to drag about and a framing to drag on the picture; the page
draws each zoom as Resolve will, and Apply puts them on the clip
(zoom.py, zoom_resolve.py). They come back from the clip for editing.

Reading the selection never freezes Buddy: the polls go through a
ResolveReader (core/resolve_reader.py), a process of its own, since while
the timeline plays Resolve holds every call until playback stops, and a
call made inside Buddy holds Python's GIL - every window - all that time.
A read still waiting after BUSY_AFTER_S says Resolve is busy, and an Apply
asked for then is turned away rather than freezing Buddy too.

It was the Text Animator page, and keeps that tool id so the sidebar
arrangement and saved settings carry over. Its own choices are saved under
"animation_previews".

Protocol:
    to the view    presets, state, keys, saved, toast, alert, hl_playhead,
                   zm_frame, zm_time, zm_ready, zm_failed, zm_pasted, zm_grade
    from the view  tab, choose, way, speed, direction, unit, order, stagger, play, refresh,
                   apply, update, remove, keys, favorite, save, delete,
                   apply_draft, color, open_packs, hl_option, hl_apply, hl_remove,
                   hl_timing, hl_playhead, zm_toggle, zm_seek, zm_apply, zm_copy, zm_paste,
                   zm_paste_many, zm_grade, tr_option, tr_apply, tr_remove
"""

import base64
import os
import re
import tempfile
import uuid
from pathlib import Path

from PySide6.QtCore import QTimer

from core import tool_dirs
from core.resolve_bridge import ResolveConnectionError
from core.resolve_reader import ResolveReader
from core.resolve_worker import ResolveWorker
from core.web_page import WebToolPage

from .titles import studio as titles_studio

from . import (highlight, highlight_resolve, motion, motion_resolve, transition, transition_resolve, units, zoom,
               zoom_resolve)

POLL_MS = 1500
BUSY_AFTER_S = 3.0          # a read waiting this long: Resolve is busy (most often, playing)
ACTION_WAIT_S = 1.0         # an action behind a read waiting longer than this is turned away
BUSY_TITLE = "Resolve is busy"
BUSY_TEXT = ("Resolve is holding Buddy's requests – usually because the timeline is playing. "
             "Stop playback and try again.")
SETTINGS_ID = "animation_previews"
DEFAULTS = {"tab": "previews", "chosen": "pop", "way": "both", "speed": 1.0, "direction": "own", "play": "hover",
            "unit": units.CLIP, "order": units.FORWARD, "stagger": units.DEFAULT_STAGGER,
            "favorites": [], "saved": [], "colors": {}, "open_packs": None, "highlight": {},
            "zoom_clipboard": None, "transition": {}}
TABS = ("previews", "editor", "favorites", "textplus", "titles", "zoom", "transitions")
APPLY_TABS = ("previews", "editor", "favorites")     # the tabs that read the selection for a preset
HIGHLIGHT = "textplus"              # ...and the one that reads it for the highlight (Presets)
ZOOM = "zoom"                       # ...and the one that reads it for the zooms
TRANSITIONS = "transitions"         # ...and the one that reads it for the cuts
TITLES = "titles"                   # Dynamic Titles: reads the timeline's size, not the selection
POLLED = APPLY_TABS + (HIGHLIGHT, ZOOM, TRANSITIONS)
# Where Transcribe keeps each run's word timings (pages/transcribe/jobs.TRANSCRIPTS_DIR).
TRANSCRIPTS = Path(tool_dirs.buddy_dir()) / "transcribe" / "transcripts"
MAX_SAVED = 200
DRAFT = "draft"                     # the Editor's unsaved edit, put on clips as it is
PLAY = ("all", "hover")
PLAY_MOVED = "_play_hover"          # set once an older "all" has been moved to the new default
_HEX = re.compile(r"#[0-9a-fA-F]{6}")

_KIND_WORDS = {
    motion_resolve.IMAGE: ("image", "images"),
    motion_resolve.VIDEO: ("video clip", "video clips"),
    motion_resolve.TITLE: ("title", "titles"),
    motion_resolve.OTHER: ("other clip", "other clips"),
    motion_resolve.NOT_VIDEO: ("audio or subtitle clip", "audio or subtitle clips"),
}


def move_play(settings):
    """Play's default became On hover (2026-10). A settings file keeps every
    default it was saved with, so an older one says "all" though hardly
    anyone chose it: it's moved over once, and a choice made after stays."""
    if settings.get(PLAY_MOVED):
        return
    settings["play"] = "hover"
    settings[PLAY_MOVED] = True
    settings.save()


def _count(n, kind):
    one, many = _KIND_WORDS[kind]
    return f"{n} {one if n == 1 else many}"


def on_clips(label, applied, whole=0):
    """What to say once a preset is on `applied` clips - `whole` of them
    moving as a whole with Animate by on, because they aren't Text+. Whole
    sentences, so each is translated as one."""
    if not whole:
        return f"{label} is on 1 clip." if applied == 1 else f"{label} is on {applied} clips."
    if applied == 1:
        return f"{label} is on 1 clip – it isn't a Text+, so it moves as a whole."
    if whole >= applied:
        return f"{label} is on {applied} clips – none of them is a Text+, so they move as a whole."
    if whole == 1:
        return f"{label} is on {applied} clips – 1 isn't a Text+, so it moves as a whole."
    return f"{label} is on {applied} clips – {whole} aren't Text+, so they move as a whole."


def held_note(held):
    """Why `held` Text+ clips moved as a whole with Animate by on: their words
    are on a modifier a text Follower would replace (motion_resolve.HELD)."""
    if held == 1:
        return ("1 Text+ has character styling or another modifier on its text, which Animate by would replace – "
                "so it moves as a whole.")
    return (f"{held} Text+ clips have character styling or another modifier on their text, which Animate by "
            "would replace – so they move as a whole.")


def apply_summary(preset, result, error):
    """(text, ok) for an Apply from Command Center's hot key."""
    if error is not None:
        return f"Couldn't apply {preset['label']}: {error}", False
    applied, failed = result["applied"], result["failed"]
    skipped = sum(result["skipped"].values())
    if not applied and not failed:
        return ("Only audio or subtitle clips are selected – they can't take a motion preset." if skipped
                else "Select the clips to animate in Resolve's timeline first."), False
    if failed:   # whole sentences, so each is translated as one
        return f"{preset['label']} is on {applied} of {applied + len(failed)} clips – the rest couldn't take it.", False
    if result.get("held"):
        # One line is all the hot key shows: the one that says what was different.
        return held_note(result["held"]), True
    return on_clips(preset["label"], applied, result.get("whole", 0)), True


def highlight_summary(result):
    """(toast, alert or None) once Highlight has run (highlight_resolve.run_apply's
    result) - the alert when some clips' words can't pop. Each a whole
    sentence, with only numbers changing, so each is translated as one."""
    applied, spoken, even, popless = result["applied"], result["spoken"], result["even"], result["popless"]
    hand = result.get("hand", 0)
    if not applied:
        if result["failed"]:
            return "None of the selected captions could take the highlight.", None
        if result["other"]:
            return "None of the selected clips is a Text+ – the highlight goes on Text+ captions.", None
        return "Select the Text+ captions to highlight in Resolve's timeline first.", None
    if hand:
        if hand >= applied:
            said = ("The highlight is on 1 clip, with the timing you set." if applied == 1
                    else f"The highlight is on {applied} clips, with the timing you set.")
        else:
            said = (f"The highlight is on {applied} clips – {hand} with the timing you set, "
                    f"{applied - hand} timed automatically.")
    elif not even:
        said = ("The highlight is on 1 clip, timed from the transcript." if applied == 1
                else f"The highlight is on {applied} clips, timed from the transcript.")
    elif not spoken:
        said = ("The highlight is on 1 clip. No transcript matched it, so its words share the clip's time evenly."
                if applied == 1 else
                f"The highlight is on {applied} clips. No transcript matched them, so their words share each "
                "clip's time evenly.")
    else:
        said = (f"The highlight is on {applied} clips – {spoken} timed from the transcript, {even} evenly "
                "(no transcript matched them).")
    alert = None
    if popless:
        # Animate by, a Subtitles letter animation, Character Level Styling or a
        # Follower of the person's own: a Text+'s text takes one of them.
        alert = ("1 of the clips already has an animation or styling on its text, and a Text+'s text takes one: "
                 "its words get the highlight but don't pop." if popless == 1 else
                 f"{popless} of the clips already have an animation or styling on their text, and a Text+'s text "
                 "takes one: their words get the highlight but don't pop.")
    return said, alert


class AnimationPage(WebToolPage):
    tool_id = "text_animator"
    display_name = "Animation"
    category = "Editing Tools"
    web_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")

    def build_state(self):
        self.settings = self.host.tool_settings(SETTINGS_ID, DEFAULTS)
        move_play(self.settings)
        self.builtin = motion.load()
        self._keys = {}             # preset id -> motion.keys_of, fitted the first time the Editor asks
        self._load_saved()
        if self.settings.get("chosen") not in self._by_id:
            self.settings["chosen"] = self.presets[0]["id"]
        self.selection = None       # motion_resolve.summary(), or None before the first read
        self.hl_selection = None    # highlight_resolve.summary(), on Presets
        self.zm_selection = None    # zoom_resolve.summary(), on Zoom
        self.tr_selection = None    # transition_resolve.summary(), on Transitions
        self._player = None         # zoom_player.ClipPlayer, made the first time Zoom shows a clip
        self.titles = titles_studio.Studio(self)    # the Titles tab (titles/studio.py)
        self.problem = ""
        self.working = ""           # "apply" / "remove" while a job runs
        self._queued = None         # (job, done) waiting for the worker to be free
        self._worker = ResolveWorker(self)
        self._reader = ResolveReader(self)  # the polls' reads, in a process of its own
        self.resolve_busy = False   # a read has waited past BUSY_AFTER_S
        self._poll = QTimer(self)
        self._poll.setInterval(POLL_MS)
        self._poll.timeout.connect(self._poll_selection)
        self._poll.start()

    def _load_saved(self):
        """The person's own presets (settings "saved") after the built-in ones.
        One that won't read is dropped rather than stopping the page."""
        self.saved = []
        for entry in self.settings.get("saved") or []:
            try:
                self.saved.append(motion.from_keys(entry["id"], entry["label"], entry["kind"],
                                                   entry["dur"], entry["keys"],
                                                   extras={k: entry.get(k) for k in motion.EXTRAS}))
            except (KeyError, TypeError, ValueError):
                continue
        self.presets = self.builtin + self.saved
        self._by_id = {p["id"]: p for p in self.presets}

    def working_on(self):
        """A change going into the timeline (not a playhead read)."""
        if not self.working or self.working == "playhead":
            return None
        return {"apply": "Applying the animation", "update": "Updating the animation",
                "remove": "Removing the animation", "title": "Placing the title"}.get(self.working, "Changing the timeline")

    def web_ready(self):
        self._send_presets()
        self._push_state()
        self.titles.view(gallery=True)
        if self.settings.get("tab") == TITLES:
            self._read_titles_timeline()

    def _send_presets(self):
        self.emit("presets", {
            "packs": motion.PACKS + ([motion.SAVED] if self.saved else []),
            "kinds": motion.KINDS,
            "presets": [motion.view(p) for p in self.presets],
        })

    def on_shown(self):
        self._read_selection()
        if self.settings.get("tab") == TITLES:
            self._read_titles_timeline()        # another timeline may be open by now

    def on_connection_changed(self, connected):
        if not connected:
            self.selection = self.hl_selection = self.zm_selection = self.tr_selection = None
        self._push_state()
        if connected:
            self._read_selection()

    def on_app_quitting(self):
        self._poll.stop()
        self.titles.close()
        self._reader.stop()
        if self._player is not None:
            self._player.shutdown()

    def hideEvent(self, event):
        # Buddy lives on in the tray: nothing may keep playing out of sight.
        if self._player is not None:
            self._player.pause()
        super().hideEvent(event)

    # ------------------------------------------------------------ Resolve --

    def _controller(self, connect=False):
        if connect:
            try:
                return self.host.ensure_connected()
            except ResolveConnectionError as exc:
                self.problem = str(exc)
                return None
        return self.host.controller if getattr(self.host, "connected", False) else None

    def _poll_selection(self):
        if self.isVisible() and self.settings.get("tab") in POLLED:
            self._read_selection()
        busy = self._reader.waiting_for() > BUSY_AFTER_S
        if busy != self.resolve_busy:
            self.resolve_busy = busy
            self._push_state()

    def _read_selection(self):
        controller = self._controller()
        if controller is None or self._worker.busy() or self._queued or self.working:
            return
        tab = self.settings.get("tab")
        if tab == HIGHLIGHT:
            name, args, done = "highlight.summary", {"transcripts_dir": str(TRANSCRIPTS)}, self._on_hl_selection
            job = lambda: highlight_resolve.summary(controller, TRANSCRIPTS)
        elif tab == ZOOM:
            name, args, done, job = "zoom.summary", {}, self._on_zm_selection, lambda: zoom_resolve.summary(controller)
        elif tab == TRANSITIONS:
            name, args, done = "transition.summary", {}, self._on_tr_selection
            job = lambda: transition_resolve.summary(controller)
        else:
            name, args, done, job = "animation.summary", {}, self._on_selection, lambda: motion_resolve.summary(controller)
        if self._reader.busy():
            return                  # still waiting on Resolve: no second read behind it
        if self._reader.available() and self._reader.ask(name, args, lambda r, e: self._read_back(done, r, e)):
            return
        self._run(job, done)        # no reader here: the old way (freezes while Resolve plays)

    def _read_back(self, done, result, error):
        """A reader's answer, as the worker's would be: its errors as the
        page knows them. A reader that couldn't start leaves the next poll to
        read the old way."""
        if self.resolve_busy:
            self.resolve_busy = False
        if error is not None and getattr(error, "kind", "") == "SelectionUnavailable":
            error = motion_resolve.SelectionUnavailable(str(error))
        if error is not None and getattr(error, "kind", "") == "Ended" and not self._reader.available():
            self._next()
            return self._push_state()
        try:
            done(result, error)
        finally:
            self._next()

    def _on_hl_selection(self, result, error):
        if isinstance(error, motion_resolve.SelectionUnavailable):
            result, self.problem = None, str(error)
        elif error is not None:
            result, self.problem = None, f"Couldn't read the selection: {error}"
        else:
            self.problem = ""
        if result != self.hl_selection or error is not None:
            self.hl_selection = result
            self._push_state()

    def _run(self, job, done):
        """Starts a worker job; when it's over, whatever is queued behind it."""
        def finished(result, error):
            try:
                done(result, error)
            finally:
                self._next()
        return self._worker.start(job, finished)

    def _next(self):
        if self._queued is None or self._worker.busy() or self._reader.busy():
            return
        job, done = self._queued
        self._queued = None
        if not self._run(job, done):
            self._queued = (job, done)

    def _on_selection(self, result, error):
        if isinstance(error, motion_resolve.SelectionUnavailable):
            result, self.problem = None, str(error)
        elif error is not None:
            result, self.problem = None, f"Couldn't read the selection: {error}"
        else:
            self.problem = ""
        if result != self.selection or error is not None:
            self.selection = result
            self._push_state()

    # --------------------------------------------------------------- view --

    def _push_state(self):
        self.emit("state", {
            "tab": self.settings.get("tab"),
            "chosen": self.settings.get("chosen"),
            "way": self.settings.get("way"),
            "speed": self.settings.get("speed"),
            "direction": self._direction(),
            **self._units(),
            "play": self.settings.get("play"),
            "favorites": [i for i in self.settings.get("favorites") or [] if i in self._by_id],
            "colors": self._colors(),
            "open_packs": self._open_packs(),
            "connected": self._controller() is not None,
            "selection": self._selection_view(),
            "shape": motion_resolve.preview_shape(self.selection["counts"] if self.selection else {}),
            "problem": self.problem,
            "working": self.working,
            "hl": self._hl(),
            "hl_selection": self.hl_selection,
            "zm_selection": self.zm_selection,
            "zm_clipboard": self._zm_clipboard(),
            "tr": transition.clean(self.settings.get("transition")),
            "tr_selection": self.tr_selection,
            "resolve_busy": self.resolve_busy,
        })

    def _selection_view(self):
        s = self.selection
        if not s:
            return None
        counts = s["counts"]
        can = sum(n for k, n in counts.items() if k in motion_resolve.ANIMATABLE)
        return {"timeline": s["timeline"], "fps": s.get("fps", 24.0), "total": s["total"], "animatable": can,
                "animated": s["animated"], "reframed": s.get("reframed", 0), "clip": s.get("clip"),
                "parts": [{"text": _count(counts[k], k), "kind": k} for k in _KIND_WORDS if counts.get(k)]}

    def _hl(self):
        """Highlight's choices (highlight.clean_options)."""
        return highlight.clean_options(self.settings.get("highlight"))

    def _direction(self):
        direction = self.settings.get("direction")
        return direction if direction in motion.DIRECTIONS else "own"

    def _units(self):
        """Animate by's choices: {"unit", "order", "stagger"}."""
        return units.clean_options({k: self.settings.get(k) for k in ("unit", "order", "stagger")})

    def _open_packs(self):
        """The packs open on Previews, by id, or None before any is opened or
        closed - then only the top one is."""
        kept = self.settings.get("open_packs")
        if not isinstance(kept, list):
            return None
        known = {p["id"] for p in motion.PACKS} | {motion.SAVED["id"]}
        return [p for p in dict.fromkeys(kept) if isinstance(p, str) and p in known]

    def _colors(self):
        """The colours picked for the Editor's channels, by channel."""
        colors = self.settings.get("colors") or {}
        return {c: v.lower() for c, v in colors.items()
                if c in motion.CHANNELS and isinstance(v, str) and _HEX.fullmatch(v)}

    def _save(self, key, value):
        self.settings[key] = value
        self.settings.save()
        self._push_state()

    # ------------------------------------------------------------ actions --

    def on_tab(self, payload):
        tab = (payload or {}).get("tab")
        if tab in TABS:
            if tab != ZOOM and self._player is not None:
                self._player.pause()
            self._save("tab", tab)
            if tab in POLLED:
                self._read_selection()
            elif tab == TITLES:
                self._read_titles_timeline()

    def on_choose(self, payload):
        pid = (payload or {}).get("id")
        if pid not in self._by_id:
            return
        # The Text pack's presets are made to play by letter or word: choosing
        # one sets Animate by to the way it's made for.
        preset = self._by_id[pid]
        made = units.clean_options({"unit": preset.get("unit"), "order": self.settings.get("order"),
                                    "stagger": preset.get("stagger")})
        if preset.get("unit") in units.UNITS:
            if not isinstance(self.settings.get("unit_own"), dict):
                # The person's own choice, kept for the next preset that has none.
                self.settings["unit_own"] = {"unit": self.settings.get("unit"),
                                             "stagger": self.settings.get("stagger")}
            self.settings["unit"], self.settings["stagger"] = made["unit"], made["stagger"]
        elif isinstance(self.settings.get("unit_own"), dict):
            own = units.clean_options({**self._units(), **self.settings.values.pop("unit_own")})
            self.settings["unit"], self.settings["stagger"] = own["unit"], own["stagger"]
        self._save("chosen", pid)

    def on_open_packs(self, payload):
        packs = (payload or {}).get("packs")
        if isinstance(packs, list):
            self.settings["open_packs"] = packs
            self._save("open_packs", self._open_packs())

    def on_way(self, payload):
        way = (payload or {}).get("way")
        if way in motion.WAYS:
            self._save("way", way)

    def on_speed(self, payload):
        try:
            speed = float((payload or {}).get("speed"))
        except (TypeError, ValueError):
            return
        if speed in motion.SPEEDS:
            self._save("speed", speed)

    def on_direction(self, payload):
        direction = (payload or {}).get("direction")
        if direction in motion.DIRECTIONS:
            self._save("direction", direction)

    def on_unit(self, payload):
        unit = (payload or {}).get("unit")
        if unit in units.UNITS:
            self.settings.values.pop("unit_own", None)   # chosen by hand: it stays, preset or not
            self._save("unit", unit)

    def on_order(self, payload):
        order = (payload or {}).get("order")
        if order in units.ORDERS:
            self._save("order", order)

    def on_stagger(self, payload):
        try:
            stagger = float((payload or {}).get("stagger"))
        except (TypeError, ValueError):
            return
        if stagger in units.STAGGERS:
            self._save("stagger", stagger)

    def on_play(self, payload):
        play = (payload or {}).get("play")
        if play in PLAY:
            self._save("play", play)

    def on_refresh(self, _payload=None):
        if self._controller(connect=True) is None:
            self.emit("alert", {"title": "Can't reach Resolve", "text": self.problem})
            return self._push_state()
        self._read_selection()

    def _start(self, what, job, done):
        """Runs an Apply / Update / Remove on the worker. While it's busy (most
        often the selection poll) the job waits its turn rather than holding
        the UI thread up for it."""
        if self.working:
            return
        controller = self._controller(connect=True)
        if controller is None:
            self.emit("alert", {"title": "Can't reach Resolve", "text": self.problem})
            return self._push_state()
        if self._reader.busy() and self._reader.waiting_for() > ACTION_WAIT_S:
            # Resolve is holding the read (playing): a call from here would
            # freeze Buddy until it stops.
            self.resolve_busy = True
            self._push_state()
            return self.emit("alert", {"title": BUSY_TITLE, "text": BUSY_TEXT})
        self.working = what
        self._push_state()
        task = (lambda: job(controller), done)
        if self._reader.busy():
            self._queued = task         # the read in hand first: it'll be back in a moment
        elif self._worker.busy():
            self._queued = task
            if self._worker.running_for() > 2:
                self.emit("toast", {"text": "Resolve is busy – this starts as soon as it's free."})
        elif not self._run(*task):
            self._queued = task

    def on_apply(self, _payload=None):
        self._apply(self._by_id[self.settings.get("chosen")])

    # ------------------------------------------- Command Center's hot keys --

    def hotkey_presets(self):
        """[(id, label)] for Command Center's Animation action: favourites
        first, then the rest in Previews' order."""
        favorites = [i for i in self.settings.get("favorites") or [] if i in self._by_id]
        rest = [p["id"] for p in self.presets if p["id"] not in favorites]
        return [(i, self._by_id[i]["label"]) for i in favorites + rest]

    def apply_for_hotkey(self, preset_id, way, done):
        """Command Center: puts preset_id on the selected clips, as Apply
        does, through this page's own worker (so its jobs never overlap).
        done(text, ok) says how it went - this tab may not be on screen."""
        preset = self._by_id.get(preset_id)
        if preset is None:
            return done("That Animation preset is gone – pick another in Command Center.", False)
        if self.working:
            return done("Animation is busy with another change – try again in a moment.", False)
        if self._controller(connect=True) is None:
            return done(self.problem or "Can't reach Resolve.", False)
        if self._reader.busy() and self._reader.waiting_for() > ACTION_WAIT_S:
            return done(BUSY_TEXT, False)      # _start would turn it away without a word to the hot key
        way = way if way in ("both", "in", "out") else (self.settings.get("way") or "both")
        speed, direction = float(self.settings.get("speed") or 1), self._direction()

        def plan_for(frames, fps, at):
            return motion.plan(preset, frames, fps, way=way, speed=speed, at=at, direction=direction)

        options = {"way": way, "speed": speed, "direction": direction, **self._units()}
        if preset.get("keys"):
            options["preset"] = motion.stored(preset)

        def finished(result, error):
            self.working = ""
            self._push_state()
            if error is None:
                self._read_after()
            done(*apply_summary(preset, result, error))

        self._start("apply", lambda c: motion_resolve.run_apply(c, preset, plan_for, options=options), finished)

    def _apply(self, preset):
        way, speed = self.settings.get("way"), float(self.settings.get("speed") or 1)
        direction = self._direction()

        def plan_for(frames, fps, at):
            return motion.plan(preset, frames, fps, way=way, speed=speed, at=at, direction=direction)

        options = {"way": way, "speed": speed, "direction": direction, **self._units()}
        if preset.get("keys"):
            # Drawn in the Editor: the keys go with the clip, so Update framing
            # still has them once the preset is changed or deleted.
            options["preset"] = motion.stored(preset)
        self._start("apply", lambda c: motion_resolve.run_apply(c, preset, plan_for, options=options),
                    lambda result, error: self._done_apply(preset, result, error))

    def _done_apply(self, preset, result, error):
        self.working = ""
        if error is not None:
            self._push_state()
            return self.emit("alert", {"title": "Couldn't apply the preset", "text": str(error)})
        applied, failed = result["applied"], result["failed"]
        skipped = sum(result["skipped"].values())
        if not applied and not failed:
            text = ("Only audio or subtitle clips are selected – they can't take a motion preset."
                    if skipped else "Select the clips to animate in Resolve's timeline first.")
            self.emit("toast", {"text": text})
        elif skipped:
            self.emit("toast", {"text": f"{preset['label']} is on {applied} of {applied + skipped + len(failed)} "
                                        "clips – audio and subtitle clips can't move."})
        else:
            whole = result.get("whole", 0) - result.get("held", 0)      # the held ones get their own note below
            self.emit("toast", {"text": on_clips(preset["label"], applied, whole)})
        if failed:
            self.emit("alert", {"title": "Some clips weren't animated",
                                "text": "\n".join(f"{name}: {why}" for name, why in failed)})
        if result.get("held"):          # as well as, not instead of: the toast no longer counts them
            self.emit("alert", {"title": "Some clips move as a whole", "text": held_note(result["held"])})
        self._read_after()

    def on_update(self, _payload=None):
        def plan_for_preset(preset_id, options):
            preset = self._by_id.get(preset_id)
            if preset is None and options.get("preset"):
                try:
                    kept = options["preset"]
                    preset = motion.from_keys(preset_id, kept["label"], kept["kind"], kept["dur"], kept["keys"],
                                              extras={k: kept.get(k) for k in motion.EXTRAS})
                except (KeyError, TypeError, ValueError):
                    preset = None
            if preset is None:
                return None
            way, speed = options.get("way") or "both", float(options.get("speed") or 1)
            direction = options.get("direction") if options.get("direction") in motion.DIRECTIONS else "own"
            return lambda frames, fps, at: motion.plan(preset, frames, fps, way=way, speed=speed, at=at,
                                                       direction=direction)

        self._start("update", lambda c: motion_resolve.run_update(c, plan_for_preset), self._done_update)

    def _done_update(self, result, error):
        self.working = ""
        if error is not None:
            self._push_state()
            return self.emit("alert", {"title": "Couldn't update the framing", "text": str(error)})
        updated, failed = result["updated"], result["failed"]
        if not updated and not failed:
            text = "None of the selected clips has a Buddy preset."
        else:
            text = "Updated the framing on 1 clip." if updated == 1 else f"Updated the framing on {updated} clips."
        self.emit("toast", {"text": text})
        if failed:
            self.emit("alert", {"title": "Some clips weren't updated",
                                "text": "\n".join(f"{name}: {why}" for name, why in failed)})
        self._read_after()

    def on_remove(self, _payload=None):
        self._start("remove", motion_resolve.run_remove, self._done_remove)

    def _done_remove(self, result, error):
        self.working = ""
        if error is not None:
            self._push_state()
            return self.emit("alert", {"title": "Couldn't remove the preset", "text": str(error)})
        removed, failed = result["removed"], result["failed"]
        if not removed:
            text = "None of the selected clips has a Buddy preset."
        else:
            text = "Took the preset off 1 clip." if removed == 1 else f"Took the preset off {removed} clips."
        self.emit("toast", {"text": text})
        if failed:
            self.emit("alert", {"title": "Some clips kept their preset",
                                "text": "\n".join(f"{name}: {why}" for name, why in failed)})
        self._read_after()

    def _read_after(self):
        self._push_state()
        QTimer.singleShot(0, self._read_selection)

    # ------------------------------------------------------------- Editor --

    def on_keys(self, payload):
        """The Editor opens a preset: its keys (fitted once, then kept)."""
        pid = (payload or {}).get("id")
        preset = self._by_id.get(pid)
        if preset is None:
            return
        if pid not in self._keys:
            self._keys[pid] = motion.keys_of(preset)
        self.emit("keys", {"id": pid, "keys": self._keys[pid]})

    def _draft(self, payload, pid=DRAFT):
        payload = payload or {}
        return motion.from_keys(pid, str(payload.get("label") or "").strip() or "Untitled",
                                payload.get("kind"), payload.get("dur"), payload.get("keys"),
                                extras={k: payload.get(k) for k in motion.EXTRAS})

    def on_apply_draft(self, payload):
        """The Editor's edit on the selected clips, saved or not."""
        try:
            preset = self._draft(payload)
        except (TypeError, ValueError, AttributeError):
            return self.emit("toast", {"text": "That edit couldn't be read."})
        self._apply(preset)

    def on_save(self, payload):
        """Saves the Editor's edit: over a preset of the person's own ("id"),
        or as a new one. The new or updated preset is chosen."""
        payload = payload or {}
        pid = payload.get("id")
        saved = list(self.settings.get("saved") or [])
        if pid not in {e.get("id") for e in saved}:
            if len(saved) >= MAX_SAVED:
                return self.emit("toast", {"text": f"There are {MAX_SAVED} saved presets already – delete one first."})
            pid = f"saved-{uuid.uuid4().hex[:10]}"
        try:
            preset = self._draft(payload, pid)
        except (TypeError, ValueError, AttributeError):
            return self.emit("toast", {"text": "That edit couldn't be read."})
        entry = motion.stored(preset)
        if any(e.get("id") == pid for e in saved):
            saved = [entry if e.get("id") == pid else e for e in saved]
        else:
            saved.append(entry)
        self.settings["saved"] = saved
        self.settings["chosen"] = pid
        self.settings.save()
        self._keys.pop(pid, None)
        self._load_saved()
        self._send_presets()
        self._push_state()
        self.emit("saved", {"id": pid})
        self.emit("toast", {"text": f"Saved {preset['label']} to your presets."})

    def on_delete(self, payload):
        """Deletes a preset of the person's own (built-in ones stay)."""
        pid = (payload or {}).get("id")
        saved = list(self.settings.get("saved") or [])
        gone = next((e for e in saved if e.get("id") == pid), None)
        if gone is None:
            return
        self.settings["saved"] = [e for e in saved if e.get("id") != pid]
        self.settings["favorites"] = [i for i in self.settings.get("favorites") or [] if i != pid]
        self._keys.pop(pid, None)
        self._load_saved()
        if self.settings.get("chosen") not in self._by_id:
            self.settings["chosen"] = self.presets[0]["id"]
        self.settings.save()
        self._send_presets()
        self._push_state()
        self.emit("toast", {"text": f"Deleted {gone.get('label') or 'the preset'}."})

    def on_favorite(self, payload):
        """Hearts a preset (on) or takes the heart off."""
        payload = payload or {}
        pid = payload.get("id")
        if pid not in self._by_id:
            return
        favorites = [i for i in self.settings.get("favorites") or [] if i != pid]
        if payload.get("on"):
            favorites.append(pid)
        self._save("favorites", favorites)

    def on_color(self, payload):
        """An Editor channel's colour (#rrggbb), or none: back to its own."""
        payload = payload or {}
        channel, color = payload.get("channel"), payload.get("color")
        if channel not in motion.CHANNELS or not (color is None or isinstance(color, str) and _HEX.fullmatch(color)):
            return
        colors = self._colors()
        if color:
            colors[channel] = color.lower()
        else:
            colors.pop(channel, None)
        self._save("colors", colors)

    # ------------------------------------------------------------ Presets --

    def on_hl_option(self, payload):
        """One of Highlight's choices; anything that isn't one is dropped."""
        payload = payload or {}
        key, value = payload.get("key"), payload.get("value")
        if key not in highlight.DEFAULTS or not isinstance(value, str):
            return
        cleaned = highlight.clean_options({**self._hl(), key: value})
        if cleaned[key] != value.lower():
            return
        self._save("highlight", cleaned)

    def on_hl_apply(self, _payload=None):
        options = self._hl()
        self._start("highlight", lambda c: highlight_resolve.run_apply(c, options, TRANSCRIPTS),
                    self._done_highlight)

    def _done_highlight(self, result, error):
        self.working = ""
        if error is not None:
            self._push_state()
            return self.emit("alert", {"title": "Couldn't highlight the captions", "text": str(error)})
        said, popless = highlight_summary(result)
        self.emit("toast", {"text": said})
        if popless:
            self.emit("alert", {"title": "Some words don't pop", "text": popless})
        if result["failed"]:
            self.emit("alert", {"title": "Some clips weren't highlighted",
                                "text": "\n".join(f"{name}: {why}" for name, why in result["failed"])})
        self._read_after()

    def on_hl_refresh(self, _payload=None):
        """Measure again: the selected clips' highlights, each with its own options
        and timing, for the words, size and position they have now."""
        self._start("highlight", lambda c: highlight_resolve.run_apply(c, None, TRANSCRIPTS),
                    self._done_highlight)

    def on_hl_remove(self, _payload=None):
        self._start("unhighlight", highlight_resolve.run_remove, self._done_unhighlight)

    def _done_unhighlight(self, result, error):
        self.working = ""
        if error is not None:
            self._push_state()
            return self.emit("alert", {"title": "Couldn't remove the highlight", "text": str(error)})
        removed = result["removed"]
        self.emit("toast", {"text": "None of the selected clips has a highlight." if not removed
                            else "Took the highlight off 1 clip." if removed == 1
                            else f"Took the highlight off {removed} clips."})
        if result["failed"]:
            self.emit("alert", {"title": "Some clips kept their highlight",
                                "text": "\n".join(f"{name}: {why}" for name, why in result["failed"])})
        self._read_after()

    def on_hl_timing(self, payload):
        """The timing strip's word starts, put on the caption they were set for."""
        payload = payload or {}
        clip_id, starts = payload.get("id"), payload.get("starts")
        if not isinstance(clip_id, str) or not isinstance(starts, list):
            return
        options = self._hl()
        self._start("timing", lambda c: highlight_resolve.apply_timing(c, clip_id, starts, options),
                    self._done_timing)

    def _done_timing(self, result, error):
        self.working = ""
        if error is not None:
            self._push_state()
            return self.emit("alert", {"title": "Couldn't set the timing", "text": str(error)})
        self.emit("toast", {"text": "The highlight follows your timing now." if result["pops"] or
                            self._hl()["pop"] == "off" else
                            "The highlight follows your timing now – without the pop, as its text is already "
                            "animated or styled."})
        self._read_after()

    def on_hl_playhead(self, payload):
        """Where Resolve's playhead is on the timing strip's caption: for Start at playhead."""
        clip_id = (payload or {}).get("id")
        if not isinstance(clip_id, str):
            return

        def done(at, error):
            self.working = ""
            self._push_state()
            if error is not None or at is None:
                return self.emit("toast", {"text": "Put Resolve's playhead on the caption first."})
            self.emit("hl_playhead", {"id": clip_id, "at": at})

        self._start("playhead", lambda c: highlight_resolve.read_playhead(c, clip_id), done)

    # --------------------------------------------------------------- Zoom --

    def _zoom_player(self):
        if self._player is None:
            from .zoom_player import ClipPlayer       # Qt Multimedia: only once Zoom needs it
            self._player = ClipPlayer(self)
            self._player.frame.connect(lambda cid, src: self.emit("zm_frame", {"id": cid, "src": src}))
            self._player.time.connect(lambda cid, t, playing: self.emit("zm_time", {"id": cid, "t": t, "playing": playing}))
            self._player.ready.connect(lambda cid: self.emit("zm_ready", {"id": cid}))
            self._player.failed.connect(lambda cid, why: self.emit("zm_failed", {"id": cid, "text": why}))
        return self._player

    def _on_zm_selection(self, result, error):
        if isinstance(error, motion_resolve.SelectionUnavailable):
            result, self.problem = None, str(error)
        elif error is not None:
            result, self.problem = None, f"Couldn't read the selection: {error}"
        else:
            self.problem = ""
        if result != self.zm_selection or error is not None:
            self.zm_selection = result
            self._push_state()
        clip = (result or {}).get("clip")
        if clip and clip.get("exists"):
            self._zoom_player().load(clip)
        elif self._player is not None:
            self._player.stop()

    def on_zm_toggle(self, _payload=None):
        if self._player is not None:
            self._player.toggle()

    def on_zm_seek(self, payload):
        try:
            t = float((payload or {}).get("t"))
        except (TypeError, ValueError):
            return
        if self._player is not None:
            self._player.seek(t)

    def on_zm_apply(self, payload):
        """The Zoom tab's zooms (none: take them off) on the clip they're for."""
        payload = payload or {}
        clip_id, zooms = payload.get("id"), payload.get("zooms")
        if not isinstance(clip_id, str) or not isinstance(zooms, list):
            return
        what = "zoom" if zooms else "unzoom"
        self._start(what, lambda c: zoom_resolve.run_apply(c, clip_id, zooms), self._done_zoom)

    def _zm_clipboard(self):
        """The zooms copied (Copy zooms): {"zooms", "duration", "name"}, or None."""
        kept = self.settings.get("zoom_clipboard")
        if not isinstance(kept, dict):
            return None
        try:
            duration = float(kept.get("duration"))
        except (TypeError, ValueError):
            return None
        zooms = zoom.clean(kept.get("zooms"), duration) if duration > 0 else []
        return {"zooms": zooms, "duration": duration, "name": str(kept.get("name") or "")} if zooms else None

    def on_zm_copy(self, payload):
        """Copy zooms: the clip's zooms, as they are on the tab, kept for pasting."""
        payload = payload or {}
        try:
            duration = float(payload.get("duration"))
        except (TypeError, ValueError):
            return
        zooms = zoom.clean(payload.get("zooms"), duration) if duration > 0 else []
        if not zooms:
            return self.emit("toast", {"text": "There are no zooms on this clip to copy."})
        self._save("zoom_clipboard", {"zooms": zooms, "duration": duration, "name": str(payload.get("name") or "")})
        self.emit("toast", {"text": "Copied 1 zoom." if len(zooms) == 1 else f"Copied {len(zooms)} zooms."})

    def on_zm_paste(self, payload):
        """Paste zooms onto the clip on the tab: the copied ones fitted to its
        length (zoom.adapt), handed back to go on the track - Apply puts them on."""
        payload = payload or {}
        kept, mode = self._zm_clipboard(), payload.get("mode")
        try:
            duration = float(payload.get("duration"))
        except (TypeError, ValueError):
            return
        if kept is None or mode not in zoom.PASTE_MODES or duration <= 0:
            return
        zooms = zoom.adapt(kept["zooms"], kept["duration"], duration, mode)
        if not zooms:
            return self.emit("toast", {"text": "None of the copied zooms reach this clip – it's shorter than where they start."})
        self.emit("zm_pasted", {"id": payload.get("id"), "zooms": zooms})

    def on_zm_paste_many(self, payload):
        """The copied zooms onto every selected clip at once, in place of theirs."""
        kept, mode = self._zm_clipboard(), (payload or {}).get("mode")
        if kept is None or mode not in zoom.PASTE_MODES:
            return
        self._start("paste", lambda c: zoom_resolve.run_paste(c, kept["zooms"], kept["duration"], mode),
                    self._done_paste)

    def _done_paste(self, result, error):
        self.working = ""
        if error is not None:
            self._push_state()
            return self.emit("alert", {"title": "Couldn't put the zooms on", "text": str(error)})
        n, short = result["applied"], result["empty"]
        if not n:
            text = ("The selected clips are all shorter than where the zooms start." if short
                    else "None of the selected clips took the zooms.")
        elif not short:
            text = "The zooms are on 1 clip." if n == 1 else f"The zooms are on {n} clips."
        else:                                   # one whole sentence, so it translates
            text = f"The zooms are on {n} of {n + short} clips – the others are shorter than where they start."
        self.emit("toast", {"text": text})
        if result["failed"]:
            self.emit("alert", {"title": "Some clips didn't take the zooms",
                                "text": "\n".join(f"{name}: {why}" for name, why in result["failed"])})
        self._read_after()

    def on_zm_grade(self, payload):
        """Graded: the clip's grade from Resolve as a LUT, for the preview to
        be drawn through (zoom_resolve.export_grade - Resolve shows its Color
        page for a moment)."""
        clip_id = (payload or {}).get("id")
        if not isinstance(clip_id, str):
            return
        path = os.path.join(tempfile.gettempdir(), f"buddy_grade_{os.getpid()}.cube")

        def done(result, error):
            self.working = ""
            self._push_state()
            if error is not None:
                self.emit("zm_grade", {"id": clip_id, "lut": None})
                return self.emit("alert", {"title": "Couldn't show the grade", "text": str(error)})
            size, data = result
            plain = zoom_resolve.is_identity(size, data)
            self.emit("zm_grade", {"id": clip_id, "size": size, "plain": plain,
                                   "lut": base64.b64encode(data).decode("ascii")})
            if plain:
                self.emit("toast", {"text": "This clip has no grade in Resolve – it looks as it is."})

        self._start("grade", lambda c: zoom_resolve.export_grade(c, clip_id, path), done)

    # -------------------------------------------------------- Transitions --

    def _on_tr_selection(self, result, error):
        if isinstance(error, motion_resolve.SelectionUnavailable):
            result, self.problem = None, str(error)
        elif error is not None:
            result, self.problem = None, f"Couldn't read the selection: {error}"
        else:
            self.problem = ""
        if result != self.tr_selection or error is not None:
            self.tr_selection = result
            self._push_state()

    def on_tr_option(self, payload):
        """A Transitions choice (kind, feel, length, amount, blur), kept."""
        payload = payload or {}
        merged = {**transition.clean(self.settings.get("transition")), **payload}
        self._save("transition", transition.clean(merged))

    def on_tr_apply(self, payload):
        """Apply: the chosen transition on the cuts the page lists."""
        ids = (payload or {}).get("cuts")
        if not isinstance(ids, list) or not ids:
            return
        settings = transition.clean(self.settings.get("transition"))
        self._start("transition", lambda c: transition_resolve.run_apply(c, {str(i) for i in ids}, settings),
                    self._done_tr_apply)

    def _done_tr_apply(self, result, error):
        self.working = ""
        if error is not None:
            self._push_state()
            return self.emit("alert", {"title": "Couldn't put the transitions on", "text": str(error)})
        n, short = result["applied"], result["shorter"]
        if n:
            text = "The transition is on the cut." if n == 1 else f"The transition is on {n} cuts."
            if short:
                text = (f"The transition is on {n} cuts – {short} shorter than asked, as the clips have "
                        "fewer unused frames past the cut." if n > 1 else
                        "The transition is on the cut – shorter than asked, as the clips have fewer unused "
                        "frames past it.")
            self.emit("toast", {"text": text})
        if result["failed"]:
            self.emit("alert", {"title": "Some cuts didn't take the transition",
                                "text": "\n".join(f"{where}: {why}" for where, why in result["failed"])})
        self._read_after()

    def on_tr_remove(self, payload):
        """Remove: the transitions on the cuts the page lists taken off."""
        ids = (payload or {}).get("cuts")
        if not isinstance(ids, list) or not ids:
            return
        self._start("untransition", lambda c: transition_resolve.run_remove(c, {str(i) for i in ids}),
                    self._done_tr_remove)

    def _done_tr_remove(self, result, error):
        self.working = ""
        if error is not None:
            self._push_state()
            return self.emit("alert", {"title": "Couldn't take the transitions off", "text": str(error)})
        self.emit("toast", {"text": "Took the transition off." if result == 1
                            else f"Took {result} transitions off." if result else "There were no transitions to take off."})
        self._read_after()

    # ------------------------------------------------------------- Titles --

    def _read_titles_timeline(self, asked=False):
        """The timeline's size and rate, so the Titles preview is the timeline's shape.
        Skipped while the worker is busy - the preview keeps the last size it had. asked:
        the person pressed refresh, so they're told what was read (or why not)."""
        controller = self._controller(connect=asked)
        if controller is None:
            if asked:
                self.emit("alert", {"title": "Can't reach Resolve", "text": self.problem})
                self._push_state()
            return
        if self._worker.busy() or self.working:
            if asked:
                self.emit("toast", {"text": "Resolve is busy – try again in a moment."})
            return

        def read():
            from .titles import titles_resolve
            _project, timeline = motion_resolve.current_timeline(controller)
            frame, fps, _drop = titles_resolve._timeline_info(timeline)
            return frame, fps

        def done(result, error):
            if error is None and result:
                self.titles.set_timeline(*result)
                if asked:
                    (w, h), fps = result
                    self.emit("toast", {"text": f"Timeline read: {w}×{h} · {float(fps):g} fps"})
            elif asked:
                self.emit("alert", {"title": "Couldn't read the timeline", "text": str(error or "No timeline is open.")})
        self._run(read, done)

    def on_ti_refresh(self, _payload=None):
        """The refresh button: read the timeline again (another may be open now)."""
        self._read_titles_timeline(asked=True)

    def on_ti_change(self, payload):
        self.titles.on_change(payload)

    def on_ti_ui(self, payload):
        self.titles.on_ui(payload)

    def on_ti_save_look(self, payload=None):
        self.titles.on_save_look(payload)

    def on_ti_use_look(self, payload):
        self.titles.on_use_look(payload)

    def on_ti_forget_look(self, payload):
        self.titles.on_forget_look(payload)

    def on_ti_place(self, _payload=None):
        """Place: the title on the timeline at the playhead (titles_resolve.place)."""
        self._start("title", self.titles.place_job(), self._done_title)

    def _done_title(self, result, error):
        self.working = ""
        self._push_state()
        if error is not None:
            return self.emit("alert", {"title": "Couldn't place the title", "text": str(error)})
        seconds = (result["end"] - result["start"]) / (result["fps"] or 24)
        self.emit("toast", {"text": f"The title is on V{result['track']} – {seconds:.1f} s."})
        if result["notes"]:
            self.emit("alert", {"title": "The title is on, with a note", "text": "\n".join(result["notes"])})
        self.titles.set_timeline(result["frame"], result["fps"])

    def _done_zoom(self, result, error):
        self.working = ""
        if error is not None:
            self._push_state()
            return self.emit("alert", {"title": "Couldn't put the zooms on", "text": str(error)})
        n = result["count"]
        self.emit("toast", {"text": "Took the zooms off the clip." if not n
                            else "1 zoom is on the clip." if n == 1 else f"{n} zooms are on the clip."})
        self._read_after()
