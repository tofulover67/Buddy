"""How a panel looks: the display list. A solved page (solve.py) goes in; a flat list
of rectangles, ellipses, lines, lines of text, polylines and wedges comes out, back to
front, in panel px - the one thing the view in Buddy and the comp in Resolve both
draw, so neither knows what a "card" or a "Mac window" is. No Qt, no Resolve.

A style is a small table (STYLES): its colours, dark and light, the corners' radius
for each role, how thick a stroke is, how soft a shadow - and three switches: "drop"
(a hard shadow, offset by that many px: Cartoon's), "buttons" (the window bar's own:
"lights" at the left, "glyphs" at the right, "dots") and "title" (which side the bar's
title sits). The drawing below is one code for all five; nothing in it asks which
style it is.

Colours carry their alpha ("#rrggbbaa"). Text never trusts a table to be readable:
every text colour is checked against what is under it (legible) and moved towards
black or white until it stands 4.5:1 clear - so any accent, on any style, can be read.
A see-through style is checked over its "backdrop", a guess at the picture behind.

A chart is Data Graphics' own (pages/data_graphics/graphs/model.py): its rows are made
a graph (clean), laid out in a frame the size of the chart's box (layout), and that
plan's bars, line or slices become prims. Nothing of how a graph is laid out is kept
here - only the fit: the plan is asked for in a frame just big enough that what it
draws fills the box.

An image box with a picture draws it as an "image" prim, filling the box and cropped
to it (crop(): the middle of the picture, in the box's shape - never stretched). A box
that types draws each of its lines from where the whole line starts (solve.typing), so
its letters appear in place, each line saying which of the box's letters it holds
("type"); a field that types draws its placeholder ("placeholder": shown until its first
letter) and its typed words.
"""

import colorsys

from pages.data_graphics.graphs import model as graphs

from . import catalogue, model, solve

NEED = 4.5                  # the contrast text must have with what's under it
SOFT = 0.2                  # how see-through the accent is as a wash (a badge, a row that's on)

_RADIUS = {"window": 10.0, "card": 8.0, "button": 6.0, "field": 6.0, "badge": 999.0, "image": 6.0, "row": 6.0,
           "bubble": 12.0, "icon": 7.0}
