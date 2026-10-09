"""Kinetic: the "kinetic" arrangement - a title built up block by block, some blocks
turned a quarter turn against the rest, and a camera that turns, moves and zooms so the
newest block always reads upright. The classic kinetic-type tile.

Blocks: a word with a mark starts a new block; the words after it, up to the next mark,
are its block, set on one line. A mark is
  "line"  the block goes underneath, as wide as the title so far
  "cw"    it turns clockwise: on the title's right, reading down, as long as its side
  "ccw"   it turns anticlockwise: on the left, reading up
Each block is sized to the edge it joins, so the title stays a tight rectangle as it
grows. The camera frames the words where they sit, so nudging one (or sizing or turning
it) reframes the camera on it - the other blocks stay put. The person sets the marks (doc "turns": word id -> mark, or "none"); the ones
they haven't set are Buddy's suggestions (suggest()).

Space: the layout is in world px - the first block as wide as the safe zone - and the
boxes are where the words sit there (box rot includes the block's quarter turns). The
camera says where the frame looks: the world point at its centre, its turn and zoom.
A block's camera looks at it upright - zoomed onto it ("each", 62% of the frame) or on
the whole title so far ("all", 80%). When a block comes in, the camera eases there from
the last block's over MOVE seconds.

screen_boxes() turns the world boxes into the frame's at a moment: each word's own
place, size and turn - so every renderer draws it sharp, and Text+ is keyed the same
way (textplus_comp), rather than a picture of it being zoomed. web/titles_motion.js
plays the same camera; tests keep the two equal.
"""

import math

MARKS = ("line", "cw", "ccw")
CAMERAS = ("each", "all")
FILL = {"each": 0.62, "all": 0.8}       # the camera's target fills this much of the frame
MOVE = 0.6                              # the camera's move to a new block, seconds
STEP = 1.35                             # blocks come in this far apart (the move, then a hold)
LONG = 6                                # a word this long (or the last) is worth a turn


def suggest(tokens):
    """Buddy's marks for these words ({index: mark}): a block every one or two words,
    turning at the longer words and the last - the payoff - and stacking the rest.
    Turns take turns, clockwise then anticlockwise, so no word ends up more than a
    quarter turn from upright."""
    marks, i, turn = {}, 1, "cw"
    n = len(tokens)
    while i < n:
        norm = tokens[i]["norm"]
        long = len(norm) >= LONG or i == n - 1
        marks[i] = turn if long else "line"
        if long:
            turn = "ccw" if turn == "cw" else "cw"
        i += 1 if len(norm) >= LONG else 2
    return marks


def marks_of(tokens, doc):
    """Every word's mark - (mark or None, "manual" | "auto") - the person's where they
    set one, else the suggestion. The first word starts the title: never marked."""
    own = doc.get("turns") or {}
    sug = suggest(tokens)
    out = []
    for i, t in enumerate(tokens):
        if i == 0:
            out.append((None, "auto"))
        elif own.get(t["id"]) in MARKS:
            out.append((own[t["id"]], "manual"))
        elif own.get(t["id"]) == "none":
            out.append((None, "manual"))
        else:
            out.append((sug.get(i), "auto"))
    return out


# ------------------------------------------------------------- 2D maths --
# An affine map as (a, b, c, d, e, f): x' = a x + c y + e, y' = b x + d y + f - y down,
# as DOMMatrix and SVG have it. Quarter turns stay exact.

IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def mul(m, n):
    a, b, c, d, e, f = m
    a2, b2, c2, d2, e2, f2 = n
    return (a * a2 + c * b2, b * a2 + d * b2, a * c2 + c * d2, b * c2 + d * d2,
            a * e2 + c * f2 + e, b * e2 + d * f2 + f)


def apply(m, x, y):
    a, b, c, d, e, f = m
    return a * x + c * y + e, b * x + d * y + f


def translate(x, y):
    return (1.0, 0.0, 0.0, 1.0, float(x), float(y))


def quarter(q):
    """q quarter turns clockwise (on screen, y down)."""
    c, s = ((1, 0), (0, 1), (-1, 0), (0, -1))[q % 4]
    return (float(c), float(s), float(-s), float(c), 0.0, 0.0)


def _bbox(points):
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return [min(xs), min(ys), max(xs), max(ys)]


def _turn_box(box, q):
    """An axis-aligned box seen from a frame q quarter turns round: its corners mapped
    back by the opposite turn."""
    inv = quarter(-q)
    x0, y0, x1, y1 = box
    return _bbox([apply(inv, x, y) for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1))])


# ---------------------------------------------------------------- layout --

