#!/usr/bin/env python3
"""
The cut plan (cutplan.py) applied to an OTIO export of the timeline: each
camera's clips razored at every switch, nothing moved, nothing dropped -
the document for a NEW timeline. No Qt, no Resolve: Project Setup's
otio_engine does the round trip (export, import), the same way.

    split(document, shots, cameras, start_frame, muted)
        -> (document, pieces, sound, warnings)

cameras: the video track numbers that are cameras; their clips are cut.
muted: those of them heard through linked mics (rig.py) - their own sound,
the audio clips of the same files on any audio track, is cut at the very
same points, so it stays in step with the picture, and every piece of it
is to be turned off. Every other track - B-roll, titles, the mics - is
left exactly as exported.

pieces: [(track, start, end, on)] in frames from the timeline's start: each
piece of a camera track, and whether it's that camera's turn on screen.
sound: [(audio track, start, end)] - the muted cameras' sound, every piece
to turn off. Resolve's own SetClipEnabled turns them off once it has the
timeline (resolve_ext.build), rather than OTIO's "enabled", which nothing
says its import keeps.

A razored piece is still the same media at the same source frames:
source_range.start_time moves on by however far into the clip the piece
starts (Resolve writes source_range at the timeline's rate). Resolve's
link group: a muted camera's picture piece and the sound piece of the same
stretch share a new one, so each pair still moves together; any other
razored piece has it taken off - one camera clip cut in twenty can't stay
linked to the one sound clip it came with.
"""

import copy

from pages.project_setup import otio_engine as oe

_META = "Resolve_OTIO"
_LINK = "Link Group ID"


def _link(clip):
    meta = clip.get("metadata", {}).get(_META)
    return meta.get(_LINK) if isinstance(meta, dict) else None


def _set_link(clip, value):
    meta = clip.get("metadata", {}).get(_META)
    if isinstance(meta, dict):
        if value is None:
            meta.pop(_LINK, None)
        else:
            meta[_LINK] = value


def _piece(clip, offset, length):
    out = copy.deepcopy(clip)
    source = out["source_range"]
    dur = source["duration"]
    start = source.get("start_time") or {"OTIO_SCHEMA": "RationalTime.1", "rate": dur["rate"], "value": 0.0}
    scale = (start.get("rate") or dur["rate"]) / dur["rate"]
    source["start_time"] = dict(start, value=start["value"] + offset * scale)
    source["duration"] = dict(dur, value=float(length))
    _set_link(out, None)
    return out


def _turn(shots, frame):
    """The camera on screen at `frame` (relative)."""
    for shot in shots:
        if shot[0] <= frame < shot[1]:
            return shot[2]
    return None


def _razor(track, cuts, wanted, on_piece):
    """Each clip of `track` that wanted(clip) is cut at `cuts` (frames from
    the timeline's start); on_piece(clip, original, lo, hi, whole) is told
    of every piece. -> how many transitions went."""
    children, position, dropped = [], 0.0, 0
    for child in track["children"]:
        if oe._is_transition(child):
            if wanted(None):
                dropped += 1
                continue
            children.append(child)
            continue
        length = oe._item_duration(child)
        if not oe._is_clip(child) or not wanted(child):
            children.append(child)
            position += length
            continue
        a, b = position, position + length
        bounds = [a, *[c for c in cuts if a < c < b], b]
        for lo, hi in zip(bounds, bounds[1:]):
            whole = len(bounds) == 2
            piece = child if whole else _piece(child, lo - a, hi - lo)
            children.append(piece)
            on_piece(piece, child, lo, hi, whole)
        position = b
    track["children"] = children
    return dropped


def split(document, shots, cameras, start_frame=0, muted=()):
    """shots: cutplan.Shot (absolute frames) - start_frame is the
    timeline's first frame, the export's 0."""
    oe.refuse_unsupported(document)
    cameras = set(int(c) for c in cameras)
    muted = set(int(c) for c in muted) & cameras
    rel = [(s.start - start_frame, s.end - start_frame, s.camera) for s in shots]
    cuts = sorted({b for s in rel for b in (s[0], s[1])})
    out = copy.deepcopy(document)
    tracks = out["tracks"]["children"]
    numeric = [v for t in tracks for c in t.get("children", []) if oe._is_clip(c)
               for v in [_link(c)] if isinstance(v, int)]
    fresh = {"next": max(numeric, default=0) + 1, "ids": {}}

    def new_link(original, lo, hi):
        key = (original, int(round(lo)), int(round(hi)))
        if key not in fresh["ids"]:
            fresh["ids"][key] = fresh["next"]
            fresh["next"] += 1
        return fresh["ids"][key]

    pieces, sound, warnings, dropped = [], [], [], 0
    muted_media = set()
    number = 0
    for track in tracks:
        if track.get("kind") != "Video":
            continue
        number += 1
        if number not in cameras:
            continue

        def on_piece(piece, original, lo, hi, whole, number=number):
            pieces.append((number, int(round(lo)), int(round(hi)), _turn(rel, lo) == number))
            if number in muted:
                muted_media.add(oe._media_path(original))
                if not whole and _link(original) is not None:
                    _set_link(piece, new_link(_link(original), lo, hi))
        dropped += _razor(track, cuts, lambda clip: True, on_piece)

    number = 0
    muted_media.discard("")
    for track in tracks:
        if track.get("kind") != "Audio":
            continue
        number += 1

        def on_sound(piece, original, lo, hi, whole, number=number):
            sound.append((number, int(round(lo)), int(round(hi))))
            if not whole and _link(original) is not None:
                key = (_link(original), int(round(lo)), int(round(hi)))
                _set_link(piece, fresh["ids"].get(key))      # its picture piece's - or none
        if muted_media:
            _razor(track, cuts, lambda clip: clip is not None and oe._media_path(clip) in muted_media, on_sound)
    if dropped:
        warnings.append(f"{dropped} transition(s) on the camera tracks were left out – every cut is a straight cut.")
    return out, pieces, sound, warnings