STYLES = {
    "mac": {
        "dark": {"surface": "#1e1f24ff", "bar": "#2a2b31ff", "raised": "#2a2c33ff", "tint": "#25262cff", "field": "#17181cff",
                 "line": "#ffffff1f", "text": "#f2f3f5ff", "dim": "#a4a8b3ff", "frame": "#ffffff2e", "backdrop": "#0f1014"},
        "light": {"surface": "#ffffffff", "bar": "#ecedf0ff", "raised": "#f6f7f9ff", "tint": "#f0f1f4ff", "field": "#ffffffff",
                  "line": "#0000001f", "text": "#1b1d22ff", "dim": "#5c616dff", "frame": "#00000029", "backdrop": "#d9dce3"},
        "radius": {**_RADIUS}, "stroke": 1.0, "shadow": 28.0, "card_shadow": 0.0, "drop": 0.0,
        "buttons": "lights", "title": "center",
    },
    "plain": {
        "dark": {"surface": "#18181bff", "bar": "#18181bff", "raised": "#222226ff", "tint": "#1f1f23ff", "field": "#111113ff",
                 "line": "#ffffff24", "text": "#fafafaff", "dim": "#a1a1aaff", "frame": "#ffffff24", "backdrop": "#09090b"},
        "light": {"surface": "#fafafaff", "bar": "#fafafaff", "raised": "#ffffffff", "tint": "#f1f1f3ff", "field": "#ffffffff",
                  "line": "#00000021", "text": "#18181bff", "dim": "#5f5f68ff", "frame": "#00000021", "backdrop": "#e4e4e7"},
        "radius": {**_RADIUS, "window": 6.0, "card": 4.0, "button": 4.0, "field": 4.0, "image": 2.0, "row": 4.0, "icon": 4.0},
        "stroke": 1.0, "shadow": 12.0, "card_shadow": 0.0, "drop": 0.0, "buttons": "dots", "title": "left",
    },
    "glass": {
        "dark": {"surface": "#141a28c7", "bar": "#ffffff14", "raised": "#ffffff1a", "tint": "#ffffff12", "field": "#00000040",
                 "line": "#ffffff47", "text": "#ffffffff", "dim": "#d3d8e4ff", "frame": "#ffffff66", "backdrop": "#3a4256"},
        "light": {"surface": "#ffffffc7", "bar": "#ffffff66", "raised": "#ffffff80", "tint": "#ffffff59", "field": "#ffffff99",
                  "line": "#ffffffb3", "text": "#10131aff", "dim": "#474d5aff", "frame": "#ffffffcc", "backdrop": "#aab4c8"},
        "radius": {**_RADIUS, "window": 18.0, "card": 14.0, "button": 999.0, "field": 10.0, "image": 10.0, "row": 10.0},
        "stroke": 1.0, "shadow": 36.0, "card_shadow": 0.0, "drop": 0.0, "buttons": "lights", "title": "center",
    },
    "windows": {
        "dark": {"surface": "#202020ff", "bar": "#1c1c1cff", "raised": "#2b2b2bff", "tint": "#272727ff", "field": "#1a1a1aff",
                 "line": "#ffffff17", "text": "#ffffffff", "dim": "#c5c5c5ff", "frame": "#ffffff26", "backdrop": "#101010"},
        "light": {"surface": "#f3f3f3ff", "bar": "#e9e9e9ff", "raised": "#ffffffff", "tint": "#ebebebff", "field": "#ffffffff",
                  "line": "#0000001a", "text": "#1a1a1aff", "dim": "#5d5d5dff", "frame": "#00000033", "backdrop": "#d6d6d6"},
        "radius": {**_RADIUS, "window": 7.0, "card": 4.0, "button": 4.0, "field": 4.0, "badge": 3.0, "image": 3.0, "row": 3.0,
                   "bubble": 6.0, "icon": 3.0},
        "stroke": 1.0, "shadow": 22.0, "card_shadow": 4.0, "drop": 0.0, "buttons": "glyphs", "title": "left",
    },
    "cartoon": {
        "dark": {"surface": "#2d2b55ff", "bar": "#3b3970ff", "raised": "#3b3970ff", "tint": "#35336aff", "field": "#242248ff",
                 "line": "#0e0d1fff", "text": "#ffffffff", "dim": "#d6d4f5ff", "frame": "#0e0d1fff", "backdrop": "#1b1a36"},
        "light": {"surface": "#fff8e7ff", "bar": "#ffe9b8ff", "raised": "#ffffffff", "tint": "#ffe9b8ff", "field": "#ffffffff",
                  "line": "#1a1a2eff", "text": "#1a1a2eff", "dim": "#4a4a5eff", "frame": "#1a1a2eff", "backdrop": "#f3e3bd"},
        "radius": {**_RADIUS, "window": 14.0, "card": 10.0, "button": 10.0, "field": 8.0, "image": 8.0, "row": 8.0},
        "stroke": 2.5, "shadow": 0.0, "card_shadow": 0.0, "drop": 4.0, "buttons": "lights", "title": "center",
        # Thick outlines go round everything that has a body, not only what the others outline.
        "outlined": ("button", "image", "badge", "icon", "tint", "bubble", "progress"),
    },
}
LIGHTS = ("#ff5f57ff", "#febc2eff", "#28c840ff")
UP, DOWN = "#22a565", "#e0503c"


# ----------------------------------------------------------------- colours --

def _rgba(text):
    text = text.lstrip("#")
    return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4)) + ((int(text[6:8], 16) / 255.0,) if len(text) >= 8 else (1.0,))


def _hex(rgb, alpha=1.0):
    return "#" + "".join(f"{min(255, max(0, int(round(v)))):02x}" for v in (*rgb[:3], alpha * 255))


def over(top, ground):
    """A colour with alpha laid over an opaque one: the opaque "#rrggbb" that shows."""
    if not top:
        return ground[:7]
    r, g, b, a = _rgba(top)
    under = _rgba(ground)
    return _hex([c * a + u * (1 - a) for c, u in zip((r, g, b), under)])[:7]


def mixed(one, other, share):
    """`share` of the way from one opaque colour to another."""
    a, b = _rgba(one), _rgba(other)
    return _hex([x + (y - x) * share for x, y in zip(a[:3], b[:3])])[:7]


