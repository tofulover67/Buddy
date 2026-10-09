#!/usr/bin/env python3
"""
Automixer: several mics recording one conversation (a podcast, an
interview, a panel), each turned down while its person isn't talking - so
what the other mics pick up of whoever is (bleed: the same voice again, a
moment later, sounding far off) and each mic's own room noise go, and only
the mic of whoever's talking is up. A mixer rides the faders for this, or a
hardware automixer shares the gain out as it goes; Buddy listens to the
whole recording first, so a mic opens a moment *before* its person starts
and the first word is never clipped - which no live gate can do.

Like ducking.py, it's the Curve panel's keys, worked out from the page's
copy of the timeline and Buddy's own measurement of the files (peaks.py's
loudness, one value per LOUD_BLOCK_S). No Qt, no Resolve.

    plan(mics, audio_of, fps, depth, hold) -> {"lines", "tracks", "missing"}
    keys(clip, line, fps)                    -> a clip's keys as heard

Who's talking, block by block:
  level    each mic's loudness as heard, smoothed over 300 ms, against its
           own (BS.1770 gated, as Match measures it): a quiet talker's mic
           counts as much as a loud one's.
  talking  a mic is talking where it's within MARGIN_DB of the mic that's
           most so, and above its own speech floor (SENSITIVITY_LU under its
           loudness). A voice on its own mic is close and loud; the same
           voice on the others is 10-20 dB down - not within the margin.
           Two people talking over each other are both up.
  pauses   a mic stays up through its person's pauses up to `hold` s; a
           sound shorter than MIN_TALK_S (a click, a breath) doesn't open it.
  nobody   while no one talks, whoever talked last stays up: the room sounds
           the same through a pause, rather than every mic dipping at once.
Each mic then opens over OPEN_S, ending PREROLL_S before its talk starts,
and closes over CLOSE_S after it ends.
"""

import numpy as np

from .ducking import FLOOR_LUFS, SENSITIVITY_LU, _heard_blocks, gain_keys
from .levels import gate

DEFAULTS = {"depth": -15.0, "hold": 0.8}
DEPTH_RANGE = (-40.0, -3.0)
HOLD_RANGE = (0.0, 5.0)
MARGIN_DB = 6.0
SMOOTH_BLOCKS = 3
MIN_TALK_S = 0.25
OPEN_S = 0.1
PREROLL_S = 0.1
CLOSE_S = 0.4


def settings(raw):
    """The view's automix settings, made safe."""
    out = dict(DEFAULTS)
    for key, (lo, hi) in (("depth", DEPTH_RANGE), ("hold", HOLD_RANGE)):
        try:
            value = float((raw or {}).get(key, out[key]))
        except (TypeError, ValueError):
            continue
        if np.isfinite(value):
            out[key] = min(hi, max(lo, value))
    return out


def _runs(on):
    """[(start, end)] of the True runs in a bool array, end exclusive."""
    edges = np.diff(np.concatenate(([0], on.astype(np.int8), [0])))
    return list(zip(np.flatnonzero(edges == 1).tolist(), np.flatnonzero(edges == -1).tolist()))


def _clean(on, hold, shortest):
    """Pauses of `hold` blocks or fewer filled, then runs under `shortest` dropped."""
    out = on.copy()
    runs = _runs(on)
    for (_a, b), (c, _d) in zip(runs, runs[1:]):
        if c - b <= hold:
            out[b:c] = True
    for a, b in _runs(out):
        if b - a < shortest:
            out[a:b] = False
    return out


