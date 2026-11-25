"""A design, as data: layers on a canvas, bottom first. The one description the canvas
in Buddy draws (web/design.js) and the comp in Resolve is written from (comp.py). No
Qt, no Resolve.

Places are the design's own pixels, across and down, on a canvas of "size". A layer's x
and y are the middle of its box, w and h the box's sides, rot degrees clockwise about
that middle. "flipx" / "flipy", there only when true, mirror its own picture about that
middle, across / up and down, before it is turned (geometry.flipped); a line has none.

    rect      a box; "radius" rounds its corners, px - or "radii", there only when the four
              differ: top left, top right, bottom right, bottom left of its own picture
    ellipse   the ellipse in the box
    line      from the middle of the box's left side to the middle of its right - stroke only
    polygon   "sides" corners on the box's ellipse, the first at the top
    star      "sides" tips, the dips between them "inner" % of the way out from the middle
              (a polygon's and a star's corners are rounded by "radius" px, too)
              (every shape but a line has "linked": its width and height change together)
    path      "points" drawn with the pen: each {x, y} a place in the box (0..1 across and
              down), {ix, iy} and {ox, oy} its handles' offsets in the same units;
              "closed" joins the last to the first
    text      one line; x, y are where a Text+'s Center goes - "align" says which side of
              x the line runs, and the baseline is the face's baseline offset below y
    image     a picture file ("path", "px" wide and high), w wide and as tall as its own
              shape makes it; "radius" (or "radii") rounds its corners, as a rect's
    group     "children", layers of any kind, with one opacity for the lot
    boolean   "children", shapes combined by "op" into one outline, the first the base
    frame     an auto layout frame: a box with a rectangle's look (fill - none to start
              with - stroke, "radius", shadow; never turned) under its "children", which
              its "layout" puts in a line: "direction" row or column (or "none": it
              places nothing), "gap" px between each and the next, "padx" and "pady" px
              inside its sides, "align" start, center or end across the line, and "w"
              and "h" each "hug" (as large as what's in it) or "fixed"
    component a main component: "children" drawn as a group's, and "box" {x, y, w, h}, the
              straight box its instances are fitted from (kept round its children by the view)
    instance  draws its main's children ("of": the component's id) as they are now, fitted
              from the main's box into its own (resolve(): see "Components" below)

Every layer but a group, a boolean and a component also has "born": what it was when it
was first made, without its id, name, hidden, locked and children - what the view's
Reset puts back (_born). The comp never reads it.

A group's, a boolean's and a component's children hold their own places on the canvas:
the parent has none of its own. A boolean has the look (fill, shadow, opacity); its
children only shape.

A design can also have "guides": {"x": [...], "y": [...]}, lines dragged out of the
canvas's rulers - x the places of the upright ones, y of the level ones - that layers
snap to. They are only for drawing by: the comp never has them (comp.py, design_id).

A frame has a box of its own as well as its children's: the view lays the children out
(web/design.js, layOut - text is only measured there) and sends the places it worked
out; they are stored as sent, never laid out again here.
"""

import hashlib
import json
import math
import os
import re

from pages.data_graphics.easing import colour, number

SHAPES = ("rect", "ellipse", "line", "polygon", "star", "path")
KINDS = SHAPES + ("text", "image", "group", "boolean", "frame", "component", "instance")
OPS = ("union", "subtract", "intersect", "exclude")
# What a boolean's children can be: outlines that enclose something.
SOLIDS = ("rect", "ellipse", "polygon", "star", "path")
IMAGE_TYPES = (".png", ".jpg", ".jpeg")     # what Fusion's Loader, Qt and the canvas all read
JOINS = ("miter", "round", "bevel")
CAPS = ("butt", "round", "square")
ALIGNS = ("left", "center", "right")
FILLS = ("none", "solid", "linear")
DIRECTIONS = ("row", "column", "none")
LAYOUT_ALIGNS = ("start", "center", "end")
SIZINGS = ("hug", "fixed")

MAX_LAYERS = 200            # in a whole design, children counted - an instance is one
MAX_DRAWN = 1000            # what is drawn and exported, every instance counted as its main's layers
MAX_POINTS = 400            # in one path
MAX_STOPS = 8
MAX_NAME = 60
MAX_TEXT = 200
MAX_PATH = 1024
MAX_PX = 32768
MAX_GUIDES = 40             # each way
MIN_SIDE, MAX_SIDE = 16, 16384
MIN_SECONDS, MAX_SECONDS = 1.0, 600.0
FAR = 100000.0              # no place is further than this from the canvas's corner
ID = re.compile(r"[A-Za-z0-9_-]{1,24}\Z")

