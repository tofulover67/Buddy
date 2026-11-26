"""A panel, as data: a mock app window - a kind of window in a style - with pages, each
a tree of boxes laid out by a few rules (solve.py), boxes that answer a cursor, and a
cursor that was moved and clicked over them. The one description the view in Buddy
draws and the comp in Resolve is written from. No Qt, no Resolve.

A panel is designed in panel px: the window is "width" wide and as tall as its bar
and its tallest page make it. On the frame it is drawn "zoom" times frame width / 1920
larger, its middle at "x", "y" (% of the frame, across and down). The cursor's "path"
and "clicks" are Interactions' (seconds, and % of the frame).

A page is {"id", "name", "layout" (the catalogue's layout it was made from, "" for
none), "secs", "by", "root"}; the root and everything under it is a box.

The story ("auto": true): Buddy moves the cursor. The clip shows the pages in the order
they are listed, each for its "secs" (0: as long as what happens on it needs), and goes
on to the next when the cursor clicks the page's "by" box - or, "by" "", when its time
is up. On each page the cursor also clicks, one after another, every box whose click
starts something there (a bar, a count, a field's typing). story() says when each of
those happens; clean() makes the cursor's "path" and "clicks", the "page_keys", the
linked boxes' "go", "start" and "seconds" from it - so the clock, the preview and the
comp read a panel with a story exactly as they read one whose cursor was recorded
("auto": false: the path, clicks, links and keys are the panel's own).

A box:

    role      section (holds "children"), text, button, field, image, icon, divider,
              badge, progress, chart
    layout    "dir", "gap", "pad" [top, right, bottom, left], "align", "justify", "wrap",
              "equal", "w" and "h" ("fill", "hug" or px), "minw", "maxw", "minh",
              "aspect", "hide_below", "center" - what each means is solve.py's to say
    look      "variant" (VARIANTS, by role), "text", "right", "strong", "on", "p" (a
              progress bar's %), "chart" ({"shape", "rows"}, a chart's only), "hue"
    behaviour "reacts" (it answers the cursor), "states" (its own hover and press, in
              place of the panel's "answers"), "go" ({"page", "with"}: clicked, the
              panel goes to that page)
    round 4   "count" (a text's number runs up from nothing to what it says), "run"
              ({"by", "seconds"}: when a progress bar's or a chart's draw-on, or a count,
              starts - "by" the box whose click starts it, "" for with the page - and how
              long it takes, at a steady rate; None: as the page builds)
    round 2   "image" ({"path", "px"}: an image box's picture, filling it and cropped to
              it), "type" ({"speed", "start", "caret", "text"}: a text's or a field's
              words type on), "arrive" ({"kind"}: its own way of arriving, in place of
              the page's build) - each None when the box hasn't one

clean() makes anything whole: every box has every key, and an id ("b" and a number)
no other box of the panel has. A new panel is the template: an app window with three
pages, the first one's button linked to the second, and a cursor that crosses to that
button and clicks it. An edit (with_*, without_*) gives a new panel, or None when it
changes nothing or can't be made.
"""

import hashlib
import json
import os
import re

from pages.data_graphics.easing import colour, number
from pages.data_graphics.graphs import model as graphs
from pages.interactions.model import CURSORS, DEFAULTS as _INTERACTIONS, MAX_PATH, MAX_PX, is_image, thinned

from . import catalogue, solve
from .catalogue import ACCENTS, KINDS, STYLES

ROLES = ("section", "text", "button", "field", "image", "icon", "divider", "badge", "progress", "chart")
VARIANTS = {
    "section": ("", "card", "tint", "row", "navitem", "hero", "bubble", "bubble-me"),
    "text": ("body", "h1", "h2", "caption", "label", "num", "up", "down"),
    "button": ("", "ghost"),
    "icon": ("", "avatar", "sm"),
}
CHART_SHAPES = ("bars", "line", "area", "pie", "donut", "spark")
GO_WITH = ("fade", "slide", "match")
STATES = ("hover", "press")
ANSWER_KEYS = {"grow": (-50.0, 50.0), "lift": (-40.0, 40.0), "glow": (0.0, 100.0)}
MIN_WIDTH, MAX_WIDTH = 320.0, 1600.0
MAX_PAGES = 8
MAX_BOXES = 400             # a page's
MAX_DEPTH = 12
MAX_RULES = 8
MAX_PAGE_KEYS = 40
MIN_SECONDS, MAX_SECONDS = 1.0, 60.0
MAX_TEXT = 240
MAX_NAME = 40
MAX_FONT = 80
MAX_KEYS = 600              # of the cursor's path
MAX_CLICKS = 60
FRAME = solve.FRAME
# Typing: letters a second, and how a box that types begins - from when it has arrived,
# or from the first click on it.
TYPE_SPEED = (2.0, 60.0)
TYPE_STARTS = ("page", "click")
TYPE_ROLES = ("text", "field")
ARRIVALS = ("rise", "fade", "pop", "none")
# What starts by itself or by a click and runs at a steady rate: a bar or chart drawing on, a number counting up.
RUN_SECONDS = (0.2, 60.0)
RUN_ROLES = ("progress", "chart")
COUNT_SECONDS = 1.5         # how long a count takes unless told
# The pages a new panel has, and the layout each is made from.
TEMPLATE_PAGES = (("Sign in", "signin"), ("Dashboard", "dashboard"), ("Settings", "settings"))
# The story's pace, seconds: a page shown with nothing to click; how long after a page
# arrives its first click comes, and the next after that; what is left after the last
# thing has run; how long before a page's time is up its leaving click comes.
PAGE_SECONDS = 2.0
MAX_PAGE_SECONDS = 30.0
STORY_LEAD, STORY_GAP, STORY_TAIL, STORY_LEAVE = 1.0, 1.2, 0.5, 0.2
CURSOR_TRAVEL, CURSOR_EARLY, CURSOR_STAYS, CLICK_SECONDS = 0.8, 0.2, 0.3, 0.12
CURSOR_HOME = (30.0, 96.0)  # where the cursor waits before its first click, % of the frame