def plan(mics, audio_of, fps, depth=DEFAULTS["depth"], hold=DEFAULTS["hold"]):
    """mics: [{"index", "clips"}] - the tracks, one mic each. Returns
    {"lines": {track index: (frames, gains)} - each mic's gain line, for
    keys(); "tracks": [{"index", "talk" (the share of the time its person
    talks), "up" (the share it's up)}]; "together": the share of the time
    two or more talk at once; "missing": clip ids whose audio isn't read
    yet}. No lines when there's no one talking."""
    heard, missing, block = {}, [], None
    for track in mics:
        got = []
        for clip in track["clips"]:
            if clip.get("transition") or not clip.get("enabled", True):
                continue
            audio = audio_of(clip)
            if audio is None:
                missing.append(clip["id"])
                continue
            block = audio["block"]
            at, energy = _heard_blocks(clip, audio, fps)
            if len(energy):
                got.append((at, energy))
        heard[track["index"]] = got
    empty = {"lines": {}, "tracks": [{"index": t["index"], "talk": 0.0, "up": 0.0} for t in mics],
             "together": 0.0, "missing": missing}
    parts = [p for got in heard.values() for p in got]
    if not parts:
        return empty
    first = min(at for at, _e in parts)
    n = max(at + len(e) for at, e in parts) - first
    order = [t["index"] for t in mics]
    energy = np.zeros((len(order), n))
    for row, index in enumerate(order):
        for at, e in heard[index]:
            energy[row, at - first:at - first + len(e)] += e
    with np.errstate(divide="ignore"):
        level = -0.691 + 10 * np.log10(np.maximum(
            np.array([np.convolve(e, np.ones(SMOOTH_BLOCKS) / SMOOTH_BLOCKS, mode="same") for e in energy]), 1e-20))
    own = []
    for e in energy:
        four = np.convolve(e, np.ones(4) / 4, mode="valid") if len(e) >= 4 else e
        own.append(gate(four) if len(four) else None)
    own = np.array([np.nan if v is None else v for v in own])
    floor = np.maximum(FLOOR_LUFS, own - SENSITIVITY_LU)
    above = level > np.where(np.isnan(floor), np.inf, floor)[:, None]
    against = np.where(above, level - np.nan_to_num(own)[:, None], -np.inf)
    best = against.max(axis=0)
    talk = above & (against >= best - MARGIN_DB)
    hold_blocks = int(round(hold / block))
    shortest = max(1, int(round(MIN_TALK_S / block)))
    talk = np.array([_clean(row, hold_blocks, shortest) for row in talk]).reshape(len(order), n)
    anyone = talk.any(axis=0)
    if not anyone.any():
        return empty
    # Nobody talking: whoever talked last stays up (before anyone has, the first to).
    last = np.maximum.accumulate(np.where(anyone, np.arange(n), -1))
    last[last < 0] = int(np.argmax(anyone))
    up = talk[:, last]

    # In whole frames: the keys are put on frames, and one between them
    # would take its level from partway up a ramp.
    to_frame = lambda b: int(round((first + b) * block * fps))
    ends = [c for t in mics for c in t["clips"] if not c.get("transition")]
    lo = min(c["start"] for c in ends) - 1
    hi = max(c["end"] for c in ends) + 1
    pre = int(round(PREROLL_S * fps))
    opening, closing = max(1, int(round(OPEN_S * fps))), max(1, int(round(CLOSE_S * fps)))
    lines = {}
    for row, index in enumerate(order):
        runs = [[to_frame(a) - pre, to_frame(b)] for a, b in _runs(up[row])]
        joined = []
        for a, b in runs:                      # ramps that would meet: up all through
            if joined and a - opening <= joined[-1][1] + closing:
                joined[-1][1] = b
            else:
                joined.append([a, b])
        frames, gains = [min(lo, joined[0][0] - opening - 1) if joined else lo], [depth]
        for a, b in joined:
            frames += [a - opening, a, b, b + closing]
            gains += [depth, 0.0, 0.0, depth]
        frames.append(max(hi, frames[-1] + 1))
        gains.append(depth)
        lines[index] = (frames, gains)
    together = float(np.mean(talk.sum(axis=0) >= 2))
    return {"lines": lines, "together": round(together, 4), "missing": missing,
            "tracks": [{"index": index, "talk": round(float(talk[row].mean()), 4),
                        "up": round(float(up[row].mean()), 4)} for row, index in enumerate(order)]}


def keys(clip, line, fps):
    """A clip's keys as heard under its mic's gain line (plan()'s "lines"
    entry) - [] where the line leaves it as it is."""
    frames, gains = line
    return gain_keys(clip, frames, gains, fps)