# What Reset can put back as it was drawn ("born"), and what of a layer it never changes.
RESETTABLE = SHAPES + ("text", "image", "frame", "instance")
OWN = ("id", "name", "hidden", "locked", "children", "born")

SIZE = (1920, 1080)
TITLES = {"rect": "Rectangle", "ellipse": "Ellipse", "line": "Line", "polygon": "Polygon", "star": "Star",
          "path": "Path", "text": "Text", "image": "Image", "group": "Group", "boolean": "Shape",
          "frame": "Auto layout", "component": "Component", "instance": "Instance"}
FILL = {"type": "solid", "colour": "#3c82e0", "angle": 0.0,
        "stops": [{"at": 0.0, "colour": "#3c82e0"}, {"at": 100.0, "colour": "#9a5cf0"}]}
STROKE = {"width": 0.0, "colour": "#ffffff", "join": "miter", "cap": "butt"}
# A new frame's layout: what's wrapped stays as close as it can to where it was.
LAYOUT = {"direction": "row", "gap": 16.0, "padx": 0.0, "pady": 0.0, "align": "center", "w": "hug", "h": "hug"}
SHADOW = {"on": False, "dx": 0.0, "dy": 12.0, "blur": 16.0, "colour": "#000000", "opacity": 50.0}
# What a new design starts with: nothing. (Its size is the open timeline's: page.py.)
DEFAULTS = {"name": "", "size": list(SIZE), "seconds": 5.0, "layers": []}


def is_image(path):
    """Whether a file's name says it's a picture an image layer can show."""
    return os.path.splitext(str(path or ""))[1].lower() in IMAGE_TYPES


def _name(raw, fallback=""):
    return " ".join(str(raw or "").split())[:MAX_NAME] or fallback


def _fill(raw, kind):
    raw = raw if isinstance(raw, dict) else {}
    stops = []
    for row in raw.get("stops") if isinstance(raw.get("stops"), list) else []:
        if isinstance(row, dict):
            stops.append({"at": round(number(row.get("at"), 0.0, 0.0, 100.0), 3),
                          "colour": colour(row.get("colour"), "#ffffff")})
    stops = sorted(stops, key=lambda s: s["at"])[:MAX_STOPS]
    if len(stops) < 2:
        stops = [dict(s) for s in FILL["stops"]]
    start = "none" if kind in ("line", "frame") else FILL["type"]
    return {"type": raw.get("type") if raw.get("type") in FILLS and kind != "line" else start,
            "colour": colour(raw.get("colour"), FILL["colour"]),
            "angle": round(number(raw.get("angle"), FILL["angle"], -360.0, 360.0), 3), "stops": stops}


def _stroke(raw, kind):
    raw = raw if isinstance(raw, dict) else {}
    return {"width": round(number(raw.get("width"), 4.0 if kind == "line" else STROKE["width"], 0.0, 500.0), 3),
            "colour": colour(raw.get("colour"), STROKE["colour"]),
            "join": raw.get("join") if raw.get("join") in JOINS else STROKE["join"],
            "cap": raw.get("cap") if raw.get("cap") in CAPS else STROKE["cap"]}


def _shadow(raw):
    raw = raw if isinstance(raw, dict) else {}
    return {"on": bool(raw.get("on", SHADOW["on"])),
            "dx": round(number(raw.get("dx"), SHADOW["dx"], -2000.0, 2000.0), 3),
            "dy": round(number(raw.get("dy"), SHADOW["dy"], -2000.0, 2000.0), 3),
            "blur": round(number(raw.get("blur"), SHADOW["blur"], 0.0, 500.0), 3),
            "colour": colour(raw.get("colour"), SHADOW["colour"]),
            "opacity": round(number(raw.get("opacity"), SHADOW["opacity"], 0.0, 100.0), 3)}


