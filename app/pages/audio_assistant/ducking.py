#!/usr/bin/env python3
"""
Auto-ducking: music (or anything) turned down under speech, as a volume
curve - the Curve panel's keys, so it's seen, can be changed, and goes to
Resolve with Apply like any curve (resolve_ext.apply_curve). No Qt, no
Resolve: it works from the page's copy of the timeline and Buddy's own
measurement of the files (peaks.py's K-weighted energy, one value per
LOUD_BLOCK_S), so it's tested directly.

    speech(clips, audio_of, fps, ...)   where someone's talking: [(first, last)] timeline frames
    duck_keys(clip, spans, fps, ...)    the clip's keys (as heard) dipping under those

Speech is found where the tracks ducked under are loud: each 100 ms of
their clips as they're heard (volume, keys, fades), smoothed over 300 ms,
against their own loudness - SENSITIVITY_LU under their integrated
loudness (BS.1770, as Match measures it) counts as talking, and never
anything under FLOOR_LUFS. Short pauses stay ducked (the music doesn't
bob up between words); a blip shorter than MIN_SPEECH_S doesn't duck it.
"""

import numpy as np

from .keys import clean, curve_db
from .levels import _fades, gate, heard_db, keyed_in_resolve

SENSITIVITY_LU = 18.0
FLOOR_LUFS = -55.0
MIN_SPEECH_S = 0.15
SMOOTH_BLOCKS = 3
DEFAULTS = {"depth": -12.0, "fade": 0.3, "pause": 0.8}
DEPTH_RANGE = (-40.0, -1.0)
FADE_RANGE = (0.05, 3.0)
PAUSE_RANGE = (0.0, 5.0)
RELEASE = 1.5            # coming back up takes this many times the fade going down


def settings(raw):
    """The view's ducking settings, made safe."""
    out = dict(DEFAULTS)
    for key, (lo, hi) in (("depth", DEPTH_RANGE), ("fade", FADE_RANGE), ("pause", PAUSE_RANGE)):
        try:
            value = float((raw or {}).get(key, out[key]))
        except (TypeError, ValueError):
            continue
        if np.isfinite(value):
            out[key] = min(hi, max(lo, value))
    return out


def _heard_blocks(clip, audio, fps):
    """(first block, energies): a clip's K-weighted energy per audio["block"]
    of the timeline, as it's heard - starting at the timeline block its first
    one falls in (blocks counted from frame 0)."""
    loud, block = audio["loud"], audio["block"]
    offset = clip.get("offset", 0.0)
    first = max(0, int(np.floor(offset / block)))
    last = min(len(loud), int(np.ceil((offset + (clip["end"] - clip["start"]) / fps) / block)))
    if last <= first:
        return 0, np.zeros(0)
    j = np.arange(first, last)
    t = (j + 0.5) * block
    frames = clip["start"] + (t - offset) * fps
    inside = (frames >= clip["start"]) & (frames < clip["end"])
    g = _fades(clip, frames)
    energy = loud[first:last] * g * g * 10 ** (heard_db(clip, t) / 10) * inside
    at = int(np.floor(frames[0] / fps / block))
    return at, energy


def speech(clips, audio_of, fps, block=0.1):
    """([(first, last)] timeline frames where the clips are talking, missing):
    missing - the clips whose audio isn't read yet (or can't be). Clips turned
    off aren't heard, so they're left out."""
    heard, missing = [], []
    for clip in clips:
        if clip.get("transition") or not clip.get("enabled", True):
            continue
        audio = audio_of(clip)
        if audio is None:
            missing.append(clip["id"])
            continue
        at, energy = _heard_blocks(clip, audio, fps)
        if len(energy):
            heard.append((at, energy))
    if not heard:
        return [], missing
    first = min(at for at, _e in heard)
    total = np.zeros(max(at + len(e) for at, e in heard) - first)
    for at, energy in heard:
        total[at - first:at - first + len(energy)] += energy
    four = np.convolve(total, np.ones(4) / 4, mode="valid")
    level = gate(four) if len(four) else None
    if level is None:
        return [], missing
    smooth = np.convolve(total, np.ones(SMOOTH_BLOCKS) / SMOOTH_BLOCKS, mode="same")
    with np.errstate(divide="ignore"):
        lufs = -0.691 + 10 * np.log10(np.maximum(smooth, 1e-20))
    talking = lufs > max(FLOOR_LUFS, level - SENSITIVITY_LU)
    spans, start = [], None
    for i, on in enumerate(np.append(talking, False)):
        if on and start is None:
            start = i
        elif not on and start is not None:
            if (i - start) * block >= MIN_SPEECH_S:
                spans.append((int(round((first + start) * block * fps)), int(round((first + i) * block * fps))))
            start = None
    return spans, missing