# Everything about a panel but its pages and its cursor, which the template has.
DEFAULTS = {
    "kind": "app", "style": "mac", "accent": ACCENTS[0], "dark": True,
    "title": "",
    "width": 620.0, "zoom": 1.5, "x": 50.0, "y": 50.0,
    "font": "Arial", "face": "Regular", "bold_face": "Bold",
    "seconds": 6.0,
    "auto": False,          # Buddy moves the cursor, by the pages' story (a new panel: true)
    # The pointer of the computer Buddy is on, to start with, as Interactions has it.
    "cursor": _INTERACTIONS["cursor"], "cursor_size": 5.0,
    # How every reacting box answers, unless it has states of its own: % bigger, px
    # lifted, % of a full glow.
    "answers": {"hover": {"grow": 4.0, "lift": 2.0, "glow": 50.0}, "press": {"grow": -4.0, "lift": 0.0, "glow": 0.0},
                "hover_seconds": 0.2, "press_seconds": 0.1},
    "go_with": "slide", "go_seconds": 0.4,
    "rules": [], "page_keys": [],
    "build": {"on": True, "stagger": 0.04, "seconds": 0.35},
}
# The keys a box has, in order, and what one that says nothing gets.
BOX_DEFAULTS = {
    "dir": "column", "gap": 0.0, "pad": [0.0, 0.0, 0.0, 0.0], "align": "stretch", "justify": "start",
    "wrap": False, "equal": 0.0, "w": "fill", "h": "hug", "minw": 0.0, "maxw": 0.0, "minh": 0.0,
    "aspect": 0.0, "hide_below": 0.0, "center": False,
    "variant": "", "text": "", "right": False, "strong": False, "on": False, "p": 60.0, "chart": None, "hue": 0.0,
    "reacts": False, "states": None, "go": None,
    "image": None, "type": None, "arrive": None,
    "count": False, "run": None,
}
BOX_KEYS = ("name", *(key for key in BOX_DEFAULTS if key != "image"))   # what with_box may change (a picture: with_image)
FILLS = ("section", "image", "chart", "field", "progress", "divider")      # the roles as wide as their room, unless told
ROLE_NAMES = {"text": "Text", "button": "Button", "field": "Field", "image": "Image", "icon": "Icon",
              "divider": "Divider", "badge": "Badge", "progress": "Progress", "chart": "Chart"}
_ID = re.compile(r"^[bpr][1-9]\d{0,8}$")


# ------------------------------------------------------------------ a box --

def _words(value, most):
    return " ".join(str(value if value is not None else "").split())[:most]


def _side(value, auto):
    """A box's width or height: "fill", "hug" or px."""
    if value in ("fill", "hug"):
        return value
    px = number(value, None, 1.0, MAX_WIDTH) if not isinstance(value, bool) else None
    return auto if px is None else px


def _pad(value):
    if isinstance(value, (list, tuple)) and len(value) == 4:
        return [number(v, 0.0, 0.0, 200.0) for v in value]
    return [number(value, 0.0, 0.0, 200.0)] * 4


def _answer(raw, auto):
    raw = raw if isinstance(raw, dict) else {}
    return {key: number(raw.get(key), auto[key], low, high) for key, (low, high) in ANSWER_KEYS.items()}


def _states(raw):
    if not isinstance(raw, dict):
        return None
    return {state: _answer(raw.get(state), DEFAULTS["answers"][state]) for state in STATES}


def _chart(raw):
    raw = raw if isinstance(raw, dict) else {}
    rows = graphs.clean_rows(raw["rows"]) if isinstance(raw.get("rows"), list) else []
    return {"shape": raw.get("shape") if raw.get("shape") in CHART_SHAPES else "bars",
            # Graphs' own idea of a row (a label, a number of 0 or more, a dozen at most), without its timings.
            "rows": [{"label": r["label"], "value": r["value"]} for r in rows] or catalogue.rows_of(catalogue.DAYS, 1)}


def _image(raw):
    """An image box's picture, {"path", "px"}, or None when there's no usable one."""
    if not isinstance(raw, dict):
        return None
    path = raw.get("path") if isinstance(raw.get("path"), str) else ""
    try:
        wide, high = (int(v) for v in raw.get("px"))
    except (TypeError, ValueError):
        return None
    if not path or len(path) > MAX_PATH or not is_image(path) or not (0 < wide <= MAX_PX and 0 < high <= MAX_PX):
        return None
    return {"path": path, "px": [wide, high]}


def _type(raw, role):
    """How a text's or a field's words type on, or None. A field types its own "text"
    (its box's "text" is the placeholder shown until then); a text types its words."""
    if not isinstance(raw, dict) or role not in TYPE_ROLES:
        return None
    field = role == "field"
    return {"speed": number(raw.get("speed"), 18.0, *TYPE_SPEED),
            "start": raw.get("start") if raw.get("start") in TYPE_STARTS else "click" if field else "page",
            "caret": bool(raw.get("caret", True)),
            "text": _words(raw.get("text"), MAX_TEXT) if field else ""}


_NUMBER = re.compile(r"(\d+):(\d\d)(?!\d)|\d[\d,]*(?:\.\d+)?")


def counted(text, share):
    """A text with its first number `share` (0..1) of the way up from nothing: a time
    ("1:24") counts its seconds, any other number keeps its decimals and its commas -
    "$1,204" at a half is "$602". The text itself for one with no number, or at 1."""
    found = _NUMBER.search(text)
    share = min(1.0, max(0.0, float(share)))
    if found is None or share >= 1.0:
        return text
    said = found.group(0)
    if found.group(1) is not None:
        now = int((int(found.group(1)) * 60 + int(found.group(2))) * share + 1e-9)
        made = f"{now // 60}:{now % 60:02d}"
    else:
        plain = said.replace(",", "")
        places = len(plain.partition(".")[2])
        now = float(plain) * share
        made = f"{now:.{places}f}" if places else str(int(now + 1e-9))
        if "," in said:
            whole, dot, rest = made.partition(".")
            made = f"{int(whole):,}" + dot + rest
    return text[:found.start()] + made + text[found.end():]


def counts(text):
    """Whether a text has a number to count up to."""
    return _NUMBER.search(text or "") is not None


