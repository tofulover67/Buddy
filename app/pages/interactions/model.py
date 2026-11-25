"""An interaction, as arithmetic: shapes on a frame (buttons, cards, circles, pictures
from image files), a cursor
that was moved and clicked over them (its path and its clicks, recorded in the view),
and how each shape answers - how far it is hovered and how far pressed on every frame.
The one clock the preview in Buddy and the comp in Resolve both follow. No Qt, no
Resolve.

How a shape answers is the rule the comp's expressions run (comp.py; measured in
Fusion, docs/RESOLVE_API_GUIDE.md): its hover on a frame is the share of the last few
frames the cursor was inside it, eased; its press the share of the last few it was
inside with the button down. Nothing is remembered from frame to frame, so in Resolve
the cursor can be moved again and the shapes still answer.

Places are % of the frame, across and down; a shape's x and y are its middle, its w
and h % of the frame's width and height (a circle's w is its diameter, of the width).
An image is a box like a card, its picture (the file at "path", "px" wide and high)
drawn as large as fits inside it, never stretched.

What a shape looks like in each state is its answers: the interaction's "answers", or
the shape's own "states" when it has them (Panels' engine, pages/panels/model.py).
Each of "hover" and "press" says how much it grows (%, negative smaller), lifts (% of
the frame's height, up), glows and is outlined (each 0..100). Hovered h and pressed p
that much (amounts(), each 0..1), they add up - look():

    size    1 + hover.grow / 100 * h + press.grow / 100 * p      about its middle
    lift    hover.lift * h + press.lift * p                      % of the frame's height
    glow    hover.glow / 100 * h + press.glow / 100 * p          kept to 0..1
    outline hover.outline / 100 * h + press.outline / 100 * p    kept to 0..1

The shape, its glow, its outline and its label lift together; where the cursor is on
it is still read at its place at rest. An interaction saved before states (with
"hover_grow", "hover_glow", "hover_outline", "press_shrink") is the same answers:
hover {grow, 0, glow, outline}, press {-shrink, 0, 0, 0} - the same sums, so it looks
exactly as it did.
"""

import hashlib
import json
import os
import sys

from pages.data_graphics.easing import colour, number

KINDS = ("button", "card", "circle", "image")
IMAGE_TYPES = (".png", ".jpg", ".jpeg")     # what Fusion's Loader, Qt and the preview all read
MAX_PATH = 1024
MAX_PX = 32768
# How the cursor is drawn: the pointer Windows has, the one a Mac has, a dot, or not at all.
CURSORS = ("windows", "mac", "dot", "none")
MAX_ELEMENTS = 12
MAX_LABEL = 40
MAX_NAME = 80
MIN_SECONDS, MAX_SECONDS = 1.0, 30.0
MAX_KEYS = 600              # of the cursor's path
MAX_CLICKS = 60
TOLERANCE = 0.25            # a recorded path is thinned to within this much of itself, % of the frame
STATES = ("hover", "press")
# What a state says, and how far each may go: grow %, lift % of the frame's height, glow and outline %.
ANSWER_KEYS = {"grow": (-50.0, 50.0), "lift": (-20.0, 20.0), "glow": (0.0, 100.0), "outline": (0.0, 100.0)}

# What a new shape of each kind starts as.
NEW = {
    "button": {"w": 14.0, "h": 9.0, "radius": 50.0, "colour": "#3c82e0", "label": "Button", "reacts": True},
    "card": {"w": 24.0, "h": 30.0, "radius": 12.0, "colour": "#23262e", "label": "", "reacts": True},
    "circle": {"w": 8.0, "h": 8.0, "radius": 100.0, "colour": "#e0503c", "label": "", "reacts": True},
    # Its colour is its glow's: the picture is its own.
    "image": {"w": 24.0, "h": 24.0, "radius": 0.0, "colour": "#ffffff", "label": "", "reacts": True},
}
# A new interaction: a panel that stays still, three buttons on it, and a cursor that
# crosses them and clicks the middle one.
TEMPLATE_ELEMENTS = [
    {"id": "e1", "kind": "card", "x": 50.0, "y": 50.0, "w": 60.0, "h": 56.0, "radius": 8.0, "colour": "#1b1e25",
     "label": "", "reacts": False},
    {"id": "e2", "kind": "button", "x": 32.0, "y": 60.0, "w": 14.0, "h": 9.0, "radius": 50.0, "colour": "#3c82e0",
     "label": "Play", "reacts": True},
    {"id": "e3", "kind": "button", "x": 50.0, "y": 60.0, "w": 14.0, "h": 9.0, "radius": 50.0, "colour": "#3cb46e",
     "label": "Save", "reacts": True},
    {"id": "e4", "kind": "button", "x": 68.0, "y": 60.0, "w": 14.0, "h": 9.0, "radius": 50.0, "colour": "#e0503c",
     "label": "Share", "reacts": True},
]
TEMPLATE_PATH = [[0.0, 12.0, 88.0], [0.9, 32.0, 61.0], [1.5, 32.0, 61.0], [2.1, 50.0, 61.0], [3.0, 50.0, 61.0],
                 [3.6, 68.0, 61.0], [4.2, 68.0, 61.0], [5.0, 90.0, 88.0]]
