#!/usr/bin/env python3
"""
Who's talking (talk.py) turned into which camera is on screen. Pure
Python and numpy: no Qt, no Resolve.

    options(raw)                                    -> the global options, made safe
    plan(rows, voice_cams, cams, talk, fps, span, opts, seed) -> [Shot(start, end, camera)]

rows: one True/False row per voice, every talk.block; voice_cams: each
voice's camera (a video track index, or None to never cut to them); cams:
the setup's cameras on this timeline (rig.py says what each role does), as
{"track", "role", ..., "of_track"} - an angle's `of` as a track.

  interjections  a voice's talk shorter than `ignore` s ("yeah", "mm-hm",
                 a laugh) doesn't move the camera
  one voice      its camera
  two at once    a wide with `crosstalk` on (the wides take turns), if
                 there is one - else whoever was on screen stays
  nobody         whoever was on screen stays; a silence longer than QUIET_S
                 goes to a wide with `quiet` on
  switch ahead   each cut comes `lead` s before the voice starts, as an
                 editor cuts - on the breath, not on the first word
  min shot       no shot shorter than `min_shot` s: a short one is folded
                 into the shot before it (the first, into the one after)

Then, in the speakers' shots, in this order: a speaker's second angles
take turns with it every `every` s; a listener's camera with `reaction` on
is cut to for `react_len` s each time someone else has talked `react_after`
s; a wide with `cutaway` set is cut to for `cut_len` s every `cutaway` s;
the random cutaway cameras come every `gap_min`-`gap_max` s (seeded, so the
plan only changes when something it depends on does); and a wide with
`ends` set holds the first and last `ends` s. Nothing put in leaves a shot
shorter than `min_shot`.
"""

from dataclasses import dataclass

import numpy as np

QUIET_S = 3.0

DEFAULTS = {"min_shot": 2.0, "lead": 0.2, "ignore": 1.0}
RANGES = {"min_shot": (0.5, 20.0), "lead": (0.0, 1.0), "ignore": (0.0, 5.0)}


@dataclass
class Shot:
    start: int       # timeline frame (absolute), inclusive
    end: int         # exclusive
    camera: int      # video track index

    def as_dict(self):
        return {"start": self.start, "end": self.end, "camera": self.camera}


def options(raw):
    """The global options, every one in range."""
    out = dict(DEFAULTS)
    raw = raw if isinstance(raw, dict) else {}
    for key, (lo, hi) in RANGES.items():
        try:
            value = float(raw.get(key, out[key]))
        except (TypeError, ValueError):
            continue
        if np.isfinite(value):
            out[key] = round(min(hi, max(lo, value)), 3)
    return out


def _runs(on):
    edges = np.diff(np.concatenate(([0], np.asarray(on, dtype=np.int8), [0])))
    return list(zip(np.flatnonzero(edges == 1).tolist(), np.flatnonzero(edges == -1).tolist()))


def _drop_short(row, shortest):
    out = np.array(row, dtype=bool)
    for a, b in _runs(out):
        if b - a < shortest:
            out[a:b] = False
    return out


def _choose(rows, voice_cams, talk_wides, quiet_wides, opts, block):
    """Each block's camera (a track index), or -1 for "whoever is on"."""
    n = rows.shape[1] if rows.ndim == 2 else 0
    shortest = int(round(opts["ignore"] / block))
    choice = np.full(n, -1, dtype=int)
    if not n:
        return choice
    cams = np.full((len(rows), n), -1, dtype=int)
    for v, row in enumerate(rows):
        camera = voice_cams[v] if v < len(voice_cams) else None
        if camera is None:
            continue
        cams[v] = np.where(_drop_short(row, shortest) if shortest > 1 else row, camera, -1)
    on = cams >= 0
    count = np.array([len({int(c) for c in cams[:, b] if c >= 0}) for b in range(n)]) if len(rows) else np.zeros(n)
    single = count == 1
    choice[single] = cams.max(axis=0)[single]
    if talk_wides:
        for turn, (a, b) in enumerate(_runs(count >= 2)):
            choice[a:b] = talk_wides[turn % len(talk_wides)]
    if quiet_wides:
        quiet = int(round(QUIET_S / block))
        turn = 0
        for a, b in _runs(~on.any(axis=0)):
            if b - a >= quiet:
                choice[a:b] = quiet_wides[turn % len(quiet_wides)]
                turn += 1
    return choice


def _segments(choice, fallback):
    """[[a, b, camera, "base"]] blocks: -1 (anyone) takes the camera before
    it - or, at the very start, the first one chosen."""
    n = len(choice)
    known = np.flatnonzero(choice >= 0)
    if not len(known):
        return [[0, n, fallback, "base"]] if n else []
    filled = choice.copy()
    filled[:known[0]] = choice[known[0]]
    last = np.maximum.accumulate(np.where(filled >= 0, np.arange(n), 0))
    filled = filled[last]
    edges = np.flatnonzero(np.diff(filled)) + 1
    bounds = [0, *edges.tolist(), n]
    return [[a, b, int(filled[a]), "base"] for a, b in zip(bounds, bounds[1:])]