def _run(raw, auto=None):
    """When a draw-on or a count starts and how long it takes, or `auto` for none."""
    if not isinstance(raw, dict):
        return auto
    return {"by": raw.get("by") if isinstance(raw.get("by"), str) else "",
            "seconds": number(raw.get("seconds"), (auto or {}).get("seconds", 3.0), *RUN_SECONDS)}


def _arrive(raw):
    if not isinstance(raw, dict) or raw.get("kind") not in ARRIVALS:
        return None
    return {"kind": raw["kind"]}


def _box(raw, along, depth, left):
    """A whole box without a sure id yet, or None for what isn't one. along: whether its
    parent is a row; left: [how many more boxes the page may have]."""
    if not isinstance(raw, dict) or raw.get("role") not in ROLES or left[0] <= 0:
        return None
    left[0] -= 1
    role = raw["role"]
    section = role == "section"
    variant = raw.get("variant") if raw.get("variant") in VARIANTS.get(role, ("",)) else VARIANTS.get(role, ("",))[0]
    direction = raw.get("dir") if raw.get("dir") in ("row", "column") else "column"
    round_chart = role == "chart" and isinstance(raw.get("chart"), dict) and raw["chart"].get("shape") in ("pie", "donut")
    wide = "fill" if role in FILLS or (role == "text" and not along) else "hug"
    high = _side(raw.get("h"), "hug")
    aspect = number(raw.get("aspect"), 0.0, 0.0, 20.0)
    if role in ("image", "chart") and aspect < 0.05 and high == "hug":
        aspect = 1.0 if round_chart else 16 / 9 if role == "image" else 2.5        # a picture is never no height
    children = []
    if section and depth < MAX_DEPTH:
        for child in raw.get("children") if isinstance(raw.get("children"), list) else []:
            made = _box(child, direction == "row", depth + 1, left)
            if made is not None:
                children.append(made)
    name = _words(raw.get("name"), MAX_NAME) or (
        ROLE_NAMES[role] if not section else "Card" if variant == "card" else "Row" if direction == "row" else "Column")
    return {
        "id": raw.get("id") if isinstance(raw.get("id"), str) else "", "role": role, "name": name,
        "dir": direction, "gap": number(raw.get("gap"), 8.0 if section else 0.0, 0.0, 200.0), "pad": _pad(raw.get("pad")),
        "align": raw.get("align") if raw.get("align") in ("start", "center", "end", "stretch")
        else "center" if direction == "row" else "stretch",
        "justify": raw.get("justify") if raw.get("justify") in ("start", "center", "end") else "start",
        "wrap": bool(raw.get("wrap", False)), "equal": number(raw.get("equal"), 0.0, 0.0, MAX_WIDTH),
        "w": _side(raw.get("w"), wide), "h": high if high != "fill" or not aspect else "hug",
        "minw": number(raw.get("minw"), 0.0, 0.0, MAX_WIDTH), "maxw": number(raw.get("maxw"), 0.0, 0.0, MAX_WIDTH),
        "minh": number(raw.get("minh"), 0.0, 0.0, MAX_WIDTH),
        "aspect": round(aspect, 4), "hide_below": number(raw.get("hide_below"), 0.0, 0.0, MAX_WIDTH),
        "center": bool(raw.get("center", False)),
        "variant": variant, "text": _words(raw.get("text"), MAX_TEXT),
        "right": bool(raw.get("right", False)), "strong": bool(raw.get("strong", False)), "on": bool(raw.get("on", False)),
        "p": number(raw.get("p"), 60.0, 0.0, 100.0), "chart": _chart(raw.get("chart")) if role == "chart" else None,
        "hue": number(raw.get("hue"), 0.0, 0.0, 360.0),
        # What's pressed in an app answers to start with: buttons, cards, rows, a sidebar's items.
        "reacts": bool(raw.get("reacts", role == "button" or (section and variant in ("card", "row", "navitem")))),
        "states": _states(raw.get("states")),
        "go": {"page": str(raw["go"].get("page") or ""), "with": raw["go"].get("with")} if isinstance(raw.get("go"), dict) else None,
        "image": _image(raw.get("image")) if role == "image" else None,
        "type": _type(raw.get("type"), role), "arrive": _arrive(raw.get("arrive")),
        "count": (count := role == "text" and bool(raw.get("count")) and counts(_words(raw.get("text"), MAX_TEXT))),
        "run": _run(raw.get("run"), {"by": "", "seconds": COUNT_SECONDS} if count else None)
        if count or role in RUN_ROLES else None,
        "children": children,
    }


def every(box):
    """A box and every box under it, a parent before its children."""
    yield box
    for child in box["children"]:
        yield from every(child)


def boxes(spec):
    """Every box of every page."""
    for page in spec["pages"]:
        yield from every(page["root"])


def _numbered(things, letter):
    """Give each of `things` an id nothing else has: its own if it's a good one and the
    first to hold it, else the next number after the highest."""
    seen, fresh = set(), []
    for thing in things:
        if _ID.match(thing["id"]) and thing["id"][0] == letter and thing["id"] not in seen:
            seen.add(thing["id"])
        else:
            fresh.append(thing)
    n = max((int(i[1:]) for i in seen), default=0)
    for thing in fresh:
        n += 1
        thing["id"] = f"{letter}{n}"


def _page(raw, n):
    raw = raw if isinstance(raw, dict) else {}
    root = _box(raw.get("root"), False, 0, [MAX_BOXES])
    if root is None or root["role"] != "section":
        root = _box({"role": "section", "pad": 14, "gap": 10, "children": [raw["root"]] if root else []}, False, 0, [MAX_BOXES])
    layout = raw.get("layout") if raw.get("layout") in catalogue.LAYOUTS else ""
    return {"id": raw.get("id") if isinstance(raw.get("id"), str) else "",
            "name": _words(raw.get("name"), MAX_NAME) or f"Page {n}", "layout": layout,
            # The story: how long it shows (0: as long as it needs), and the box whose click goes on ("": its time does).
            "secs": number(raw.get("secs"), 0.0, 0.0, MAX_PAGE_SECONDS) if not isinstance(raw.get("secs"), bool) else 0.0,
            "by": raw.get("by") if isinstance(raw.get("by"), str) else "",
            "root": root}