def compose(words, look, doc, tokens, frame, safe_px, geometry):
    """The kinetic layout of composer words (composer._Word, measured): boxes in world
    px, the blocks (with what their camera looks at) and lines (one a block).
    geometry: composer._geometry, for a block's line at a letter size."""
    fw, fh = frame
    safe_w = safe_px[2] - safe_px[0]
    over = doc.get("over") or {}
    offsets = over.get("offset") or {}
    marks = marks_of(tokens, doc)
    groups = []
    for i, w in enumerate(words):
        mark = marks[i][0]
        if i == 0 or mark:
            groups.append({"how": "first" if i == 0 else mark, "words": [w], "from": marks[i][1]})
        else:
            groups[-1]["words"].append(w)

    C, q = IDENTITY, 0                  # the frame the newest block reads upright in -> world; its quarter turns
    tile = None                         # the title so far, in that frame
    boxes, blocks, lines = [], [], []
    for k, g in enumerate(groups):
        ws = g["words"]
        one = geometry([ws], [1.0], look)[0]
        width1, thick1 = max(one["w"], 1e-9), one["asc"] + one["desc"]
        big1 = max(one["sizes"])
        how = g["how"]
        if tile is None:
            s = safe_w / width1
            M = C
            region = [0.0, 0.0, width1 * s, thick1 * s]
        elif how == "line":
            s = (tile[2] - tile[0]) / width1
            y = tile[3] + float(look.get("gap", 0.14)) * big1 * s
            M = mul(C, translate(tile[0], y))
            region = [tile[0], y, tile[2], y + thick1 * s]
        else:
            s = (tile[3] - tile[1]) / width1
            h, gap = thick1 * s, float(look.get("gap", 0.14)) * big1 * s
            if how == "cw":         # on the right, reading down
                region = [tile[2] + gap, tile[1], tile[2] + gap + h, tile[3]]
                M = mul(C, mul(translate(region[2], tile[1]), quarter(1)))
            else:                   # on the left, reading up
                region = [tile[0] - gap - h, tile[1], tile[0] - gap, tile[3]]
                M = mul(C, mul(translate(region[0], tile[3]), quarter(-1)))
        grown = region if tile is None else [min(tile[0], region[0]), min(tile[1], region[1]),
                                             max(tile[2], region[2]), max(tile[3], region[3])]
        turn = 1 if how == "cw" else -1 if how == "ccw" else 0
        if turn:
            C, q = mul(C, quarter(turn)), q + turn
            tile = _turn_box(grown, turn)
        else:
            tile = grown
        angle = 90.0 * q
        # "own" and "all", what its camera looks at, once every word is placed (_frame_words).
        blocks.append({"first": ws[0].id, "how": how, "from": g["from"], "words": [w.id for w in ws],
                       "frame": list(C), "angle": angle, "own": None, "all": None})
        lines.append([w.id for w in ws])
        block_angle = angle                                  # the block's own turn in the world
        for w, x, z, oy in zip(ws, one["xs"], one["sizes"], one["oys"]):
            size = z * s
            dx, dy = offsets.get(w.id) or (0.0, 0.0)
            left = x * s + dx * size
            base = one["asc"] * s + oy * s + dy * size
            ink = size * (w.above if w.caps else w.asc)
            bw = w.w100 / 100.0 * size
            bh = ink + size * (w.below if w.caps else w.desc)
            cxu, cyu = left + bw / 2, base - ink + bh / 2
            cx, cy = apply(M, cxu, cyu)
            boxes.append({
                "id": w.id, "text": w.text, "role": w.role, "line": k, "font": w.font,
                "x": cx - bw / 2, "y": cy - bh / 2, "w": bw, "h": bh, "size": size,
                "baseline": cy + (base - cyu), "rot": block_angle + w.rot, "track": w.track * size,
                "frameRot": block_angle,
            })
    _frame_words(blocks, boxes)
    return {"boxes": boxes, "lines": lines, "blocks": blocks}


def corners(box):
    """A box's four corners in world px, turned about its centre as it's drawn."""
    cx, cy = box["x"] + box["w"] / 2, box["y"] + box["h"] / 2
    r = math.radians(box.get("rot", 0.0))
    c, s = math.cos(r), math.sin(r)
    hw, hh = box["w"] / 2, box["h"] / 2
    return [(cx + u * hw * c - v * hh * s, cy + u * hw * s + v * hh * c) for u, v in ((-1, -1), (1, -1), (-1, 1), (1, 1))]


def _frame_words(blocks, boxes):
    """What each block's camera looks at - the block's words ("own") and every word so
    far ("all"), seen upright in its frame - from the words where they sit: nudged,
    sized and turned by the person. Moving a word reframes the camera on it; the other
    blocks stay where the layout put them."""
    at = {b["id"]: corners(b) for b in boxes}
    seen = []
    for block in blocks:
        inv = _invert(tuple(block["frame"]))
        seen += block["words"]
        block["own"] = _bbox([apply(inv, x, y) for wid in block["words"] for x, y in at[wid]])
        block["all"] = _bbox([apply(inv, x, y) for wid in seen for x, y in at[wid]])


def _invert(m):
    a, b, c, d, e, f = m
    det = a * d - b * c
    ia, ib, ic, idd = d / det, -b / det, -c / det, a / det
    return (ia, ib, ic, idd, -(ia * e + ic * f), -(ib * e + idd * f))


# ---------------------------------------------------------------- camera --