def luminance(colour):
    parts = [c / 255.0 for c in _rgba(colour)[:3]]
    r, g, b = (c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in parts)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(one, other):
    """The contrast of two opaque colours, 1..21, as the web's guidelines count it."""
    a, b = sorted((luminance(one), luminance(other)), reverse=True)
    return (a + 0.05) / (b + 0.05)


def legible(colour, ground):
    """The colour, opaque, moved towards black or white only as far as it must be to
    stand NEED clear of the opaque colour under it."""
    colour = over(colour, ground)
    if contrast(colour, ground) >= NEED:
        return colour + "ff"
    end = "#ffffff" if contrast("#ffffff", ground) >= contrast("#000000", ground) else "#000000"
    for step in range(1, 21):
        tried = mixed(colour, end, step / 20)
        if contrast(tried, ground) >= NEED:
            return tried + "ff"
    return end + "ff"


def look(spec):
    """A panel's tokens: its style's table for its mode, with its accent."""
    style = STYLES[spec["style"]]
    tokens = {**style, **style["dark" if spec["dark"] else "light"], "accent": spec["accent"] + "ff",
              "soft": spec["accent"] + f"{int(round(SOFT * 255)):02x}", "outlined": style.get("outlined", ())}
    tokens["ground"] = over(tokens["surface"], tokens["backdrop"])
    return tokens


def backdrop(spec):
    """The opaque colour a panel's text is checked over: its style's guess at what's behind the window."""
    return look(spec)["backdrop"]


# ------------------------------------------------------------------- prims --

def _r(v):
    return round(v, 2)


def _rect(box, x, y, w, h, radius=0.0, fill="", stroke="", stroke_w=0.0, shadow=0.0, **more):
    return {"box": box, "kind": "rect", "x": _r(x), "y": _r(y), "w": _r(max(0.0, w)), "h": _r(max(0.0, h)),
            "radius": _r(max(0.0, min(radius, w / 2, h / 2))), "fill": fill, "stroke": stroke,
            "stroke_w": stroke_w if stroke else 0.0, "shadow": shadow, **more}


def _ellipse(box, x, y, w, h, fill="", stroke="", stroke_w=0.0):
    return {"box": box, "kind": "ellipse", "x": _r(x), "y": _r(y), "w": _r(w), "h": _r(h), "fill": fill, "stroke": stroke,
            "stroke_w": stroke_w if stroke else 0.0}


def _line(box, x1, y1, x2, y2, stroke, stroke_w=1.0):
    return {"box": box, "kind": "line", "x1": _r(x1), "y1": _r(y1), "x2": _r(x2), "y2": _r(y2), "stroke": stroke,
            "stroke_w": stroke_w}


def _text(box, x, y, words, size, fill, bold=False, align="left"):
    return {"box": box, "kind": "text", "x": _r(x), "y": _r(y), "text": words, "size": _r(size), "bold": bool(bold),
            "align": align, "fill": fill}


def _poly(box, points, closed=False, fill="", stroke="", stroke_w=0.0, draw=False):
    return {"box": box, "kind": "poly", "points": [[_r(x), _r(y)] for x, y in points], "closed": closed, "fill": fill,
            "stroke": stroke, "stroke_w": stroke_w if stroke else 0.0, "draw": draw}


def _body(t, box, x, y, w, h, radius, fill, stroke="", shadow=0.0, ground="", **more):
    """A filled shape with the style's outline - and, in a style with a hard shadow, that
    shadow under it: the same shape in the outline's colour, a little down and right.
    ground: the opaque colour under it - over a hard shadow a fill is made flat, or the
    shadow would show through it."""
    out = []
    if t["drop"] and stroke and fill:          # an outline with nothing in it casts none
        fill = over(fill, ground) + "ff" if ground else fill
        drop = min(t["drop"], max(2.0, h * 0.1))            # a small thing casts a small one
        out.append(_rect(box, x + drop, y + drop, w, h, radius, fill=t["frame"]))
    out.append(_rect(box, x, y, w, h, radius, fill=fill, stroke=stroke, stroke_w=t["stroke"], shadow=shadow, **more))
    return out