# -------------------------------------------------------------- the cursor --

def _path(raw, seconds):
    keys = []
    for row in raw if isinstance(raw, list) else []:
        try:
            t, x, y = (float(v) for v in row)
        except (TypeError, ValueError):
            continue
        if not all(v == v and abs(v) < 1e6 for v in (t, x, y)) or t < 0 or t > seconds:
            continue
        if keys and t <= keys[-1][0]:
            continue                                            # in order of time, one key a moment
        keys.append([round(t, 3), round(min(100.0, max(0.0, x)), 2), round(min(100.0, max(0.0, y)), 2)])
    return keys[:MAX_KEYS] or [[0.0, 50.0, 50.0]]


def _clicks(raw, seconds):
    out = []
    for row in raw if isinstance(raw, list) else []:
        try:
            down, up = (float(v) for v in row)
        except (TypeError, ValueError):
            continue
        if not (down == down and up == up) or down < 0 or up <= down or down >= seconds:
            continue
        if out and down < out[-1][1]:
            continue
        out.append([round(down, 3), round(min(up, seconds), 3)])
    return out[:MAX_CLICKS]


# ------------------------------------------------------------ the template --

_made = {}


def _template():
    """What a new panel has that DEFAULTS doesn't: three pages - Sign in, whose button
    goes to Dashboard, and Settings - and a cursor that crosses to the button, clicks
    it, and looks round the page it lands on."""
    if _made:
        return _made
    pages = [{"id": f"p{n}", "name": name, "layout": layout, "root": catalogue.layout(layout)}
             for n, (name, layout) in enumerate(TEMPLATE_PAGES, 1)]
    # The story: Sign in's button goes on to the Dashboard, whose sidebar's Settings item goes on to Settings.
    base = clean({**DEFAULTS, "pages": pages, "path": [[0.0, 50.0, 50.0]], "clicks": []})
    button = next(b["id"] for b in base["pages"][0]["root"]["children"] if b["role"] == "button" and not b["variant"])
    item = next(b["id"] for b in every(base["pages"][1]["root"]) if b["variant"] == "navitem" and b["name"] == "Settings")
    made = clean({**base, "auto": True, "pages": [{**base["pages"][0], "by": button}, {**base["pages"][1], "by": item, "secs": 3.0},
                                                  base["pages"][2]]})
    _made.update({"title": "Booking app", "auto": True, "pages": made["pages"]})
    return _made


def story(spec):
    """The clip, page by page, when Buddy moves the cursor: [{"page", "at" (the second it
    starts), "secs", "own" (whether its length was given), "by" (the box whose click goes
    on to the next page, "" for its time), "leave" (when that click comes, None for
    none), "clicks": [[second, box id]] (the clicks that start things on it), "last"}]
    - the pages in the order they are listed."""
    out, t = [], 0.0
    for i, page in enumerate(spec["pages"]):
        boxes = list(every(page["root"]))
        last = i == len(spec["pages"]) - 1
        by = "" if last else page["by"]
        starters, longest = [], 0.6
        for box in boxes:
            run, types = box["run"], box["type"]
            if run and run["by"]:
                longest = max(longest, run["seconds"])
                if run["by"] not in starters:
                    starters.append(run["by"])
            if types and types["start"] == "click":
                longest = max(longest, len(types["text"] or box["text"]) / types["speed"])
                if box["id"] not in starters:
                    starters.append(box["id"])
        starters = [s for s in starters if s != by]           # what the leaving click starts, starts as the page goes
        needs = STORY_LEAD + STORY_GAP * (len(starters) - 1) + longest + STORY_TAIL if starters else PAGE_SECONDS
        secs = page["secs"] or round(min(MAX_PAGE_SECONDS, needs), 1)
        clicks = [[round(t + min(STORY_LEAD + STORY_GAP * k, max(0.1, secs - 2 * STORY_LEAVE)), 3), s] for k, s in enumerate(starters)]
        out.append({"page": page["id"], "at": round(t, 3), "secs": secs, "own": bool(page["secs"]), "by": by,
                    "leave": round(t + secs - STORY_LEAVE, 3) if by else None, "clicks": clicks, "last": last})
        t += secs
    return out


def _staged(spec):
    """What a story makes of a panel: the cursor's "path" and "clicks", the "page_keys",
    how long the clip is and where it starts - the cursor crossing to each box in time to
    click it."""
    told = story(spec)
    seconds = round(min(MAX_SECONDS, max(MIN_SECONDS, sum(part["secs"] for part in told))), 3)
    solved = solve.solve_all(spec)
    placed = {box["id"]: (pid, box) for pid, page in solved.items() for box in page["boxes"]}
    keys, events = [], []
    for n, part in enumerate(told):
        events += [(t, box_id) for t, box_id in part["clicks"]]
        if part["by"]:
            events.append((part["leave"], part["by"]))
        elif not part["last"]:
            keys.append([round(part["at"] + part["secs"], 3), told[n + 1]["page"], spec["go_with"]])
    path, clicks = [[0.0, *CURSOR_HOME]], []
    for t, box_id in sorted(events):
        if box_id not in placed or t >= seconds:
            continue                                # a box the window is too narrow to show: nothing to click
        pid, box = placed[box_id]
        x, y = solve.to_frame(spec, solved[pid]["h"], box["x"] + box["w"] / 2, box["y"] + box["h"] / 2)
        x, y = round(min(100.0, max(0.0, x)), 2), round(min(100.0, max(0.0, y)), 2)
        went = max(path[-1][0] + 0.02, t - CURSOR_TRAVEL)
        if went < t - CURSOR_EARLY - 0.02:
            if went > path[-1][0] + 0.03:
                path.append([round(went, 3), path[-1][1], path[-1][2]])     # it waits where it was until it's time to go
            path.append([round(t - CURSOR_EARLY, 3), x, y])
        else:
            path.append([round(went, 3), x, y])
        path.append([round(max(path[-1][0] + 0.02, t + CURSOR_STAYS), 3), x, y])
        clicks.append([round(t, 3), round(t + CLICK_SECONDS, 3)])
    return {"seconds": seconds, "start": spec["pages"][0]["id"], "page_keys": keys,
            "path": [key for key in path if key[0] <= seconds], "clicks": clicks}