def _layout(raw):
    """A frame's layout settings, each one its own default when it isn't one."""
    raw = raw if isinstance(raw, dict) else {}

    def pick(key, choices):
        return raw.get(key) if raw.get(key) in choices else LAYOUT[key]
    return {"direction": pick("direction", DIRECTIONS),
            "gap": round(number(raw.get("gap"), LAYOUT["gap"], -FAR, FAR), 3),
            "padx": round(number(raw.get("padx"), LAYOUT["padx"], 0.0, FAR), 3),
            "pady": round(number(raw.get("pady"), LAYOUT["pady"], 0.0, FAR), 3),
            "align": pick("align", LAYOUT_ALIGNS), "w": pick("w", SIZINGS), "h": pick("h", SIZINGS)}


def _points(raw):
    out = []
    for row in raw if isinstance(raw, list) else []:
        if not isinstance(row, dict):
            continue
        out.append({key: round(number(row.get(key), 0.0, -50.0, 50.0), 5) for key in ("x", "y", "ix", "iy", "ox", "oy")})
    return out[:MAX_POINTS]


def _picture(raw):
    """{"path", "px"} of an image layer, or None when it has no usable picture."""
    path = str(raw.get("path") or "")[:MAX_PATH]
    try:
        wide, high = (int(v) for v in raw.get("px"))
    except (TypeError, ValueError):
        return None
    if not is_image(path) or not (0 < wide <= MAX_PX and 0 < high <= MAX_PX):
        return None
    return {"path": path, "px": [wide, high]}


def _corners(raw, out):
    """A box's corner rounding onto it: "radius" px at every corner - and "radii", the
    four (top left, top right, bottom right, bottom left of its own picture), there only
    when they aren't all the same."""
    out["radius"] = round(number(raw.get("radius"), 0.0, 0.0, FAR), 3)
    rows = raw.get("radii")
    if isinstance(rows, list) and len(rows) == 4:
        radii = [round(number(r, out["radius"], 0.0, FAR), 3) for r in rows]
        if len(set(radii)) > 1:
            out["radii"] = radii
        else:
            out["radius"] = radii[0]


def _box(raw, w=200.0, h=200.0):
    return {"x": round(number(raw.get("x"), 0.0, -FAR, FAR), 3), "y": round(number(raw.get("y"), 0.0, -FAR, FAR), 3),
            "w": round(number(raw.get("w"), w, 1.0, FAR), 3), "h": round(number(raw.get("h"), h, 1.0, FAR), 3),
            "rot": round(number(raw.get("rot"), 0.0, -3600.0, 3600.0) % 360.0, 3)}


def _flip(raw, out):
    """A layer's flips onto it: each there only when true. Both at once are the picture
    turned half round, so they are kept as neither, with rot + 180."""
    across, down = bool(raw.get("flipx")), bool(raw.get("flipy"))
    if across and down:
        out["rot"] = round((out["rot"] + 180.0) % 360.0, 3)
    elif across or down:
        out["flipx" if across else "flipy"] = True


def _layer(raw, room, inside=None):
    """A whole layer, as _made() makes it - with "born" (_born) when it is one Reset can
    put back as it was drawn."""
    out = _made(raw, room, inside)
    if out is not None and out["kind"] in RESETTABLE:
        out["born"] = _born(raw, out, inside)
    return out


def _born(raw, out, inside):
    """What a layer was when it was first made - its kind, size, turn and look, all but
    what's its own (OWN) - kept with it so the view's Reset can put it back. It is the
    "born" it came with, cleaned as any layer is (a rectangle made a path by Edit points
    is still born a rectangle), or else the layer as it is now: the first time a layer is
    cleaned is when it has just been drawn."""
    was = raw.get("born")
    made = None
    if isinstance(was, dict) and was.get("kind") in RESETTABLE:
        made = _made({k: v for k, v in was.items() if k not in ("born", "children")}, [1], inside)
    made = made if made is not None else out
    return json.loads(json.dumps({k: v for k, v in made.items() if k not in OWN}))