# ------------------------------------------------------------------ charts --

_GRAPH = {"bars": "bars", "line": "line", "area": "line", "spark": "line", "pie": "pie", "donut": "pie"}


def _graph(chart, t, ground):
    """The chart as Graphs' spec: no title, no numbers, the panel's accent."""
    shape = chart["shape"]
    rows = [dict(row) for row in chart["rows"]]
    if _GRAPH[shape] == "pie":
        # A panel's pie is shades of its accent, each slice given its colour as Graphs lets a row have one.
        for i, row in enumerate(rows):
            row["colour"] = mixed(t["accent"][:7], ground, 0.72 * i / max(1, len(rows)))
    return graphs.clean({"rows": rows, "shape": _GRAPH[shape], "values": False, "labels": shape in ("bars", "line", "area"),
                         "grid": shape in ("line", "area"), "fill": shape == "area", "hole": shape == "donut", "dots": False,
                         "rounded": True, "colours": "one", "bar_colour": t["accent"][:7], "stagger": False})


def _reach(plan):
    """(left, top, right, bottom) of what a bars or line plan draws, in its frame."""
    bars, axis, label = plan["bars"], plan["axis"], plan["label"]
    top = min([b["base"] - b["h"] for b in bars] + [axis["y"]]) - (plan["line"]["lift"] if plan["line"] else 0.0)
    if plan["grid"]:
        top = min([top] + [mark["y"] for mark in plan["marks"]])           # the lines across go up to a round number
    bottom = label["baseline"] + label["size"] * 0.3 if label else axis["y"] + axis["h"]
    return axis["x0"], top, axis["x1"], bottom


def _chart(p, t, ground):
    chart, bid = p["chart"], p["id"]
    spec = _graph(chart, t, ground)
    x, y, w, h = p["x"], p["y"], p["w"], p["h"]
    if w < 4 or h < 4 or not spec["rows"]:
        return []
    out = []
    if spec["shape"] == "pie":
        disc = graphs.layout(spec, (w, h))["pie"]
        by = min(w, h) / (2 * disc["r"])
        disc = graphs.layout(spec, (w * by, h * by))["pie"]         # the same plan, in a frame its disc fills the box of
        for piece in disc["slices"]:
            out.append({"box": bid, "kind": "wedge", "cx": _r(x + w / 2), "cy": _r(y + h / 2), "r": _r(disc["r"]),
                        "hole": _r(disc["inner"]), "from": _r(piece["a"] * 360), "to": _r(piece["b"] * 360),
                        "fill": piece["colour"] + "ff", "draw": True})
        return out
    frame = (w, h)
    for _again in range(3):                     # a plan's text doesn't quite scale with its frame: close in on the fit
        left, top, right, bottom = _reach(graphs.layout(spec, frame))
        frame = (frame[0] * w / max(1.0, right - left), frame[1] * h / max(1.0, bottom - top))
    plan = graphs.layout(spec, frame)
    left, top, right, bottom = _reach(plan)
    dx, dy = x - left, y - top
    faint, dim = t["line"], legible(t["dim"], ground)
    if plan["grid"]:
        for mark in plan["marks"]:
            out.append(_line(bid, plan["axis"]["x0"] + dx, mark["y"] + dy, plan["axis"]["x1"] + dx, mark["y"] + dy, faint))
    if chart["shape"] == "bars":
        axis = plan["axis"]
        out.append(_rect(bid, axis["x0"] + dx, axis["y"] + dy, axis["x1"] - axis["x0"], max(1.0, axis["h"]), fill=faint))
        for bar in plan["bars"]:
            out.append(_rect(bid, bar["cx"] - bar["w"] / 2 + dx, bar["base"] - bar["h"] + dy, bar["w"], bar["h"],
                             plan["radius"], fill=bar["colour"] + "ff", draw="up"))
    else:
        points = [(px + dx, py + dy) for px, py in graphs.points(plan)]
        if plan["line"]["fill"] and len(points) > 1:
            floor = plan["axis"]["y"] + dy
            # The room under the line wipes on with it, left to right ("draw": "across").
            out.append(_poly(bid, [*points, (points[-1][0], floor), (points[0][0], floor)], closed=True,
                             fill=plan["line"]["fill"], draw="across"))
        out.append(_poly(bid, points, stroke=plan["line"]["colour"] + "ff", stroke_w=_r(max(1.5, plan["line"]["w"])), draw=True))
    if plan["label"]:
        for bar in plan["bars"]:
            if bar["label"]:
                out.append(_text(bid, bar["cx"] + dx, plan["label"]["baseline"] + dy, bar["label"], plan["label"]["size"],
                                 dim, align="center"))
    return out


