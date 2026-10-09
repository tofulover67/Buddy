"""The Titles tab's Python side: the title being made (a composer document, kept in the
page's settings) and what the page needs to draw it - for page.py, which routes the
tab's "ti_*" messages here.

The page draws the title itself (web/titles_svg.js) and plays it (web/titles_motion.js),
so dragging, scrubbing and playing never wait on Python. What it can't work out alone
comes from here, as numbers: the composer's layout (each word's box, with its letters'
outline as SVG path data from render.outline - Qt's own letters), each word's style
with the person's changes on (looks.box_styles) and the clock (timing.timing). One
change in, one "ti" out - a few ms - then "ti_side": the Suggestions and, when the
words change, every look's layout for the Looks rail, so they never hold up the title.

The settings are written a moment after the last change, not on every one.

Place composes again at the timeline's own size, then titles_resolve.place puts it on
- on the page's Resolve worker, like every other Animation action.
"""

import copy
import math

from PySide6.QtCore import QTimer

from . import composer, kinetic, looks, measure, render, timing

SETTINGS_KEY = "titles"
MINE_KEY = "titles_mine"        # the person's own looks: [{"name", "doc"}]
UI_KEY = "titles_ui"            # how the tab's laid out: the dock folded, what's behind the frame
SAVE_DELAY_MS = 600
GALLERY_DELAY_MS = 250          # while the words are typed, the Looks rail waits for a pause
SECONDS = (1.0, 10.0)           # the title's length, s
DEFAULT_SECONDS = 3.0
ENTRANCES = ("look",) + timing.KINDS
EXITS = ("off", "reverse") + tuple(k for k in timing.KINDS if k != "type")
MAX_MINE = 24
MAX_IDS = 200                   # entries in one of the per-word maps, at most
BACKS = ("dark", "light", "checker")
UNITS = ("s", "f")              # times shown in seconds or in frames
EXPANDS = ("tl", "cv")           # a dock card made big: the timeline, or Curves

_DOC_KEYS = {
    "text": str, "look": str, "lines": (str, int), "arrange": str, "bw": (float, int, type(None)),
    "pos": str, "anim": str, "dir": (str, type(None)), "plays": str, "stagger": (float, int),
    "lang": str, "forced": (list, type(None)), "pulse": bool, "custom": bool, "camera": str,
}
# What "Save as my look" keeps: everything but the words themselves.
_LOOK_KEYS = ("look", "lines", "arrange", "bw", "pos", "custom", "gpos", "anim", "dir", "plays", "stagger",
              "exit", "pulse", "curves", "exitCurves", "over", "lineOffset", "lineRot", "lineColour", "lineAnim",
              "roleColour", "roleStyle", "roleAnim", "seconds", "camera")


# --------------------------------------------------------------- cleaning --

def _number(v, lo, hi):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return min(max(float(v), lo), hi)


def _pair(v, lo, hi):
    if isinstance(v, (list, tuple)) and len(v) == 2:
        a, b = _number(v[0], lo, hi), _number(v[1], lo, hi)
        if a is not None and b is not None:
            return [a, b]
    return None


def _ids(value, check):
    """A per-word (or per-line) map {word id: value}: ids are strings, values kept
    as check makes them (None drops one)."""
    out = {}
    if isinstance(value, dict):
        for key, v in list(value.items())[:MAX_IDS]:
            if isinstance(key, str) and 0 < len(key) <= 120:
                made = check(v)
                if made is not None:
                    out[key] = made
    return out