def _made(raw, room, inside=None):
    """A whole layer (its id as it came, not yet checked) or None for one that can't be drawn. room: a
    one-item list, how many more layers the design can hold. inside: "boolean" for a
    boolean's child (a solid shape only), "group" for a group's, "frame" for a frame's."""
    raw = raw if isinstance(raw, dict) else {}
    kind = raw.get("kind")
    if kind not in KINDS or room[0] <= 0 or (inside == "boolean" and kind not in SOLIDS):
        return None
    room[0] -= 1
    out = {"id": raw.get("id"), "kind": kind, "name": _name(raw.get("name"), TITLES[kind]),
           "hidden": bool(raw.get("hidden")),
           "locked": bool(raw.get("locked")),
           "opacity": round(number(raw.get("opacity"), 100.0, 0.0, 100.0), 3)}
    if kind in ("group", "boolean", "component"):
        rows = raw.get("children") if isinstance(raw.get("children"), list) else []
        children = [c for c in (_layer(row, room, kind) for row in rows) if c is not None]
        if not children:
            room[0] += 1
            return None
        out["children"] = children
        if kind == "component":
            out["box"] = _main_box(raw.get("box"), children)
        if kind == "boolean":
            out.update(op=raw.get("op") if raw.get("op") in OPS else "union", fill=_fill(raw.get("fill"), kind),
                       shadow=_shadow(raw.get("shadow")), blur=round(number(raw.get("blur"), 0.0, 0.0, 500.0), 3))
        return out
    if kind == "frame":
        # Kept with nothing in it: a box still, that hugs again when something is put in.
        rows = raw.get("children") if isinstance(raw.get("children"), list) else []
        out.update(_box(raw), rot=0.0, fill=_fill(raw.get("fill"), kind), stroke=_stroke(raw.get("stroke"), kind),
                   shadow=_shadow(raw.get("shadow")), layout=_layout(raw.get("layout")))
        _corners(raw, out)
        out["children"] = [c for c in (_layer(row, room, kind) for row in rows) if c is not None]
        return out
    out.update(_box(raw))
    if kind == "instance":
        # Its own place and size; what it draws is its main's (resolve()).
        out.update(of=str(raw.get("of") or "")[:24], linked=bool(raw.get("linked", True)))
        return out
    if kind != "line":
        _flip(raw, out)
    out.update(shadow=_shadow(raw.get("shadow")), blur=round(number(raw.get("blur"), 0.0, 0.0, 500.0), 3))
    if kind == "text":
        text = " ".join(str(raw.get("text") or "").split())[:MAX_TEXT]
        out.update(text=text or "Text", font=str(raw.get("font") or "Arial")[:80],
                   style=str(raw.get("style") or "Bold")[:80],
                   size=round(number(raw.get("size"), 96.0, 4.0, 4000.0), 3),
                   align=raw.get("align") if raw.get("align") in ALIGNS else "center",
                   colour=colour(raw.get("colour"), "#ffffff"))
        return out
    if kind == "image":
        picture = _picture(raw)
        if picture is None:
            room[0] += 1
            return None
        # Never stretched: as tall as its width and the picture's own shape make it.
        out.update(picture, h=round(max(1.0, out["w"] * picture["px"][1] / picture["px"][0]), 3))
        _corners(raw, out)
        return out
    out.update(fill=_fill(raw.get("fill"), kind), stroke=_stroke(raw.get("stroke"), kind))
    if kind != "line":
        # Its width and height kept in step as it is sized in the view.
        out["linked"] = bool(raw.get("linked"))
    if kind == "rect":
        _corners(raw, out)
    elif kind in ("polygon", "star"):
        out["sides"] = int(number(raw.get("sides"), 5 if kind == "star" else 6, 3, 60))
        out["radius"] = round(number(raw.get("radius"), 0.0, 0.0, FAR), 3)
        if kind == "star":
            out["inner"] = round(number(raw.get("inner"), 45.0, 5.0, 95.0), 3)
    elif kind == "path":
        points = _points(raw.get("points"))
        if len(points) < 2:
            room[0] += 1
            return None
        out.update(points=points, closed=bool(raw.get("closed")) and len(points) > 2)
        if inside == "boolean" and not out["closed"]:
            room[0] += 1
            return None
    return out


def _guides(raw):
    """{"x": [...], "y": [...]}: each way's places as numbers, once each, in order -
    those that aren't numbers left out."""
    raw = raw if isinstance(raw, dict) else {}
    out = {}
    for axis in ("x", "y"):
        rows = raw.get(axis) if isinstance(raw.get(axis), list) else []
        places = []
        for row in rows:
            if isinstance(row, bool) or not isinstance(row, (int, float, str)):
                continue
            value = number(row, None, -FAR, FAR)
            if value is not None and round(value, 3) not in places:
                places.append(round(value, 3))
        out[axis] = sorted(places[:MAX_GUIDES])
    return out