# ------------------------------------------------------------------- boxes --

def _picture(spec, p, t, ground):
    """An image's placeholder: a tinted box with a sun and a hill in it."""
    hue, light, sat = colorsys.rgb_to_hls(*[c / 255.0 for c in _rgba(t["accent"])[:3]])
    r, g, b = colorsys.hls_to_rgb((hue + p["hue"] / 360.0) % 1.0, 0.36 if spec["dark"] else 0.74, min(sat, 0.5))
    fill = _hex((r * 255, g * 255, b * 255))[:7]
    stroke = t["frame"] if "image" in t["outlined"] else ""
    x, y, w, h = p["x"], p["y"], p["w"], p["h"]
    out = _body(t, p["id"], x, y, w, h, t["radius"]["image"], fill + "ff", stroke)
    if min(w, h) >= 28:
        mark = mixed(fill, "#ffffff" if spec["dark"] else "#000000", 0.16) + "ff"
        s = min(w, h)
        out.append(_ellipse(p["id"], x + w * 0.68, y + h * 0.2, s * 0.16, s * 0.16, fill=mark))
        out.append(_poly(p["id"], [(x + w * 0.12, y + h * 0.86), (x + w * 0.4, y + h * 0.46), (x + w * 0.58, y + h * 0.7),
                                   (x + w * 0.7, y + h * 0.58), (x + w * 0.9, y + h * 0.86)], closed=True, fill=mark))
    return out


def crop(px, w, h):
    """[sx, sy, sw, sh]: the part of a picture `px` ([width, height], its own px) that
    fills a box w x h - the middle of it with the box's shape, as large as the picture
    allows (fill and crop: never stretched, never letterboxed)."""
    wide, high = px
    if w <= 0 or h <= 0 or wide <= 0 or high <= 0:
        return [0.0, 0.0, float(wide), float(high)]
    shape = w / h
    if wide / high > shape:                     # wider than the box: its sides are cut
        sw = high * shape
        return [_r((wide - sw) / 2), 0.0, _r(sw), float(high)]
    sh = wide / shape                           # taller (or the same): its top and foot are cut
    return [0.0, _r((high - sh) / 2), float(wide), _r(sh)]


def _photo(p, t, ground):
    """An image box's picture, filling it and cropped to it, rounded as its placeholder
    would be - with the style's outline (and hard shadow) round it."""
    x, y, w, h = p["x"], p["y"], p["w"], p["h"]
    radius = t["radius"]["image"]
    stroke = t["frame"] if "image" in t["outlined"] else ""
    out = []
    if t["drop"] and stroke:
        drop = min(t["drop"], max(2.0, h * 0.1))
        out.append(_rect(p["id"], x + drop, y + drop, w, h, radius, fill=t["frame"]))
    out.append({"box": p["id"], "kind": "image", "x": _r(x), "y": _r(y), "w": _r(max(0.0, w)), "h": _r(max(0.0, h)),
                "radius": _r(max(0.0, min(radius, w / 2, h / 2))), "path": p["image"]["path"],
                "px": list(p["image"]["px"]), "crop": crop(p["image"]["px"], w, h)})
    if stroke:
        out.append(_rect(p["id"], x, y, w, h, radius, stroke=stroke, stroke_w=t["stroke"]))
    return out


def _typing(p, colour, bold):
    """The lines of a box that types, each from its start (solve.typing), with which of
    the box's letters it holds: "type" [first, last)."""
    return [{**_text(p["id"], x, baseline, line, p["size"], colour, bold), "type": [first, last]}
            for line, first, last, x, baseline in solve.typing(p) if line]


def _words(p, x, colour, bold, align="left"):
    """A box's lines of text, the first line's top at the box's own."""
    size = p["size"]
    step = size * solve.LINE
    return [_text(p["id"], x, p["y"] + i * step + (step + solve.CAP * size) / 2, line, size, colour, bold, align)
            for i, line in enumerate(p["lines"]) if line]