def ease(p):
    p = 0.0 if p <= 0 else 1.0 if p >= 1 else p
    return 4 * p ** 3 if p < 0.5 else 1 - (-2 * p + 2) ** 3 / 2


def camera_on(block, which, frame):
    """The camera on a block: {"cx", "cy"} the world point at the frame's centre,
    "angle" its turn (degrees), "z" its zoom - looking at the block itself ("each") or
    the title so far ("all")."""
    fw, fh = frame
    x0, y0, x1, y1 = block["own" if which == "each" else "all"]
    cx, cy = apply(tuple(block["frame"]), (x0 + x1) / 2, (y0 + y1) / 2)
    fill = FILL[which]
    z = min(fw * fill / max(x1 - x0, 1e-9), fh * fill / max(y1 - y0, 1e-9))
    return {"cx": cx, "cy": cy, "angle": block["angle"], "z": z}


def camera_keys(blocks, starts, which, frame):
    """The camera's keys, [{"t", "cx", "cy", "angle", "z"}]: on the first block from the
    start; for each block after, held on the last until the block's first word comes in
    (starts: one a block), then eased onto it over MOVE seconds."""
    which = which if which in CAMERAS else "each"
    keys = []
    for k, (block, start) in enumerate(zip(blocks, starts)):
        cam = camera_on(block, which, frame)
        if k == 0:
            keys.append({"t": 0.0, **cam})
            continue
        start = max(float(start), keys[-1]["t"])
        keys.append({"t": start, **{n: keys[-1][n] for n in ("cx", "cy", "angle", "z")}})
        keys.append({"t": start + MOVE, **cam})
    return keys


def camera_at(keys, t):
    """Where the camera is at t: eased from key to key - its centre straight across,
    its turn the short way round, its zoom evenly (in log)."""
    if not keys:
        return {"cx": 0.0, "cy": 0.0, "angle": 0.0, "z": 1.0}
    if t <= keys[0]["t"]:
        return dict(keys[0])
    for a, b in zip(keys, keys[1:]):
        if t < b["t"]:
            span = b["t"] - a["t"]
            u = ease((t - a["t"]) / span) if span > 0 else 1.0
            da = (b["angle"] - a["angle"] + 180.0) % 360.0 - 180.0
            return {"t": t, "cx": a["cx"] + (b["cx"] - a["cx"]) * u, "cy": a["cy"] + (b["cy"] - a["cy"]) * u,
                    "angle": a["angle"] + da * u, "z": a["z"] * (b["z"] / a["z"]) ** u}
    return dict(keys[-1])


def view(cam, frame):
    """The camera as a map from world px to the frame's."""
    fw, fh = frame
    r = math.radians(-cam["angle"])
    c, s = math.cos(r), math.sin(r)
    z = cam["z"]
    rot = (c * z, s * z, -s * z, c * z, 0.0, 0.0)
    return mul(translate(fw / 2, fh / 2), mul(rot, translate(-cam["cx"], -cam["cy"])))


def screen_box(box, cam, frame):
    """A world box as the camera shows it: its centre mapped, its size zoomed, its turn
    less the camera's."""
    m = view(cam, frame)
    z = cam["z"]
    cx, cy = box["x"] + box["w"] / 2, box["y"] + box["h"] / 2
    sx, sy = apply(m, cx, cy)
    w, h = box["w"] * z, box["h"] * z
    return {**box, "x": sx - w / 2, "y": sy - h / 2, "w": w, "h": h, "size": box["size"] * z,
            "baseline": sy + (box["baseline"] - cy) * z, "rot": box.get("rot", 0.0) - cam["angle"],
            "track": (box.get("track") or 0.0) * z, "frameRot": box.get("frameRot", 0.0) - cam["angle"]}


def screen_boxes(boxes, keys, t, frame):
    """Every world box as the frame shows it t seconds in (keys: camera_keys)."""
    cam = camera_at(keys, t)
    return [screen_box(b, cam, frame) for b in boxes]


def on_screen(layout):
    """The layout in the frame's px: a kinetic one as its last frame shows it (unless it's
    been through at() already), any other as it is."""
    return at_rest(layout) if layout.get("kinetic") and not layout.get("screen") else layout


def at_rest(layout):
    """The layout as its last frame shows it - the camera settled on its last block - for
    whatever draws a still (thumbnails, the contact sheet, a still placed in Resolve)."""
    kin = layout.get("kinetic")
    if not kin or not kin.get("blocks"):
        return layout
    cam = camera_on(kin["blocks"][-1], kin.get("camera") or "each", layout["frame"])
    return {**layout, "boxes": [screen_box(b, cam, layout["frame"]) for b in layout["boxes"]], "screen": True}


def at(layout, clock, t):
    """The layout as the frame shows it t seconds in (clock: timing.timing)."""
    keys = (clock or {}).get("camera")
    if not layout.get("kinetic") or not keys:
        return at_rest(layout)
    return {**layout, "boxes": screen_boxes(layout["boxes"], keys, t, layout["frame"]), "screen": True}