def _numbered(layers, used):
    """The layers with their ids: the one each came with when it is a good one nobody
    has yet, else the next free "l<n>" - so the canvas's selection lasts through a change."""
    out = []
    for layer in layers:
        lid = layer["id"]
        if not (isinstance(lid, str) and ID.match(lid)) or lid in used:
            n = len(used) + 1
            while f"l{n}" in used:
                n += 1
            lid = f"l{n}"
        used.add(lid)
        layer = {**layer, "id": lid}
        if "children" in layer:
            layer["children"] = _numbered(layer["children"], used)
        out.append(layer)
    return out


def clean(raw):
    """A whole, valid design from whatever was saved or sent."""
    raw = raw if isinstance(raw, dict) else {}
    try:
        size = [int(number(v, d, MIN_SIDE, MAX_SIDE)) for v, d in zip(raw.get("size"), SIZE)]
    except TypeError:
        size = list(SIZE)
    size = size if len(size) == 2 else list(SIZE)
    rows = raw.get("layers") if isinstance(raw.get("layers"), list) else []
    room = [MAX_LAYERS]
    made = [layer for layer in (_layer(row, room) for row in rows) if layer is not None]
    out = {"name": _name(raw.get("name")), "size": size,
           "seconds": round(number(raw.get("seconds"), DEFAULTS["seconds"], MIN_SECONDS, MAX_SECONDS), 3),
           "layers": _linked_up(_numbered(made, set()))}
    guides = _guides(raw.get("guides"))
    if guides["x"] or guides["y"]:          # a design without any has no "guides" at all
        out["guides"] = guides
    return out


# -------------------------------------------------------------- the layers --

def every(layers):
    """Every layer of a pile, children after their parent."""
    for layer in layers:
        yield layer
        yield from every(layer.get("children") or [])


def count(design):
    return sum(1 for _layer_ in every(design["layers"]))


def with_new(design, layer):
    """(the design with a new layer on top, its id) - or (None, None) when the layer
    can't be made or there's no room."""
    if count(design) >= MAX_LAYERS:
        return None, None
    made = clean({**design, "layers": [*design["layers"], {**layer, "id": None}]})
    if len(made["layers"]) != len(design["layers"]) + 1:
        return None, None
    return made, made["layers"][-1]["id"]


def boxed(px, size, share=0.5):
    """(w, h) of the box a picture of px gets on a canvas: its own size, or smaller when
    that is more than `share` of the canvas either way - never stretched."""
    k = min(1.0, share * size[0] / px[0], share * size[1] / px[1])
    return round(px[0] * k, 3), round(px[1] * k, 3)


def faces(design):
    """[(family, style)] the design's text is set in, each once, in order."""
    out = []
    for layer in every(design["layers"]):
        if layer["kind"] == "text" and (layer["font"], layer["style"]) not in out:
            out.append((layer["font"], layer["style"]))
    return out


def images(design):
    return [layer for layer in every(design["layers"]) if layer["kind"] == "image"]


def frames(design, fps):
    """The clip's frames."""
    return max(2, int(round(design["seconds"] * fps)))


def design_id(design, frame, fps):
    """A hash of exactly what was placed, so the same design is known again. Its guides
    aren't placed: moving one makes no other comp."""
    placed = {key: value for key, value in design.items() if key != "guides"}
    raw = json.dumps({"design": placed, "frame": list(frame), "fps": float(fps)}, sort_keys=True)
    return "bd-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


# -------------------------------------------------------------- components --
# A main component is a container like a group, with a box of its own: the straight
# box round its children when they were last settled (the view keeps it so as they're
# edited, web/design.js: settleMains). An instance is a leaf with a box (x, y, w, h,
# rot) that draws the main's children as they are now, carried from the main's box
# into its own:
#
#     a place p      goes to the instance's middle + turn(rot) of
#                    (sx * (p.x - box.x), sy * (p.y - box.y))
#     sx, sy         the instance's w / box w and h / box h - the same while it is
#                    "linked" (the default: a uniform scale, which is exact)
#     a layer's turn, width and height
#                    its own width's way, stretched by (sx, sy), is its new turn (plus the
#                    instance's) and says how much longer it gets; its height takes what
#                    keeps its area. Exact for a uniform scale, and for any stretch of a
#                    layer turned a multiple of 90; otherwise the slant a stretch would
#                    give it is left out (a non-uniform stretch of a turned child is an
#                    approximation)
#     text           its size goes with its height; an image keeps its own shape, its
#                    area scaled; a stroke, a corner radius, a blur and a shadow's softness
#                    and offset by sqrt(sx * sy) - a shadow still falls the way it does on
#                    the canvas (it isn't turned with the instance, as no layer's shadow is)
#     a combined shape's gradient turns with the instance, across its new bounds
#
# The canvas (web/design.js: resolveInstance) draws by the same rule, and comp.py
# writes an instance as what resolve() returns. There are no per-instance changes yet.