def _one(spec, p, t, ground):
    """(a placed box's prims, the opaque colour under whatever is drawn in it)."""
    role, variant, bid = p["role"], p["variant"], p["id"]
    x, y, w, h = p["x"], p["y"], p["w"], p["h"]
    radius, frame = t["radius"], t["frame"]
    if role == "section":
        fill = {"card": t["raised"], "tint": t["tint"], "hero": t["soft"], "bubble": t["raised"], "bubble-me": t["accent"]}.get(variant)
        if variant in ("row", "navitem"):
            fill = t["soft"] if p["on"] else None
        if fill is None:
            return [], ground
        stroke = (t["accent"] if p["on"] else t["line"]) if variant in ("card", "bubble") else frame if variant in t["outlined"] else ""
        corner = radius["bubble" if variant.startswith("bubble") else "row" if variant in ("row", "navitem") else "card"]
        shadow = t["card_shadow"] if variant == "card" else 0.0
        return _body(t, bid, x, y, w, h, corner, fill, stroke, shadow, ground), over(fill, ground)
    if role == "text":
        tone = {"caption": t["dim"], "label": t["dim"], "up": UP, "down": DOWN}.get(variant, t["text"])
        bold = variant in solve.BOLD or p["strong"]
        if p.get("type"):
            return _typing(p, legible(tone, ground), bold), ground
        align = "right" if p["right"] else "center" if p["center"] else "left"
        at = x + w if align == "right" else x + w / 2 if align == "center" else x
        return _words(p, at, legible(tone, ground), bold, align), ground
    if role == "button":
        ghost = variant == "ghost"
        fill = "" if ghost else t["accent"]
        stroke = frame if "button" in t["outlined"] else t["line"] if ghost else ""
        under = over(fill, ground)
        out = _body(t, bid, x, y, w, h, radius["button"], fill, stroke, ground=ground)
        out += [_text(bid, x + w / 2, y + (h + solve.CAP * p["size"]) / 2, line, p["size"],
                      legible(t["accent"] if ghost else t["text"], under), True, "center") for line in p["lines"] if line]
        return out, under
    if role == "field":
        under = over(t["field"], ground)
        out = [_rect(bid, x, y, w, h, radius["field"], fill=t["field"], stroke=t["line"], stroke_w=t["stroke"])]
        placeholder = [_text(bid, x + solve.FIELD["pad"], y + (h + solve.CAP * p["size"]) / 2, line, p["size"],
                             legible(t["dim"], under)) for line in p["lines"] if line]
        if p.get("type"):
            # Its placeholder shows until its first letter does; what it types is in the text's colour.
            out += [{**prim, "placeholder": True} for prim in placeholder]
            out += _typing(p, legible(t["text"], under), False)
        else:
            out += placeholder
        return out, under
    if role == "badge":
        under = over(t["soft"], ground)
        out = _body(t, bid, x, y, w, h, radius["badge"], t["soft"], frame if "badge" in t["outlined"] else "", ground=ground)
        out += [_text(bid, x + w / 2, y + (h + solve.CAP * p["size"]) / 2, line, p["size"], legible(t["accent"], under), True,
                      "center") for line in p["lines"] if line]
        return out, under
    if role == "image":
        return (_photo(p, t, ground) if p.get("image") else _picture(spec, p, t, ground)), ground
    if role == "icon":
        stroke = frame if "icon" in t["outlined"] else ""
        if variant == "avatar":
            return [_ellipse(bid, x, y, w, h, fill=mixed(t["accent"][:7], ground, 0.45) + "ff", stroke=stroke,
                             stroke_w=t["stroke"])], ground
        if variant == "sm":
            return [_rect(bid, x, y, w, h, min(radius["icon"], 4.0), fill=legible(t["dim"], ground)[:7] + "99")], ground
        return [*_body(t, bid, x, y, w, h, radius["icon"], t["soft"], stroke, ground=ground),
                _rect(bid, x + w * 0.3, y + h * 0.3, w * 0.4, h * 0.4, radius["icon"] / 2, fill=t["accent"])], ground
    if role == "divider":
        return [_rect(bid, x, y, w, h, fill=t["line"])], ground
    if role == "progress":
        stroke = frame if "progress" in t["outlined"] else ""
        return [_rect(bid, x, y, w, h, h / 2, fill=t["line"], stroke=stroke, stroke_w=t["stroke"]),
                _rect(bid, x, y, w * p["p"] / 100.0, h, h / 2, fill=t["accent"], draw="across")], ground
    if role == "chart":
        return _chart(p, t, ground), ground
    return [], ground


