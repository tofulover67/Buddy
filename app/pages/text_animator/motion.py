#!/usr/bin/env python3
"""
Motion presets - the moves Animation's Previews tab shows and puts on clips.

Each preset (motion_presets.json) is eight channels sampled 41 times across
`dur` seconds: position, rotation, scale and opacity, the way the approved
Presets Store design plays them - and blur, and a width and a height that
squash and stretch it on top of its scale. A preset has up to three moves in there -
the In at the start, an Emphasis in the middle, the Out at the end - with
the thing at rest between them. plan() lifts the moves out and lays them on
a clip of any length: the In from the clip's first frame, the Out ending on
its last, an Emphasis where the playhead is - or, a Loop, its one cycle over
and over from the first frame to the last. What comes out is Fusion
keyframes for motion_resolve.py to put on a Merge (Center, Size, Angle,
Blend), a Transform in front of it (width and height) and a Blur after it,
in the clip's own comp frames: a few eased keys per move fitted to
the samples (fit), not one per sample, so they edit like hand-set keys in
Fusion's spline editor.

No Resolve and no Qt in here: tests/test_motion_presets.py runs it on
plain Python.
"""

import functools
import json
import os

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "motion_presets.json")

SAMPLES = 41                    # 0..40
LAST = SAMPLES - 1

# Where each pack comes from - kept with the data, not shown (the licences of
# the two ported libraries are in THIRD_PARTY_NOTICES.md).
PACKS = [
    {"id": "Essentials", "credit": "Originals"},
    {"id": "Text", "credit": "Originals · by letter or word"},
    {"id": "Loops", "credit": "Originals · repeat across the clip"},
    {"id": "Camera", "credit": "Originals · across the whole clip"},
    {"id": "Classic", "credit": "From animate.css · MIT"},
    {"id": "Magic", "credit": "From Magic Animations · MIT"},
]
KIND_IN_OUT, KIND_EMPHASIS, KIND_OUT = "In · Out", "Emphasis", "Out"
KIND_IN = "In"                  # only made in the Editor: just an In's curve
KIND_LOOP = "Loop"              # one cycle, repeated from the clip's first frame to its last
KIND_WHOLE = "Whole clip"       # one move stretched from the clip's first frame to its last (a push in)
KINDS = [KIND_IN_OUT, KIND_IN, KIND_EMPHASIS, KIND_OUT, KIND_LOOP, KIND_WHOLE]

# Which part of an In · Out preset goes on the clip.
WAYS = ("both", "in", "out")
# How fast the move plays: 1 is the preset's own length.
SPEEDS = (0.5, 1.0, 1.5, 2.0)
# Which way a preset that moves across the frame goes (turned): "own" is
# the way it was made; the rest are the way it travels as it comes in (an
# Emphasis or a Loop: the way it moves first, an Out: the way it leaves).
DIRECTIONS = ("own", "left", "right", "up", "down")
# A Loop goes on at most this many times; a longer clip rests after them.
MAX_LOOPS = 500

# A sample counts as moving when it's this far from rest (x/y in the
# preset's half-units, degrees, scale, opacity, blur in half-units - 1 is
# 30 px on a 1920-wide frame - width and height as shares of the scale).
_REST = {"x": 0.0, "y": 0.0, "r": 0.0, "s": 1.0, "o": 1.0, "b": 0.0, "w": 1.0, "h": 1.0, "m": 0.0, "k": 0.0,
         "e": 0.0, "l": 0.0, "c": 0.0, "f": 0.0, "g": 0.0}
_EPS = {"x": 0.05, "y": 0.05, "r": 0.05, "s": 0.005, "o": 0.005, "b": 0.02, "w": 0.005, "h": 0.005, "m": 0.002,
        "k": 0.002, "e": 0.005, "l": 0.002, "c": 0.02, "f": 0.05, "g": 0.05}
# Trails (e): how strongly copies of the clip, a moment behind it, trail it
# (0 none, 1 full - the preset's "echo": how many, how far apart, how fast
# they fade). Sweep (l): a band of light across the clip's own picture, 0
# before it and 1 past it (its "shine": how wide, its slant, how bright).
# Split (c): the red and blue pulled apart sideways, half-units each way.
# Flip (f, g): turned in depth about its upright and its level middle (or an
# edge: its "flip" pivot), degrees, with perspective.
# Tracking (k): added to a Text+'s own Character Spacing (1 is as the font
# sets it; each 0.5 more spreads the letters about 0.7 em further apart,
# measured on Studio 21.1). Other clips have no letters to spread.
# The reveal (m): a mask across the frame (the preset's "mask": its shape,
# the axis a wipe or split runs along, its soft edge). 0 shows it all; -1
# and 1 hide it - a wipe's edge off the frame's one side or the other, an
# iris or split closed. An In goes -1 to 0, an Out 0 to 1. A held mask
# stays on at 0 too, over the clip's picture as it rests, so what moves
# outside it is hidden - text rising from behind a line. A wipe or split may
# run at an angle; blinds are a split in slats.
MASK_SHAPES = ("wipe", "split", "iris", "blinds")
# A move that passes through rest for this many samples (Pop's overshoot)
# is still one move.
_GAP = 3