_ROLE_OF = lambda v: v if v in looks.ROLES else None
_KIND_OF = lambda v: v if v in timing.KINDS else None
_DIR_OF = lambda v: v if v in timing.DIRECTIONS else None
_COLOUR_OF = lambda v: v if looks.is_colour(v) else None
_OVER = {
    "roles": _ROLE_OF,
    "size": lambda v: _number(v, 0.25, 4.0),
    "offset": lambda v: _pair(v, -20.0, 20.0),
    "colour": _COLOUR_OF,
    "brk": lambda v: v if isinstance(v, bool) else None,
    "anim": _KIND_OF,
    "rot": lambda v: _number(v, -180.0, 180.0),
    "stroke": lambda v: _number(v, 0.0, 4.0),
    "len": lambda v: _number(v, 1 / 60, 4.0),
    "exitLen": lambda v: _number(v, 1 / 60, 4.0),
    "exitAnim": lambda v: v if v in EXITS[1:] else None,
    "exitDir": lambda v: v if v in timing.DIRECTIONS else None,
    "exitCurves": timing.clean_curve,
    "dir": _DIR_OF,
    "curves": timing.clean_curve,
}


def clean(saved):
    """The kept title: a composer document plus its length ("seconds"), every value the
    page sent made safe."""
    doc = composer.new_doc("3 secrets nobody tells you", "hormozi")
    seconds = DEFAULT_SECONDS
    saved = saved if isinstance(saved, dict) else {}       # nothing kept yet: every map still made
    for key, kinds in _DOC_KEYS.items():
        if key in saved and isinstance(saved[key], kinds):
            doc[key] = saved[key]
    if isinstance(saved.get("exit"), dict):
        doc["exit"] = {**doc["exit"], **{k: v for k, v in saved["exit"].items() if k in ("on", "style", "order", "dir")}}
    if isinstance(saved.get("seconds"), (int, float)):
        seconds = float(saved["seconds"])
    over = saved.get("over") if isinstance(saved.get("over"), dict) else {}
    doc["over"] = {name: _ids(over.get(name), check) for name, check in _OVER.items()}
    doc["lineOffset"] = _ids(saved.get("lineOffset"), lambda v: _pair(v, -20.0, 20.0))
    doc["lineRot"] = _ids(saved.get("lineRot"), lambda v: _number(v, -180.0, 180.0))
    doc["lineColour"] = _ids(saved.get("lineColour"), _COLOUR_OF)
    doc["lineAnim"] = _ids(saved.get("lineAnim"), _KIND_OF)
    roles = lambda value, check: {r: check(v) for r, v in (value or {}).items()
                                  if r in looks.ROLES and check(v) is not None} if isinstance(value, dict) else {}
    doc["roleColour"] = roles(saved.get("roleColour"), _COLOUR_OF)
    doc["roleAnim"] = roles(saved.get("roleAnim"), _KIND_OF)
    doc["roleStyle"] = roles(saved.get("roleStyle"),
                             lambda v: {"font": list(v["font"])} if isinstance(v, dict) and looks.is_face(v.get("font")) else None)
    doc["times"] = _ids(saved.get("times"), lambda v: _number(v, 0.0, SECONDS[1]))
    doc["exitTimes"] = _ids(saved.get("exitTimes"), lambda v: _number(v, 0.0, SECONDS[1]))
    doc["curves"] = timing.clean_curve(saved.get("curves"))
    doc["exitCurves"] = timing.clean_curve(saved.get("exitCurves"))
    doc["gpos"] = _pair(saved.get("gpos"), -0.25, 1.25)
    doc["turns"] = _ids(saved.get("turns"), lambda v: v if v in kinetic.MARKS + ("none",) else None)
    if doc["camera"] not in kinetic.CAMERAS:
        doc["camera"] = "each"
    if doc["look"] not in looks.LOOKS:
        doc["look"] = next(iter(looks.LOOKS))
    if doc["lines"] not in ("auto", 1, 2, 3, 4, 5):
        doc["lines"] = "auto"
    if doc["arrange"] not in ("auto",) + composer.ARRANGES:
        doc["arrange"] = "auto"
    if doc["pos"] not in ("look",) + composer.POSITIONS:
        doc["pos"] = "look"
    if doc["anim"] not in ENTRANCES:
        doc["anim"] = "look"
    if doc["dir"] not in timing.DIRECTIONS:
        doc["dir"] = None
    if doc["plays"] not in ("word", "line", "all"):
        doc["plays"] = "word"
    if doc["exit"].get("style") not in EXITS[1:]:
        doc["exit"]["style"] = "reverse"
    if doc["exit"].get("order") not in ("same", "reverse", "all"):
        doc["exit"]["order"] = "same"
    doc["exit"]["on"] = bool(doc["exit"].get("on"))
    if doc["exit"].get("dir") not in timing.DIRECTIONS:
        doc["exit"]["dir"] = None
    if doc["forced"] is not None and not all(isinstance(i, str) for i in doc["forced"]):
        doc["forced"] = None
    doc["custom"] = bool(doc["custom"] and doc.get("gpos"))
    doc["stagger"] = min(max(float(doc["stagger"]), 0.0), 0.5)
    if doc["bw"] is not None:
        doc["bw"] = min(max(float(doc["bw"]), 0.3), 1.0)
    doc["text"] = doc["text"][:200]
    seconds = min(max(seconds, SECONDS[0]), SECONDS[1])
    return {**doc, "seconds": seconds}