def _boxes(spec, solved, t):
    grounds, out = {"": t["ground"]}, []
    for placed in solved["boxes"]:
        prims, grounds[placed["id"]] = _one(spec, placed, t, grounds[placed["parent"]])
        out += prims
    return out


# ------------------------------------------------------------- the window --

def _bar_buttons(t, w, bar, top=0.0):
    """The window bar's own buttons, and (left, right) of the room they leave its title."""
    middle = top + bar / 2
    if t["buttons"] == "lights":
        ring = t["frame"] if t["drop"] else ""
        return ([_ellipse("", 13 + i * 19, middle - 6, 12, 12, fill=c, stroke=ring, stroke_w=min(t["stroke"], 1.5))
                 for i, c in enumerate(LIGHTS)], (76.0, w - 16.0))
    ink = legible(t["dim"], over(t["bar"], t["ground"]))
    if t["buttons"] == "dots":
        return [_ellipse("", w - 22 - i * 16, middle - 4, 8, 8, fill=ink[:7] + "80") for i in range(3)], (14.0, w - 66.0)
    x = w - 46 * 3
    out = [_line("", x + 18, middle, x + 28, middle, ink),                                  # minimise
           _rect("", x + 46 + 18, middle - 5, 10, 10, 1.0, stroke=ink, stroke_w=1.0),       # maximise
           _line("", x + 92 + 18, middle - 5, x + 92 + 28, middle + 5, ink),                # close
           _line("", x + 92 + 28, middle - 5, x + 92 + 18, middle + 5, ink)]
    return out, (14.0, x - 8.0)


def _title(spec, page_id):
    page = model.page_of(spec, page_id) or model.page_of(spec, spec["start"])
    return spec["title"] or page["name"]


def _fitted(words, size, room, bold=True):
    """A bar's title, cut to the room it has - by the solver's rule for a character's width."""
    return solve._one_line(words, size * (solve.BOLD_W if bold else solve.CHAR_W["body"]), room)