TEMPLATE_CLICKS = [[2.4, 2.6]]

DEFAULTS = {
    "seconds": 5.0,
    "elements": TEMPLATE_ELEMENTS, "path": TEMPLATE_PATH, "clicks": TEMPLATE_CLICKS,
    # How every shape that answers looks hovered and pressed, unless it has states of
    # its own: % bigger (negative smaller), % of the frame's height lifted, % of a full
    # glow round it, % of a full outline traced round it - no lift, no outline to start with.
    "answers": {"hover": {"grow": 8.0, "lift": 0.0, "glow": 70.0, "outline": 0.0},
                "press": {"grow": -6.0, "lift": 0.0, "glow": 0.0, "outline": 0.0}},
    "outline_colour": "#ffffff",
    "outline_width": 0.5,       # the outline's thickness, % of the frame's height
    "hover_seconds": 0.2,       # how long a hover takes to come up and to go
    "press_seconds": 0.1,
    # The pointer of the computer Buddy is on, to start with; its height, % of the frame's height.
    "cursor": "mac" if sys.platform == "darwin" else "windows", "cursor_size": 5.0,
    # The labels' face.
    "font": "Arial", "style": "Bold", "label_size": 3.6, "label_colour": "#ffffff",
}


def is_image(path):
    """Whether a file's name says it's a picture an image shape can show."""
    return os.path.splitext(str(path or ""))[1].lower() in IMAGE_TYPES


def _picture(raw):
    """{"path", "px"} of an image shape, or None when it has no usable picture."""
    path = str(raw.get("path") or "")[:MAX_PATH]
    try:
        wide, high = (int(v) for v in raw.get("px"))
    except (TypeError, ValueError):
        return None
    if not is_image(path) or not (0 < wide <= MAX_PX and 0 < high <= MAX_PX):
        return None
    return {"path": path, "px": [wide, high]}


def _answer(raw, auto):
    """One state's look, whole: what isn't said is `auto`'s."""
    raw = raw if isinstance(raw, dict) else {}
    return {key: number(raw.get(key), auto[key], low, high) + 0.0 for key, (low, high) in ANSWER_KEYS.items()}


def _answers(raw):
    """{"hover", "press"} - each state's look, whole."""
    raw = raw if isinstance(raw, dict) else {}
    return {state: _answer(raw.get(state), DEFAULTS["answers"][state]) for state in STATES}


def _states(raw):
    """A shape's own states, or None: it follows the interaction's answers."""
    return _answers(raw) if isinstance(raw, dict) else None


def _old_answers(raw):
    """The answers an interaction saved before states had: "hover_grow", "hover_glow",
    "hover_outline" and "press_shrink", each as it was kept then - the same sums."""
    grow = number(raw.get("hover_grow"), 8.0, 0.0, 50.0)
    glow = number(raw.get("hover_glow"), 70.0, 0.0, 100.0)
    edge = number(raw.get("hover_outline"), 0.0, 0.0, 100.0)
    shrink = number(raw.get("press_shrink"), 6.0, 0.0, 30.0)
    return {"hover": {"grow": grow, "lift": 0.0, "glow": glow, "outline": edge},
            "press": {"grow": 0.0 - shrink, "lift": 0.0, "glow": 0.0, "outline": 0.0}}