def load(path=DATA):
    """The presets, in the file's order: dicts with id, label, pack, kind,
    dur (seconds) and the channels x, y, r, s, o, b, w, h (41 floats each;
    b, w and h at rest when the file has none)."""
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)["presets"]
    out = []
    for p in raw:
        t = p["t"]
        channel = lambda values: [float(values[min(k, len(values) - 1)]) for k in range(SAMPLES)]
        preset = {
            "id": p["id"], "label": p["label"], "pack": p["pack"], "kind": p["kind"],
            "dur": float(p["dur"]),
            "x": channel([v[0] for v in t]), "y": channel([v[1] for v in t]),
            "r": channel(p["r"]), "s": channel(p["s"]), "o": channel(p["o"]),
            "b": channel(p.get("b") or [0.0]), "w": channel(p.get("w") or [1.0]),
            "m": channel(p.get("m") or [0.0]), "k": channel(p.get("k") or [0.0]),
            **{c: channel(p.get(c) or [0.0]) for c in "elcfg"},
            "h": channel(p.get("h") or [1.0]),
        }
        # The Text pack's: the Animate by it's made for, chosen with it.
        for extra in ("unit", "stagger", "heading", "shape") + EXTRAS:
            if extra in p:
                preset[extra] = p[extra]
        if p.get("keys"):
            # The keys the preset plays - every built-in one has them, baked
            # once from its samples (a key on every peak and valley, which 41
            # samples alone can't place) or drawn by hand where the samples
            # were too coarse for the move (Drop's bounces). A channel without
            # any is fitted to the samples here; the samples - the tiles, the
            # moves - are then worked out from the keys, as for a preset saved
            # from the Editor.
            # (A channel the file has no keys for and that never moves - blur,
            # width and height on most - is simply held at rest.)
            missing = [c for c in CHANNELS if c not in p["keys"]]
            fitted = (_fit_keys(preset) if any(not _still(preset[c], c) for c in missing)
                      else {c: [[0.0, _REST[c], None, None], [float(LAST), _REST[c], None, None]] for c in missing})
            keys = clean_keys({**fitted, **p["keys"]})
            preset["keys"] = keys
            for c in CHANNELS:
                preset[c] = [evaluate_keys(keys[c], k) for k in range(SAMPLES)]
        out.append(preset)
    return out


def _still(values, c):
    return all(abs(v - _REST[c]) <= _EPS[c] for v in values)


def moving(preset, k):
    return any(abs(preset[c][k] - _REST[c]) > _EPS[c] for c in _REST)


def segments(preset):
    """[(first, last)] sample ranges where the preset moves, a short pass
    through rest merged into the move around it. A range includes the rest
    sample it lands on (or starts from), so a move ends where it settles."""
    if preset.get("keys"):
        runs = _keyed_runs(preset["keys"])
    else:
        runs, start = [], None
        for k in range(SAMPLES):
            if moving(preset, k):
                if start is None:
                    start = max(0, k - 1) if k else 0
            elif start is not None:
                runs.append([start, k])
                start = None
        if start is not None:
            runs.append([start, LAST])
    merged = []
    for run in runs:
        if merged and run[0] - merged[-1][1] <= _GAP:
            merged[-1][1] = run[1]
        else:
            merged.append(run)
    return [tuple(r) for r in merged]