def _join(segments):
    out = []
    for seg in segments:
        if out and out[-1][2] == seg[2]:
            out[-1][1] = seg[1]
        elif seg[1] > seg[0]:
            out.append(list(seg))
    return out


def _lead(segments, lead):
    """Every cut `lead` blocks earlier - never past the shot before's start."""
    if lead <= 0:
        return segments
    out = [list(s) for s in segments]
    for i in range(1, len(out)):
        cut = max(out[i - 1][0] + 1, out[i][0] - lead)
        out[i - 1][1] = cut
        out[i][0] = cut
    return out


def _min_shot(segments, shortest):
    """Shots under `shortest` blocks folded into their neighbour."""
    out = _join(segments)
    changed = True
    while changed and len(out) > 1:
        changed = False
        for i, seg in enumerate(out):
            if seg[1] - seg[0] >= shortest:
                continue
            if i > 0:
                out[i - 1][1] = seg[1]
            else:
                out[i + 1][0] = seg[0]
            del out[i]
            out = _join(out)
            changed = True
            break
    return out


def _fills(segments, open_to, place):
    """Puts shots into the segments `open_to(segment)` allows: place(a, b,
    camera) -> [(start, end, camera, kind)] inside [a, b) for one, in order -
    the rest of the segment stays as it was."""
    out = []
    for seg in segments:
        a, b, cam, kind = seg
        if not open_to(seg):
            out.append(list(seg))
            continue
        at = a
        for start, end, other, other_kind in place(a, b, cam):
            if start > at:
                out.append([at, start, cam, kind])
            out.append([start, end, other, other_kind])
            at = end
        if b > at:
            out.append([at, b, cam, kind])
    return _join(out)