def defaults():
    """Reset all: every key of a fresh title but its words and look (and their language)."""
    return {k: v for k, v in clean({}).items() if k not in ("text", "look", "lang")}


def clean_ui(saved):
    saved = saved if isinstance(saved, dict) else {}
    guides = saved.get("guides") if isinstance(saved.get("guides"), dict) else {}
    return {"dock": saved.get("dock") is not False,
            "back": saved.get("back") if saved.get("back") in BACKS else "dark",
            "units": saved.get("units") if saved.get("units") in UNITS else "s",
            "askReset": saved.get("askReset") is not False,      # Reset all asks first
            "expand": saved.get("expand") if saved.get("expand") in EXPANDS else None,
            "guides": {"safe": guides.get("safe") is not False, "thirds": guides.get("thirds") is True}}


def compose(title, frame, prev=None):
    return composer.compose(_doc(title), frame, measure.measure, measure.metrics, prev=prev, ink=measure.ink)


def _doc(title):
    return {k: v for k, v in title.items() if k != "seconds"}


def numbers(layout, doc):
    """A layout as the page draws it: the boxes, each with its letters' outline ("d")
    and their ink box ("ink"), its bars, and each box's style with doc's changes on."""
    boxes = []
    for b in layout["boxes"]:
        d, ink = render.outline(b)
        boxes.append({**b, "font": list(b["font"]), "d": d, "ink": ink})
    return {"frame": list(layout["frame"]), "boxes": boxes, "bars": layout.get("bars") or [],
            "lines": layout["lines"], "styles": looks.box_styles(layout, doc), "look": layout["look"],
            "kinetic": layout.get("kinetic")}