def _element(raw):
    """A whole shape (without its id yet), or None for an image with no picture."""
    raw = raw if isinstance(raw, dict) else {}
    kind = raw.get("kind") if raw.get("kind") in KINDS else "button"
    new = NEW[kind]
    w = number(raw.get("w"), new["w"], 1.0, 100.0)
    picture = _picture(raw) if kind == "image" else {}
    if picture is None:
        return None
    return {
        "kind": kind,
        "x": number(raw.get("x"), 50.0, 0.0, 100.0), "y": number(raw.get("y"), 50.0, 0.0, 100.0),
        "w": w, "h": w if kind == "circle" else number(raw.get("h"), new["h"], 1.0, 100.0),
        "radius": 100.0 if kind == "circle" else number(raw.get("radius"), new["radius"], 0.0, 100.0),
        "colour": colour(raw.get("colour"), new["colour"]),
        "label": " ".join(str(raw.get("label") or "").split())[:MAX_LABEL],
        "reacts": bool(raw.get("reacts", new["reacts"])),
        # Its own hover and press, or None: it answers as the interaction's "answers" say.
        "states": _states(raw.get("states")),
        **picture,
    }


def _elements(raw):
    """The shapes, bottom first, renumbered e1.. in that order - so an id says where a
    shape is in the pile, and the comp's tools are named by it."""
    if not isinstance(raw, list):
        raw = TEMPLATE_ELEMENTS
    kept = [el for el in (_element(row) for row in raw) if el is not None][:MAX_ELEMENTS]
    return [{"id": f"e{n}", **el} for n, el in enumerate(kept, 1)]


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


def clean(raw):
    """A whole, valid interaction from whatever was saved or sent. One saved before
    states (no "answers", perhaps the four old keys) gets the answers it drew with."""
    raw = raw if isinstance(raw, dict) else {}
    seconds = number(raw.get("seconds"), DEFAULTS["seconds"], MIN_SECONDS, MAX_SECONDS)
    recorded = isinstance(raw.get("path"), list)
    answers = raw.get("answers") if isinstance(raw.get("answers"), dict) else _old_answers(raw)
    return {
        "seconds": seconds,
        "elements": _elements(raw.get("elements")),
        "path": _path(raw["path"] if recorded else TEMPLATE_PATH, seconds),
        "clicks": _clicks(raw.get("clicks") if recorded else TEMPLATE_CLICKS, seconds),
        "answers": _answers(answers),
        "outline_colour": colour(raw.get("outline_colour"), DEFAULTS["outline_colour"]),
        "outline_width": number(raw.get("outline_width"), DEFAULTS["outline_width"], 0.1, 5.0),
        "hover_seconds": number(raw.get("hover_seconds"), DEFAULTS["hover_seconds"], 0.0, 1.0),
        "press_seconds": number(raw.get("press_seconds"), DEFAULTS["press_seconds"], 0.0, 0.5),
        "cursor": raw.get("cursor") if raw.get("cursor") in CURSORS else DEFAULTS["cursor"],
        "cursor_size": number(raw.get("cursor_size"), DEFAULTS["cursor_size"], 1.0, 20.0),
        "font": str(raw.get("font") or DEFAULTS["font"])[:MAX_NAME],
        "style": str(raw.get("style") or DEFAULTS["style"])[:MAX_NAME],
        "label_size": number(raw.get("label_size"), DEFAULTS["label_size"], 1.0, 20.0),
        "label_colour": colour(raw.get("label_colour"), DEFAULTS["label_colour"]),
    }


# ------------------------------------------------------------- the shapes --

def element(spec, eid):
    return next((e for e in spec["elements"] if e["id"] == eid), None)


def with_element(spec, eid, key, value):
    """One thing about a shape changed - or "box": {x, y, w, h}, it dragged or
    stretched. None for a shape or a key there isn't."""
    if element(spec, eid) is None or key not in ("box", "label", "colour", "radius", "reacts"):
        return None
    change = ({k: value.get(k) for k in ("x", "y", "w", "h") if k in value} if key == "box" and isinstance(value, dict)
              else {} if key == "box" else {key: value})
    return clean({**spec, "elements": [{**e, **change} if e["id"] == eid else e for e in spec["elements"]]})


def with_answers(spec, eid, state, key, value):
    """How shapes look in a state: `state` "hover" or "press", `key` "grow", "lift",
    "glow" or "outline". eid "" is the interaction's own answers, which every shape
    without states of its own follows; a shape's first change starts its own from the
    interaction's, and key "reset" has it follow the interaction's again. None for a
    state, a shape or a key there isn't."""
    if state not in STATES:
        return None
    if not eid:
        if key not in ANSWER_KEYS:
            return None
        answers = spec["answers"]
        return clean({**spec, "answers": {**answers, state: {**answers[state], key: value}}})
    found = element(spec, eid)
    if found is None or (key not in ANSWER_KEYS and key != "reset"):
        return None
    if key == "reset":
        states = None
    else:
        own = found["states"] or {s: dict(spec["answers"][s]) for s in STATES}
        states = {**own, state: {**own[state], key: value}}
    return clean({**spec, "elements": [{**e, "states": states} if e["id"] == eid else e for e in spec["elements"]]})


