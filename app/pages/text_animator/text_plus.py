#!/usr/bin/env python3
"""
Text+ tools - font styling, placement, word layouts and animation presets for the
Text+ clips on the open timeline. Transcribe's page hosts them as five of its tabs
(Font Styling, Timeline Layout, Word-by-word, Timeline Animation, Custom Animation), right
after Subtitle Conversion, which makes the Text+ in the first place. The view is
transcribe/web/textplus.js, the placement canvases transcribe/web/canvas.js; this
package keeps the engine (subtitle_engine.py, animation_engine.py, canvas_math.py...).
It was the Text Animator page; the Animation page (page.py) now only holds an empty
Text+ tab. Settings are still saved under the tool id "text_animator".

TextPlusTools is not a page: it lives inside one (see __init__). Every Resolve/Fusion
call for these tabs is here; the controls' values are read from options.Options (the
settings bucket).

The placement canvases (Timeline Layout, Word-by-word, Custom Animation) show every Text+ clip under
the playhead at its real position, font, size and colour, polled every 500 ms while the
page and one of those tabs are on screen - Resolve has no change notifications. How big
each clip's text draws, and where around its Center, is measured here (canvas_math.text_box,
from the font files Resolve's FontManager names) and sent as fractions of the frame; the
page draws, drags, resizes and snaps locally and reports each finished edit - an edit
only reaches Resolve on release, never mid-drag. Every edit is undoable (Ctrl+Z,
Ctrl+Shift+Z): positions, sizes, word layouts and animation presets.

Resolve access goes through host.ensure_connected(); while Resolve is unreachable the
poll retries only every _RECONNECT_COOLDOWN_SECONDS - a failed connect spawns a probe
subprocess (core/resolve_bridge.py). Long actions - the Apply buttons, Space words over
the timeline, Stack - are made behind the busy overlay in the Resolve reader's process
(core/busy_job.py, the edits in text_plus_resolve.py), so Buddy keeps drawing meanwhile;
with no reader, here as before. Which clips they're for is found here first (as ids for
that process), and Undo keeps this process's clips.
The poll reads the Text+ (Fusion comps, whose tools the edits then use) on this thread,
but first asks the Resolve reader's process (core/resolve_reader.py) where Resolve is -
project, timeline, playhead. While the timeline plays Resolve holds every call, and the
poll's reads froze all of Buddy until it stopped; now the look waits there instead, and
nothing is read here until it's answered. Unchanged, the Text+ are read again only every
LIVE_FULL_S (an edit in the Inspector moves no playhead).

Also: a canvas that fills the page at the timeline's own aspect ratio, arrow-key
nudging, a live font-style preview, the colour picker, track choices limited to the
timeline's tracks, and remembered grid/safe-zone/snapping settings.

Protocol (each name "tp_"-prefixed on the wire; toast and alert go out unprefixed, as
the page's own):
    to the view    state, fonts, options, overlay, canvas, history, alert, toast
    from the view  refresh, set, overlay, reset_font, apply_style, move, group_move,
                   resize, bounding, apply_position, apply_bounding, apply_layout,
                   space_words, stack_words, apply_animation, remove_animations, undo, redo, dump
"""

import os
import re
import sys
import time
import traceback
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

from PySide6.QtCore import QObject, QTimer
from PySide6.QtGui import QFontDatabase

from core import busy_job, resolve_where
from core.resolve_bridge import ResolveConnectionError
from core.resolve_reader import ResolveCalls
from pages.transcribe import drt

from . import canvas_math, overlays, text_measure, text_plus_resolve as tpr, word_by_word
from .animation_engine import FusionAnimationEngine
from .canvas_math import (
    compute_auto_spaced_row,
    compute_bounding_fit_size,
    compute_large_word_layout,
)
from .coordinate_translator import CoordinateTranslator
from .diagnostics import dump_selected_node_settings
from .font_utils import (
    DEFAULT_FONT_NAME,
    DEFAULT_FONT_STYLE,
    face_label,
    find_face,
    get_installed_font_family_names,
    guessed_faces,
    parse_bold_italic_from_name,
    resolve_faces,
    split_font_name_and_style,
)
from .layout_presets import LAYOUT_PRESETS, compute_vertical_stack_centers, group_overlapping_clips
from .live_preview import _get_current_frame, get_active_text_plus_items, parse_point
from .options import Options
from .subtitle_engine import get_timeline_resolution

# How often the live preview polls DaVinci's playhead/timeline state (milliseconds).
# DaVinci's scripting API has no push/callback mechanism, so this is the only way to keep
# the preview in sync as the user scrubs.
LIVE_PREVIEW_POLL_INTERVAL_MS = 500
# Resolve where it was (same project, timeline and playhead): the Text+ are read again
# this often, for an edit made in Resolve's Inspector.
LIVE_FULL_S = 2.0
_RECONNECT_COOLDOWN_SECONDS = 10
MAX_LIVE_PREVIEW_UNDO_HISTORY = 50
# Starting Size "Side-by-Side" tries before shrinking to fit.
AUTO_SPACE_WORDS_DEFAULT_SIZE = 0.08

# The host page's tabs these tools draw: tab id -> name.
TABS = {
    "style": "Font Styling",
    "layout": "Timeline Layout",
    "wordbyword": "Word-by-word",
    "animation": "Timeline Animation",
    "words": "Custom Animation",
}
_CANVAS_TABS = ("layout", "wordbyword", "words")
_LIVE_TABS = _CANVAS_TABS + ("style",)   # polled for the Text+ under the playhead
# Around each clip's box, as a fraction of frame width: none - the box is the lines' own
# (canvas_math.text_box), so its edges are what lines words up.
_BOX_PADDING = 0.0
_SAMPLE_MAX_LINES = 6      # the style preview's text: a clip's own, up to this
_SAMPLE_MAX_CHARS = 400
# What an Apply says when its "Apply to" holds no clips.
_EMPTY_SCOPE = {
    "timeline": "No clips on the timeline",
    "selected": "No clips selected on the timeline",
    "playhead": "No Text+ clips under the playhead",
    "track": "Video track {track} is empty",
}
# A log line that went wrong, for the activity log's colour.
_BAD_LOG = re.compile(r"\[Error|Exception|Warning|Failed|Could not")


class _LivePreviewAction(NamedTuple):
    """One undoable edit. kind is "position" | "size" | "animation" | "batch"."""

    clip: Any
    kind: str
    old_value: Any
    new_value: Any
    clip_key: Optional[tuple] = None


def _fallback_faces() -> Dict[str, Tuple[str, str]]:
    """The picker's faces before Resolve answers: Windows' registered font files (the
    style guessed from each name), elsewhere Qt's families and their styles."""
    names = get_installed_font_family_names()
    if names:
        return guessed_faces(names)
    return {face_label(family, style): (family, style)
            for family in QFontDatabase.families() for style in (QFontDatabase.styles(family) or ["Regular"])}


def _css_font(name: str, style: Optional[str] = None) -> dict:
    """A Fusion font name ("Open Sans Semibold") as CSS: family, weight, italic. With a
    `style` (a Text+ tool's own Style input), `name` is the family as it is."""
    if style is None:
        family, style = split_font_name_and_style(name or DEFAULT_FONT_NAME)
    else:
        family, name = name or DEFAULT_FONT_NAME, f"{name} {style}"
    s = style.lower()
    weight = 400
    for words, value in ((("thin",), 100), (("extralight", "ultralight"), 200), (("light",), 300),
                         (("medium",), 500), (("semibold", "demibold"), 600), (("bold",), 700),
                         (("extrabold", "ultrabold"), 800), (("black", "heavy"), 900)):
        if any(w in s.replace(" ", "") for w in words):
            weight = value
    bold, italic = parse_bold_italic_from_name(name or "")
    if bold and weight < 700:
        weight = 700
    return {"family": family, "weight": weight, "italic": italic or "oblique" in s}


class _ScopeUnavailable(Exception):
    """An "Apply to" choice this Resolve can't do - the message says why."""


def _unique_id(item) -> str:
    """A timeline's or timeline item's GetUniqueId(), "" when it has none."""
    try:
        return str(item.GetUniqueId() or "")
    except Exception:
        return ""


def _tuple(value):
    """A preset or clip key as Undo keeps it - a tuple, though JSON brought a list."""
    return tuple(value) if isinstance(value, list) else value


class _Refs:
    """Clips as the reader's process finds them again: {"timeline_id", "clips":
    [[GetUniqueId, track]]} - read from Resolve once, the first time they're asked
    for (only with a reader there to send them to). picks: some of them, by place."""

    def __init__(self, timeline, clips):
        self._timeline, self._clips, self._ids = timeline, clips, None

    def __call__(self, picks=None) -> dict:
        if self._ids is None:
            self._ids = {"timeline_id": _unique_id(self._timeline),
                         "clips": [[_unique_id(clip), int(track)] for clip, track in self._clips]}
        if picks is None:
            return dict(self._ids)
        return {"timeline_id": self._ids["timeline_id"], "clips": [self._ids["clips"][i] for i in picks]}


class _Held:
    """The host, for one step of an edit made in two (read in Resolve, measure here
    with Qt, write in Resolve): the busy overlay goes up with the first step and down
    with the second - not in between."""

    def __init__(self, host, up, down):
        self._host, self._up, self._down = host, up, down

    def set_busy(self, on, message=None):
        if on and self._up:
            self._host.set_busy(True, message)
        elif not on and self._down:
            self._host.set_busy(False)