def _main_box(raw, children):
    """A component's box as sent, or, without a usable one, the bounds of its children."""
    raw = raw if isinstance(raw, dict) else {}
    try:
        box = {key: float(raw[key]) for key in ("x", "y", "w", "h")}
    except (KeyError, TypeError, ValueError):
        box = None
    if box is None or not all(math.isfinite(v) and abs(v) <= FAR for v in box.values()) or box["w"] < 1 or box["h"] < 1:
        box = _bounds_box(children)
    return {key: round(value, 3) for key, value in box.items()}


def _bounds_box(layers):
    """The straight box round some layers, a text's line guessed from its size."""
    xs, ys = [], []
    for layer in every(layers):
        if "children" in layer:
            continue
        if layer["kind"] == "text":
            wide = max(4.0, 0.55 * layer["size"] * len(layer["text"]))
            shift = {"left": wide / 2, "right": -wide / 2}.get(layer["align"], 0.0)
            layer = {**layer, "x": layer["x"] + shift, "w": wide, "h": layer["size"]}
        turn = math.radians(layer["rot"])
        c, s = math.cos(turn), math.sin(turn)
        a, b = layer["w"] / 2, 0.0 if layer["kind"] == "line" else layer["h"] / 2
        for x, y in ((-a, -b), (a, -b), (a, b), (-a, b)):
            xs.append(layer["x"] + x * c - y * s)
            ys.append(layer["y"] + x * s + y * c)
    left, top, right, bottom = min(xs), min(ys), max(xs), max(ys)
    return {"x": (left + right) / 2, "y": (top + bottom) / 2, "w": max(1.0, right - left), "h": max(1.0, bottom - top)}


def mains(design_or_layers):
    """{id: main component} of a design (or a pile of layers), wherever they are in it."""
    layers = design_or_layers["layers"] if isinstance(design_or_layers, dict) else design_or_layers
    return {layer["id"]: layer for layer in every(layers) if layer["kind"] == "component"}


def instances_of(design_or_layers, main_id):
    layers = design_or_layers["layers"] if isinstance(design_or_layers, dict) else design_or_layers
    return [layer for layer in every(layers) if layer["kind"] == "instance" and layer["of"] == main_id]


def _weigher(found):
    """A function: how many layers a main draws, its own instances counted in full."""
    weight = {}

    def of(main_id):
        if main_id not in found:
            return 1
        if main_id not in weight:
            weight[main_id] = 1         # (a loop, were there one, ends here)
            weight[main_id] = 1 + sum(_drawn(child, of) for child in found[main_id]["children"])
        return weight[main_id]
    return of


def _drawn(layer, of):
    if layer["kind"] == "instance":
        return of(layer["of"])
    return 1 + sum(_drawn(child, of) for child in layer.get("children") or [])


def drawn(design):
    """How many layers a design draws, and the comp is written from: instances counted in full."""
    of = _weigher(mains(design))
    return sum(_drawn(layer, of) for layer in design["layers"])


def _demote(layers, inside_main=False):
    """A main inside another main becomes a group: a component isn't made of components."""
    out = []
    for layer in layers:
        if "children" in layer:
            nested = inside_main or layer["kind"] == "component"
            layer = {**layer, "children": _demote(layer["children"], nested)}
            if inside_main and layer["kind"] == "component":
                layer = {key: value for key, value in layer.items() if key != "box"}
                layer["kind"] = "group"
        out.append(layer)
    return out


def _without(layers, drop):
    """The pile with the layers whose ids are in drop taken out, and any container left empty."""
    out = []
    for layer in layers:
        if layer["id"] in drop:
            continue
        if "children" in layer:
            children = _without(layer["children"], drop)
            if not children:
                continue
            layer = {**layer, "children": children}
        out.append(layer)
    return out