def is_template(spec):
    """Whether a panel is still the one a new panel starts as."""
    return spec == clean({})


# ------------------------------------------------------------------- clean --

def clean(raw):
    """A whole, valid panel from whatever was saved or sent."""
    raw = raw if isinstance(raw, dict) else {}
    if not isinstance(raw.get("pages"), list):
        raw = {**_template(), **{k: v for k, v in raw.items() if k != "pages"}}
    seconds = number(raw.get("seconds"), DEFAULTS["seconds"], MIN_SECONDS, MAX_SECONDS)
    auto = bool(raw.get("auto", DEFAULTS["auto"]))
    pages = [_page(page, n) for n, page in enumerate(raw["pages"][:MAX_PAGES], 1)] or [_page({}, 1)]
    _numbered(pages, "p")
    _numbered([box for page in pages for box in every(page["root"])], "b")
    page_ids = [page["id"] for page in pages]
    drawn_on = set()
    for at, page in enumerate(pages):
        here = list(every(page["root"]))
        # The box whose click goes on to the next page: one of this page's own.
        if not any(box["id"] == page["by"] for box in here[1:]):
            page["by"] = ""
        for box in here:
            if auto:
                # In a story the links are the story's: the "by" box goes to the next page, nothing else goes anywhere.
                onward = at + 1 < len(pages) and box["id"] == page["by"]
                box["go"] = {"page": page_ids[at + 1], "with": ""} if onward else None
            # What starts it: a box of its own page - by id, or (a layout's own boxes,
            # which have no ids yet) "@" and the box's words or name.
            by = box["run"]["by"] if box["run"] else ""
            if by.startswith("@"):
                by = next((b["id"] for b in here if by[1:] in (b["text"], b["name"]) and b is not box), "")
            if box["run"]:
                box["run"] = {**box["run"], "by": by if any(b["id"] == by and b is not box for b in here) else ""}
            go = box["go"]
            if go is not None:
                box["go"] = {"page": go["page"], "with": go["with"] if go["with"] in GO_WITH else ""} \
                    if go["page"] in page_ids and go["page"] != page["id"] else None
            if box["role"] in ("chart", "progress"):
                drawn_on.add(box["id"])

    rules = []
    for rule in raw.get("rules") if isinstance(raw.get("rules"), list) else []:
        if isinstance(rule, dict) and rule.get("box") in drawn_on and rule.get("page") in page_ids:
            rules.append({"id": rule.get("id") if isinstance(rule.get("id"), str) else "", "box": rule["box"],
                          "over": number(rule.get("over"), 60.0, 0.0, 99.0), "page": rule["page"],
                          "with": rule.get("with") if rule.get("with") in GO_WITH else "match"})
    rules = rules[:MAX_RULES]
    _numbered(rules, "r")

    go_with = raw.get("go_with") if raw.get("go_with") in GO_WITH else DEFAULTS["go_with"]
    keys = []
    for row in raw.get("page_keys") if isinstance(raw.get("page_keys"), list) else []:
        if not isinstance(row, (list, tuple)) or len(row) < 2 or row[1] not in page_ids:
            continue
        t = number(row[0], None, 0.0, seconds) if not isinstance(row[0], bool) else None
        if t is not None:
            keys.append([round(t, 3), row[1], row[2] if len(row) > 2 and row[2] in GO_WITH else go_with])
    keys.sort(key=lambda key: key[0])

    answers = raw.get("answers") if isinstance(raw.get("answers"), dict) else {}
    build = raw.get("build") if isinstance(raw.get("build"), dict) else {}
    made = {
        "kind": raw.get("kind") if raw.get("kind") in KINDS else DEFAULTS["kind"],
        "style": raw.get("style") if raw.get("style") in STYLES else DEFAULTS["style"],
        "accent": colour(raw.get("accent"), DEFAULTS["accent"]),
        "dark": bool(raw.get("dark", DEFAULTS["dark"])),
        "title": _words(raw.get("title"), MAX_NAME),
        "width": number(raw.get("width"), DEFAULTS["width"], MIN_WIDTH, MAX_WIDTH),
        "zoom": number(raw.get("zoom"), DEFAULTS["zoom"], 0.1, 10.0),
        "x": number(raw.get("x"), DEFAULTS["x"], -100.0, 200.0), "y": number(raw.get("y"), DEFAULTS["y"], -100.0, 200.0),
        "font": str(raw.get("font") or DEFAULTS["font"])[:MAX_FONT],
        "face": str(raw.get("face") or DEFAULTS["face"])[:MAX_FONT],
        "bold_face": str(raw.get("bold_face") or DEFAULTS["bold_face"])[:MAX_FONT],
        "pages": pages,
        "start": raw.get("start") if raw.get("start") in page_ids else page_ids[0],
        "seconds": seconds, "auto": auto,
        "path": _path(raw.get("path"), seconds), "clicks": _clicks(raw.get("clicks"), seconds),
        "cursor": raw.get("cursor") if raw.get("cursor") in CURSORS else DEFAULTS["cursor"],
        "cursor_size": number(raw.get("cursor_size"), DEFAULTS["cursor_size"], 1.0, 20.0),
        "answers": {**{state: _answer(answers.get(state), DEFAULTS["answers"][state]) for state in STATES},
                    "hover_seconds": number(answers.get("hover_seconds"), DEFAULTS["answers"]["hover_seconds"], 0.0, 1.0),
                    "press_seconds": number(answers.get("press_seconds"), DEFAULTS["answers"]["press_seconds"], 0.0, 0.5)},
        "go_with": go_with, "go_seconds": number(raw.get("go_seconds"), DEFAULTS["go_seconds"], 0.0, 3.0),
        "rules": rules, "page_keys": keys[:MAX_PAGE_KEYS],
        "build": {"on": bool(build.get("on", DEFAULTS["build"]["on"])),
                  "stagger": number(build.get("stagger"), DEFAULTS["build"]["stagger"], 0.0, 0.5),
                  "seconds": number(build.get("seconds"), DEFAULTS["build"]["seconds"], 0.05, 2.0)},
    }
    if auto:
        made.update(_staged(made))
    return made


