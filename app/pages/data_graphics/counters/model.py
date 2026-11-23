"""A counter, as arithmetic: what was asked for made valid (clean), the number at any
moment (value_at) and the text on every frame (texts) - the one clock the preview in
Buddy and the keys in Resolve are both made from. No Qt, no Resolve."""

import hashlib
import json

from .. import figures, parts
from ..easing import DEFAULT as EASE, clean_curve, ease, named, number
from ..text_look import ALIGN, CAP, LOOK_DEFAULTS, clean_look

LIMIT = 1e12                # the largest number counted to, either way
MIN_SECONDS, MAX_SECONDS = 0.2, 120.0
MAX_HOLD = 60.0
DEFAULTS = {
    "from": 0.0, "to": 1000.0, "seconds": 3.0, "hold": 1.0, "easing": EASE, "curve": named(EASE),
    "decimals": 0, "separator": "comma", "prefix": "", "suffix": "", "form": "number",
    "size": 18.0,           # the letter size, % of the frame's height
    "x": 50.0, "y": 50.0,   # where it sits, % across and down (the middle of the digits' height)
    "align": "center",
    "count_start": 0.0,     # the count begins this many seconds into the clip
    # How it arrives: already there, or fading, rising or popping in - from in_start, for in_seconds.
    "in_kind": "none", "in_start": 0.0, "in_seconds": parts.ARRIVES_IN, "in_easing": EASE, "in_curve": named(EASE),
    **LOOK_DEFAULTS,
}
# Ways of writing it to start from: {name: the fields it sets}. Only how the number is
# written - never the numbers themselves, which way they run (From may be above To or
# below it: that's the person's choice), how long it runs or its timing.
PRESETS = {
    "Number": {"prefix": "", "suffix": "", "form": "number"},
    "Money": {"prefix": "$", "suffix": "", "form": "number"},
    "Percent": {"prefix": "", "suffix": "%", "form": "number"},
    "Timer": {"prefix": "", "suffix": "", "form": "clock"},
}


def with_preset(spec, name):
    """The counter written the preset's way, or None for a preset there isn't."""
    fields = PRESETS.get(name)
    if fields is None:
        return None
    return clean({**spec, **fields})


def clean(raw):
    """A whole, valid counter from whatever was saved or sent."""
    raw = raw if isinstance(raw, dict) else {}
    spec = {
        "from": number(raw.get("from"), DEFAULTS["from"], -LIMIT, LIMIT),
        "to": number(raw.get("to"), DEFAULTS["to"], -LIMIT, LIMIT),
        "seconds": number(raw.get("seconds"), DEFAULTS["seconds"], MIN_SECONDS, MAX_SECONDS),
        "hold": number(raw.get("hold"), DEFAULTS["hold"], 0.0, MAX_HOLD),
        "size": number(raw.get("size"), DEFAULTS["size"], 2.0, 80.0),
        "x": number(raw.get("x"), DEFAULTS["x"], 0.0, 100.0),
        "y": number(raw.get("y"), DEFAULTS["y"], 0.0, 100.0),
        "align": raw.get("align") if raw.get("align") in ALIGN else DEFAULTS["align"],
    }
    spec.update(clean_curve(raw, DEFAULTS))
    spec["count_start"] = round(number(raw.get("count_start"), 0.0, 0.0, parts.MAX_START), 4)
    arrives = parts.clean_timing({"kind": raw.get("in_kind"), "start": raw.get("in_start"),
                                  "length": raw.get("in_seconds", DEFAULTS["in_seconds"]),
                                  "easing": raw.get("in_easing"), "curve": raw.get("in_curve")}, parts.KINDS)
    spec.update({"in_kind": arrives["kind"], "in_start": arrives["start"], "in_seconds": arrives["length"],
                 "in_easing": arrives["easing"], "in_curve": arrives["curve"]})
    spec.update(figures.clean_format(raw, DEFAULTS))
    spec.update(clean_look(raw, DEFAULTS))
    return spec


def count_frames(spec, fps):
    """The frames the number runs for - its last one shows where it ends, exactly."""
    return max(2, int(round(spec["seconds"] * fps)))


def start_frame(spec, fps):
    """The frame the count begins on."""
    return int(round(spec["count_start"] * fps))


def arrival(spec):
    """How the counter arrives, as a part's timing (parts.py)."""
    return {"kind": spec["in_kind"], "start": spec["in_start"], "length": spec["in_seconds"],
            "easing": spec["in_easing"], "curve": spec["in_curve"]}


def frames(spec, fps):
    """The whole clip's frames: up to the count's last frame (or the arrival's, if that
    is later), then the hold on the last number."""
    done = start_frame(spec, fps) + count_frames(spec, fps)
    if spec["in_kind"] != "none":
        done = max(done, int(round((spec["in_start"] + spec["in_seconds"]) * fps)) + 1)
    return done + int(round(spec["hold"] * fps))


# The dock's lanes: the count itself (its bar is count_start and seconds, its curve the
# counter's own timing) and how the counter arrives.
FIELDS = {"count": {"start": "count_start", "length": "seconds", "easing": "easing", "curve": "curve"},
          "arrive": {"kind": "in_kind", "start": "in_start", "length": "in_seconds", "easing": "in_easing",
                     "curve": "in_curve"}}


def lanes(spec, frame=None):        # frame: as Graphs' lanes take it; a counter's timing doesn't depend on it
    count = {"start": spec["count_start"], "length": spec["seconds"], "easing": spec["easing"], "curve": spec["curve"]}
    return [parts.lane("count", "The count", count), parts.lane("arrive", "Arriving", arrival(spec), parts.KINDS)]


def with_part(spec, part, key, value, frame=None):
    """The counter with one thing about a part's timing changed in the dock, or None
    for a part or a key there isn't."""
    field = FIELDS.get(part, {}).get(key)
    if field is None:
        return None
    raw = {**spec, field: value}
    if key == "curve":
        raw[FIELDS[part]["easing"]] = "custom"          # dragged: no longer one of the named curves
    return clean(raw)


def value_at(spec, progress):
    """The number at progress 0..1 through the run."""
    return spec["from"] + (spec["to"] - spec["from"]) * ease(spec["curve"], progress)


def texts(spec, fps, total=None):
    """What the counter says on each frame of a clip of `total` frames (frames(spec,
    fps) if None): where it starts until the count begins, the run, then the last
    number held."""
    run, first = count_frames(spec, fps), start_frame(spec, fps)
    total = frames(spec, fps) if total is None else max(1, int(total))
    down = spec["to"] < spec["from"]
    return [figures.shown(value_at(spec, min(1.0, max(0, f - first) / (run - 1))), spec, down) for f in range(total)]


def widest(spec):
    """The longer of the first and the last text - what has to fit in the frame."""
    down = spec["to"] < spec["from"]
    return max((figures.shown(spec["from"], spec, down), figures.shown(spec["to"], spec, down)), key=len)


def place_of(spec, frame):
    """Where the text goes in a frame, px: {"size", "x", "baseline"} - y is the middle
    of the digits' height, so the baseline is half a digit below it."""
    fw, fh = frame
    size = spec["size"] / 100 * fh
    return {"size": size, "x": spec["x"] / 100 * fw, "baseline": spec["y"] / 100 * fh + CAP * size / 2}


def counter_id(spec, frame, fps):
    """A hash of exactly what was placed, so the same counter is known again."""
    raw = json.dumps({"spec": spec, "frame": list(frame), "fps": float(fps)}, sort_keys=True)
    return "bc-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
