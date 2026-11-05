#!/usr/bin/env python3
"""
Text Edit's "Build an edited timeline": the open timeline exported to OTIO
(the same round trip as Project Setup's, project_setup/otio_engine.py),
the struck stretches cut out of the dialogue and picture tracks offline,
and the result imported as a NEW timeline. Nothing in the original changes.

cut_document(document, cuts, tracks) is pure: `cuts` are [first, end)
frames from the timeline's start; every clip and gap on the cut tracks
loses what falls inside them and what's after slides left - a clip across a
cut becomes two, the second starting later in its source. Tracks not
chosen (music, B-roll) are copied as they are - they'd be cut mid-note or
mid-shot, so they're left for the person to lay again.

A split clip's pieces are relinked piece by piece: Resolve links a picture
and its sound by the "Link Group ID" it writes, and the halves of one clip
on either side of a cut must not stay one link.
"""

import copy

from pages.project_setup import otio_engine as oe

SUFFIX = "(Text Edit)"


def _cut_spans(length, cuts):
    """The kept pieces of [0, length) for cuts relative to its start:
    [(kept first, kept end, removed before it)]."""
    pieces, removed, cursor = [], 0, 0
    for first, end in cuts:
        if end <= 0 or first >= length:
            continue
        first, end = max(first, 0), min(end, length)
        if first > cursor:
            pieces.append((cursor, first))
        cursor = max(cursor, end)
    if cursor < length:
        pieces.append((cursor, length))
    return pieces


def _scale(value, rate_from, rate_to):
    return value if rate_from == rate_to or not rate_from else value * rate_to / rate_from


def _piece_of(child, first, end, rate):
    """child limited to [first, end) of its own length (frames at `rate`)."""
    piece = copy.deepcopy(child)
    source = piece["source_range"]
    dur = source["duration"]
    start = source["start_time"]
    start["value"] = start["value"] + _scale(first, rate, start.get("rate") or rate)
    dur["value"] = _scale(end - first, rate, dur.get("rate") or rate)
    if oe._is_clip(piece):
        lo = start["value"]
        hi = lo + _scale(end - first, rate, start.get("rate") or rate)
        kept = []
        for marker in piece.get("markers") or []:
            try:
                at = marker["marked_range"]["start_time"]["value"]
            except (KeyError, TypeError):
                continue
            if lo <= at < hi:
                kept.append(marker)
        piece["markers"] = kept
    return piece


def cut_document(document, cuts, tracks, fps):
    """(new document, warnings). tracks: {"Audio": {numbers}, "Video": {numbers}}
    - the ones to cut. fps: the timeline's rate, which `cuts` are in."""
    oe_cuts = sorted((int(a), int(b)) for a, b in cuts if b > a)
    result = copy.deepcopy(document)
    warnings = []
    numbering = {}
    relink, next_link = {}, [900000]
    dropped_transitions = 0
    for track in result["tracks"]["children"]:
        kind = track.get("kind")
        numbering[kind] = numbering.get(kind, 0) + 1
        if numbering[kind] not in tracks.get(kind, ()):
            continue
        children, position = [], 0.0
        for child in track["children"]:
            if oe._is_transition(child):
                dropped_transitions += 1
                continue
            if not (oe._is_clip(child) or oe._is_gap(child)):
                raise oe.OtioError(
                    f'"{child.get("name") or "A compound clip"}" on a cut track can\'t be cut here – '
                    "decompose it in place (right-click > Decompose in Place) and build again. "
                    "Nothing was changed.")
            rate = oe._rate(child) or fps
            length = oe._duration(child)
            at = _scale(position, fps, rate)             # this child's start, in its own frames
            local = [(_scale(a, fps, rate) - at, _scale(b, fps, rate) - at) for a, b in oe_cuts]
            pieces = _cut_spans(length, local)
            link = (child.get("metadata", {}).get(oe._RESOLVE_META, {}).get(oe._LINK_KEY))
            for first, end in pieces:
                if end - first < 1e-6:
                    continue
                piece = _piece_of(child, first, end, rate)
                if link is not None and len(pieces) > 1:
                    # Its new place on the timeline names the piece: the
                    # video and audio halves of one clip land together.
                    where = round(_scale(at + first, rate, fps) - sum(
                        min(b, at + first) - a for a, b in oe_cuts if a < at + first))
                    new = relink.setdefault((link, where), None)
                    if new is None:
                        next_link[0] += 1
                        new = relink[(link, where)] = next_link[0]
                    piece["metadata"][oe._RESOLVE_META][oe._LINK_KEY] = new
                children.append(piece)
            position += _scale(length, rate, fps)
        # Neighbouring gaps (a cut between two) become one.
        merged = []
        for child in children:
            if merged and oe._is_gap(child) and oe._is_gap(merged[-1]):
                prev = merged[-1]
                prev["source_range"]["duration"]["value"] += _scale(
                    oe._duration(child), oe._rate(child), oe._rate(prev))
            else:
                merged.append(child)
        while merged and oe._is_gap(merged[-1]):
            merged.pop()
        track["children"] = merged
    if dropped_transitions:
        warnings.append(f"{dropped_transitions} transition(s) on the cut tracks aren't carried into the new "
                        "timeline – add them again where you want them.")
    _shift_markers(result, oe_cuts, fps)
    return result, warnings


def _shift_markers(document, cuts, fps):
    """The timeline's own markers moved with the cuts (one inside a cut goes
    to where the cut is)."""
    stack = document.get("tracks") or {}
    for marker in stack.get("markers") or []:
        try:
            start = marker["marked_range"]["start_time"]
        except (KeyError, TypeError):
            continue
        rate = start.get("rate") or fps
        at = _scale(start["value"], rate, fps)
        removed = sum(min(b, at) - a for a, b in cuts if a < at)
        start["value"] = _scale(at - removed, fps, rate)


def build_name(name):
    """The new timeline's name: "<name> (Text Edit)", never "(Text Edit) (Text Edit)"."""
    base = str(name or "Timeline").strip()
    if base.endswith(SUFFIX):
        base = base[: -len(SUFFIX)].rstrip()
    return base