def with_new(spec, kind, more=None):
    """(the interaction with a new shape of `kind` on top, the new shape's id) - or
    (None, None) for a kind there isn't, when it's full, or for an image without its
    picture. more: what else the shape starts with (an image's "path", "px", "w", "h")."""
    if kind not in KINDS or len(spec["elements"]) >= MAX_ELEMENTS:
        return None, None
    if kind == "image" and _picture(more or {}) is None:
        return None, None
    n = len(spec["elements"])
    # Each new one a little down and right of the last, so they don't hide each other.
    at = {"x": 30.0 + 6.0 * (n % 6), "y": 30.0 + 6.0 * (n % 6)}
    made = clean({**spec, "elements": [*spec["elements"], {"kind": kind, **NEW[kind], **at, **(more or {})}]})
    return made, made["elements"][-1]["id"]


def without(spec, eid):
    if element(spec, eid) is None:
        return None
    return clean({**spec, "elements": [e for e in spec["elements"] if e["id"] != eid]})


def to_front(spec, eid):
    """(the interaction with the shape on top of the pile, its new id) or (None, None)."""
    picked = element(spec, eid)
    if picked is None:
        return None, None
    made = clean({**spec, "elements": [e for e in spec["elements"] if e["id"] != eid] + [picked]})
    return made, made["elements"][-1]["id"]


# ------------------------------------------------------------ the cursor --

def thinned(samples, tolerance=TOLERANCE):
    """[[t, x, y]] with the samples a straight move between their neighbours already
    passes through (to within `tolerance`) left out - Fusion moves the cursor in a
    straight line from key to key, so what's left plays the same."""
    points = [list(p) for p in samples]
    if len(points) < 3:
        return points
    keep = {0, len(points) - 1}
    spans = [(0, len(points) - 1)]
    while spans:
        a, b = spans.pop()
        (ta, xa, ya), (tb, xb, yb) = points[a], points[b]
        worst, at = 0.0, None
        for i in range(a + 1, b):
            t, x, y = points[i]
            p = (t - ta) / (tb - ta) if tb > ta else 0.0
            off = ((x - (xa + (xb - xa) * p)) ** 2 + (y - (ya + (yb - ya) * p)) ** 2) ** 0.5
            if off > worst:
                worst, at = off, i
        if at is not None and worst > tolerance:
            keep.add(at)
            spans += [(a, at), (at, b)]
    return [points[i] for i in sorted(keep)]


def with_recording(spec, samples, clicks, seconds):
    """The interaction with a newly recorded cursor: its path thinned, the clip as long
    as the recording."""
    seconds = number(seconds, spec["seconds"], MIN_SECONDS, MAX_SECONDS)
    path = _path(samples, seconds)
    return clean({**spec, "seconds": seconds, "path": thinned(path), "clicks": clicks})


def frames(spec, fps):
    """The clip's frames."""
    return max(2, int(round(spec["seconds"] * fps)))


def cursor_keys(spec, fps, total):
    """[(frame, x, y)]: the path's keys on the frames nearest them (a later key wins a
    frame), with one on the first frame and one on the last - what the comp's cursor is
    keyed with."""
    last = max(1, int(total)) - 1
    at = {}
    for t, x, y in spec["path"]:
        at[min(last, max(0, int(round(t * fps))))] = (x, y)
    first, final = min(at), max(at)
    at.setdefault(0, at[first])
    at.setdefault(last, at[final])
    return [(f, *at[f]) for f in sorted(at)]


def cursor(spec, fps, total):
    """[(x, y)] on every frame: straight from key to key."""
    keys = cursor_keys(spec, fps, total)
    out, i = [], 0
    for f in range(max(1, int(total))):
        while i + 1 < len(keys) and keys[i + 1][0] <= f:
            i += 1
        fa, xa, ya = keys[i]
        if i + 1 < len(keys) and f > fa:
            fb, xb, yb = keys[i + 1]
            p = (f - fa) / (fb - fa)
            out.append((xa + (xb - xa) * p, ya + (yb - ya) * p))
        else:
            out.append((xa, ya))
    return out