# ------------------------------------------------------------------- reads --

def page_of(spec, page_id):
    return next((page for page in spec["pages"] if page["id"] == page_id), None)


def find(spec, box_id):
    """{"box", "parent" (None for a page's root), "page"} of a box, or None."""
    for page in spec["pages"]:
        pile = [(page["root"], None)]
        while pile:
            box, parent = pile.pop()
            if box["id"] == box_id:
                return {"box": box, "parent": parent, "page": page}
            pile += [(child, box) for child in box["children"]]
    return None


def frames(spec, fps):
    """The clip's frames."""
    return max(2, int(round(spec["seconds"] * fps)))


def faces(spec):
    """[(family, style)] the panel's text is set in, each once: its face and its bold one."""
    out = []
    for face in ((spec["font"], spec["face"]), (spec["font"], spec["bold_face"])):
        if face not in out:
            out.append(face)
    return out


def images(spec):
    """Every box of the panel that shows a picture."""
    return [box for box in boxes(spec) if box["image"]]


def _stamp(path):
    try:
        known = os.stat(path)
    except OSError:
        return None
    return [known.st_size, known.st_mtime_ns]


def panel_id(spec, frame, fps):
    """A hash of exactly what was placed, so the same panel is known again - a picture
    changed in its file (at the same path) makes it another."""
    stamps = {box["image"]["path"]: _stamp(box["image"]["path"]) for box in images(spec)}
    raw = json.dumps({"spec": spec, "frame": list(frame), "fps": float(fps), **({"files": stamps} if stamps else {})},
                     sort_keys=True)
    return "bp-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


# ------------------------------------------------------------------- edits --

def _changed(spec, made):
    made = clean(made)
    return None if made == spec else made


def _mapped(box, change):
    """The tree with change(box) in place of each box - change gives the box back, another, or None to drop it."""
    kept = []
    for child in box["children"]:
        made = _mapped(child, change)
        if made is not None:
            kept.append(made)
    return change({**box, "children": kept})


def _with_roots(spec, change, only=None):
    return {**spec, "pages": [{**page, "root": _mapped(page["root"], change)} if only in (None, page["id"]) else page
                              for page in spec["pages"]]}


def _count(tree):
    """How many boxes a tree from the catalogue has."""
    return 1 + sum(_count(child) for child in tree.get("children", ()))


def with_box(spec, box_id, key, value):
    """One thing about a box changed (BOX_KEYS). None for a box or a key there isn't.
    A dict for "type" or "arrive" is laid over what the box has ({"speed": 30} keeps its
    start); None takes it off."""
    found = find(spec, box_id) if key in BOX_KEYS else None
    if found is None:
        return None
    if key in ("type", "arrive", "run") and isinstance(value, dict):
        value = {**(found["box"][key] or {}), **value}
    # A plain row turned to run downwards is a column from then on, and is called one.
    renamed = {"name": ""} if key == "dir" and _wrapper(found["box"]) else {}
    return _changed(spec, _with_roots(spec, lambda box: {**box, key: value, **renamed} if box["id"] == box_id else box))


def with_image(spec, box_id, path, px=None):
    """An image box shows the picture at `path` (`px`: [width, height], its own px),
    filling the box and cropped to it - or, path "", its placeholder again. None for a
    box that isn't an image, a picture that can't be one, or no change."""
    found = find(spec, box_id)
    if found is None or found["box"]["role"] != "image":
        return None
    picture = None
    if path:
        picture = _image({"path": path, "px": px})
        if picture is None:
            return None
    return _changed(spec, _with_roots(spec, lambda box: {**box, "image": picture} if box["id"] == box_id else box,
                                      found["page"]["id"]))


def with_move(spec, box_id, into, at=None):
    """A box, and everything in it, moved to place `at` among the children of section
    `into` (counted without the box itself; the end for None). None for a page's own box,
    a box or section there isn't, one that isn't a section, on another page, the box
    itself or one inside it - or a move that changes nothing."""
    found, target = find(spec, box_id), find(spec, into)
    if found is None or target is None or found["parent"] is None or target["box"]["role"] != "section":
        return None
    if target["page"]["id"] != found["page"]["id"] or any(b["id"] == into for b in every(found["box"])):
        return None
    moving = found["box"]

    def put(box):
        if box["id"] != into:
            return box
        n = len(box["children"])
        where = n if at is None or isinstance(at, bool) else int(min(n, max(0, number(at, n))))
        return {**box, "children": [*box["children"][:where], moving, *box["children"][where:]]}
    page = found["page"]
    root = _mapped(_taken(page["root"], box_id, keep=(into,)), put)
    return _changed(spec, {**spec, "pages": [{**p, "root": root} if p["id"] == page["id"] else p for p in spec["pages"]]})


def _wrapper(box):
    """Whether a box is only there to line others up: a plain row or column nobody named."""
    return box["role"] == "section" and box["variant"] == "" and box["name"] in ("Row", "Column")


def _taken(root, box_id, keep=()):
    """A page's tree without a box - and without the plain rows and columns (_wrapper)
    taking it out leaves with nothing to line up: one left empty goes, one left with a
    single box gives its place to that box.
    `keep`: the boxes that stay even so (the one the box is going into or beside)."""
    def without(box, top):
        kids, lost = [], False
        for child in box["children"]:
            made = None if child["id"] == box_id else without(child, False)
            if made is None:
                lost = True
            else:
                kids.append(made)
        if lost and len(kids) < 2 and not top and box["id"] not in keep and _wrapper(box):
            return kids[0] if kids else None        # one box left in it: that box takes its place
        return {**box, "children": kids} if lost or any(a is not b for a, b in zip(kids, box["children"])) else box
    return without(root, True)


SIDES = ("left", "right", "above", "below")