def _merged(spans, gap_frames):
    """Spans closer than gap_frames joined into one, in order."""
    out = []
    for a, b in sorted(spans):
        if out and a - out[-1][1] <= gap_frames:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def duck_keys(clip, spans, fps, depth=DEFAULTS["depth"], fade=DEFAULTS["fade"], pause=DEFAULTS["pause"]):
    """The clip's keys as heard (file time, like the Curve panel's), dipping by
    `depth` dB under each span of speech: down over `fade` s before it starts,
    back up over RELEASE x fade after it ends, staying down through pauses up
    to `pause` s. On top of what the clip is heard at now - its volume, or its
    keys (a curve's own keys keep their shape under the dips). [] when no
    speech is under the clip."""
    down, up = int(round(fade * fps)), int(round(fade * RELEASE * fps))
    # Pauses the ramps would meet in stay down too: joined before ramping.
    runs = [(a, b) for a, b in _merged(spans, max(int(round(pause * fps)), down + up))
            if b + up > clip["start"] and a - down < clip["end"]]
    if not runs:
        return []
    points = []                                    # (frame, dB of ducking)
    for a, b in runs:
        points += [(a - down, 0.0), (a, depth), (b, depth), (b + up, 0.0)]
    return gain_keys(clip, [f for f, _d in points], [d for _f, d in points], fps)


def gain_keys(clip, frames, gains, fps):
    """The clip's keys as heard (file time, like the Curve panel's) with a
    gain line on top of what it's heard at now: `gains` dB at timeline
    `frames` (in order), straight between them, 0 dB outside them. Its
    volume, or its keys (a curve's own keys keep their shape under the
    line). [] when the line never moves off 0 dB on the clip."""
    frames = np.asarray(frames, dtype=np.float64)
    ducks = np.asarray(gains, dtype=np.float64)
    if not len(frames):
        return []
    offset = clip.get("offset", 0.0)
    t_of = lambda frame: offset + (frame - clip["start"]) / fps

    def duck_at(frame):
        return np.interp(frame, frames, ducks, left=0.0, right=0.0)

    inside = np.arange(clip["start"], clip["end"] + 1)
    if not np.any(np.abs(duck_at(inside)) > 0.05):
        return []
    own = clip.get("keys") or []
    if clip.get("curve"):
        base = lambda t: curve_db(own, t) + (clip.get("volume") or 0.0)
    elif keyed_in_resolve(clip):
        base = lambda t: curve_db(own, t)
    else:
        base = lambda t: np.full_like(np.asarray(t, dtype=np.float64), clip.get("volume") or 0.0)
    wanted = {int(round(f)) for f in frames} | {clip["start"], clip["end"]}
    wanted |= {int(round(clip["start"] + (k["t"] - offset) * fps)) for k in own}
    keys = []
    for frame in sorted(f for f in wanted if clip["start"] <= f <= clip["end"]):
        t = t_of(frame)
        keys.append({"t": round(t, 6), "db": round(float(base(t)) + float(duck_at(frame)), 2), "ease": False})
    return _thin(clean(keys))


def _thin(keys):
    """Keys in a straight line with both neighbours (at the same level, or
    mid-ramp) add nothing: dropped, but the first and last."""
    if len(keys) < 3:
        return keys
    out = [keys[0]]
    for prev, key, nxt in zip(keys, keys[1:], keys[2:]):
        span = nxt["t"] - prev["t"]
        line = prev["db"] + (nxt["db"] - prev["db"]) * (key["t"] - prev["t"]) / span if span > 0 else prev["db"]
        if abs(line - key["db"]) > 0.05:
            out.append(key)
    out.append(keys[-1])
    return out