class TextPlusTools(QObject):
    """The Text+ tabs, inside a page that provides: host, emit(name, payload) to its
    view, isVisible(), tab (the tab on screen) and _add_log(text, kind). The page
    sends its view's "tp_<name>" messages to on_<name> here, calls web_ready(),
    on_shown() and on_app_quitting() from its own, and tab_shown() when one of TABS
    comes on screen."""

    def __init__(self, page):
        # A child of its page (a QObject in Buddy), so its poll timer goes when the page does.
        super().__init__(page if isinstance(page, QObject) else None)
        self.page = page
        self._live_preview_undo_stack: list = []
        self._live_preview_redo_stack: list = []
        self._style_kept = 0            # an Apply style's clips left with a shading element that isn't Font Styling's
        self.current_fusion_pos: Tuple[float, float] = (0.5, 0.5)
        self._no_connection = False
        self._last_connect_attempt = 0.0
        self.opts = Options(self.host.tool_settings("text_animator", {}))
        self.tracks = {"video": 0, "subtitle": 0}
        self.timeline_name = ""
        self.resolution = (1920, 1080)
        self.bounding = {"on": False, "left": 0.15, "right": 0.85}
        # The clips under the playhead, per canvas tab: id -> ActiveTextItem.
        self._items: Dict[str, Dict[str, Any]] = {tab: {} for tab in _CANVAS_TABS}
        self._canvas_sig = {tab: None for tab in _CANVAS_TABS}
        self._sample = ""
        self._fonts_read = False     # Resolve's font files handed to canvas_math (see _read_font_files)
        self._measured = self.host.tool_settings("text_animator", {})  # + "baselines" (_measure_faces)
        self._unmeasurable = set()   # faces Resolve drew nothing for, this session

        # The font picker: {label: (family, style)} - Resolve's own faces once it answers
        # (_read_font_files), until then the computer's (_fallback_faces).
        self._faces = _fallback_faces()
        self._faces_from_resolve = False

        self._calls = ResolveCalls(self)     # the look at where Resolve is, before a read here
        self._where_sig = None               # what that look last saw
        self._read_at = 0.0                  # time.monotonic() of the last read here
        self._working = False                # a long edit is being made (_run): nothing else starts

        self._preview_timer = QTimer(self)
        self._preview_timer.setInterval(LIVE_PREVIEW_POLL_INTERVAL_MS)
        self._preview_timer.timeout.connect(self._refresh_live_preview)
        self._preview_timer.start()

    @property
    def host(self):
        return self.page.host

    @property
    def tab(self) -> str:
        return self.page.tab

    def emit(self, name, payload=None):
        """To the view: the Text+ script's messages are "tp_"-prefixed; a toast or an
        alert is the page's own."""
        self.page.emit(name if name in ("toast", "alert") else f"tp_{name}", payload)

    def web_ready(self):
        self._push_fonts()
        self._push_state()
        self._push_options()
        self.emit("overlay", self._overlay_view())
        for tab in _CANVAS_TABS:
            self._canvas_sig[tab] = None
            self._push_canvas(tab)
        self._push_history()

    def on_shown(self):
        """Sync tracks and canvas size from the current timeline, and refresh the live
        preview straight away - once Resolve answers the reader's look, so coming here
        while the timeline plays doesn't freeze Buddy."""
        self._where_sig = None
        if not self._ask_where(self._on_shown_answered):
            self._sync_timeline()
            self._refresh_live_preview()

    def _on_shown_answered(self, where):
        self._sync_timeline()
        self._on_where(where)

    def on_app_quitting(self):
        self._preview_timer.stop()

    # ------------------------------------------------------------ Resolve access --

    def _resolve(self, quiet: bool = False) -> Optional[Any]:
        """The page's single Resolve entry point. Logs failures and returns None - but
        not for a quiet (background) call: being offline is Buddy's header's to say,
        and the log is for what the user asked for."""
        try:
            resolve = self.host.ensure_connected().resolve
            self._no_connection = False
            return resolve
        except ResolveConnectionError as exc:
            if not quiet:
                self.log(f"[Error] {exc}")
            self._no_connection = True
            self._last_connect_attempt = time.monotonic()
            return None

    def _get_current_timeline(self, resolve: Any) -> Optional[Any]:
        try:
            pm = resolve.GetProjectManager()
            proj = pm.GetCurrentProject() if pm is not None else None
            return proj.GetCurrentTimeline() if proj is not None else None
        except Exception:
            return None

    def _timeline_or_log(self, resolve):
        """The open timeline, or None after saying which part is missing."""
        pm = resolve.GetProjectManager()
        if pm is None:
            self.log("[Error] Unable to get ProjectManager.")
            return None
        proj = pm.GetCurrentProject()
        if proj is None:
            self.log("[Error] No active project found.")
            return None
        timeline = proj.GetCurrentTimeline()
        if timeline is None:
            self.log("[Error] No active timeline found.")
            return None
        return timeline

    def log(self, msg: str):
        """Into the page's activity log."""
        text = str(msg)
        self.page._add_log(text, "error" if _BAD_LOG.search(text) else "info")

    # ------------------------------------------------------------ long edits --

    def _run(self, name, message, args, job, done, where, host=None):
        """One long edit behind the busy overlay (core/busy_job.py): the reader's job
        `name` (text_plus_resolve.py) when there's a reader - args() its arguments,
        worked out only then: the clips as ids - else job() here, as before. done(result,
        error) once it's made; nothing else of these tabs starts meanwhile. A done that
        fails is logged as its handler's error (`where`)."""
        payload = args() if self._calls.reader.available() else {}

        def finished(result, error):
            self._working = False
            try:
                done(result, error)
            except Exception as err:  # noqa: BLE001 - said, as the handlers always did
                self.host.set_busy(False)       # a first step's overlay (_Held), left up for the next
                self._failed(where, err)

        self._working = True
        try:
            busy_job.run(host or self.host, self._calls, name, payload, job, finished, message)
        except Exception:
            self._working = False
            raise

    def _failed(self, where, error):
        """A long edit that went wrong, said in the log the way its handler always did -
        with the traceback, when it happened in this process."""
        self.log(f"[Error in {where}]: {error}")
        if getattr(error, "__traceback__", None) is not None:
            self.log("".join(traceback.format_exception(type(error), error, error.__traceback__)))

    def _said(self, result):
        """The log lines a job in the reader's process made (here, they're logged as they come)."""
        for line in (result or {}).get("log") or []:
            self.log(line)

    @staticmethod
    def _position_steps(clips, steps) -> list:
        """Undo steps for Center changes a job made - {"i", "old", "new"} by the clip's
        place in `clips` - on this process's clips."""
        return [_LivePreviewAction(clip=clips[s["i"]][0], kind="position", old_value=list(s["old"]),
                                   new_value=list(s["new"])) for s in steps]

    # ------------------------------------------------------------------ pushes --

    def _push_state(self):
        self.emit("state", {
            "timeline": self.timeline_name,
            "resolution": list(self.resolution),
            "tracks": self.tracks,
            "bounding": self.bounding,
            "sample": self._sample,
        })

    def _push_options(self):
        view = self.opts.view()
        family, style = self.opts.font_face
        view["font_display"] = face_label(family, style)
        view["font_css"] = _css_font(family, style)
        # Ascent + descent and line step in ems, as Text+ measures them (canvas_math) - the
        # browser's own figures can be the Windows metrics, which aren't always the same.
        # Text+ steps lines by ascent + descent alone: no line gap.
        ascent, descent, _gap = canvas_math.font_vertical_metrics(family, style)
        view["font_css"].update(height=ascent + descent, line=ascent + descent)
        for entry in view["looks"]["saved"]:        # for the Saved looks menu's samples
            font = entry["look"].get("font") or [family, style]
            entry["css"] = _css_font(*font) if len(font) == 2 else _css_font(font[0])
        self.emit("options", view)

    def _overlay_view(self):
        view = self.opts.overlay_view()
        view.update({
            "grid_types": overlays.GRID_TYPES, "safe_types": overlays.SAFE_ZONE_TYPES,
            "fractions": overlays.GRID_FRACTIONS, "safe_rects": overlays.SAFE_ZONE_RECTS,
            "default_spacing": canvas_math.standard_grid_spacing(*self.resolution),
        })
        return view

    def _push_history(self):
        self.emit("history", {"undo": len(self._live_preview_undo_stack),
                              "redo": len(self._live_preview_redo_stack)})

    @staticmethod
    def _item_id(item) -> str:
        track, name, start, end = item.key
        return f"{track}|{start}|{end}|{name}"

    def _canvas_payload(self, items):
        out = []
        for item in items:
            center = item.center if item.center is not None else (0.5, 0.5)
            out.append({
                "id": self._item_id(item),
                "track": item.track_index,
                "text": item.text,
                "font": _css_font(item.font_name, item.font_style),
                "color": "#" + "".join(f"{max(0, min(255, round(c * 255))):02X}" for c in item.color),
                "cx": center[0], "cy": 1.0 - center[1],
                "size": item.font_size,
                "box": canvas_math.text_box(item.font_name, item.text, item.font_size,
                                            item.font_style, item.line_spacing),
                "known": item.center is not None,
            })
        return out

    def _push_canvas(self, tab):
        items = list(self._items[tab].values())
        payload = self._canvas_payload(items)
        signature = repr((payload, self.resolution))
        if signature == self._canvas_sig[tab]:
            return
        self._canvas_sig[tab] = signature
        self.emit("canvas", {"tab": tab, "resolution": list(self.resolution), "items": payload,
                             "padding": _BOX_PADDING})

    # ------------------------------------------------------------ defaults sync --

    def _read_font_files(self, resolve):
        """Once per connection (and on Refresh): Resolve's fonts - the picker's list, and
        which file Resolve renders each font from, so the canvas and the preview measure
        what Resolve draws (see canvas_math)."""
        if self._fonts_read:
            return
        self._fonts_read = True
        try:
            font_list = resolve.Fusion().FontManager.GetFontList()
        except Exception:
            return          # Qt's own metrics, then: right for most fonts
        picker = self._take_faces(resolve_faces(font_list))
        if canvas_math.set_font_files(font_list):
            for tab in _CANVAS_TABS:
                self._canvas_sig[tab] = None
                self._push_canvas(tab)
            picker = True
        if picker:
            self._push_options()

    def _take_faces(self, faces) -> bool:
        """Resolve's faces for the picker. A font an older Buddy kept by one name
        ("Franklin Gothic Medium") becomes the face Resolve has by that name. True if the
        picker changed."""
        if not faces:
            return False
        if self.opts.font_picked_by_name:
            face = find_face(faces, self.opts.s.get("selected_font_name") or "")
            if face is not None:
                self.opts.set("font_face", face)
        changed = not self._faces_from_resolve or faces != self._faces
        self._faces, self._faces_from_resolve = faces, True
        if changed:
            self._push_fonts()
        return changed

    def _push_fonts(self):
        self.emit("fonts", sorted(self._faces, key=str.casefold))

    def _face_key(self, family, style) -> str:
        """A measured baseline's key: the face and the file it's drawn from, so a font
        updated or installed over is measured again."""
        path = canvas_math.font_file(family, style) or ""
        try:
            stat = os.stat(path) if path else None
        except OSError:
            stat = None
        sig = f"{stat.st_size}|{stat.st_mtime_ns}" if stat else ""
        return f"{family}|{style}|{os.path.normcase(path)}|{sig}"

    def _measure_faces(self, items):
        """Each face under the playhead gets its baseline from Resolve once (see
        text_measure) - remembered with its font file, so after the first time it's read
        from settings rather than measured."""
        faces = {canvas_math.face(item.font_name, item.font_style): item for item in items}
        missing = {f: item for f, item in faces.items() if not canvas_math.has_baseline(*f)}
        if not missing:
            return
        saved = self._measured.get("baselines") or {}
        known = {f: saved[self._face_key(*f)] for f in missing if self._face_key(*f) in saved}
        todo = [f for f in missing if f not in known and f not in self._unmeasurable]
        if todo:
            comp = self._get_fusion_comp(missing[todo[0]].clip)
            try:
                found = text_measure.measure_baselines(comp, todo, self.resolution, log=self.log)
            except Exception as err:
                self.log(f"[Placement Canvas] Couldn't measure the fonts' baselines in Resolve: {err}")
                found = {}
            self._unmeasurable.update(f for f in todo if f not in found)
            if found:
                saved = dict(saved)
                saved.update({self._face_key(*f): round(v, 5) for f, v in found.items()})
                self._measured["baselines"] = saved
                save = getattr(self._measured, "save", None)
                if callable(save):
                    save()
            known.update(found)
        if canvas_math.set_baselines(known):
            for tab in _CANVAS_TABS:
                self._canvas_sig[tab] = None

    def _sync_timeline(self):
        """Syncs the timeline's name, tracks and resolution."""
        resolve = self._resolve(quiet=True)
        if resolve is None:
            return self._push_state()
        self._read_font_files(resolve)
        timeline = self._get_current_timeline(resolve)
        if timeline is None:
            return self._push_state()
        try:
            self._sync_resolution(timeline)
            self.timeline_name = timeline.GetName() if callable(getattr(timeline, "GetName", None)) else ""
            self.tracks = {"video": int(timeline.GetTrackCount("video") or 0),
                           "subtitle": int(timeline.GetTrackCount("subtitle") or 0)}
        except Exception:
            pass
        self._push_state()

    def _sync_resolution(self, timeline) -> bool:
        """Takes the timeline's resolution for the canvases and the style preview; True
        if it changed. Also run on every poll: the page may first show before Resolve
        is reachable, and the user can switch to a timeline of another shape. Only a
        real answer counts: get_timeline_resolution's 1920x1080 fallback for a read
        that failed would flip a vertical preview back to 16:9 on one bad poll."""
        try:
            width = int(timeline.GetSetting("timelineResolutionWidth") or 0)
            height = int(timeline.GetSetting("timelineResolutionHeight") or 0)
        except Exception:
            return False
        if width <= 0 or height <= 0 or (width, height) == self.resolution:
            return False
        self.resolution = (width, height)
        self.log(f"[Placement Canvas] Sized to match timeline resolution {width}x{height}.")
        self.emit("overlay", self._overlay_view())
        return True

    # ------------------------------------------------------------ live preview --

    def _ask_where(self, then):
        """Asks the reader's process where Resolve is; then(where) once it answers.
        False when there's no reader (or no connection to look through): the caller
        reads the old way. True also when a look is still waiting - Resolve is
        holding it (most often, playing), so nothing's read meanwhile."""
        shell = self.host.controller if getattr(self.host, "connected", False) else None
        if shell is None or not self._calls.reader.available():
            return False
        if not self._calls.busy():
            self._calls.call("resolve.where", {}, lambda: resolve_where.where(shell),
                             lambda where, error: error is None and then(where))
        return True

    def _on_where(self, where):
        """The reader's look, answered: read the Text+ here if anything moved, or
        LIVE_FULL_S on - not while a long edit is being made (_run): read once it's done."""
        if not self.page.isVisible() or self.tab not in _LIVE_TABS or self._working:
            return
        signature = (where["project"], where["timeline"], where["timecode"])
        if signature == self._where_sig and time.monotonic() - self._read_at < LIVE_FULL_S:
            return
        self._where_sig = signature
        self._read_live_preview()

    def _refresh_live_preview(self, force=False):
        """Polling tick. Only calls into Resolve while this page is the visible one AND a
        tab showing the playhead's Text+ is active (a canvas, or the style preview's
        text); reconnect attempts are throttled. Not forced (the timer), it goes by the
        reader's look first (_ask_where); forced (after an edit), it reads straight away."""
        if not force and not self.page.isVisible():
            return
        if self.tab not in _LIVE_TABS or self._working:
            return                      # (a long edit: read again once it's made)
        if self._no_connection and (time.monotonic() - self._last_connect_attempt) < _RECONNECT_COOLDOWN_SECONDS:
            return
        if force or not self._ask_where(self._on_where):
            self._read_live_preview()

    def _read_live_preview(self):
        self._read_at = time.monotonic()
        resolve = self._resolve(quiet=True)
        if resolve is None:
            return
        self._read_font_files(resolve)
        timeline = self._get_current_timeline(resolve)
        if timeline is None:
            return
        try:
            if self._sync_resolution(timeline):
                self._push_state()
        except Exception:
            pass
        try:
            items = get_active_text_plus_items(timeline, self._get_fusion_comp, self._find_text_tool, log=self.log)
        except Exception as err:
            self.log(f"[Error refreshing live preview]: {err}")
            return
        self._update_sample(items)
        self._measure_faces(items)
        if self.tab in _CANVAS_TABS:
            self._items[self.tab] = {self._item_id(item): item for item in items}
            self._push_canvas(self.tab)

    def _update_sample(self, items):
        """The style preview's text: the playhead's Text+, every line as Resolve draws
        them - capped only for sanity, not a cut a subtitle would hit. With nothing
        under the playhead the last one stays, rather than flicking back to a stand-in."""
        if not items or not items[0].text:
            return
        sample = "\n".join(items[0].text.split("\n")[:_SAMPLE_MAX_LINES])[:_SAMPLE_MAX_CHARS]
        if sample != self._sample:
            self._sample = sample
            self._push_state()

    def _clip_for(self, tab, item_id):
        item = self._items.get(tab, {}).get(item_id)
        return item.clip if item is not None else None

    def on_refresh(self, _payload=None):
        self._no_connection = False
        self._fonts_read = False     # a font installed since is found
        self._unmeasurable.clear()
        self._sync_timeline()
        self._refresh_live_preview(force=True)

    def on_move(self, payload):
        """One box dragged (or nudged) and let go: its new centre, top-left-origin fractions."""
        payload = payload or {}
        clip = self._clip_for(payload.get("tab"), payload.get("id"))
        if clip is None:
            return
        fusion_x, fusion_y = CoordinateTranslator.pyside_to_fusion(float(payload["cx"]), float(payload["cy"]), 1, 1)
        self._on_live_item_position_updated(clip, fusion_x, fusion_y)
        self._refresh_live_preview(force=True)

    def on_group_move(self, payload):
        payload = payload or {}
        moves = []
        for move in payload.get("moves") or []:
            clip = self._clip_for(payload.get("tab"), move.get("id"))
            if clip is not None:
                fx, fy = CoordinateTranslator.pyside_to_fusion(float(move["cx"]), float(move["cy"]), 1, 1)
                moves.append((clip, fx, fy))
        if moves:
            self._on_live_group_position_updated(moves)
            self._refresh_live_preview(force=True)

    def on_resize(self, payload):
        payload = payload or {}
        clip = self._clip_for(payload.get("tab"), payload.get("id"))
        size = payload.get("size")
        if clip is None or not isinstance(size, (int, float)):
            return
        self._on_live_item_size_changed(clip, max(0.005, float(size)))
        self._refresh_live_preview(force=True)

    def _on_live_item_position_updated(self, clip: Any, fusion_x: float, fusion_y: float):
        self.current_fusion_pos = (fusion_x, fusion_y)
        comp = self._get_fusion_comp(clip)
        text_tool = self._find_text_tool(comp) if comp is not None else None
        if text_tool is None:
            self.log("[Live Position] Could not re-locate the Text+ tool for the moved clip.")
            return
        try:
            parsed_old_center = parse_point(text_tool.GetInput("Center"))
            old_center = list(parsed_old_center) if parsed_old_center is not None else None
        except Exception:
            old_center = None
        try:
            text_tool.SetInput("Center", [fusion_x, fusion_y])
            self.log(f"[Live Position] Updated Center = ({fusion_x:.4f}, {fusion_y:.4f}).")
            self._record_live_preview_action(clip, "position", old_center, [fusion_x, fusion_y])
        except Exception as err:
            self.log(f"[Live Position] Center SetInput Exception: {err}")

    def _on_live_item_size_changed(self, clip: Any, new_font_size: float):
        comp = self._get_fusion_comp(clip)
        text_tool = self._find_text_tool(comp) if comp is not None else None
        if text_tool is None:
            self.log("[Live Resize] Could not re-locate the Text+ tool for the resized clip.")
            return
        try:
            old_size = text_tool.GetInput("Size")
            old_size = float(old_size) if isinstance(old_size, (int, float)) else None
        except Exception:
            old_size = None
        try:
            text_tool.SetInput("Size", new_font_size)
            self.log(f"[Live Resize] Updated Size = {new_font_size:.4f}.")
            self._record_live_preview_action(clip, "size", old_size, new_font_size)
        except Exception as err:
            self.log(f"[Live Resize] Size SetInput Exception: {err}")

    def _on_live_group_position_updated(self, moves: list):
        sub_actions = []
        updated = 0
        for clip, fusion_x, fusion_y in moves:
            comp = self._get_fusion_comp(clip)
            text_tool = self._find_text_tool(comp) if comp is not None else None
            if text_tool is None:
                self.log("[Live Position] Could not re-locate the Text+ tool for one moved word – skipped.")
                continue
            try:
                parsed_old_center = parse_point(text_tool.GetInput("Center"))
                old_center = list(parsed_old_center) if parsed_old_center is not None else None
            except Exception:
                old_center = None
            try:
                text_tool.SetInput("Center", [fusion_x, fusion_y])
                updated += 1
                if old_center is not None:
                    sub_actions.append(_LivePreviewAction(clip=clip, kind="position", old_value=old_center,
                                                          new_value=[fusion_x, fusion_y]))
            except Exception as err:
                self.log(f"[Live Position] Center SetInput Exception: {err}")
        self.log(f"[Live Position] Moved {updated}/{len(moves)} word(s) together.")
        self._record_batch_action(sub_actions)

    # ------------------------------------------------------------ page state --

    def tab_shown(self):
        """The page switched to one of TABS."""
        if self.tab in _LIVE_TABS:
            self._refresh_live_preview(force=True)

    def on_set(self, payload):
        payload = payload or {}
        name, value = payload.get("name"), payload.get("value")
        if name == "font_name":         # a picker label: its face, else a name to split
            face = find_face(self._faces, value)
            if face is not None:
                name, value = "font_face", face
        if self.opts.set(name, value):
            self._push_options()

    def on_overlay(self, payload):
        payload = payload or {}
        if self.opts.set(payload.get("name"), payload.get("value")):
            self.emit("overlay", self._overlay_view())

    def on_bounding(self, payload):
        payload = payload or {}
        on = bool(payload.get("on", self.bounding["on"]))
        left = max(0.0, min(1.0, float(payload.get("left", self.bounding["left"]))))
        right = max(0.0, min(1.0, float(payload.get("right", self.bounding["right"]))))
        was_on = self.bounding["on"]
        self.bounding = {"on": on, "left": left, "right": right}
        self._push_state()
        if on and not was_on:
            self._snap_stacked_items_vertically()

    # ------------------------------------------------------------ layout presets --

    def _word_entries(self, tab="words"):
        """(key, text, font_name, fusion centre y) per word under the playhead, in clip order."""
        items = list(self._items[tab].values())

        def _clip_start(item):
            clip = item.clip
            try:
                return clip.GetStart() if callable(getattr(clip, "GetStart", None)) else 0
            except Exception:
                return 0

        items.sort(key=_clip_start)
        return [(self._item_id(i), i.text, i.font_name, i.center[1] if i.center else 0.5) for i in items]

    def on_apply_layout(self, payload):
        payload = payload or {}
        preset_name = payload.get("preset") or self.opts.layout_preset
        if preset_name in LAYOUT_PRESETS:
            self.opts.set("layout_preset", preset_name)
        resolve = self._resolve()
        if resolve is None:
            return

        words = self._word_entries()
        if len(words) < 2:
            self.log("[Layout Preset] Need at least 2 Text+ words visible under the playhead to arrange a layout.")
            return self.emit("toast", {"text": "Needs at least 2 words under the playhead."})

        if preset_name == "Hero + Stack":
            selected = [k for k in payload.get("selected") or [] if k in self._items["words"]]
            hero_key = selected[0] if selected else words[len(words) // 2][0]
            targets = compute_large_word_layout(hero_key, words) if hero_key is not None else {}
        elif preset_name == "Side-by-Side":
            targets = compute_auto_spaced_row(words, target_size=AUTO_SPACE_WORDS_DEFAULT_SIZE)
        else:
            preset_fn = LAYOUT_PRESETS.get(preset_name)
            if preset_fn is None:
                self.log(f"[Layout Preset] Unknown preset '{preset_name}'.")
                return
            raw_targets = preset_fn(len(words))
            targets = {key: target for (key, *_rest), target in zip(words, raw_targets)}

        sub_actions, applied = self._apply_word_layout_targets(targets)
        self.log(f"[Layout Preset] Applied '{preset_name}' to {applied}/{len(words)} word(s).")
        self._record_batch_action(sub_actions)
        self._refresh_live_preview(force=True)

    def _apply_word_layout_targets(self, targets: dict) -> Tuple[list, int]:
        sub_actions = []
        applied = 0
        for key, (target_x, target_y, target_size) in targets.items():
            clip = self._clip_for("words", key)
            if clip is None:
                continue
            comp = self._get_fusion_comp(clip)
            text_tool = self._find_text_tool(comp) if comp is not None else None
            if text_tool is None:
                self.log("[Word Layout] Could not re-locate the Text+ tool for one word – skipped.")
                continue
            try:
                parsed_old_center = parse_point(text_tool.GetInput("Center"))
                old_center = list(parsed_old_center) if parsed_old_center is not None else None
            except Exception:
                old_center = None
            try:
                old_size = text_tool.GetInput("Size")
                old_size = float(old_size) if isinstance(old_size, (int, float)) else None
            except Exception:
                old_size = None
            try:
                text_tool.SetInput("Center", [target_x, target_y])
                text_tool.SetInput("Size", target_size)
                applied += 1
                if old_center is not None:
                    sub_actions.append(_LivePreviewAction(clip=clip, kind="position", old_value=old_center,
                                                          new_value=[target_x, target_y]))
                if old_size is not None:
                    sub_actions.append(_LivePreviewAction(clip=clip, kind="size", old_value=old_size,
                                                          new_value=target_size))
            except Exception as err:
                self.log(f"[Word Layout] SetInput Exception: {err}")
        return sub_actions, applied

    # ------------------------------------------------------------ word-by-word --

    @staticmethod
    def _clip_start(clip) -> int:
        try:
            return int(clip.GetStart()) if callable(getattr(clip, "GetStart", None)) else 0
        except Exception:
            return 0

    @staticmethod
    def _word_entry(key, clip, track, state, span=None) -> Optional[dict]:
        """A clip Space words can set out - one word, a known Center - as {clip, track,
        start, end, word}; None for any other. span: its [start, end] as already read
        (tpr.read_words), else they're read from the clip."""
        if not state or state.get("center") is None or not word_by_word.is_single_word(state.get("text")):
            return None
        if span is not None:
            start, end = int(span[0]), int(span[1])
        else:
            try:
                start, end = int(clip.GetStart()), int(clip.GetEnd())
            except Exception:
                start = end = 0
        cx, cy = state["center"]
        return {"clip": clip, "track": track, "start": start, "end": end, "word": word_by_word.Word(
            key, state["text"], state["font_name"], state["font_style"], state["font_size"],
            state["line_spacing"], cx, 1.0 - cy)}

    def _playhead_words(self, selected) -> list:
        """The single-word Text+ under the playhead - only the ones selected in the preview,
        if any are."""
        resolve = self._resolve()
        timeline = self._get_current_timeline(resolve) if resolve is not None else None
        if timeline is None:
            return []
        items = get_active_text_plus_items(timeline, self._get_fusion_comp, self._find_text_tool, log=self.log)
        chosen = {key for key in selected or []}
        entries = []
        for item in items:
            key = self._item_id(item)
            if chosen and key not in chosen:
                continue
            state = {"text": item.text, "font_name": item.font_name, "font_style": item.font_style,
                     "font_size": item.font_size, "line_spacing": item.line_spacing, "center": item.center}
            entry = self._word_entry(key, item.clip, item.track_index, state)
            if entry is not None:
                entries.append(entry)
        return entries

    def _word_groups(self, clips, read) -> list:
        """Every group of single-word Text+ clips on screen together (each clip overlapping
        every other in its group), two words or more - from `read`, tpr.read_words of
        `clips`. Each entry's "i" is its clip's place in `clips`."""
        entries = []
        for i, ((clip, track), record) in enumerate(zip(clips, read["clips"])):
            entry = self._word_entry(len(entries), clip, track, record["state"], record["span"])
            if entry is not None:
                entry["i"] = i
                entries.append(entry)
        groups = group_overlapping_clips([(i, e["start"], e["end"]) for i, e in enumerate(entries)])
        return [[entries[i] for i in group] for group in groups if len(group) > 1]

    @staticmethod
    def _layout_group(entries, lines, aspect, spacing=1.0) -> Tuple[list, list, Any]:
        """One group set out: (its entries in order, their new Centers - Fusion's,
        bottom-up - the layout)."""
        entries = sorted(entries, key=lambda e: (e["start"], e["track"]))
        placed = word_by_word.layout([e["word"] for e in entries], lines, aspect, spacing)
        centers = []
        for entry in entries:
            cx, cy = placed.centers[entry["word"].key]
            centers.append([cx, 1.0 - cy])
        return entries, centers, placed

    def _space_group(self, entries, lines, aspect, spacing=1.0) -> Tuple[list, int, Any]:
        """One group set out and put in Resolve, here: (undo steps, clips moved, the layout)."""
        entries, centers, placed = self._layout_group(entries, lines, aspect, spacing)
        clips = [(e["clip"], e["track"]) for e in entries]
        done = tpr.space_words(clips, centers, log=self.log, comp_of=self._get_fusion_comp,
                               tool_of=self._find_text_tool)
        return self._position_steps(clips, done["steps"]), done["moved"], placed

    def on_space_words(self, payload):
        """Space words: the single-word clips under the playhead (those selected in the
        preview, if any), or every group of them on the timeline, set out as one Text+
        clip would show the sentence - see word_by_word.py."""
        if self._working:
            return
        payload = payload or {}
        lines, scope, spacing = self.opts.wbw_lines, self.opts.wbw_scope, self.opts.slider("wbw_spacing")
        resolve = self._resolve()
        if resolve is None:
            return
        timeline = self._timeline_or_log(resolve)
        if timeline is None:
            return
        width, height = get_timeline_resolution(timeline)
        aspect = width / height if height else 16 / 9
        try:
            if scope == "timeline":
                # Every clip read in the reader's process, the groups set out here (Qt
                # measures the words), then put in Resolve there - the overlay up throughout.
                clips, _desc = self._scope_clips(timeline, "timeline", 1)
                refs = _Refs(timeline, clips)
                return self._run(
                    "textplus.read", "Spacing words…", lambda: {**refs(), "kind": "words"},
                    lambda: tpr.read_words(clips, log=self.log, comp_of=self._get_fusion_comp,
                                           tool_of=self._find_text_tool),
                    lambda read, error: self._space_timeline(read, error, clips, refs, lines, aspect, spacing),
                    "space_words", host=_Held(self.host, up=True, down=False))
            else:
                entries = self._playhead_words(payload.get("selected"))
                if len(entries) < 2:
                    self.log("[Word-by-word] Needs at least 2 single-word Text+ clips under the playhead.")
                    return self.emit("toast", {"text": "Needs at least 2 single-word Text+ clips under the playhead."})
                sub_actions, moved, placed = self._space_group(entries, lines, aspect, spacing)
                self.log(f"[Word-by-word] Spaced {len(entries)} words on {len(placed.lines)} line(s); "
                         f"{moved} Text+ clip(s) moved.")
                if placed.widest > 1.0:
                    self.log("[Warning] The words are wider than the frame – allow more lines, or make them smaller.")
                self.emit("toast", {"text": f"Spaced {len(entries)} words on {len(placed.lines)} line(s)"})
            self._record_batch_action(sub_actions)
        except Exception as err:
            self.log(f"[Error in space_words]: {err}")
            self.log(traceback.format_exc())
        self._refresh_live_preview(force=True)

    def _space_timeline(self, read, error, clips, refs, lines, aspect, spacing):
        """Space words over the timeline, its clips read (tpr.read_words): each group set
        out here, then put in Resolve (tpr.space_words) - or, with none, said."""
        if error is not None:
            self.host.set_busy(False)
            self._failed("space_words", error)
            return self._refresh_live_preview(force=True)
        self._said(read)
        try:
            groups = self._word_groups(clips, read)
            if not groups:
                self.host.set_busy(False)
                self.log("[Word-by-word] No single-word Text+ clips are on screen together on this timeline.")
                return self.emit("toast", {"text": "No single-word Text+ clips on screen together on this timeline."})
            picks, centers, wide = [], [], 0
            for group in groups:
                entries, placed_centers, placed = self._layout_group(group, lines, aspect, spacing)
                picks += [entry["i"] for entry in entries]
                centers += placed_centers
                wide += placed.widest > 1.0
        except Exception as err:
            self.host.set_busy(False)
            self._failed("space_words", err)
            return self._refresh_live_preview(force=True)
        moved = [clips[i] for i in picks]
        self._run("textplus.space", "Spacing words…", lambda: {**refs(picks), "centers": centers},
                  lambda: tpr.space_words(moved, centers, log=self.log, comp_of=self._get_fusion_comp,
                                          tool_of=self._find_text_tool),
                  lambda result, err: self._spaced_timeline(result, err, moved, groups, wide),
                  "space_words", host=_Held(self.host, up=False, down=True))

    def _spaced_timeline(self, result, error, clips, groups, wide):
        if error is not None:
            self._failed("space_words", error)
            return self._refresh_live_preview(force=True)
        self._said(result)
        self.log(f"[Word-by-word] Spaced {sum(len(g) for g in groups)} words in {len(groups)} group(s); "
                 f"{result['moved']} Text+ clip(s) moved.")
        if wide:
            self.log(f"[Warning] {wide} group(s) are wider than the frame – allow more lines, or make the words smaller.")
        self.emit("toast", {"text": f"Spaced {len(groups)} groups of words"})
        self._record_batch_action(self._position_steps(clips, result["steps"]))
        self._refresh_live_preview(force=True)

    def on_stack_words(self, _payload=None):
        """Stack on their own tracks: the Text+ clips selected on Resolve's timeline, each
        on a video track of its own and lasting until the playhead, in a copy of the
        timeline that's then opened (word_by_word.stack_plan, drt.restack_video_clips).
        The timeline itself isn't changed."""
        if self._working:
            return
        resolve = self._resolve()
        if resolve is None:
            return
        timeline = self._timeline_or_log(resolve)
        if timeline is None:
            return
        try:
            chosen, _desc = self._scope_clips(timeline, "selected", 1)
        except _ScopeUnavailable as exc:
            self.log(f"[Warning] {exc}")
            return self.emit("toast", {"text": str(exc)})
        words = []
        for clip, track in chosen:
            comp = self._get_fusion_comp(clip)
            tool = self._find_text_tool(comp) if comp is not None else None
            if tool is not None:
                try:
                    text = str(FusionAnimationEngine.words_of(tool) or "").strip()
                except Exception:
                    text = ""
                words.append((track, int(clip.GetStart()), int(clip.GetEnd()), text))
        if not words:
            self.log("[Word-by-word] No Text+ clips are selected on the timeline.")
            return self.emit("toast", {"text": "Select the Text+ clips to stack on Resolve's timeline first."})
        blank = sum(1 for w in words if not w[3])
        if blank:
            # Most likely Resolve's Inspector: with several Text+ selected, clicking into its
            # Text box (it shows "--") and away writes that empty box into every one of them.
            self.log(f"[Word-by-word] {blank} of the selected Text+ clip(s) have no text – nothing was stacked.")
            return self.emit("alert", {"title": "Stack on their own tracks", "text": (
                f"{blank} of the {len(words)} selected Text+ clips have no text, so nothing was stacked. If they had "
                "words, Resolve's Inspector may have emptied them: with several Text+ clips selected, clicking into "
                f"its Text box (it shows --) writes that empty box into every selected clip. Undo in Resolve "
                f"({'Cmd+Z' if sys.platform == 'darwin' else 'Ctrl+Z'}) may bring the words back.")})
        playhead = _get_current_frame(timeline)
        late = [w for w in words if playhead is None or w[1] >= playhead]
        if late:
            text = late[0][3] or "A selected clip"
            self.log(f"[Word-by-word] '{text}' starts at or after the playhead – nothing was stacked.")
            return self.emit("toast", {"text": f"'{text}' starts at or after the playhead – put the playhead where the words should end."})
        clips = []
        for track in range(1, int(timeline.GetTrackCount("video") or 0) + 1):
            for item in timeline.GetItemListInTrack("video", track) or []:
                try:
                    clips.append((track, int(item.GetStart()), int(item.GetEnd())))
                except Exception:
                    continue
        plan = word_by_word.stack_plan(clips, [w[:3] for w in words], playhead)
        # The export, the rewrite and the import in the reader's process: Buddy keeps drawing.
        self._run("textplus.stack", "Stacking the clips…",
                  lambda: {"timeline_id": _unique_id(timeline),
                           "plan": [[track, start, new, frames] for (track, start), (new, frames) in plan.items()]},
                  lambda: {"name": self._stack_into_new_timeline(resolve, timeline, plan)},
                  lambda result, error: self._stacked(result, error, plan), "stack_words")

    def _stacked(self, result, error, plan):
        if error is not None:
            if isinstance(error, drt.DrtError) or getattr(error, "kind", "") == "DrtError":
                self.log(f"[Error] {error}")
            else:
                self._failed("stack_words", error)
            return self.emit("alert", {"title": "Stack on their own tracks", "text": str(error)})
        name = result["name"]
        tracks = len({new for new, _duration in plan.values()})
        self.log(f"[Word-by-word] Stacked {len(plan)} Text+ clip(s) on {tracks} track(s) until the playhead, "
                 f"in a new timeline, '{name}' (opened). The timeline they came from is unchanged.")
        self.emit("toast", {"text": f"Stacked {len(plan)} Text+ clips on {tracks} tracks in '{name}'"})
        self._sync_timeline()
        self._refresh_live_preview(force=True)

    def _stack_into_new_timeline(self, resolve, timeline, plan) -> str:
        """The timeline exported as .drt, its clips restacked by `plan`, imported as a new
        timeline and opened (tpr.stack_into_new_timeline). Its name."""
        return tpr.stack_into_new_timeline(resolve, timeline, plan)

    # ------------------------------------------------------------ bounding --

    def _snap_stacked_items_vertically(self):
        """Enabling Bounding snaps 2+ clips stacked under the playhead together vertically
        (touching, no gaps or overlap), keeping the group's centre where it was."""
        items = self._items["layout"]
        if len(items) < 2:
            return
        width, height = self.resolution
        aspect = (width / height) if height else 1.0

        text_tools = {}
        for key, item in items.items():
            comp = self._get_fusion_comp(item.clip)
            text_tools[key] = self._find_text_tool(comp) if comp is not None else None

        # Heights as fractions of frame HEIGHT, centres top-origin: the canvas's own terms.
        # A box's middle is `offset` below its clip's Center (the Center is the line box's).
        stack, offsets = [], {}
        for key, item in items.items():
            if item.center is None:
                continue
            box = canvas_math.text_box(item.font_name, item.text, item.font_size, item.font_style, item.line_spacing)
            box_height = (box["h"] + 2 * _BOX_PADDING) * aspect
            offsets[key] = (box["top"] + box["h"] / 2) * aspect
            text_tool = text_tools.get(key)
            if text_tool is not None:
                try:
                    background_enabled = bool(text_tool.GetInput("Enabled4"))
                    extend_vertical = text_tool.GetInput("ExtendVertical4")
                except Exception:
                    background_enabled, extend_vertical = False, None
                if background_enabled and isinstance(extend_vertical, (int, float)):
                    box_height += 2.0 * float(extend_vertical)
            stack.append((key, 1.0 - item.center[1] + offsets[key], box_height))
        new_centers = compute_vertical_stack_centers(stack)

        sub_actions = []
        moved = 0
        for key, top_y, _h in stack:
            new_top_y = new_centers.get(key)
            if new_top_y is None or abs(new_top_y - top_y) < 0.5 / 360:
                continue
            item, text_tool = items[key], text_tools.get(key)
            if text_tool is None:
                self.log("[Bounding] Could not re-locate the Text+ tool for one stacked clip – skipped.")
                continue
            try:
                parsed_old_center = parse_point(text_tool.GetInput("Center"))
                old_center = list(parsed_old_center) if parsed_old_center is not None else None
            except Exception:
                old_center = None
            fusion_x, fusion_y = CoordinateTranslator.pyside_to_fusion(item.center[0], new_top_y - offsets[key], 1, 1)
            try:
                text_tool.SetInput("Center", [fusion_x, fusion_y])
                moved += 1
                if old_center is not None:
                    sub_actions.append(_LivePreviewAction(clip=item.clip, kind="position", old_value=old_center,
                                                          new_value=[fusion_x, fusion_y]))
            except Exception as err:
                self.log(f"[Bounding] SetInput Exception while vertically stacking: {err}")
        if moved:
            self.log(f"[Bounding] Vertically snapped {moved} stacked Text+ clip(s) together.")
            self._record_batch_action(sub_actions)
            self._refresh_live_preview(force=True)

    def on_apply_bounding(self, _payload=None):
        if self._working:
            return
        try:
            if not self.bounding["on"]:
                self.log("[Warning] Turn on Show bounding lines first.")
                return
            left_frac = min(self.bounding["left"], self.bounding["right"])
            right_frac = max(self.bounding["left"], self.bounding["right"])

            resolve = self._resolve()
            if resolve is None:
                return
            timeline = self._timeline_or_log(resolve)
            if timeline is None:
                return
            found = self._scope_or_toast(timeline, self.opts.scope("layout_scope"), self.opts.track("layout_track"))
            if found is None:
                return
            clips, scope_desc = found
            self.log(f"[Action] Applying Bounding to {scope_desc}…")
            timeline_width, timeline_height = get_timeline_resolution(timeline)
            # Read in the reader's process, each fit measured here (Qt), then put in
            # Resolve there - the overlay up throughout.
            refs = _Refs(timeline, clips)
            self._run("textplus.read", "Applying bounding…", lambda: {**refs(), "kind": "bounding"},
                      lambda: tpr.read_bounding(clips, log=self.log, comp_of=self._get_fusion_comp,
                                                tool_of=self._find_text_tool),
                      lambda read, error: self._fit_bounding(read, error, clips, refs, scope_desc, left_frac,
                                                             right_frac, timeline_width, timeline_height),
                      "apply_bounding_to_timeline", host=_Held(self.host, up=True, down=False))
        except Exception as err:
            self.log(f"[Error in apply_bounding_to_timeline]: {err}")
            self.log(traceback.format_exc())

    @staticmethod
    def _bounding_plan(records, left_frac, right_frac) -> Tuple[list, int]:
        """What Bounding does to each clip read (tpr.read_bounding), in order, measured
        here where there's Qt - (plan, clips scanned) for tpr.bounding: the lines to log,
        each fit's Size and Center, and the cap height its vertical snap goes by."""
        plan, scanned = [], 0
        new_center_x = (left_frac + right_frac) / 2.0
        for i, record in enumerate(records):
            if record is None:
                continue
            scanned += 1
            if not record["tool"]:
                continue
            clip_name = record["name"]
            if "error" in record:
                plan.append({"i": i, "line": f"  - Warning: Could not read style/position for clip '{clip_name}': "
                                             f"{record['error']}"})
                continue
            styled_text, font_name, font_size, center = (record["words"], record["font"], record["size"],
                                                         record["center"])
            if center is None or not isinstance(font_size, (int, float)):
                plan.append({"i": i, "line": f"  - Warning: Missing Center/Size for clip '{clip_name}' – skipped."})
                continue
            if styled_text is None and record.get("chained"):
                plan.append({"i": i, "line": f"  - Skipped clip '{clip_name}': its words are on chained text "
                                             "modifiers, which aren't read."})
                continue
            step = {"i": i, "name": clip_name, "processed": True,
                    "cap": list(canvas_math.cap_height_factors(font_name or "Arial"))}
            new_size = compute_bounding_fit_size(font_name, float(font_size), styled_text or "", left_frac, right_frac)
            if new_size is None:
                step["line"] = (f"  - Skipped clip '{clip_name}': the Bounding lines are degenerate (right must be "
                                "right of left).")
            else:
                step.update(size=new_size, center=[new_center_x, center[1]])
            plan.append(step)
        return plan, scanned

    def _fit_bounding(self, read, error, clips, refs, scope_desc, left_frac, right_frac, width, height):
        """Bounding's clips read: each one's fit measured here, then put in Resolve."""
        if error is None:
            self._said(read)
            try:
                plan, scanned = self._bounding_plan(read["clips"], left_frac, right_frac)
            except Exception as err:  # noqa: BLE001 - said below
                error = err
        if error is not None:
            self.host.set_busy(False)
            return self._failed("apply_bounding_to_timeline", error)
        self._run("textplus.bounding", "Applying bounding…",
                  lambda: {**refs(), "plan": plan, "scanned": scanned, "scope_desc": scope_desc,
                           "width": width, "height": height},
                  lambda: tpr.bounding(clips, plan, scanned, scope_desc, width, height, log=self.log,
                                       comp_of=self._get_fusion_comp, tool_of=self._find_text_tool),
                  self._bounded, "apply_bounding_to_timeline", host=_Held(self.host, up=False, down=True))

    def _bounded(self, result, error):
        if error is not None:
            return self._failed("apply_bounding_to_timeline", error)
        self._said(result)
        self.emit("toast", {"text": f"Bounding applied to {result['updated']} Text+ clip(s)"})
        self._refresh_live_preview(force=True)

    def on_apply_position(self, _payload=None):
        if self._working:
            return
        try:
            scope = self.opts.scope("layout_scope")
            if scope == "playhead":
                # Those are the clips it copies FROM; the view disables the button for this.
                return self.emit("toast", {"text": "Apply position copies from the clips under the playhead – choose another Apply to."})
            resolve = self._resolve()
            if resolve is None:
                return
            timeline = self._timeline_or_log(resolve)
            if timeline is None:
                return
            active_items = get_active_text_plus_items(timeline, self._get_fusion_comp, self._find_text_tool, log=self.log)
            reference_by_track = {item.track_index: item.center for item in active_items if item.center is not None}
            if not reference_by_track:
                self.log("[Warning] No Text+ clip with a readable position is currently under the playhead – nothing to apply.")
                return self.emit("toast", {"text": "No Text+ clip under the playhead to copy the position from."})
            found = self._scope_or_toast(timeline, scope, self.opts.track("layout_track"))
            if found is None:
                return
            clips, scope_desc = found
            self.log(f"[Action] Applying Position to {scope_desc}…")
            refs = _Refs(timeline, clips)
            self._run("textplus.position", "Applying position…",
                      lambda: {**refs(), "references": [[track, center[0], center[1]]
                                                        for track, center in reference_by_track.items()]},
                      lambda: tpr.apply_position(clips, reference_by_track, log=self.log,
                                                 comp_of=self._get_fusion_comp, tool_of=self._find_text_tool),
                      lambda result, error: self._positioned(result, error, clips, len(reference_by_track)),
                      "apply_position_to_timeline")
        except Exception as err:
            self.log(f"[Error in apply_position_to_timeline]: {err}")
            self.log(traceback.format_exc())

    def _positioned(self, result, error, clips, tracks):
        if error is not None:
            return self._failed("apply_position_to_timeline", error)
        self._said(result)
        updated = result["updated"]
        self.log(f"[Apply Position] Updated {updated}/{result['scanned']} Text+ clip(s) across "
                 f"{tracks} track(s) with a playhead reference.")
        self._record_batch_action(self._position_steps(clips, result["steps"]))
        self.emit("toast", {"text": f"Position copied to {updated} Text+ clip(s)"})
        self._refresh_live_preview(force=True)

    # ------------------------------------------------------------ undo / redo --

    def _apply_live_preview_action_part(self, action: "_LivePreviewAction", is_undo: bool) -> bool:
        clip = action.clip
        kind = action.kind
        value = action.old_value if is_undo else action.new_value
        if clip is None:
            return False
        comp = self._get_fusion_comp(clip)
        text_tool = self._find_text_tool(comp) if comp is not None else None
        if text_tool is None:
            self.log("[Undo/Redo] Could not re-locate the Text+ tool for this clip.")
            return False
        if kind == "animation":
            if value is None:
                _removed, remove_logs = FusionAnimationEngine.remove_animations_from_clip(text_tool, comp)
                for log_msg in remove_logs:
                    self.log(log_msg)
                # None of this tab's animation on it now is the step - done whether or not there
                # was any to take off, but not if a letter preset had to stay (a chain behind it).
                success = not (FusionAnimationEngine.animation_on(text_tool) or {}).get("follower")
            else:
                clip_name = clip.GetName() if callable(getattr(clip, "GetName", None)) else "Clip"
                if isinstance(value, tuple):
                    preset, speed, direction = value[:3]
                    fps = value[3] if len(value) > 3 else 24.0
                else:
                    preset, speed, direction, fps = value, None, None, 24.0
                success = self._apply_animation_to_tool(text_tool, comp, clip_name, preset,
                                                        speed=speed, direction=direction, fps=fps)
            return success
        input_name = "Center" if kind == "position" else "Size"
        try:
            text_tool.SetInput(input_name, value)
            return True
        except Exception as err:
            self.log(f"[Undo/Redo] {input_name} SetInput Exception: {err}")
            return False

    def _record_live_preview_action(self, clip: Any, kind: str, old_value: Any, new_value: Any):
        if old_value is None:
            self.log("[Undo/Redo] Could not record undo state for this edit (previous value unreadable).")
            return
        self._push_live_preview_action(_LivePreviewAction(clip=clip, kind=kind, old_value=old_value, new_value=new_value))

    def _record_batch_action(self, sub_actions: list):
        if not sub_actions:
            return
        self._push_live_preview_action(_LivePreviewAction(clip=None, kind="batch", old_value=sub_actions, new_value=sub_actions))

    def _push_live_preview_action(self, action: "_LivePreviewAction"):
        self._live_preview_undo_stack.append(action)
        if len(self._live_preview_undo_stack) > MAX_LIVE_PREVIEW_UNDO_HISTORY:
            self._live_preview_undo_stack.pop(0)
        self._live_preview_redo_stack.clear()
        self._push_history()

    @staticmethod
    def _action_label(action) -> str:
        """What a step was, for "Undid ... change": position, size, animation or layout."""
        if action.kind != "batch":
            return action.kind
        return "animation" if all(sub.kind == "animation" for sub in action.old_value) else "layout"

    def _apply_action(self, action, backward):
        if action.kind == "batch":
            results = [self._apply_live_preview_action_part(sub, backward) for sub in action.old_value]
            return any(results) if results else False
        return self._apply_live_preview_action_part(action, backward)

    def on_undo(self, _payload=None):
        if self._working:
            return
        if not self._live_preview_undo_stack:
            self.log("[Undo] Nothing to undo.")
            return self.emit("toast", {"text": "Nothing to undo"})
        action = self._live_preview_undo_stack.pop()
        if self._apply_action(action, True):
            self._live_preview_redo_stack.append(action)
            label = self._action_label(action)
            self.log(f"[Undo] Reverted {label} change.")
            self.emit("toast", {"text": f"Undid {label} change"})
            self._refresh_live_preview(force=True)
        else:
            self.log("[Undo] Failed to revert change, some clips may be missing.")
        self._push_history()

    def on_redo(self, _payload=None):
        if self._working:
            return
        if not self._live_preview_redo_stack:
            self.log("[Redo] Nothing to redo.")
            return self.emit("toast", {"text": "Nothing to redo"})
        action = self._live_preview_redo_stack.pop()
        if self._apply_action(action, False):
            self._live_preview_undo_stack.append(action)
            label = self._action_label(action)
            self.log(f"[Redo] Reapplied {label} change.")
            self.emit("toast", {"text": f"Redid {label} change"})
            self._refresh_live_preview(force=True)
        else:
            self.log("[Redo] Failed to reapply change, some clips may be missing.")
        self._push_history()

    # ------------------------------------------------------------ fusion lookups --

    def _get_fusion_comp(self, clip: Any) -> Optional[Any]:
        return tpr.fusion_comp(clip, self.log)

    def _find_text_tools(self, comp: Any) -> List[Any]:
        """Every Text+ in a comp - a Buddy title (Animation > Titles) is one per
        word, and a restyle is for all of them. [] when there's none."""
        return tpr.text_tools(comp, self.log, self._find_text_tool)

    def _find_text_tool(self, comp: Any) -> Optional[Any]:
        return tpr.text_tool(comp, self.log)

    # ------------------------------------------------------------ animations --

    def _scope_clips(self, timeline, scope: str, track: int) -> Tuple[list, str]:
        """[(clip, video track)] an "Apply to" choice covers, and how to say which:
        "timeline" every video track, "selected" the clips selected on Resolve's timeline
        (Timeline.GetSelectedClips - Resolve 21.0.4 and later; raises _ScopeUnavailable on an
        older one), "playhead" the Text+ under the playhead, "track" one video track. Not
        every clip is a Text+ - callers skip the ones without one."""
        if scope == "playhead":
            items = get_active_text_plus_items(timeline, self._get_fusion_comp, self._find_text_tool, log=self.log)
            return [(item.clip, item.track_index) for item in items], "the Text+ clip(s) under the playhead"
        if scope == "track":
            return [(c, track) for c in timeline.GetItemListInTrack("video", track) or []], f"Video Track {track}"
        if scope == "selected":
            selected = getattr(timeline, "GetSelectedClips", None)
            if not callable(selected):
                raise _ScopeUnavailable("Selected clips needs DaVinci Resolve 21.0.4 or later.")
            clips = []
            for clip in selected() or []:
                try:
                    kind, index = clip.GetTrackTypeAndIndex()
                except Exception:
                    continue
                if kind == "video":             # audio and subtitle clips hold no Text+
                    clips.append((clip, int(index)))
            clips.sort(key=lambda c: (c[1], c[0].GetStart() if callable(getattr(c[0], "GetStart", None)) else 0))
            return clips, "the selected clips"
        try:
            track_count = timeline.GetTrackCount("video") if hasattr(timeline, "GetTrackCount") else 0
        except Exception:
            track_count = 0
        clips_with_tracks = []
        for track_index in range(1, track_count + 1):
            track_clips = timeline.GetItemListInTrack("video", track_index) or []
            clips_with_tracks.extend((c, track_index) for c in track_clips)
        return clips_with_tracks, f"every video track ({track_count} track(s))"

    def _scope_or_toast(self, timeline, scope: str, track: int) -> Optional[Tuple[list, str]]:
        """_scope_clips, or None after saying what's wrong: nothing there, or a choice this
        Resolve can't do."""
        try:
            clips, desc = self._scope_clips(timeline, scope, track)
        except _ScopeUnavailable as exc:
            self.log(f"[Warning] {exc}")
            self.emit("toast", {"text": str(exc)})
            return None
        if not clips:
            self.log(f"[Info] No video clips found on {desc}.")
            self.emit("toast", {"text": _EMPTY_SCOPE.get(scope, _EMPTY_SCOPE["timeline"]).format(track=track)})
            return None
        return clips, desc

    def _apply_animation_to_tool(self, text_tool, comp, clip_name: str, preset_choice: str,
                                 speed: Optional[str] = None, direction: Optional[str] = None,
                                 fps: float = 24.0) -> bool:
        """One Text+ animated (tpr.apply_preset) - at the tab's speed and direction, unless
        given (Undo's, the ones it went on with)."""
        return tpr.apply_preset(text_tool, comp, clip_name, preset_choice,
                                self.opts.anim_speed if speed is None else speed,
                                self.opts.anim_direction if direction is None else direction,
                                fps, self.current_fusion_pos, self.log)

    @staticmethod
    def _timeline_fps(timeline: Any) -> float:
        try:
            fps = float(timeline.GetSetting("timelineFrameRate"))
        except Exception:
            return 24.0
        return fps if fps > 0 else 24.0

    def on_apply_animation(self, _payload=None):
        if self._working:
            return
        scope = self.opts.scope("anim_scope")
        try:
            resolve = self._resolve()
            if resolve is None:
                return
            timeline = self._timeline_or_log(resolve)
            if timeline is None:
                return
            fps = self._timeline_fps(timeline)
            preset_choice = self.opts.anim_preset
            self.log("[Action] Starting text animation…")
            found = self._scope_or_toast(timeline, scope, self.opts.track("anim_track"))
            if found is None:
                return
            video_clips_with_tracks, scope_desc = found
            self.log(f"Found {len(video_clips_with_tracks)} clip(s) on {scope_desc}. Applying animations…")
            speed, direction, center = self.opts.anim_speed, self.opts.anim_direction, self.current_fusion_pos
            refs = _Refs(timeline, video_clips_with_tracks)
            self._run("textplus.animate", "Applying animations…",
                      lambda: {**refs(), "preset": preset_choice, "speed": speed, "direction": direction, "fps": fps,
                               "fallback_center": list(center)},
                      lambda: tpr.apply_animation(video_clips_with_tracks, preset_choice, speed, direction, fps, center,
                                                  log=self.log, comp_of=self._get_fusion_comp,
                                                  tool_of=self._find_text_tool),
                      lambda result, error: self._animated(result, error, video_clips_with_tracks, scope_desc,
                                                           preset_choice),
                      "apply_animations")
        except Exception as err:
            self.log(f"[Error in apply_animations]: {err}")
            self.log(traceback.format_exc())

    def _animated(self, result, error, clips, scope_desc, preset_choice):
        if error is not None:
            return self._failed("apply_animations", error)
        self._said(result)
        count, left = result["count"], result["left"]
        self.log(f"Successfully animated {count}/{result['total']} Text+ clips on {scope_desc} using '{preset_choice}'.")
        self._record_batch_action([
            _LivePreviewAction(clip=clips[s["i"]][0], kind="animation", old_value=_tuple(s["old"]),
                               new_value=_tuple(s["new"]), clip_key=_tuple(s["key"]))
            for s in result["steps"]])
        # "has", not "animates": Character Level Styling holds the text without animating it.
        self.emit("toast", {"text": f"Animated {count} Text+ clip(s)" if not left else
                            f"Animated {count} Text+ clip(s). Left {left} alone: something else already has the "
                            "text, size, opacity or pivot there."})

    def on_remove_animations(self, _payload=None):
        if self._working:
            return
        try:
            resolve = self._resolve()
            if resolve is None:
                return
            timeline = self._timeline_or_log(resolve)
            if timeline is None:
                return
            self.log("[Action] Removing animations…")
            fps = self._timeline_fps(timeline)
            found = self._scope_or_toast(timeline, self.opts.scope("anim_scope"), self.opts.track("anim_track"))
            if found is None:
                return
            video_clips_with_tracks, scope_desc = found
            refs = _Refs(timeline, video_clips_with_tracks)
            self._run("textplus.unanimate", "Removing animations…", lambda: {**refs(), "fps": fps},
                      lambda: tpr.remove_animations(video_clips_with_tracks, fps, log=self.log,
                                                    comp_of=self._get_fusion_comp, tool_of=self._find_text_tool),
                      lambda result, error: self._unanimated(result, error, video_clips_with_tracks, scope_desc),
                      "remove_animations_from_timeline")
        except Exception as err:
            self.log(f"[Error in remove_animations_from_timeline]: {err}")
            self.log(traceback.format_exc())

    def _unanimated(self, result, error, clips, scope_desc):
        if error is not None:
            return self._failed("remove_animations_from_timeline", error)
        self._said(result)
        count, stuck = result["count"], result["stuck"]
        self.log(f"Removed animations from {count}/{result['total']} Text+ clips on {scope_desc}.")
        self._record_batch_action([
            _LivePreviewAction(clip=clips[s["i"]][0], kind="animation", old_value=_tuple(s["old"]), new_value=None,
                               clip_key=_tuple(s["key"]))
            for s in result["steps"]])
        if stuck:
            text = (f"Removed animations from {count} Text+ clip(s). On {stuck}, a letter animation stays: another "
                    "text modifier is chained behind its Follower – take the Follower off in Fusion.")
        elif count:
            text = f"Removed animations from {count} Text+ clip(s)"
        else:
            text = "None of those clips has an animation from this tab."
        self.emit("toast", {"text": text})

    def on_dump(self, _payload=None):
        self.log("[Action] Dumping selected node settings from Fusion…")
        resolve = self._resolve()
        if resolve is None:
            return
        try:
            success, message, saved_path = dump_selected_node_settings(resolve)
            self.log(message)
            if success and saved_path:
                self.log("Share that file's contents to help debug animation behavior.")
                self.emit("toast", {"text": "Saved the node's settings to your Desktop"})
        except Exception as err:
            self.log(f"[Error in dump_selected_node_settings]: {err}")
            self.log(traceback.format_exc())

    # ------------------------------------------------------------ font style --

    def on_look(self, payload):
        """Puts a ready-made ("wide"/"tall") or saved look on Font Styling's controls."""
        payload = payload or {}
        look = self.opts.pick_look(payload.get("kind"), payload.get("name"))
        if look is None:
            return self._push_options()
        if look.get("bold"):
            face = self._boldest_face(self.opts.font_name)
            if face is not None:
                self.opts.set("font_face", face)
        self._push_options()

    def _boldest_face(self, family: str):
        """The heaviest upright face of a family in the font picker, or None."""
        best, best_weight = None, _css_font(*self.opts.font_face)["weight"]
        for face_family, style in self._faces.values():
            if face_family.casefold() != family.casefold():
                continue
            css = _css_font(face_family, style)
            if not css["italic"] and css["weight"] > best_weight:
                best, best_weight = (face_family, style), css["weight"]
        return best

    def on_save_look(self, payload):
        """Keeps Font Styling's controls as a saved look (over one of the same name)."""
        name = str((payload or {}).get("name") or "").strip()
        width, height = self.resolution
        if not self.opts.save_look(name, tall=height > width):
            return self.emit("toast", {"text": "Couldn't save that look – give it a name." if not name
                                       else "That's as many saved looks as Buddy keeps – delete one first."})
        self._push_options()
        self.emit("toast", {"text": f"Saved the look '{self.opts.current_look[1]}'"})

    def on_rename_look(self, payload):
        payload = payload or {}
        if not self.opts.rename_look(payload.get("name"), payload.get("to")):
            return self.emit("toast", {"text": "There's already a saved look with that name."})
        self._push_options()

    def on_delete_look(self, payload):
        if self.opts.delete_look((payload or {}).get("name")):
            self._push_options()

    def on_reset_font(self, _payload=None):
        if self._working:
            return
        self.opts.set("font_face", (DEFAULT_FONT_NAME, DEFAULT_FONT_STYLE))
        self.opts.set("font_size", 0.08)
        self._push_options()
        self.log(f"Font reset to '{DEFAULT_FONT_NAME} {DEFAULT_FONT_STYLE}' – applying it to the clips in Apply to…")
        self._apply_style(force_font_only=True)

    def _get_playhead_text_tools(self, timeline):
        items = get_active_text_plus_items(timeline, self._get_fusion_comp, self._find_text_tool, log=self.log)
        results = []
        for item in items:
            comp = self._get_fusion_comp(item.clip)
            if comp is None:
                continue
            text_tool = self._find_text_tool(comp)
            if text_tool is None:
                continue
            results.append((item.clip, text_tool))
        return results

    def _style_settings(self) -> dict:
        """Font Styling's controls, as tpr.style_tool takes them."""
        o = self.opts
        settings = {name: o.toggle(name) for name in ("font_on", "outline_on", "shadow_on", "background_on")}
        settings.update({name: o.slider(name) for name in (
            "font_size", "outline_thickness", "outline_opacity", "shadow_offset_x", "shadow_offset_y", "shadow_blur",
            "shadow_opacity", "background_opacity", "background_corner_radius", "background_extend_horizontal",
            "background_extend_vertical")})
        settings.update({name: o.color(name) for name in ("font_color", "outline_color", "shadow_color",
                                                          "background_color")})
        settings["font_face"] = o.font_face
        return settings

    def _apply_font_style_to_tool(self, text_tool, clip_name: str, force_font_only=False) -> bool:
        """Font Styling on one Text+ (tpr.style_tool), counted in _style_kept if it keeps
        an outline, shadow or box that isn't Font Styling's."""
        styled, kept = tpr.style_tool(text_tool, clip_name, self._style_settings(), force_font_only, self.log)
        if kept:
            self._style_kept += 1
        return styled

    def on_apply_style(self, _payload=None):
        self._apply_style()

    def _apply_style(self, force_font_only=False):
        if self._working:
            return
        try:
            scope = self.opts.style_scope
            resolve = self._resolve()
            if resolve is None:
                return
            timeline = self._timeline_or_log(resolve)
            if timeline is None:
                return
            found = self._scope_or_toast(timeline, scope, self.opts.track("style_track"))
            if found is None:
                return
            clips, scope_desc = found
            self.log(f"[Action] Applying font style to {scope_desc}…")
            settings = self._style_settings()
            refs = _Refs(timeline, clips)
            # Made here (no reader), each clip is pumped (the window would otherwise go "Not
            # Responding" for the whole run): the poll mustn't call Resolve between two clips.
            self._preview_timer.stop()
            self._run("textplus.style", "Applying font style…",
                      lambda: {**refs(), "settings": settings, "force_font_only": force_font_only},
                      lambda: tpr.apply_style(
                          clips, settings, force_font_only, log=self.log, comp_of=self._get_fusion_comp,
                          tool_of=self._find_text_tool,
                          progress=lambda n, total: self.host.pump_busy(f"Applying font style… {n} of {total}")),
                      lambda result, error: self._styled(result, error, scope_desc), "apply_style")
        except Exception as err:
            self._preview_timer.start()
            self.log(f"[Error in apply_style]: {err}")
            self.log(traceback.format_exc())

    def _styled(self, result, error, scope_desc):
        self._preview_timer.start()
        if error is not None:
            return self._failed("apply_style", error)
        self._said(result)
        updated, scanned = result["updated"], result["scanned"]
        self._style_kept = result["kept"]
        self.log(f"Applied font style ('{face_label(*self.opts.font_face)}', size {self.opts.slider('font_size')}) "
                 f"to {updated}/{scanned} clip(s) on {scope_desc}.")
        if self._style_kept:
            self.log(f"{self._style_kept} clip(s) keep an outline, shadow or box Font Styling has no note of "
                     "switching on (set in the Inspector, or by an earlier Buddy) – tick it here and apply to "
                     "take it over; unticked after that, it goes off.")
        self.emit("toast", {"text": f"Styled {updated} Text+ clip(s)" if not self._style_kept else
                            f"Styled {updated} Text+ clip(s). On {self._style_kept}, an outline, shadow or box stays "
                            "that Buddy has no note of setting – tick it and apply to take it over."})
