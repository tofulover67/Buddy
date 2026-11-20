"""The parts of a Data Graphics graphic that can be timed, and how one arrives.

Every part - a counter's count and its arriving; a graph's title, its bars, each
bar's name and number, the scale, the lines across - has a start, a length (seconds
from the clip's first frame) and a timing curve (easing.py). The page's dock shows a
lane per part (lanes are built by each page's model: {"id", "label", "start",
"length", "easing", "curve", "kind", "kinds", "own"}), where its bar is dragged and
its curve reshaped.

A part that isn't growing or counting arrives: already there ("none"), or fading,
rising or popping in. states() is that arrival frame by frame - what the preview in
Buddy draws and what the comp's keys are made from. Pure arithmetic.
"""

import math

from . import easing

KINDS = ("none", "fade", "rise", "pop")
MIN_LENGTH, MAX_LENGTH = 0.05, 60.0
MAX_START = 600.0
ARRIVES_IN = 0.5            # an arrival just chosen takes this long
RISE = 0.6                  # rises from this far below its place, as a share of the letter size
POP = 0.6                   # pops from this share of its size
MIN_SCALE = 0.001           # never 0: a picture scaled to nothing can render the frame black
KEYS = ("kind", "start", "length", "easing", "curve")


def clean_timing(raw, kinds=None, kind="none"):
    """A part's own timing from whatever was saved or sent - {"start", "length",
    "easing", "curve"} and, for a part that arrives (kinds: the ways it may), "kind" -
    or None when there's none."""
    if not isinstance(raw, dict):
        return None
    out = {"start": round(easing.number(raw.get("start"), 0.0, 0.0, MAX_START), 4),
           "length": round(easing.number(raw.get("length"), ARRIVES_IN, MIN_LENGTH, MAX_LENGTH), 4),
           **easing.clean_curve(raw, {"easing": easing.DEFAULT})}
    if kinds:
        out["kind"] = raw.get("kind") if raw.get("kind") in kinds else kind
    return out


def changed(timing, key, value):
    """A part's timing with one thing changed in the dock (key: one of KEYS). A curve
    dragged is no longer one of the named ones."""
    out = {k: v for k, v in timing.items() if k in KEYS}
    out[key] = value
    if key == "curve":
        out["easing"] = easing.CUSTOM
    return out


def states(timing, fps, total):
    """[(alpha, dy, scale)] for each frame from the clip's first until the part has
    arrived (dy: letter sizes below its place) - or [] when it's simply there. Before
    its start it isn't there yet (alpha 0); after the last state it is at rest: (1, 0, 1)."""
    kind = timing.get("kind", "none")
    if kind == "none":
        return []
    last = min(max(1, int(total)) - 1, int(math.ceil((timing["start"] + timing["length"]) * fps - 1e-9)))
    out = []
    for f in range(last + 1):
        p = easing.ease(timing["curve"], (f / fps - timing["start"]) / timing["length"])
        dy = (1 - p) * RISE if kind == "rise" else 0.0
        scale = max(MIN_SCALE, POP + (1 - POP) * p) if kind == "pop" else 1.0
        out.append((round(easing.clamp(p), 6), round(dy, 6), round(scale, 6)))
    return out


def lane(part_id, label, timing, kinds=None, own=True, sub=None, plain=False):
    """A lane for the dock. label: what it's called - plain: a name the person typed
    (never translated); sub: "name" / "number" under a bar's own name."""
    return {"id": part_id, "label": label, "sub": sub, "plain": plain,
            "start": timing["start"], "length": timing["length"], "easing": timing["easing"], "curve": timing["curve"],
            "kind": timing.get("kind"), "kinds": list(kinds) if kinds else None, "own": own}