class Studio:
    """The tab's state and its answers. page: the AnimationPage (settings, emit,
    _start, _controller)."""

    def __init__(self, page):
        self.page = page
        self.frame = (1920, 1080)       # the timeline's size, once read
        self.fps = 24.0
        self._prev = None               # the layout's key before this edit (composer's STICK)
        self._layout = None
        self._gallery_for = None        # what the Looks rail was last laid out for
        self._save = QTimer()
        self._save.setSingleShot(True)
        self._save.setInterval(SAVE_DELAY_MS)
        self._save.timeout.connect(self._write)
        self._gallery = QTimer()
        self._gallery.setSingleShot(True)
        self._gallery.setInterval(GALLERY_DELAY_MS)
        self._gallery.timeout.connect(lambda: self._side(self.title, self._layout, True))
        measure.load_bundled()

    # ------------------------------------------------------------- state --

    @property
    def title(self):
        return clean(self.page.settings.get(SETTINGS_KEY))

    @property
    def ui(self):
        return clean_ui(self.page.settings.get(UI_KEY))

    @property
    def mine(self):
        saved = self.page.settings.get(MINE_KEY)
        return [m for m in saved if isinstance(m, dict) and isinstance(m.get("doc"), dict)][:MAX_MINE] \
            if isinstance(saved, list) else []

    def _keep(self, key, value):
        """Into the settings now; onto disk a moment after the last change."""
        self.page.settings[key] = value
        self._save.start()

    def _write(self):
        self.page.settings.save()

    # -------------------------------------------------------------- view --

    def view(self, gallery=False, seq=None, typing=False):
        """The title as the page draws it: "ti", then "ti_side". typing: the words just
        changed - the Looks rail is laid out again once they stop changing, not on every
        letter (ten layouts would hold up the next one)."""
        title = self.title
        doc = _doc(title)
        layout = compose(title, self.frame, self._prev)
        self._layout, self._prev = layout, layout["key"]
        drawn = numbers(layout, doc)
        clock = timing.timing(doc, layout, title["seconds"], drawn["styles"])
        look = looks.get(title["look"])
        out = {
            **drawn, "seq": seq, "title": title, "fps": self.fps, "safe": list(layout["safe"]),
            "block": layout["block"], "key": list(layout["key"]),
            "tokens": layout["tokens"],
            "alternates": [{"key": list(a["key"]), "lines": a["lines"]} for a in layout["alternates"]],
            "clock": clock, "intro": clock["intro"], "min_seconds": round(timing.min_duration(clock), 2),
            "curves": timing.CURVES, "slide": timing.SLIDE,
            "pulses": any(timing.spread_len(looks.role_style(look, role)) for role in looks.ROLES),
            "looks": [{"id": i, "label": looks.get(i)["label"], "anim": looks.get(i).get("anim") or "pop",
                       "group": looks.get(i).get("group") or ""}
                      for i in looks.LOOKS],
            "roleStyles": {r: looks.role_style(look, r, doc) for r in looks.ROLES},
            "faces": sorted([list(f) for f in measure.bundled_files()]),
            "mine": [m.get("name") or "My look" for m in self.mine],
            "ui": self.ui,
            "defaults": defaults(),
        }
        self.page.emit("ti", out)
        key = (title["text"], title["lang"], self.frame)
        changed = key != self._gallery_for
        self._gallery_for = key
        if typing and changed and not gallery:
            self._gallery.start()
            gallery = False
        else:
            gallery = gallery or changed
            if gallery:
                self._gallery.stop()
        QTimer.singleShot(0, lambda: self._side(title, layout, gallery))

    def _side(self, title, layout, gallery):
        """The Suggestions drawn small and, when asked, the Looks rail and the person's
        own looks - each with the current words."""
        doc = _doc(title)
        alts = []
        for a in layout["alternates"]:
            alt = composer.compose({**doc, "forced": list(a["key"])}, self.frame, measure.measure, measure.metrics,
                                   ink=measure.ink)
            alts.append({**numbers(alt, doc), "key": list(a["key"]), "count": a["lines"]})
        out = {"alternates": alts}
        if gallery:
            plain = {"text": title["text"], "lang": title["lang"]}
            out["gallery"] = {}
            for look_id in looks.LOOKS:
                d = {**composer.new_doc(title["text"], look_id), **plain}
                out["gallery"][look_id] = numbers(composer.compose(d, self.frame, measure.measure, measure.metrics,
                                                                   ink=measure.ink), d)
            out["mine"] = []
            for m in self.mine:
                d = clean({**m["doc"], **plain})
                out["mine"].append(numbers(compose(d, self.frame), _doc(d)))
        self.page.emit("ti_side", out)

    # ---------------------------------------------------------- messages --

    def on_change(self, payload):
        """A change to the title: {"change": {...}, "seq": n} - top-level keys replaced
        (the page sends a whole map, e.g. all of "over", when one entry in it changes).
        A new text or look clears a picked Suggestion (its breaks were for the old words);
        a new built-in entrance clears the title's own drawn one."""
        change = (payload or {}).get("change")
        if not isinstance(change, dict):
            return
        title = self.title
        if "exit" in change and isinstance(change["exit"], dict):
            change = {**change, "exit": {**title["exit"], **change["exit"]}}
        if ("text" in change and change["text"] != title["text"]) or ("look" in change and change["look"] != title["look"]):
            title["forced"] = None
            if "look" in change:
                self._prev = None
        new_breaks = change["over"].get("brk") if isinstance(change.get("over"), dict) else title["over"].get("brk")
        if "lines" in change or new_breaks != title["over"].get("brk"):
            title["forced"] = None
        if "anim" in change and "curves" not in change:
            title["curves"] = None
        title.update(change)
        title = clean(title)
        if {"arrange", "turns", "text", "camera"} & set(change):
            title = self._room_for_kinetic(title)
        self._keep(SETTINGS_KEY, title)
        self.view(seq=(payload or {}).get("seq"), typing="text" in change or "lang" in change)

    def _room_for_kinetic(self, title):
        """A kinetic title's blocks come in one after another, the camera moving on with
        each: as it's chosen (or its words or marks change), the title gets the length
        to play them all - it's never shortened."""
        doc = _doc(title)
        layout = compose(title, self.frame)
        if not layout.get("kinetic"):
            return title
        need = timing.min_duration(timing.timing(doc, layout, title["seconds"]))
        if need > title["seconds"]:
            title = {**title, "seconds": min(SECONDS[1], math.ceil(need * 10) / 10)}
        return title

    def on_ui(self, payload):
        """How the tab's laid out: {"dock": bool, "back": ..., "units": ..., "askReset": bool, "guides": {...}}."""
        if isinstance(payload, dict):
            current = self.ui
            ui = clean_ui({**current, **payload})
            # A value it can't use leaves the one before, not the default.
            for key, allowed in (("back", BACKS), ("units", UNITS)):
                if key in payload and payload[key] not in allowed:
                    ui[key] = current[key]
            self._keep(UI_KEY, ui)

    def on_save_look(self, payload=None):
        """The title's look, as it is now, kept as one of the person's own."""
        mine = self.mine
        if len(mine) >= MAX_MINE:
            return self.page.emit("toast", {"text": f"You have {MAX_MINE} looks of your own - remove one first."})
        title = self.title
        name = str((payload or {}).get("name") or "").strip()[:40] or f"My look {len(mine) + 1}"
        mine.append({"name": name, "doc": copy.deepcopy({k: title[k] for k in _LOOK_KEYS if k in title})})
        self._keep(MINE_KEY, mine)
        self.page.emit("toast", {"text": f"Saved \"{name}\" - it's under Yours in Looks."})
        self.view(gallery=True)

    def on_use_look(self, payload):
        mine, i = self.mine, (payload or {}).get("index")
        if not isinstance(i, int) or not 0 <= i < len(mine):
            return
        title = self.title
        title.update(copy.deepcopy(mine[i]["doc"]))
        title["forced"] = None
        self._prev = None
        self._keep(SETTINGS_KEY, clean(title))
        self.view()

    def on_forget_look(self, payload):
        mine, i = self.mine, (payload or {}).get("index")
        if isinstance(i, int) and 0 <= i < len(mine):
            del mine[i]
            self._keep(MINE_KEY, mine)
            self.view(gallery=True)

    def set_timeline(self, frame, fps):
        """The timeline's size and rate (read by the page): the layout follows them."""
        frame = (int(frame[0]), int(frame[1]))
        if frame != self.frame or float(fps) != self.fps:
            self.frame, self.fps = frame, float(fps)
            self._prev = None
            self.view(gallery=True)

    def place_job(self):
        """Place, as a job for the page's worker: (job(controller), what to say after)."""
        from .. import motion_resolve
        from . import titles_resolve
        title = self.title

        def job(controller):
            project, timeline = motion_resolve.current_timeline(controller)
            frame, fps, _drop = titles_resolve._timeline_info(timeline)
            layout = compose(title, frame)
            notes = []
            done = titles_resolve.place(controller.resolve, project, timeline, layout, _doc(title),
                                        seconds=title["seconds"], log=notes.append)
            return {**done, "notes": notes, "frame": list(frame), "fps": fps}
        return job

    def close(self):
        if self._save.isActive():
            self._save.stop()
            self._write()
