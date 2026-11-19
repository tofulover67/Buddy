#!/usr/bin/env python3
"""
Shorts' arithmetic. No Qt, no Resolve.

A clip here is (kind, track, start, end): "video" / "audio" / "subtitle",
the track's number, and frames from the timeline's start (end exclusive).
The clips selected on the timeline fall into sections - stretches where
something selected is always on screen or heard; a short is one or more
sections, moved up against each other from the timeline's start.
"""

import re

KINDS = ("video", "audio", "subtitle")
# The shapes a short is made in: label, width, height (None: the timeline's own).
FORMATS = {
    "vertical": ("Vertical 9:16", 1080, 1920),
    "portrait": ("Portrait 4:5", 1080, 1350),
    "square": ("Square 1:1", 1080, 1080),
    "same": ("Same as the timeline", None, None),
}
LIMIT_S = 180           # the longest a YouTube Short or an Instagram Reel can be
NAME_MAX = 80
FORBIDDEN = ':/\\?*"<>|'        # Resolve refuses a timeline name holding any of these


def clean(selection):
    """The selection as sorted, distinct (kind, track, start, end) - rows
    that aren't a clip (no track, no length) left out."""
    out = set()
    for row in selection or []:
        try:
            if isinstance(row, dict):
                kind, track, start, end = row["kind"], row["track"], row["start"], row["end"]
            else:
                kind, track, start, end = row[:4]
            clip = (str(kind).lower(), int(track), int(start), int(end))
        except (KeyError, TypeError, ValueError):
            continue
        if clip[0] in KINDS and clip[1] >= 1 and clip[3] > clip[2]:
            out.add(clip)
    return sorted(out, key=lambda c: (c[2], KINDS.index(c[0]), c[1], c[3]))


def sections(clips):
    """[{"start", "end", "clips"}] in time order: clips that overlap or touch
    are one section; a frame with nothing selected on it starts the next."""
    out = []
    for clip in sorted(clips, key=lambda c: (c[2], c[3])):
        if out and clip[2] <= out[-1]["end"]:
            out[-1]["end"] = max(out[-1]["end"], clip[3])
            out[-1]["clips"].append(clip)
        else:
            out.append({"start": clip[2], "end": clip[3], "clips": [clip]})
    return out


def length(parts):
    return sum(s["end"] - s["start"] for s in parts)


def plans(clips, separate=False):
    """The shorts to make, each a list of sections: one of them all, or one each."""
    parts = sections(clips)
    if not parts:
        return []
    return [[s] for s in parts] if separate else [parts]


def placed(parts):
    """[(section, frame it starts at in the short)] - closed up from 0."""
    out, at = [], 0
    for section in parts:
        out.append((section, at))
        at += section["end"] - section["start"]
    return out


def moved(parts):
    """The clips where a short has them: [(clip, clip in the short)]."""
    out = []
    for section, to in placed(parts):
        shift = to - section["start"]
        out += [(c, (c[0], c[1], c[2] + shift, c[3] + shift)) for c in section["clips"]]
    return out


def tracks(clips):
    """"V1 V2 A1" - the tracks the clips are on."""
    letters = {"video": "V", "audio": "A", "subtitle": "ST"}
    seen = sorted({(KINDS.index(c[0]), c[1]) for c in clips})
    return " ".join(f"{letters[KINDS[k]]}{n}" for k, n in seen)


def clean_name(text):
    """What was typed as a name Resolve will take ("" if nothing is left)."""
    name = "".join(" " if ch in FORBIDDEN or ord(ch) < 32 else ch for ch in str(text or ""))
    return " ".join(name.split())[:NAME_MAX].strip()


def default_name(timeline_name):
    """"<timeline> – Short" - and from a short itself, its podcast's: the next
    one made there is "... – Short 2", not "... – Short 1 – Short 1"."""
    base = re.sub(r"\s+–\s+Short(\s+\d+)?$", "", clean_name(timeline_name))
    return clean_name(f"{base or 'Timeline'} – Short")


def names(base, count, taken, numbered=True):
    """`count` timeline names nobody has: "<base> 1", "<base> 2"... from the
    first numbers free - or, for one short with a name typed for it
    (numbered False), that name itself, "<base> 2" if it's taken."""
    taken, out = set(taken or ()), []
    if count == 1 and not numbered and base not in taken:
        return [base]
    n = 1 if numbered else 2
    while len(out) < count:
        candidate = f"{base} {n}"
        if candidate not in taken:
            out.append(candidate)
            taken.add(candidate)
        n += 1
    return out