def _angles(segments, angles_of, shortest):
    """A speaker's shots shared with its second angles, every `every`
    blocks: the speaker, an angle, the speaker, the next angle..."""
    def place(a, b, cam):
        group = angles_of[cam]
        every = max(shortest, min(e for _t, e in group))
        chunks = (b - a) // every          # the last one runs on to the shot's end
        return [(a + i * every, b if i == chunks - 1 else a + (i + 1) * every, group[(i // 2) % len(group)][0],
                 "angle") for i in range(1, chunks, 2)]
    return _fills(segments, lambda s: s[3] == "base" and s[2] in angles_of, place)


def _reactions(segments, listeners, shortest, speakers):
    """listeners [(track, after, length)] blocks: in a speaker's shot, once
    they've talked `after`, a listener's camera for `length` - in turn."""
    turn = [0]

    def place(a, b, cam):
        out, at = [], a
        others = [item for item in listeners if item[0] != cam]
        while others:
            track, after, length = others[turn[0] % len(others)]
            start, end = at + max(after, shortest), at + max(after, shortest) + max(length, shortest)
            if end + shortest > b:
                break
            out.append((start, end, track, "react"))
            at = end
            turn[0] += 1
        return out
    return _fills(segments, lambda s: bool(listeners) and s[3] in ("base", "angle") and s[2] in speakers, place)


def _wide_cutaways(segments, wides, shortest, open_cams):
    """wides [(track, every, length)] blocks: a long shot cut away to the
    wide for `length` every `every` - the wides in turn."""
    if not wides:
        return segments
    every = max(shortest, min(e for _t, e, _l in wides))
    turn = [0]

    def place(a, b, cam):
        out, at = [], a
        while True:
            track, _every, length = wides[turn[0] % len(wides)]
            length = max(length, shortest)
            if at + every + length + shortest > b:
                break
            out.append((at + every, at + every + length, track, "wide"))
            at += every + length
            turn[0] += 1
        return out
    return _fills(segments, lambda s: s[3] in ("base", "angle", "react") and s[2] in open_cams, place)


def _random_cutaways(segments, cutaways, shortest, rng, open_cams):
    """cutaways [(track, gap_min, gap_max, length, weight)] blocks: one of
    them (by weight) every gap_min-gap_max, for its length."""
    if not cutaways or not segments:
        return segments
    weights = np.array([c[4] for c in cutaways], dtype=float)
    weights /= weights.sum()
    state = {}

    def pick(after):
        cam = cutaways[int(rng.choice(len(cutaways), p=weights))]
        state["cam"], state["due"] = cam, after + int(round(rng.uniform(cam[1], cam[2])))
    pick(segments[0][0])

    def place(a, b, cam):
        out, at = [], a
        while True:
            track, _lo, _hi, length, _w = state["cam"]
            length = max(length, shortest)
            start = max(state["due"], at + shortest)
            if track == cam or start + length + shortest > b:
                break
            out.append((start, start + length, track, "cut"))
            at = start + length
            pick(at)
        return out
    return _fills(segments, lambda s: s[3] in ("base", "angle", "react") and s[2] in open_cams, place)


def _hold(segments, a, b, cam):
    """[a, b) given to `cam`, whatever was there."""
    out = []
    for seg in segments:
        lo, hi = seg[0], seg[1]
        if hi <= a or lo >= b:
            out.append(list(seg))
            continue
        if lo < a:
            out.append([lo, a, seg[2], seg[3]])
        if hi > b:
            out.append([b, hi, seg[2], seg[3]])
    out.append([a, b, cam, "wide"])
    return _join(sorted(out, key=lambda s: s[0]))


def _blocks(seconds, block):
    return int(round(seconds / block))


def plan(rows, voice_cams, cams, talk, fps, span, opts=None, seed=0):
    """The shots, covering span = (first frame, end frame) of the timeline
    (absolute frames, end exclusive): rows and talk as talk.py gives them,
    voice_cams each row's track (or None), cams the setup's cameras placed
    on this timeline (only those with a track)."""
    opts = options(opts)
    block = talk.block
    rows = np.asarray(rows, dtype=bool).reshape(len(rows), -1) if len(rows) else np.zeros((0, talk.length), bool)
    first_frame, end_frame = int(span[0]), int(span[1])
    if end_frame <= first_frame:
        return []
    cams = [c for c in cams if c.get("track") is not None]
    by_role = {role: [c for c in cams if c["role"] == role] for role in ("speaker", "angle", "wide", "cutaway")}
    wide_tracks = [c["track"] for c in by_role["wide"]]
    fallback = next(iter(wide_tracks), None)
    if fallback is None:
        fallback = next((c for c in voice_cams if c is not None), None)
    if fallback is None:
        fallback = next((c["track"] for c in cams), None)
    if fallback is None:
        return []
    choice = _choose(rows, voice_cams, [c["track"] for c in by_role["wide"] if c["crosstalk"]],
                     [c["track"] for c in by_role["wide"] if c["quiet"]], opts, block)
    segments = _segments(choice, fallback)
    segments = _lead(segments, _blocks(opts["lead"], block))
    shortest = max(1, _blocks(opts["min_shot"], block))
    segments = _min_shot(segments, shortest)

    speakers = {c for c in voice_cams if c is not None} | {c["track"] for c in by_role["speaker"]}
    speakers -= set(wide_tracks)
    angles_of = {}
    for cam in by_role["angle"]:
        if cam.get("of_track") is not None and cam["of_track"] != cam["track"]:
            angles_of.setdefault(cam["of_track"], []).append((cam["track"], _blocks(cam["every"], block)))
    segments = _angles(segments, angles_of, shortest)
    listeners = [(c["track"], _blocks(c["react_after"], block), _blocks(c["react_len"], block))
                 for c in by_role["speaker"] if c["reaction"]]
    segments = _reactions(segments, listeners, shortest, speakers)
    open_cams = speakers | {c["track"] for c in by_role["angle"]}
    segments = _wide_cutaways(segments, [(c["track"], _blocks(c["cutaway"], block), _blocks(c["cut_len"], block))
                                         for c in by_role["wide"] if c["cutaway"] > 0], shortest, open_cams)
    rng = np.random.default_rng(int(seed) & 0xFFFFFFFF)
    segments = _random_cutaways(segments, [(c["track"], _blocks(c["gap_min"], block), _blocks(c["gap_max"], block),
                                            _blocks(c["length"], block), c["weight"]) for c in by_role["cutaway"]],
                                shortest, rng, open_cams)
    if segments:
        ends = next((c for c in by_role["wide"] if c["ends"] > 0), None)
        if ends is not None:
            lo = int(np.floor(first_frame / fps / block)) - talk.first
            hi = int(np.ceil(end_frame / fps / block)) - talk.first
            hold = _blocks(ends["ends"], block)
            segments[0][0], segments[-1][1] = min(segments[0][0], lo), max(segments[-1][1], hi)
            if hi - lo > 2 * hold:
                segments = _hold(_hold(segments, lo, lo + hold, ends["track"]), hi - hold, hi, ends["track"])
        segments = _min_shot(segments, shortest)

    shots = []
    for a, b, cam, _kind in segments:
        start = int(round((talk.first + a) * block * fps))
        end = int(round((talk.first + b) * block * fps))
        shots.append([start, end, cam])
    if not shots:
        return [Shot(first_frame, end_frame, fallback)]
    # The first and last shots reach the span's edges; nothing falls outside it.
    shots[0][0] = min(shots[0][0], first_frame)
    shots[-1][1] = max(shots[-1][1], end_frame)
    out = []
    for start, end, cam in shots:
        start, end = max(start, first_frame), min(end, end_frame)
        if end > start:
            if out and out[-1].camera == cam:
                out[-1].end = end
            elif out:
                out[-1].end = start
                out.append(Shot(start, end, cam))
            else:
                out.append(Shot(start, end, cam))
    return out


def summary(shots, fps):
    """{camera: {"shots", "seconds"}} and the number of cuts."""
    per = {}
    for shot in shots:
        entry = per.setdefault(shot.camera, {"shots": 0, "seconds": 0.0})
        entry["shots"] += 1
        entry["seconds"] = round(entry["seconds"] + (shot.end - shot.start) / fps, 2)
    return {"cameras": per, "cuts": max(0, len(shots) - 1)}