def with_beside(spec, box_id, target_id, side):
    """A box, and everything in it, put next to another on its page: to its "left" or
    "right", "above" or "below". Where the two can't sit that way as they are - side by
    side in a column, one over the other in a row - the other box's place is taken by a
    new row (or column) holding them both. None for a page's own box, boxes on different
    pages, a box put beside itself or something in it, or no change."""
    found, target = find(spec, box_id), find(spec, target_id)
    if found is None or target is None or side not in SIDES or found["parent"] is None or target["parent"] is None:
        return None
    if target["page"]["id"] != found["page"]["id"] or any(b["id"] == target_id for b in every(found["box"])):
        return None
    moving, holder = found["box"], target["parent"]
    along = "row" if side in ("left", "right") else "column"
    first = side in ("left", "above")
    # Lines of a wrapping row run across but stack downwards: beside is still beside.
    if holder["dir"] == along:
        def put(box):
            if box["id"] != holder["id"]:
                return box
            kids = []
            for kid in box["children"]:
                kids += [moving, kid] if kid["id"] == target_id and first else [kid, moving] if kid["id"] == target_id else [kid]
            return {**box, "children": kids}
    else:
        def put(box):
            if box["id"] != holder["id"]:
                return box
            # The other box as it is now: the one moved may have come out of it.
            return {**box, "children": [
                {"role": "section", "dir": along, "gap": holder["gap"] or 8.0, "children": [moving, kid] if first else [kid, moving],
                 **({"align": "stretch"} if along == "row" else {})} if kid["id"] == target_id else kid
                for kid in box["children"]]}
    page = found["page"]
    root = _mapped(_taken(page["root"], box_id, keep=(holder["id"], target_id)), put)
    return _changed(spec, {**spec, "pages": [{**p, "root": root} if p["id"] == page["id"] else p for p in spec["pages"]]})


def with_copy(spec, box_id):
    """A second one of a box, and of everything in it, right after it - with ids of its
    own (clean gives them). None for a page's own box, or a page that's full."""
    found = find(spec, box_id)
    if found is None or found["parent"] is None:
        return None
    if sum(1 for _ in every(found["page"]["root"])) + sum(1 for _ in every(found["box"])) > MAX_BOXES:
        return None

    def put(box):
        if box["id"] != found["parent"]["id"]:
            return box
        kids = []
        for kid in box["children"]:
            kids += [kid, kid] if kid["id"] == box_id else [kid]
        return {**box, "children": kids}
    return _changed(spec, _with_roots(spec, put, found["page"]["id"]))


def animations(spec, box_id):
    """Every way a box moves, for the page's Animations list - each {"id", "on", ...}:

        arrive  {"on", "kind": its own ("rise" | "fade" | "pop" | "none") or "" (the
                page's build: a rise), "build": whether the page builds on}       every box
        type    {"on", "speed", "start", "caret", "text"}           a text or a field
        count   {"on"}                                               a text with a number in it
        answer  {"on": it answers the cursor, "own": it has states of its own,
                "go": the page a click on it goes to, or ""}         every box
        draw    {"on": True}                                         a chart or a progress bar

    A box with its own arrival arrives even when the page doesn't build on. [] for a box
    there isn't."""
    found = find(spec, box_id)
    if found is None:
        return []
    box = found["box"]
    kind = (box["arrive"] or {}).get("kind", "")
    out = [{"id": "arrive", "on": kind != "none" and (spec["build"]["on"] or bool(kind)), "kind": kind,
            "build": spec["build"]["on"]}]
    if box["role"] in TYPE_ROLES:
        out.append({"id": "type", "on": box["type"] is not None, **(box["type"] or _type({}, box["role"]))})
    if box["role"] == "text" and counts(box["text"]):
        out.append({"id": "count", "on": box["count"]})
    out.append({"id": "answer", "on": box["reacts"], "own": box["states"] is not None,
                "go": (box["go"] or {}).get("page", "")})
    if box["role"] in ("chart", "progress"):
        out.append({"id": "draw", "on": True})
    return out


def with_insert(spec, component, into, at=None):
    """A component from the catalogue put into a box: `at` that place among its children
    (the end, for none) - or, into a box that holds nothing, after it in its parent.
    None for a component or a box there isn't, or a page that's full."""
    tree, found = catalogue.component(component), find(spec, into)
    if tree is None or found is None:
        return None
    if sum(1 for _ in every(found["page"]["root"])) + _count(tree) > MAX_BOXES:
        return None
    target = found["box"]
    if target["role"] != "section":
        target, at = found["parent"], found["parent"]["children"].index(found["box"]) + 1

    def put(box):
        if box["id"] != target["id"]:
            return box
        n = len(box["children"])
        where = n if at is None or isinstance(at, bool) else int(min(n, max(0, number(at, n))))
        return {**box, "children": [*box["children"][:where], tree, *box["children"][where:]]}
    return _changed(spec, _with_roots(spec, put, found["page"]["id"]))


def without_box(spec, box_id):
    """The panel without a box and what's in it. None for a page's root."""
    found = find(spec, box_id)
    if found is None or found["parent"] is None:
        return None
    return _changed(spec, _with_roots(spec, lambda box: None if box["id"] == box_id else box, found["page"]["id"]))


def with_layout(spec, page_id, layout_id):
    """A page made over as a layout from the catalogue - named after it, unless it was
    given a name of its own."""
    page, entry = page_of(spec, page_id), catalogue.LAYOUTS.get(layout_id)
    if page is None or entry is None:
        return None
    old = catalogue.LAYOUTS.get(page["layout"])
    # Its name is the layout's still - or the one a new panel's page of that layout has ("Sign in").
    given = {old["name"] if old else "", *(name for name, layout in TEMPLATE_PAGES if layout == page["layout"])}
    plain = page["name"] in given or re.fullmatch(r"Page \d+", page["name"])
    made = {**page, "layout": layout_id, "root": entry["make"](), "name": entry["name"] if plain else page["name"]}
    return _changed(spec, {**spec, "pages": [made if p["id"] == page_id else p for p in spec["pages"]]})


def with_page(spec):
    """The panel with one more page, empty. None when it has all it may."""
    if len(spec["pages"]) >= MAX_PAGES:
        return None
    return _changed(spec, {**spec, "pages": [*spec["pages"], {"name": f"Page {len(spec['pages']) + 1}"}]})


def without_page(spec, page_id):
    """The panel without a page - and without the links, rules and keys that went to
    it. None for its only page."""
    if page_of(spec, page_id) is None or len(spec["pages"]) < 2:
        return None
    return _changed(spec, {**spec, "pages": [p for p in spec["pages"] if p["id"] != page_id]})