def down(spec, fps, total):
    """Whether the button is down on each frame - a click is down for a frame at least."""
    held = [False] * max(1, int(total))
    for start, end in spec["clicks"]:
        a = int(round(start * fps))
        for f in range(a, max(a + 1, int(round(end * fps)))):
            if 0 <= f < len(held):
                held[f] = True
    return held


def look_back(seconds, fps):
    """The frames a hover (or a press) takes: how many past frames its amount is shared over."""
    return max(1, int(round(seconds * fps)))


def inside(el, x, y, frame):
    """Whether the cursor at (x, y) is on the shape: a box by its sides, a circle by its
    round."""
    if el["kind"] == "circle":
        fw, fh = frame
        dx, dy = (x - el["x"]) / 100 * fw, (y - el["y"]) / 100 * fh
        return dx * dx + dy * dy < (el["w"] / 100 * fw / 2) ** 2
    return abs(x - el["x"]) < el["w"] / 2 and abs(y - el["y"]) < el["h"] / 2


def boxed(px, frame, w=NEW["image"]["w"], most=80.0):
    """(w, h) of the box a picture of px gets on a frame, % of its width and height: w
    wide and as tall as the picture's own shape makes it - smaller if that is taller
    than `most` of the frame."""
    fw, fh = frame
    h = w / 100 * fw * px[1] / px[0] / fh * 100
    if h > most:
        w, h = w * most / h, most
    return round(w, 3), round(h, 3)


def fit(el, frame):
    """(width, height) in px of an image shape's picture: as large as fits its box."""
    fw, fh = frame
    box_w, box_h = el["w"] / 100 * fw, el["h"] / 100 * fh
    wide, high = el["px"]
    if box_w * high > box_h * wide:                 # the box is the wider: the height decides
        return box_h * wide / high, box_h
    return box_w, box_w * high / wide


def eased(share):
    """A share 0..1 with a gentle start and stop."""
    return share * share * (3 - 2 * share)


def amounts(spec, frame, fps, total):
    """{id: [(hover, press) on every frame]} for the shapes that answer: hover 0..1
    (eased), press 0..1 - a frame before the clip's first counts as the first."""
    where, held = cursor(spec, fps, total), down(spec, fps, total)
    n, m = look_back(spec["hover_seconds"], fps), look_back(spec["press_seconds"], fps)
    out = {}
    for el in spec["elements"]:
        if not el["reacts"]:
            continue
        on = [inside(el, x, y, frame) for x, y in where]
        rows = []
        for f in range(len(where)):
            hover = sum(on[max(0, f - k)] for k in range(n)) / n
            press = sum(on[max(0, f - k)] and held[max(0, f - k)] for k in range(m)) / m
            rows.append((round(eased(hover), 6), round(press, 6)))
        out[el["id"]] = rows
    return out


def answers_of(spec, el):
    """How a shape answers: its own states, or the interaction's."""
    return (el or {}).get("states") or spec["answers"]


def look(answers, hover, press):
    """{"scale", "lift", "glow", "outline"} of a shape answering by `answers`, hovered
    and pressed that much - the sums in this module's docstring."""
    h, p = answers["hover"], answers["press"]
    return {"scale": 1 + h["grow"] / 100 * hover + p["grow"] / 100 * press,
            "lift": h["lift"] * hover + p["lift"] * press,
            "glow": min(1.0, max(0.0, h["glow"] / 100 * hover + p["glow"] / 100 * press)),
            "outline": min(1.0, max(0.0, h["outline"] / 100 * hover + p["outline"] / 100 * press))}


def looks(spec, frame, fps, total):
    """{id: [look() on every frame]} for the shapes that answer, each by its own answers."""
    shapes = {el["id"]: el for el in spec["elements"]}
    return {eid: [look(answers_of(spec, shapes[eid]), hover, press) for hover, press in rows]
            for eid, rows in amounts(spec, frame, fps, total).items()}


def scale(spec, hover, press, el=None):
    """A shape's size, hovered and pressed that much - `el`'s, or one that follows the
    interaction's answers."""
    return look(answers_of(spec, el), hover, press)["scale"]


def interaction_id(spec, frame, fps):
    """A hash of exactly what was placed, so the same interaction is known again."""
    raw = json.dumps({"spec": spec, "frame": list(frame), "fps": float(fps)}, sort_keys=True)
    return "bi-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