def _loops(found):
    """The ids of the instances that would have a main draw itself, directly or through others."""
    bad, done = set(), set()

    def visit(main_id, stack):
        done.add(main_id)
        for inner in every(found[main_id]["children"]):
            if inner["kind"] != "instance" or inner["of"] not in found:
                continue
            if inner["of"] in stack:
                bad.add(inner["id"])
            elif inner["of"] not in done:
                visit(inner["of"], (*stack, inner["of"]))
    for main_id in found:
        if main_id not in done:
            visit(main_id, (main_id,))
    return bad


def _linked_up(layers):
    """The layers with every instance's main there, no main drawing itself, and no more
    drawn than MAX_DRAWN. An instance whose main is missing, or that would make a loop,
    is dropped (deleting a main in the view detaches its instances first, so this only
    meets designs from elsewhere); instances over the limit go, the last first."""
    layers = _demote(layers)
    while True:
        found = mains(layers)
        drop = {layer["id"] for layer in every(layers) if layer["kind"] == "instance" and layer["of"] not in found}
        drop |= _loops(found)
        if not drop:
            of = _weigher(found)
            if sum(_drawn(layer, of) for layer in layers) <= MAX_DRAWN:
                return layers
            drop = {[layer for layer in every(layers) if layer["kind"] == "instance"][-1]["id"]}
        layers = _without(layers, drop)


def resolve(instance, found):
    """What an instance draws: a group, in its place, of copies of its main's children
    carried by the rule above (their ids "<instance id>/<child id>", so they're its own)
    - or None when its main isn't in found ({id: main}, mains()). A nested instance
    comes out as an instance, in its new place: resolve that in turn."""
    main = found.get(instance.get("of"))
    if main is None:
        return None
    box = main["box"]
    sx, sy = instance["w"] / box["w"], instance["h"] / box["h"]
    turn = math.radians(instance["rot"])
    c, s = math.cos(turn), math.sin(turn)
    k = math.sqrt(sx * sy)

    def place(x, y):
        dx, dy = (x - box["x"]) * sx, (y - box["y"]) * sy
        return instance["x"] + dx * c - dy * s, instance["y"] + dx * s + dy * c

    def carried(layer):
        out = json.loads(json.dumps(layer))
        out["id"] = f"{instance['id']}/{layer['id']}"
        if "children" in layer:
            out["children"] = [carried(child) for child in layer["children"]]
            if layer["kind"] == "boolean":
                out["fill"]["angle"] = (layer["fill"]["angle"] + instance["rot"]) % 360.0
        if "children" not in layer or "w" in layer:
            # A leaf - or an auto layout frame's own box, carried as a leaf's is.
            own = math.radians(layer["rot"])
            ax, ay = sx * math.cos(own), sy * math.sin(own)
            across = math.hypot(ax, ay)
            down = sx * sy / across
            out["x"], out["y"] = place(layer["x"], layer["y"])
            out["rot"] = (math.degrees(math.atan2(ay, ax)) + instance["rot"]) % 360.0
            if "layout" in layer:
                out["layout"] = dict(layer["layout"], gap=layer["layout"]["gap"] * k,
                                     padx=layer["layout"]["padx"] * k, pady=layer["layout"]["pady"] * k)
            if layer["kind"] == "image":
                out["w"] = layer["w"] * k
                out["h"] = out["w"] * layer["px"][1] / layer["px"][0]
            else:
                out["w"], out["h"] = layer["w"] * across, layer["h"] * down
            if layer["kind"] == "text":
                out["size"] = layer["size"] * down
            if "radius" in layer:
                out["radius"] = layer["radius"] * k
            if "radii" in layer:
                out["radii"] = [r * k for r in layer["radii"]]
            if "stroke" in layer:
                out["stroke"]["width"] = layer["stroke"]["width"] * k
        if "shadow" in layer:
            for key in ("dx", "dy", "blur"):
                out["shadow"][key] = layer["shadow"][key] * k
        if "blur" in layer:
            out["blur"] = layer["blur"] * k
        return out
    return {"id": instance["id"], "kind": "group", "name": instance["name"], "hidden": instance["hidden"],
            "locked": instance["locked"], "opacity": instance["opacity"],
            "children": [carried(child) for child in main["children"]]}