def _keyed_runs(keys):
    """segments' runs for a preset drawn as keys: between every two key
    times (any channel's), it rests if every channel holds at rest there;
    the rest is moves, from the key they start on to the key they land on,
    widened to whole samples. Not from the samples: a fitted curve may sit
    within its tolerance of rest (1.5 degrees) and still count as moving
    (0.05), which moved a move's end by a sample."""
    times = sorted({round(k[0], 6) for c in CHANNELS for k in keys[c]})
    runs = []
    for t0, t1 in zip(times, times[1:]):
        probes = [t0 + (t1 - t0) * f / 6 for f in range(7)]
        if any(abs(evaluate_keys(keys[c], t) - _REST[c]) > _EPS[c] for c in CHANNELS for t in probes):
            if runs and abs(runs[-1][1] - t0) < 1e-6:
                runs[-1][1] = t1
            else:
                runs.append([t0, t1])
    return [[int(a), min(LAST, -int(-b // 1))] for a, b in runs]


def moves(preset):
    """{"in": (a, b), "emphasis": (a, b), "out": (a, b)} - whichever the
    preset has. An In starts on the first sample, an Out ends on the last;
    anything else is an Emphasis (Hinge, an Out, only has "out"; an In
    preset made in the Editor only "in"). A Loop is all of it: {"loop":
    (0, LAST)}, if anything moves."""
    if preset["kind"] == KIND_LOOP:
        return {"loop": (0, LAST)} if segments(preset) else {}
    if preset["kind"] == KIND_WHOLE:
        return {"whole": (0, LAST)} if segments(preset) else {}
    found = {}
    for a, b in segments(preset):
        if a == 0 and "in" not in found and preset["kind"] in (KIND_IN_OUT, KIND_IN):
            found["in"] = (a, b)
        elif b == LAST and preset["kind"] in (KIND_IN_OUT, KIND_OUT):
            found["out"] = (a, b)
        elif preset["kind"] == KIND_EMPHASIS:
            # An emphasis preset's moves are one beat, however many runs it's in.
            lo, hi = found.get("emphasis", (a, b))
            found["emphasis"] = (min(lo, a), max(hi, b))
    return found


def view(preset):
    """What the Previews tab draws for a preset: its samples, and where its
    moves are along its length (0..1) for the timing bar."""
    return {
        "id": preset["id"], "label": preset["label"], "pack": preset["pack"],
        "kind": preset["kind"], "dur": preset["dur"],
        **{c: preset[c] for c in CHANNELS},
        "segs": [[a / LAST, b / LAST] for a, b in segments(preset)],
        "heading": heading(preset),         # which way it moves, for Direction (turned)
        "unit": preset.get("unit"), "stagger": preset.get("stagger"), "mask": mask_of(preset),
        "echo": echo_of(preset), "shine": shine_of(preset), "flip": flip_of(preset),
        "shape": preset.get("shape"),       # the tile's shape, when the move needs one (letters to spread)
        "keys": preset.get("keys"),         # a saved preset's own; built-ins remain on request in the Editor
        "previewKeys": keys_of(preset),     # same fitted curves that plan() writes to Fusion
    }


# ----------------------------------------------------------- direction --
#
# Direction turns a preset that moves across the frame to go another way:
# Whip from the right, Drop rising from below, Shake up and down. The
# preset's own way (heading) is read off its position: the way it travels
# as it comes in. Turned to face back the other way it's mirrored (its turns
# too: a roll in from the right rolls the other way); turned a quarter, the
# whole move is turned with it.

_WAYS = {"left": (-1, 0), "right": (1, 0), "up": (0, -1), "down": (0, 1)}
_HALF = {"x": 64.0, "y": 36.0}      # half-units across half the frame
_FAR = 0.004                        # a move under this share of the frame (8 px at 1080p) has no direction


def heading(preset):
    """The way a preset moves: "left", "right", "up" or "down" - how its
    first move that goes anywhere travels (an In: towards where it rests,
    an Out: away, an Emphasis or a Loop: to where it goes furthest, a
    Whole clip: from where it starts to where it ends) - or
    None when it stays put. motion_presets.json can say ("heading", "none"
    for a move with no way to it, like Handheld)."""
    if "heading" in preset:
        return preset["heading"] if preset["heading"] in _WAYS else None
    found = moves(preset)
    for name in ("in", "emphasis", "loop", "whole", "out"):
        if name not in found:
            continue
        a, b = found[name]
        # Half-units are as wide as they're tall, on a 16:9 frame. A wipe's
        # edge counts as moving along its axis, a whole frame for each 1.
        mask = mask_of(preset)
        along = mask["axis"] if mask and mask["shape"] == "wipe" else None
        at = lambda k: (preset["x"][k] + (preset["m"][k] * _HALF["x"] if along == "x" else 0.0),
                        preset["y"][k] + (preset["m"][k] * _HALF["y"] if along == "y" else 0.0))
        far = max((at(k) for k in range(a, b + 1)), key=lambda p: p[0] * p[0] + p[1] * p[1])
        if name == "whole":
            far = (at(b)[0] - at(a)[0], at(b)[1] - at(a)[1])
        if (far[0] / _HALF["x"]) ** 2 + (far[1] / _HALF["y"]) ** 2 < _FAR * _FAR:
            continue
        if name == "in":
            far = (-far[0], -far[1])        # comes from there, so travels the other way
        if abs(far[0]) >= abs(far[1]):
            return "right" if far[0] > 0 else "left"
        return "down" if far[1] > 0 else "up"
    return None


def turning(own, direction):
    """How turning a preset from `own` to `direction` moves its channels:
    ({"x": (from channel, sign), "y": ...}, whether its rotation reverses),
    or None when there's nothing to turn."""
    if own not in _WAYS or direction not in _WAYS or own == direction:
        return None
    (hx, hy), (dx, dy) = _WAYS[own], _WAYS[direction]
    if (hx, hy) == (-dx, -dy):
        # Back the other way: mirrored across the axis it moves along.
        return {"x": ("x", -1.0 if hx else 1.0), "y": ("y", -1.0 if hy else 1.0)}, True
    if (-hy, hx) == (dx, dy):
        return {"x": ("y", -1.0), "y": ("x", 1.0)}, False      # a quarter turn clockwise
    return {"x": ("y", 1.0), "y": ("x", -1.0)}, False          # ...or anticlockwise


def _across(values, src, dst):
    """How much a channel grows going from src to dst (x to y or back): a
    move that reaches half the frame or further keeps its share of the
    frame, so what came in from off screen still does; a smaller one keeps
    its size (half-units are the same size both ways on a 16:9 frame)."""
    if src == dst:
        return 1.0
    far = max((abs(v) for v in values), default=0.0) / _HALF[src]
    return _HALF[dst] / _HALF[src] if far >= 0.5 else 1.0


def turned(preset, direction):
    """The preset going `direction` (DIRECTIONS) - itself for "own", or for
    one that doesn't move across the frame. Its keys turned with it."""
    how = turning(heading(preset), direction)
    if how is None:
        return preset
    mix, reverse = how
    keys = keys_of(preset)
    out = {**preset, "heading": direction, "keys": dict(keys)}
    for dst, (src, sign) in mix.items():
        k = sign * _across(preset[src], src, dst)
        out[dst] = [v * k for v in preset[src]]
        out["keys"][dst] = [[t, v * k, h and [h[0], h[1] * k], g and [g[0], g[1] * k]]
                            for t, v, h, g in keys[src]]
    if reverse:
        out["r"] = [-v for v in preset["r"]]
        out["keys"]["r"] = [[t, -v, h and [h[0], -h[1]], g and [g[0], -g[1]]] for t, v, h, g in keys["r"]]
    elif mix["x"][0] == "y":
        # A quarter turn: a squash that was across the move is still across it.
        for a, b in (("w", "h"), ("h", "w")):
            out[a], out["keys"][a] = preset[b], keys[b]
    # Flips turn about the axes the move turns: about the upright for x.
    by_axis = {"x": "f", "y": "g"}
    for dst, (src, sign) in mix.items():
        a, b = by_axis[dst], by_axis[src]
        out[a] = [v * sign for v in preset[b]]
        out["keys"][a] = [[t, v * sign, h and [h[0], h[1] * sign], g and [g[0], g[1] * sign]] for t, v, h, g in keys[b]]
    mask = mask_of(preset)
    if mask and mask["shape"] != "iris":
        # A wipe or split turns with the move: onto the other axis, or
        # mirrored along its own.
        axis, sign = next((dst, sign) for dst, (src, sign) in mix.items() if src == mask["axis"])
        out["mask"] = {**mask, "axis": axis}
        out["m"] = [v * sign for v in preset["m"]]
        out["keys"]["m"] = [[t, v * sign, h and [h[0], h[1] * sign], g and [g[0], g[1] * sign]]
                            for t, v, h, g in keys["m"]]
    return out


# The settings a preset's channels may come with (motion_presets.json, or
# kept with a preset saved from the Editor).
EXTRAS = ("mask", "echo", "shine", "flip")
FLIP_PIVOTS = ("center", "top", "bottom", "left", "right")


def _setting(raw, key, default, lo, hi):
    try:
        return max(lo, min(hi, float(raw.get(key, default))))
    except (TypeError, ValueError, AttributeError):
        return default


def echo_of(preset):
    """The trails' settings - {"copies", "delay" (seconds apart), "fade"
    (each copy's share of the one before)} - or None without trails."""
    raw = preset.get("echo")
    if not isinstance(raw, dict) and not any(abs(v) > _EPS["e"] for v in preset.get("e") or [0.0]):
        return None
    raw = raw if isinstance(raw, dict) else {}
    return {"copies": int(_setting(raw, "copies", 3, 1, 6)), "delay": _setting(raw, "delay", 0.05, 0.01, 0.5),
            "fade": _setting(raw, "fade", 0.55, 0.05, 1.0)}


def shine_of(preset):
    """The sweep's settings - {"width" (a share of the picture's width),
    "angle" (degrees), "strength", "soft"} - or None without one."""
    raw = preset.get("shine")
    if not isinstance(raw, dict) and not any(abs(v) > _EPS["l"] for v in preset.get("l") or [0.0]):
        return None
    raw = raw if isinstance(raw, dict) else {}
    return {"width": _setting(raw, "width", 0.18, 0.02, 1.0), "angle": _setting(raw, "angle", 20, -60, 60),
            "strength": _setting(raw, "strength", 0.7, 0.05, 1.0), "soft": _setting(raw, "soft", 0.04, 0.0, 0.5)}


def flip_of(preset):
    """The flip's settings - {"pivot" (centre or an edge), "depth" (how far
    the eye is, in heights of the picture: nearer is stronger perspective)} -
    or None without one."""
    raw = preset.get("flip")
    moving = any(abs(v) > _EPS["f"] for c in "fg" for v in preset.get(c) or [0.0])
    if not isinstance(raw, dict) and not moving:
        return None
    raw = raw if isinstance(raw, dict) else {}
    pivot = raw.get("pivot") if raw.get("pivot") in FLIP_PIVOTS else "center"
    return {"pivot": pivot, "depth": _setting(raw, "depth", 3.0, 1.2, 20.0)}


def flip_corners(f, g, aspect, pivot="center", depth=3.0):
    """Where a picture's corners land turned f degrees about its upright and
    g about its level line (through `pivot`), seen in perspective from
    `depth` picture-heights away: {"TopLeft": (x, y), ...} as shares of the
    picture, y up - what a Fusion CornerPositioner takes."""
    import math
    a, b = math.radians(f), math.radians(g)
    px = {"left": -0.5, "right": 0.5}.get(pivot, 0.0) * aspect
    py = {"bottom": -0.5, "top": 0.5}.get(pivot, 0.0)
    out = {}
    for name, (cx, cy) in (("TopLeft", (0, 1)), ("TopRight", (1, 1)), ("BottomLeft", (0, 0)), ("BottomRight", (1, 0))):
        x, y, z = (cx - 0.5) * aspect - px, (cy - 0.5) - py, 0.0
        x, z = x * math.cos(a), x * math.sin(a)                       # about the upright
        y, z = y * math.cos(b) - z * math.sin(b), y * math.sin(b) + z * math.cos(b)    # about the level
        x, y = x + px, y + py
        k = depth / max(0.05, depth + z)
        out[name] = (0.5 + x * k / aspect, 0.5 + y * k)
    return out


def mask_of(preset):
    """The preset's mask, made whole - {"shape", "axis", "soft", "angle"
    (degrees, anticlockwise), "count" (blinds' slats), "hold"} - or None
    when it has none (a reveal channel drawn in the Editor without one is a
    wipe from the left)."""
    mask = preset.get("mask")
    if not isinstance(mask, dict):
        if "m" in preset and any(abs(v) > _EPS["m"] for v in preset["m"]):
            mask = {}
        else:
            return None
    shape = mask.get("shape") if mask.get("shape") in MASK_SHAPES else "wipe"
    axis = mask.get("axis") if mask.get("axis") in ("x", "y") else "x"
    try:
        soft = max(0.0, min(0.5, float(mask.get("soft", 0.04))))
    except (TypeError, ValueError):
        soft = 0.04
    out = {"shape": shape, "axis": axis, "soft": soft}
    angle = _setting(mask, "angle", 0.0, -89.0, 89.0)
    if angle:
        out["angle"] = angle
    if shape == "blinds":
        out["count"] = int(_setting(mask, "count", 6, 2, 12))
    if mask.get("hold"):
        out["hold"] = True
    return out


def _seconds(preset, a, b):
    return (b - a) / LAST * preset["dur"]


# How far a fitted curve may stray from the design's samples, per channel:
# about 1% of the frame, 2% of scale, 3% of opacity, a degree and a half.
# The aim is the same look with keys a person would set - a long ease, not
# every wobble of 41 samples - so it's loose on purpose.
TOLERANCE = {"Center.X": 0.01, "Center.Y": 0.01, "Size": 0.02, "Angle": 1.5, "Blend": 0.03, "Reveal": 0.01,
             "Tracking": 0.01, "Echo": 0.02, "Shine": 0.005, "Split": 0.05, "FlipY": 1.0, "FlipX": 1.0,
             "Blur": 0.1, "Width": 0.02, "Height": 0.02}
RELATIVE = 0.08                 # ...and at most this share of the move's own range

_CHANNELS = {
    "Center.X": ("x", lambda v: 0.5 + v / 64.0),
    "Center.Y": ("y", lambda v: 0.5 - v / 36.0),
    "Size": ("s", lambda v: max(0.0, v)),
    "Angle": ("r", lambda v: -v),
    "Blend": ("o", lambda v: min(1.0, max(0.0, v))),
    "Blur": ("b", lambda v: max(0.0, v)),           # half-units: motion_resolve makes them pixels
    "Width": ("w", lambda v: max(0.0, v)),
    "Height": ("h", lambda v: max(0.0, v)),
    "Reveal": ("m", lambda v: max(-1.0, min(1.0, v))),       # motion_resolve shapes the mask from it
    "Tracking": ("k", lambda v: v),                           # ...and adds it to a Text+'s spacing
    "Echo": ("e", lambda v: min(1.0, max(0.0, v))),
    "Shine": ("l", lambda v: v),
    "Split": ("c", lambda v: v),
    "FlipY": ("f", lambda v: v),
    "FlipX": ("g", lambda v: v),
}


def _bezier(v0, a, b, v1, u):
    w = 1 - u
    return w * w * w * v0 + 3 * w * w * u * a + 3 * w * u * u * b + u * u * u * v1


# Where a segment's two handles may sit in time, as fractions of it: the
# fit tries each pair. A Fusion (and a CSS cubic-bezier) ease is a curve in
# time as well as value - expo-out bunches its handles at the start - and a
# handle fixed at a third would need extra keys to follow one.
_TIMES = [i / 20 for i in range(1, 20)]


@functools.lru_cache(maxsize=None)
def _params(x1, x2, n):
    """u for each of n+1 evenly spaced times 0..1 on the time curve with
    handles at x1, x2 (bisection: that curve only ever rises)."""
    out = []
    for i in range(n + 1):
        t, lo, hi = i / n, 0.0, 1.0
        for _ in range(30):
            u = (lo + hi) / 2
            w = 1 - u
            if 3 * w * w * u * x1 + 3 * w * u * u * x2 + u * u * u < t:
                lo = u
            else:
                hi = u
        out.append((lo + hi) / 2)
    return tuple(out)


# A turn this sharp - both sides moving at over this share of the move's
# fastest step - is a hit (a bounce on the floor) and keeps its corner; any
# gentler peak or dip gets flat handles, like a key set by hand.
_SHARP = 0.25
_BULGE_STEPS = 24


def _turns(values, tolerance):
    """The ends of a run and the peaks and dips worth a key: a turn whose
    swing to the turns either side of it is under twice `tolerance` (Pop
    settling from 1.01 back to 1) is smoothed over instead."""
    n = len(values) - 1
    turns = [0] + [i for i in range(1, n) if (values[i] - values[i - 1]) * (values[i + 1] - values[i]) < 0] + [n]
    while True:
        small = [(min(abs(values[turns[k]] - values[turns[k - 1]]), abs(values[turns[k]] - values[turns[k + 1]])), k)
                 for k in range(1, len(turns) - 1)]
        small = [(swing, k) for swing, k in small if swing < 2 * tolerance]
        if not small:
            return turns
        turns.pop(min(small)[1])


def _tangents(values, turns):
    """(slope in, slope out) at every sample, per sample step - what a key
    there would get: flat on a peak or dip that's one of `turns`, a corner
    on a sharp hit, the same slope both sides anywhere else (no kink), a
    one-sided estimate at the ends."""
    n = len(values) - 1
    d = [values[i + 1] - values[i] for i in range(n)]
    fastest = max((abs(x) for x in d), default=0.0) or 1.0
    out = []
    for i in range(n + 1):
        if i == 0:
            slope = (-3 * values[0] + 4 * values[1] - values[2]) / 2 if n >= 2 else d[0]
            out.append((slope if slope * d[0] > 0 else 0.0,) * 2)
        elif i == n:
            slope = (3 * values[n] - 4 * values[n - 1] + values[n - 2]) / 2 if n >= 2 else d[-1]
            out.append((slope if slope * d[-1] > 0 else 0.0,) * 2)
        elif i in turns:
            sharp = min(abs(d[i - 1]), abs(d[i])) > _SHARP * fastest
            out.append((d[i - 1], d[i]) if sharp else (0.0, 0.0))
        elif d[i - 1] * d[i] == 0:
            out.append((0.0, 0.0))  # the edge of a flat stretch: level, like a peak
        else:
            slope = (values[i + 1] - values[i - 1]) / 2
            out.append((slope, slope))
    return out


def _segment(values, tangents, i, j, tolerance):
    """The best curve from key i to key j with their slopes fixed - only
    how long each handle is (its time) is free. (worst miss, where, x1, y1,
    x2, y2); a curve that bulges past the samples it spans counts as a
    miss, so none shoots out between two samples."""
    run = values[i:j + 1]
    n = j - i
    v0, v1 = run[0], run[-1]
    s0, s1 = tangents[i][1], tangents[j][0]
    lo, hi = min(run) - tolerance, max(run) + tolerance
    best = None
    for x1 in _TIMES:
        for x2 in _TIMES:
            y1 = v0 + s0 * x1 * n
            y2 = v1 - s1 * (1 - x2) * n
            worst, where = 0.0, None
            if n >= 2:
                us = _params(x1, x2, n)
                for k in range(1, n):
                    off = abs(_bezier(v0, y1, y2, v1, us[k]) - run[k])
                    if off > worst:
                        worst, where = off, k
            for step in range(1, _BULGE_STEPS):
                v = _bezier(v0, y1, y2, v1, step / _BULGE_STEPS)
                over = max(lo - v, v - hi, 0.0) + tolerance if (v < lo or v > hi) else 0.0
                if over > worst:
                    worst, where = over, None
            if best is None or worst < best[0]:
                best = (worst, where, x1, y1, x2, y2)
    return best


@functools.lru_cache(maxsize=None)
def _fit_samples(values, tolerance):
    """fit() on evenly spaced samples, in sample units - cached: a move's
    curve only stretches with the clip, and a Bezier stretches exactly."""
    cuts = _turns(values, tolerance)
    tangents = _tangents(values, set(cuts))
    pieces, todo = [], list(zip(cuts, cuts[1:]))
    while todo:
        i, j = todo.pop()
        worst, where, x1, y1, x2, y2 = _segment(values, tangents, i, j, tolerance)
        if worst > tolerance and j - i >= 2:
            k = i + where if where else (i + j) // 2
            todo += [(i, k), (k, j)]
            continue
        pieces.append((i, j, x1, y1, x2, y2))
    pieces.sort()
    keys = []
    for n, (i, j, x1, y1, x2, y2) in enumerate(pieces):
        if n == 0:
            keys.append([i, values[i], None, None])
        keys[-1][3] = (i + x1 * (j - i), y1)
        keys.append([j, values[j], (i + x2 * (j - i), y2), None])
    return tuple(tuple(k) for k in keys)


def fit(points, tolerance):
    """A few eased keys for a run of evenly spaced (frame, value) samples,
    set the way an animator would: a key where the run starts and ends and
    on every peak and dip (flat handles there, a corner only on a sharp
    hit), more where one curve between two keys can't come within
    `tolerance` of every sample. A key's two handles line up, and a curve
    never bulges past the samples it spans. Returns [(frame, value, left handle or None, right handle or
    None)] - handles as (frame, value), as a Fusion BezierSpline keys them."""
    t0, t1 = points[0][0], points[-1][0]
    step = (t1 - t0) / max(1, len(points) - 1)
    at = lambda i: t0 + i * step
    hand = lambda h: None if h is None else (at(h[0]), h[1])
    values = tuple(round(v, 6) for _t, v in points)
    # Never looser than a fraction of the move itself, or a small move
    # (Shake's ~9 px) would be smoothed away altogether.
    tolerance = min(tolerance, RELATIVE * (max(values) - min(values)) or tolerance)
    return [(at(i), v, hand(lh), hand(rh)) for i, v, lh, rh in _fit_samples(values, tolerance)]


def product(a, b, tolerance=TOLERANCE["Size"], most=2000):
    """Two planned channels ({frame: key}) multiplied - a unit's Size X is
    its scale times its width - as a few eased keys again (fit). Either may
    be None: then it's the other as it is."""
    if not a or not b:
        return a or b
    frames = sorted(set(a) | set(b))
    lo, hi = frames[0], frames[-1]
    n = max(8, min(most, int((hi - lo) * 2) + 1))
    points = [(lo + (hi - lo) * i / n, evaluate(a, lo + (hi - lo) * i / n) * evaluate(b, lo + (hi - lo) * i / n))
              for i in range(n + 1)]
    out = {}
    for t, v, lh, rh in fit(points, tolerance):
        hand = lambda h: None if h is None else (round(h[0], 3), round(h[1], 5))
        out[round(t, 3)] = {"value": round(v, 5), "lh": hand(lh), "rh": hand(rh)}
    return out


def evaluate(keys, frame):
    """A fitted channel's value at a frame (for tests and checks)."""
    frames = sorted(keys)
    if frame <= frames[0]:
        return keys[frames[0]]["value"]
    for f0, f1 in zip(frames, frames[1:]):
        if f0 <= frame <= f1:
            k0, k1 = keys[f0], keys[f1]
            (h0, a), (h1, b) = k0["rh"] or (f0, k0["value"]), k1["lh"] or (f1, k1["value"])
            x1, x2, t = (h0 - f0) / (f1 - f0), (h1 - f0) / (f1 - f0), (frame - f0) / (f1 - f0)
            lo, hi = 0.0, 1.0
            for _ in range(40):
                u = (lo + hi) / 2
                w = 1 - u
                if 3 * w * w * u * x1 + 3 * w * u * u * x2 + u * u * u < t:
                    lo = u
                else:
                    hi = u
            return _bezier(k0["value"], a, b, k1["value"], (lo + hi) / 2)
    return keys[frames[-1]]["value"]


# ------------------------------------------------------------ edited keys --
#
# The Editor tab edits a preset as keys rather than samples: per channel,
# [[t, value, left handle, right handle], ...] with t in samples (0..LAST)
# and each handle an absolute [t, value] point or None - the shape fit()
# gives and a Fusion BezierSpline takes. A preset saved from there carries
# them ("keys"); its samples are worked out from them, for the tiles and for
# finding its moves, and plan() lays the keys themselves on the clip, so
# what Fusion gets is exactly the curve that was drawn.

CHANNELS = ("x", "y", "r", "s", "o", "b", "w", "h", "m", "k", "e", "l", "c", "f", "g")
SAVED = {"id": "Saved", "credit": "Your presets"}
# fit()'s TOLERANCE in the preset's own units (x/64 and y/36 of the frame).
KEY_TOLERANCE = {"x": 0.64, "y": 0.36, "r": 1.5, "s": 0.02, "o": 0.03, "b": 0.1, "w": 0.02, "h": 0.02, "m": 0.01,
                 "k": 0.01, "e": 0.02, "l": 0.005, "c": 0.05, "f": 1.0, "g": 1.0}
MAX_KEYS = 64                   # per channel, from the Editor
DURATION = (0.2, 10.0)          # seconds a saved preset may last
_LIMIT = 10000.0                # no value or handle past this


# Built-in presets' fitted keys, by id: they never change, and view() gives
# every tile its curves - fitting all of them took 0.6 s each time the page
# sent its presets (opening it, and every save or delete).
_FITTED = {}


def keys_of(preset):
    """A preset's keys: its own (one saved from the Editor, or drawn by hand
    in motion_presets.json), else fitted to its samples - a few eased keys
    per channel, as plan() would lay them."""
    if preset.get("keys"):
        return preset["keys"]
    if preset["id"] not in _FITTED:
        _FITTED[preset["id"]] = _fit_keys(preset)
    return _FITTED[preset["id"]]


def _fit_keys(preset):
    """keys_of for a preset with samples only."""
    out = {}
    for c in CHANNELS:
        # Move by move, the way plan() fits them: fitted across the whole
        # run, a hold at rest came out a hair off it (Pop's 1.01) and the
        # preset never quite rested between its moves.
        keys = []
        for a, b in segments(preset):
            points = [(k, preset[c][k]) for k in range(a, b + 1)]
            if all(abs(v - _REST[c]) <= _EPS[c] for _t, v in points):
                fitted = [(a, preset[c][a], None, None), (b, preset[c][b], None, None)]
            else:
                fitted = fit(points, KEY_TOLERANCE[c])
            for t, v, lh, rh in fitted:
                row = [round(t, 4), round(v, 5), _hand(lh), _hand(rh)]
                if keys and abs(keys[-1][0] - row[0]) < 1e-6:
                    keys[-1][3] = row[3]
                else:
                    keys.append(row)
        if not keys or keys[0][0] > 0:
            keys.insert(0, [0.0, round(preset[c][0], 5), None, None])
        if keys[-1][0] < LAST:
            keys.append([float(LAST), round(preset[c][LAST], 5), None, None])
        out[c] = keys
    return out


def _hand(h):
    return None if h is None else [round(h[0], 4), round(h[1], 5)]


def _controls(k0, k1):
    """A segment's four points (t, value): its keys and their handles - a
    key without one on that side is its own handle."""
    return ((k0[0], k0[1]), tuple(k0[3]) if k0[3] else (k0[0], k0[1]),
            tuple(k1[2]) if k1[2] else (k1[0], k1[1]), (k1[0], k1[1]))


def _solve(p, t):
    """u along a segment where its time is t (bisection: time only rises)."""
    lo, hi = 0.0, 1.0
    for _ in range(40):
        u = (lo + hi) / 2
        if _bezier(p[0][0], p[1][0], p[2][0], p[3][0], u) < t:
            lo = u
        else:
            hi = u
    return (lo + hi) / 2


def evaluate_keys(keys, t):
    """A channel's value at sample time t."""
    if t <= keys[0][0]:
        return keys[0][1]
    for k0, k1 in zip(keys, keys[1:]):
        if t <= k1[0]:
            if k1[0] - k0[0] <= 1e-9:
                return k1[1]
            p = _controls(k0, k1)
            if p[0][1] == p[1][1] == p[2][1] == p[3][1]:
                return k0[1]        # flat: the same at every time (most channels, held at rest)
            return _bezier(p[0][1], p[1][1], p[2][1], p[3][1], _solve(p, t))
    return keys[-1][1]


def split_at(keys, t):
    """The keys with one more at time t, the curve's shape unchanged (de
    Casteljau: the segment's two halves, and the new key's handles from
    where they meet). The same keys when there's one there already."""
    out = [list(k) for k in keys]
    for i, (k0, k1) in enumerate(zip(out, out[1:])):
        if abs(k0[0] - t) < 1e-6 or abs(k1[0] - t) < 1e-6:
            return out
        if k0[0] < t < k1[0]:
            p = _controls(k0, k1)
            u = _solve(p, t)
            lerp = lambda a, b: (a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u)
            q0, q1, q2 = lerp(p[0], p[1]), lerp(p[1], p[2]), lerp(p[2], p[3])
            r0, r1 = lerp(q0, q1), lerp(q1, q2)
            s = lerp(r0, r1)
            k0[3] = list(q0) if k0[3] else None
            k1[2] = list(q2) if k1[2] else None
            out.insert(i + 1, [t, s[1], list(r0), list(r1)])
            return out
    return out


def cut(keys, a, b):
    """The keys from time a to b, with keys made at a and b if there aren't
    any - its first without a left handle, its last without a right one."""
    part = [k for k in split_at(split_at(keys, a), b) if a - 1e-6 <= k[0] <= b + 1e-6]
    if part:
        part[0] = [part[0][0], part[0][1], None, part[0][3]]
        part[-1] = [part[-1][0], part[-1][1], part[-1][2], None]
    return part


def _number(value, default=0.0):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return max(-_LIMIT, min(_LIMIT, value)) if value == value else default


def clean_keys(raw):
    """Keys from the Editor made safe: numbers, in time order, one per time,
    a key at 0 and at LAST, handles on their own side and within their
    neighbours; a channel missing or empty is held at rest."""
    out = {}
    for c in CHANNELS:
        rows = []
        for row in list((raw or {}).get(c) or [])[:MAX_KEYS]:
            if not isinstance(row, (list, tuple)) or len(row) < 2:
                continue
            hand = lambda h: [_number(h[0]), _number(h[1])] if isinstance(h, (list, tuple)) and len(h) >= 2 else None
            rows.append([max(0.0, min(float(LAST), _number(row[0]))), _number(row[1]),
                         hand(row[2] if len(row) > 2 else None), hand(row[3] if len(row) > 3 else None)])
        rows.sort(key=lambda r: r[0])
        keys = []
        for row in rows:
            if keys and abs(keys[-1][0] - row[0]) < 1e-6:
                keys[-1] = row
            else:
                keys.append(row)
        if not keys:
            keys = [[0.0, _REST[c], None, None], [float(LAST), _REST[c], None, None]]
        if keys[0][0] > 0:
            keys.insert(0, [0.0, keys[0][1], None, None])
        if keys[-1][0] < LAST:
            keys.append([float(LAST), keys[-1][1], None, None])
        for i, k in enumerate(keys):
            before = keys[i - 1][0] if i else k[0]
            after = keys[i + 1][0] if i + 1 < len(keys) else k[0]
            k[2] = [max(before, min(k[0], k[2][0])), k[2][1]] if k[2] and i else None
            k[3] = [max(k[0], min(after, k[3][0])), k[3][1]] if k[3] and i + 1 < len(keys) else None
        out[c] = keys
    return out


def from_keys(pid, label, kind, dur, keys, pack=SAVED["id"], mask=None, extras=None):
    """A preset from the Editor's keys (clean_keys), with the samples the
    tiles play and moves() reads worked out from them - and the mask its
    reveal shapes, if it was made from a preset with one."""
    keys = clean_keys(keys)
    preset = {"id": str(pid), "label": str(label or "Untitled")[:60], "pack": pack,
              "kind": kind if kind in KINDS else KIND_IN_OUT,
              "dur": max(DURATION[0], min(DURATION[1], _number(dur, 1.0))), "keys": keys}
    for c in CHANNELS:
        preset[c] = [evaluate_keys(keys[c], k) for k in range(SAMPLES)]
    extras = {**(extras or {}), **({"mask": mask} if mask is not None else {})}
    for name, made in (("mask", lambda r: mask_of({"mask": r})), ("echo", lambda r: echo_of({"echo": r})),
                       ("shine", lambda r: shine_of({"shine": r})), ("flip", lambda r: flip_of({"flip": r}))):
        if isinstance(extras.get(name), dict):
            preset[name] = made(extras[name])
    return preset


def stored(preset):
    """What's saved of a preset made in the Editor (and what from_keys takes)."""
    out = {"id": preset["id"], "label": preset["label"], "kind": preset["kind"],
           "dur": preset["dur"], "keys": preset["keys"]}
    for name in EXTRAS:
        if preset.get(name):
            out[name] = preset[name]
    return out


# A channel's change to Fusion's units without plan's clamps - for handles,
# which may overshoot where a key may not.
_LINEAR = {"Center.X": lambda v: 0.5 + v / 64.0, "Center.Y": lambda v: 0.5 - v / 36.0,
           "Size": lambda v: v, "Angle": lambda v: -v, "Blend": lambda v: v,
           "Blur": lambda v: v, "Width": lambda v: v, "Height": lambda v: v, "Reveal": lambda v: v,
           "Tracking": lambda v: v, "Echo": lambda v: v, "Shine": lambda v: v, "Split": lambda v: v,
           "FlipY": lambda v: v, "FlipX": lambda v: v}


def plan(preset, clip_frames, fps, way="both", speed=1.0, at=None, direction="own"):
    """Keyframes for one clip: {"Center.X": {frame: key}, "Center.Y", "Size",
    "Angle", "Blend"} in comp frames 0..clip_frames-1, only the channels
    that move; a key is {"value", "lh", "rh"}, its handles (frame, value) or
    None. A few eased keys per move (fit). Plus "moves": [(name, first
    frame, last frame, and the two exactly - where the keys are)].

    way        "both", "in" or "out" - which part of an In · Out preset.
    speed      how fast it plays (2 = half as long).
    at         an Emphasis's first frame in the clip (the playhead); None puts
               it in the middle. Ignored by the others.
    direction  which way it moves (DIRECTIONS, turned).

    A clip too short for the whole move gets it squeezed to fit; In and Out
    together share the clip in proportion. A Whole clip move is stretched
    from the first frame to the last, whatever the speed. A Loop goes on as many times as
    come nearest to filling the clip, each stretched or squeezed alike so the
    last ends on its last frame - one "loop" move each, a turn that doesn't
    come back to where it started (Turn's 360 degrees) carried on from one
    to the next. A one-frame clip has no room for a move at all: ValueError."""
    if int(clip_frames) < 2:
        raise ValueError("it's only one frame long - too short for a move")
    preset = turned(preset, direction)
    end = int(clip_frames) - 1
    rate = float(fps) / max(0.01, float(speed))
    found = moves(preset)
    kind = preset["kind"]
    wanted = []
    if kind == KIND_IN_OUT:
        if way in ("both", "in") and "in" in found:
            wanted.append("in")
        if way in ("both", "out") and "out" in found:
            wanted.append("out")
    elif kind == KIND_IN and "in" in found:
        wanted.append("in")
    elif kind == KIND_EMPHASIS and "emphasis" in found:
        wanted.append("emphasis")
    elif kind == KIND_OUT and "out" in found:
        wanted.append("out")

    lengths = {m: _seconds(preset, *found[m]) * rate for m in wanted}
    squeeze = min(1.0, end / sum(lengths.values())) if lengths and sum(lengths.values()) > end else 1.0

    placed = []                  # (name, a, b, first frame, frames long, cycle)
    for m in wanted:
        a, b = found[m]
        length = lengths[m] * squeeze
        if m == "in":
            first = 0.0
        elif m == "out":
            first = end - length
        else:
            first = (end - length) / 2 if at is None else min(max(0.0, float(at)), end - length)
        placed.append((m, a, b, first, length, 0))
    if kind == KIND_WHOLE and "whole" in found:
        placed.append(("whole", *found["whole"], 0.0, float(end), 0))      # its whole length, at any speed
    if kind == KIND_LOOP and "loop" in found:
        cycle = max(1e-6, _seconds(preset, *found["loop"]) * rate)
        times = max(1, round(end / cycle))
        if times > MAX_LOOPS:
            times = MAX_LOOPS
        else:
            cycle = end / times
        a, b = found["loop"]
        placed += [("loop", a, b, n * cycle, cycle, n) for n in range(times)]

    def rounded(h):
        return None if h is None else (round(h[0], 3), round(h[1], 5))

    keys = {}
    for name, (c, value) in _CHANNELS.items():
        used = [k for _m, a, b, _f, _l, _n in placed for k in range(a, b + 1)]
        held = name == "Reveal" and (mask_of(preset) or {}).get("hold")
        if not held and all(abs(preset[c][k] - _REST[c]) <= _EPS[c] for k in used):
            continue            # doesn't move in the parts being put on (a held mask stays on regardless)
        channel = {}
        for _m, a, b, first, length, n in placed:
            # A Loop's later passes carry on from where the one before ended.
            add = n * (preset[c][b] - preset[c][a])
            if preset.get("keys"):
                # Drawn in the Editor: its own keys, stretched onto the clip.
                frame = lambda t: first + (t - a) / max(1e-9, b - a) * length
                line = _LINEAR[name]
                hand = lambda h: None if h is None else (frame(h[0]), line(h[1] + add))
                part = [(round(frame(t), 3), {"value": round(value(v + add), 5), "lh": rounded(hand(lh)),
                                              "rh": rounded(hand(rh))})
                        for t, v, lh, rh in cut(preset["keys"][c], a, b)]
            else:
                points = [(first + (k - a) / max(1, b - a) * length, value(preset[c][k] + add))
                          for k in range(a, b + 1)]
                part = [(round(t, 3), {"value": round(v, 5), "lh": rounded(lh), "rh": rounded(rh)})
                        for t, v, lh, rh in fit(points, TOLERANCE[name])]
            for f, key in part:
                if f in channel:
                    # Where one pass ends and the next begins: one key, arriving
                    # as the first ends and leaving as the next starts.
                    key = {**key, "lh": channel[f]["lh"]}
                channel[f] = key
        keys[name] = dict(sorted(channel.items()))
    keys["moves"] = [(m, round(f), round(f + l), round(f, 3), round(f + l, 3)) for m, _a, _b, f, l, _n in placed]
    # The settings the channels come with, for motion_resolve (turned with the preset).
    if "Reveal" in keys:
        keys["mask"] = mask_of(preset)
    if "Echo" in keys:
        keys["echo"] = {**echo_of(preset), "frames": echo_of(preset)["delay"] * float(fps)}
    if "Shine" in keys:
        keys["shine"] = shine_of(preset)
    if "FlipY" in keys or "FlipX" in keys:
        keys["flip"] = flip_of(preset)
    return keys