def with_page_move(spec, page_id, at):
    """A page moved to place `at` among the pages (counted without it). The pages' order
    is the order they are listed and numbered in; which one the clip starts on is
    "start" (with_start). None for a page there isn't, or no change."""
    page = page_of(spec, page_id)
    if page is None or isinstance(at, bool):
        return None
    rest = [p for p in spec["pages"] if p["id"] != page_id]
    where = int(min(len(rest), max(0, number(at, len(rest)))))
    return _changed(spec, {**spec, "pages": [*rest[:where], page, *rest[where:]]})


def with_page_copy(spec, page_id):
    """A second one of a page, right after it: the same boxes (with ids of their own) and
    the same links, named after it with the next number. None when the panel has all
    the pages it may."""
    page = page_of(spec, page_id)
    if page is None or len(spec["pages"]) >= MAX_PAGES:
        return None
    names = {p["name"] for p in spec["pages"]}
    stem = re.sub(r" \d+$", "", page["name"])[:MAX_NAME - 3]
    name = next(f"{stem} {n}" for n in range(2, MAX_PAGES + 3) if f"{stem} {n}" not in names)
    pages = []
    for p in spec["pages"]:
        pages += [p, {**p, "id": "", "name": name}] if p["id"] == page_id else [p]
    return _changed(spec, {**spec, "pages": pages})


def with_start(spec, page_id):
    """The page the clip starts on. None for a page there isn't, or the one it is."""
    if page_of(spec, page_id) is None:
        return None
    return _changed(spec, {**spec, "start": page_id})


def with_story(spec, page_id, key, value):
    """One thing about a page's part in the story: "secs", how long it shows (0: as long
    as what happens on it needs), or "by", the box of the page whose click goes on to the
    next one ("": its time does). None for a page or a key there isn't, or no change."""
    if page_of(spec, page_id) is None or key not in ("secs", "by"):
        return None
    return _changed(spec, {**spec, "pages": [{**p, key: value} if p["id"] == page_id else p for p in spec["pages"]]})


def with_started(spec, box_id, by):
    """A click on box `by` starts box `box_id`: a progress bar or a chart draws on from it,
    a text with a number counts up from it. None for a box that can do neither, boxes on
    different pages, a box started by itself, or no change."""
    found, starter = find(spec, box_id), find(spec, by)
    if found is None or starter is None or box_id == by or found["page"]["id"] != starter["page"]["id"]:
        return None
    box = found["box"]
    if box["role"] not in RUN_ROLES and not (box["role"] == "text" and counts(box["text"])):
        return None
    run = {"by": by, "seconds": (box["run"] or {}).get("seconds", 3.0)}
    more = {"count": True} if box["role"] == "text" else {}
    return _changed(spec, _with_roots(spec, lambda b: {**b, **more, "run": run} if b["id"] == box_id else b, found["page"]["id"]))


def with_page_name(spec, page_id, name):
    if page_of(spec, page_id) is None or not _words(name, MAX_NAME):
        return None
    return _changed(spec, {**spec, "pages": [{**p, "name": name} if p["id"] == page_id else p for p in spec["pages"]]})


def with_answers(spec, box_id, state, key, value):
    """How boxes answer the cursor: `state` "hover" or "press", `key` "grow", "lift" or
    "glow" - or "seconds", how long the state takes (the panel's only). box_id "" is
    the panel's own answers; a box's first change starts from the panel's, and key
    "reset" gives a box the panel's again."""
    if state not in STATES:
        return None
    if not box_id:
        if key == "seconds":
            return _changed(spec, {**spec, "answers": {**spec["answers"], f"{state}_seconds": value}})
        if key not in ANSWER_KEYS:
            return None
        return _changed(spec, {**spec, "answers": {**spec["answers"], state: {**spec["answers"][state], key: value}}})
    found = find(spec, box_id)
    if found is None or (key not in ANSWER_KEYS and key != "reset"):
        return None
    if key == "reset":
        return with_box(spec, box_id, "states", None)
    own = found["box"]["states"] or {s: dict(spec["answers"][s]) for s in STATES}
    return with_box(spec, box_id, "states", {**own, state: {**own[state], key: value}})


def with_rule(spec, rule_id, key, value):
    """One thing about a rule changed ("box", "over", "page", "with") - or, rule_id "",
    a new rule with that set: when the first chart or progress bar is over 60 % drawn,
    go to the next page. None for a rule or a key there isn't, with nothing a rule
    could watch, or when there are all the rules there may be."""
    if key not in ("box", "over", "page", "with", ""):
        return None
    rules = [dict(rule) for rule in spec["rules"]]
    if rule_id:
        rule = next((r for r in rules if r["id"] == rule_id), None)
        if rule is None or not key:
            return None
    else:
        watched = next((box for box in boxes(spec) if box["role"] in ("chart", "progress")), None)
        if watched is None or len(rules) >= MAX_RULES or len(spec["pages"]) < 2:
            return None
        ids = [page["id"] for page in spec["pages"]]
        here = find(spec, watched["id"])["page"]["id"]
        rule = {"id": "", "box": watched["id"], "over": 60.0, "page": ids[(ids.index(here) + 1) % len(ids)], "with": "match"}
        rules.append(rule)
    if key:
        rule[key] = value
    made = clean({**spec, "rules": rules})
    # A rule that names a box or a page there isn't is dropped by clean(): that edit can't be made.
    return None if made == spec or len(made["rules"]) != len(rules) else made


def without_rule(spec, rule_id):
    if not any(rule["id"] == rule_id for rule in spec["rules"]):
        return None
    return _changed(spec, {**spec, "rules": [rule for rule in spec["rules"] if rule["id"] != rule_id]})


def with_recording(spec, samples, clicks, seconds):
    """The panel with a newly recorded cursor: its path thinned, the clip as long as
    the recording."""
    seconds = number(seconds, spec["seconds"], MIN_SECONDS, MAX_SECONDS)
    # A cursor recorded is the panel's own from then on: the story no longer moves it.
    return _changed(spec, {**spec, "auto": False, "seconds": seconds, "path": thinned(_path(samples, seconds)), "clicks": clicks})