def _chrome(spec, solved, t, page_id):
    kind, w, h, bar = spec["kind"], solved["w"], solved["h"], solved["bar"]
    ground, radius, frame = t["ground"], t["radius"]["window"], t["frame"]
    title = _title(spec, page_id)
    if kind == "phone":
        inset = solve.INSET["phone"]
        body = "#0b0b0dff" if spec["dark"] else "#e7e7ebff"
        out = _body(t, "", 0, 0, w, h, 40.0, body, frame, t["shadow"])
        out.append(_rect("", inset, inset, w - 2 * inset, h - 2 * inset, 32.0, fill=over(t["surface"], t["backdrop"]) + "ff"))
        ink = legible(t["text"], ground)
        baseline = inset + (bar - inset + solve.CAP * 12) / 2
        out.append(_text("", inset + 22, baseline, "9:41", 12, ink, True))
        out.append(_rect("", w - inset - 44, baseline - 9, 22, 10, 3.0, stroke=ink, stroke_w=1.0))
        out.append(_rect("", w - inset - 42, baseline - 7, 14, 6, 1.5, fill=ink))
        out.append(_rect("", w / 2 - 50, h - inset - 13, 100, 4, 2.0, fill=ink[:7] + "99"))
        return out
    if kind == "widget":
        return _body(t, "", 0, 0, w, h, max(22.0, radius), t["surface"], frame, t["shadow"])
    out = _body(t, "", 0, 0, w, h, radius, t["surface"], frame, t["shadow"])
    under = over(t["bar"], ground)
    ink, dim = legible(t["text"], under), legible(t["dim"], under)
    # The bar: rounded like the window at its top, square where it meets the page.
    out.append(_rect("", 0, 0, w, bar, radius, fill=t["bar"]))
    out.append(_rect("", 0, bar / 2, w, bar / 2, fill=t["bar"]))
    out.append(_rect("", 0, bar - t["stroke"], w, t["stroke"], fill=t["line"] if not t["drop"] else frame))
    # The window's outline again, last: the bar was laid over the first.
    edge = _rect("", 0, 0, w, h, radius, stroke=frame, stroke_w=t["stroke"])
    if kind == "dialog":
        out.append(_text("", 14, (bar + solve.CAP * 13) / 2, _fitted(title, 13, w - 56), 13, ink, True))
        out.append(_line("", w - 24, bar / 2 - 5, w - 14, bar / 2 + 5, dim, 1.5))
        out.append(_line("", w - 14, bar / 2 - 5, w - 24, bar / 2 + 5, dim, 1.5))
        return [*out, edge]
    row = 32.0 if kind == "browser" else bar
    buttons, (left, right) = _bar_buttons(t, w, row)
    out += buttons
    if kind == "browser":
        # A tab over an address field.
        tab_w = max(60.0, min(190.0, right - left))
        tab = over(t["surface"], t["backdrop"])
        out.append(_rect("", left, 5, tab_w, row - 5, t["radius"]["field"], fill=tab + "ff"))
        out.append(_rect("", left, row / 2, tab_w, row / 2, fill=tab + "ff"))
        out.append(_text("", left + 10, 5 + (row - 5 + solve.CAP * 11) / 2, _fitted(title, 11, tab_w - 20), 11,
                         legible(t["text"], tab), True))
        out.append(_rect("", 0, row, w, bar - row, fill=tab + "ff"))
        out.append(_rect("", 0, bar - t["stroke"], w, t["stroke"], fill=t["line"] if not t["drop"] else frame))
        field_y, field_h = row + 6, bar - row - 12
        out.append(_rect("", 12, field_y, w - 24, field_h, t["radius"]["field"], fill=t["field"], stroke=t["line"],
                         stroke_w=t["stroke"]))
        out.append(_text("", 24, field_y + (field_h + solve.CAP * 11) / 2, _fitted("example.com", 11, w - 48, False), 11,
                         legible(t["dim"], over(t["field"], tab))))
        return [*out, edge]
    words = _fitted(title, 12, right - left)
    if t["title"] == "center":
        out.append(_text("", (left + right) / 2, (bar + solve.CAP * 12) / 2, words, 12, ink, True, "center"))
    else:
        out.append(_text("", left, (bar + solve.CAP * 12) / 2, words, 12, ink, True))
    return [*out, edge]


def chrome(spec, page_id=""):
    """The window's frame, bar and bar buttons only - its title the panel's, or that
    page's name (the start page's, for none)."""
    solved = solve.solve_all(spec)
    return _chrome(spec, solved.get(page_id) or solved[spec["start"]], look(spec), page_id)


def drawn(spec, page_id, solved=None):
    """A page's display list, back to front, the window's own chrome first. solved:
    solve.solve_all(spec), when it's already in hand."""
    solved = solved or solve.solve_all(spec)
    page = solved.get(page_id) or solved[spec["start"]]
    t = look(spec)
    return _chrome(spec, page, t, page_id) + _boxes(spec, page, t)


# ---------------------------------------------------------------- the shelf --

TILE_W = {"layout": 420.0, "component": 320.0}


def tile(layout_or_component, spec):
    """A shelf tile's small picture: {"w", "h", "prims"} of a layout or a component (by
    its id in the catalogue) by itself, in the panel's style, accent and mode - the view
    scales it to the tile. None for an id the catalogue hasn't."""
    whole = catalogue.layout(layout_or_component)
    part = None if whole else catalogue.component(layout_or_component)
    if whole is None and part is None:
        return None
    root = whole or catalogue.col([part], pad=14)
    mini = model.clean({"kind": "widget", "style": spec["style"], "accent": spec["accent"], "dark": spec["dark"],
                        "width": TILE_W["layout" if whole else "component"], "pages": [{"root": root}], "path": []})
    solved = solve.solve_all(mini)
    return {"w": solved["p1"]["w"], "h": solved["p1"]["h"], "prims": drawn(mini, "p1", solved)}
