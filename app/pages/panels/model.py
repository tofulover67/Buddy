"""A panel, as data: a mock app window - a kind of window in a style - with pages, each
a tree of boxes laid out by a few rules (solve.py), boxes that answer a cursor, and a
cursor that was moved and clicked over them. The one description the view in Buddy
draws and the comp in Resolve is written from. No Qt, no Resolve.

A panel is designed in panel px: the window is "width" wide and as tall as its bar
and its tallest page make it. On the frame it is drawn "zoom" times frame width / 1920
larger, its middle at "x", "y" (% of the frame, across and down). The cursor's "path"
and "clicks" are Interactions' (seconds, and % of the frame).

A page is {"id", "name", "layout" (the catalogue's layout it was made from, "" for
none), "root"}; the root and everything under it is a box:

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

# Everything about a panel but its pages and its cursor, which the template has.
DEFAULTS = {
    "kind": "app", "style": "mac", "accent": ACCENTS[0], "dark": True,
    "title": "",
    "width": 620.0, "zoom": 1.5, "x": 50.0, "y": 50.0,
    "font": "Arial", "face": "Regular", "bold_face": "Bold",
    "seconds": 6.0,
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
            "name": _words(raw.get("name"), MAX_NAME) or f"Page {n}", "layout": layout, "root": root}


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
             for n, (name, layout) in enumerate((("Sign in", "signin"), ("Dashboard", "dashboard"), ("Settings", "settings")), 1)]
    for box in pages[0]["root"]["children"]:
        if box["role"] == "button" and not box.get("variant"):
            box["go"] = {"page": "p2", "with": ""}
    base = clean({**DEFAULTS, "pages": pages, "start": "p1", "path": [[0.0, 50.0, 50.0]], "clicks": []})
    solved = solve.solve_all(base)

    def middle(page_id, found, down=0.5):
        placed = next(b for b in solved[page_id]["boxes"] if found(b))
        x, y = solve.to_frame(base, solved[page_id]["h"], placed["x"] + placed["w"] / 2, placed["y"] + placed["h"] * down)
        return [round(x, 2), round(y, 2)]
    press = middle("p1", lambda b: b["role"] == "button" and b["variant"] == "")
    tile = middle("p2", lambda b: b["name"] == "Stat")
    graph = middle("p2", lambda b: b["role"] == "chart")
    _made.update({
        "title": "Booking app", "pages": base["pages"], "start": "p1",
        "path": [[0.0, 22.0, 90.0], [1.1, *press], [1.9, *press], [3.0, *tile], [3.6, *tile], [4.7, *graph], [6.0, 80.0, 92.0]],
        "clicks": [[1.5, 1.65]],
    })
    return _made


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
    pages = [_page(page, n) for n, page in enumerate(raw["pages"][:MAX_PAGES], 1)] or [_page({}, 1)]
    _numbered(pages, "p")
    _numbered([box for page in pages for box in every(page["root"])], "b")
    page_ids = [page["id"] for page in pages]
    drawn_on = set()
    for page in pages:
        for box in every(page["root"]):
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
    return {
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
        "seconds": seconds,
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
    if key in ("type", "arrive") and isinstance(value, dict):
        value = {**(found["box"][key] or {}), **value}
    return _changed(spec, _with_roots(spec, lambda box: {**box, key: value} if box["id"] == box_id else box))


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
        kids = [kid for kid in box["children"] if kid["id"] != box_id]
        if box["id"] == into:
            n = len(kids)
            where = n if at is None or isinstance(at, bool) else int(min(n, max(0, number(at, n))))
            kids = [*kids[:where], moving, *kids[where:]]
        elif len(kids) == len(box["children"]):
            return box
        return {**box, "children": kids}
    return _changed(spec, _with_roots(spec, put, found["page"]["id"]))


def animations(spec, box_id):
    """Every way a box moves, for the page's Animations list - each {"id", "on", ...}:

        arrive  {"on", "kind": its own ("rise" | "fade" | "pop" | "none") or "" (the
                page's build: a rise), "build": whether the page builds on}       every box
        type    {"on", "speed", "start", "caret", "text"}           a text or a field
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
    plain = page["name"] == (old["name"] if old else "") or re.fullmatch(r"Page \d+", page["name"])
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
    return _changed(spec, {**spec, "seconds": seconds, "path": thinned(_path(samples, seconds)), "clicks": clicks})
