#!/usr/bin/env python3
"""
The cut plan (cutplan.py) applied to an OTIO export of the timeline: each
camera's clips razored at every switch, nothing moved, nothing dropped -
the document for a NEW timeline. No Qt, no Resolve: Project Setup's
otio_engine does the round trip (export, import), the same way.

    split(document, shots, cameras, start_frame, muted, paired)
        -> (document, pieces, sound, warnings)

cameras: the video track numbers that are cameras; their clips are cut -
and so is the sound: every audio clip on every audio track is cut at the
very same points, since a synced timeline's sound is in step with its
picture and has to stay so. What happens to each piece of sound:

    a camera's own   (the same file as a clip on a camera track) linked to
                     that camera's picture piece of the same stretch; for a
                     camera in `muted` - one heard through paired audio
                     (rig.py) - every piece is to be turned off as well.
    paired audio     paired: {camera track: [audio tracks]} - the audio an
                     angle is heard through (a recorder's or a wireless
                     mic's track): linked to its camera's picture piece of
                     the same stretch. It stays ON - a person is heard
                     whether or not they're on screen.
    any other        cut, and left on its own.

Left exactly as exported: the video tracks that aren't cameras (B-roll,
titles) and any audio clip linked to a clip on one of them - B-roll's own
sound stays whole with its picture.

pieces: [(track, start, end, on)] in frames from the timeline's start: each
piece of a camera track, and whether it's that camera's turn on screen.
sound: [(audio track, start, end)] - the muted cameras' sound, every piece
to turn off. Resolve's own SetClipEnabled turns them off once it has the
timeline (resolve_ext.build), rather than OTIO's "enabled", which nothing
says its import keeps.

A razored piece is still the same media at the same source frames:
source_range.start_time moves on by however far into the clip the piece
starts (Resolve writes source_range at the timeline's rate). Resolve's
link group: a camera's picture piece, the piece of its own sound and the
piece of its paired audio of the same stretch share one, so they still
move, select and ripple together (Text Edit's cuts, Shorts' selections);
any other razored piece has it taken off - one clip cut in twenty can't
stay linked to the one clip it came with. An audio track paired with
several cameras (people sharing a mic) is linked, stretch by stretch, to
the one of them on screen - else to the first.
"""

import bisect
import copy

from pages.project_setup import otio_engine as oe

_META = "Resolve_OTIO"
_LINK = "Link Group ID"


def _link(clip):
    meta = clip.get("metadata", {}).get(_META)
    return meta.get(_LINK) if isinstance(meta, dict) else None


def _set_link(clip, value):
    meta = clip.get("metadata", {}).get(_META)
    if value is not None and not isinstance(meta, dict):      # a clip Resolve gave no metadata of its own
        if not isinstance(clip.get("metadata"), dict):
            clip["metadata"] = {}
        meta = clip["metadata"][_META] = {}
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


def split(document, shots, cameras, start_frame=0, muted=(), paired=None):
    """shots: cutplan.Shot (absolute frames) - start_frame is the
    timeline's first frame, the export's 0."""
    oe.refuse_unsupported(document)
    cameras = set(int(c) for c in cameras)
    muted = set(int(c) for c in muted) & cameras
    heard = {}                                   # audio track -> the cameras it's paired with
    for camera, audio in (paired or {}).items():
        if int(camera) in cameras:
            for index in audio:
                heard.setdefault(int(index), []).append(int(camera))
    rel = [(s.start - start_frame, s.end - start_frame, s.camera) for s in shots]
    cuts = sorted({b for s in rel for b in (s[0], s[1])})
    out = copy.deepcopy(document)
    tracks = out["tracks"]["children"]
    numeric = [v for t in tracks for c in t.get("children", []) if oe._is_clip(c)
               for v in [_link(c)] if isinstance(v, int)]
    fresh = {"next": max(numeric, default=0) + 1, "ids": {}, "stretch": {}}

    def new_link(key):
        if key not in fresh["ids"]:
            fresh["ids"][key] = fresh["next"]
            fresh["next"] += 1
        return fresh["ids"][key]

    def stretch(lo):
        """Which stretch between two switches a piece starting at `lo` is in."""
        return bisect.bisect_right(cuts, lo)

    pieces, sound, warnings, dropped = [], [], [], 0
    camera_of = {}                               # a camera's file -> its track
    elsewhere = set()                            # the links of clips on the other video tracks
    number = 0
    for track in tracks:
        if track.get("kind") != "Video":
            continue
        number += 1
        if number not in cameras:
            elsewhere.update(_link(c) for c in track.get("children", []) if oe._is_clip(c))
            continue

        def on_piece(piece, original, lo, hi, whole, number=number):
            pieces.append((number, int(round(lo)), int(round(hi)), _turn(rel, lo) == number))
            camera_of.setdefault(oe._media_path(original), number)
            had = _link(original)
            if whole and had is not None:
                link = had                                   # uncut: still with the sound it came with
            elif had is not None:
                link = new_link((had, int(round(lo)), int(round(hi))))
            else:
                link = new_link(("camera", number, int(round(lo)), int(round(hi))))
            fresh["stretch"].setdefault((number, stretch(lo)), link)
            if not whole or had is None:
                _set_link(piece, link)
        dropped += _razor(track, cuts, lambda clip: True, on_piece)

    number = 0
    camera_of.pop("", None)
    elsewhere.discard(None)
    for track in tracks:
        if track.get("kind") != "Audio":
            continue
        number += 1
        cams = heard.get(number, [])

        def on_sound(piece, original, lo, hi, whole, number=number, cams=cams):
            at = stretch(lo)
            own = camera_of.get(oe._media_path(original))
            if own is not None:                              # a camera's own sound: with its picture piece
                if own in muted:
                    sound.append((number, int(round(lo)), int(round(hi))))
                if whole and _link(original) is not None:
                    return
                key = (_link(original), int(round(lo)), int(round(hi)))
                _set_link(piece, fresh["ids"].get(key, fresh["stretch"].get((own, at))))
                return
            on_screen = _turn(rel, lo)
            order = ([on_screen] if on_screen in cams else []) + cams
            link = next((fresh["stretch"][(c, at)] for c in order if (c, at) in fresh["stretch"]), None)
            if link is not None or not whole:
                _set_link(piece, link)                       # its camera's picture piece's - or none
        _razor(track, cuts, lambda clip: clip is not None and _link(clip) not in elsewhere, on_sound)
    if dropped:
        warnings.append(f"{dropped} transition(s) on the camera tracks were left out – every cut is a straight cut.")
    return out, pieces, sound, warnings
