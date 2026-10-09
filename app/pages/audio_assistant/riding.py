#!/usr/bin/env python3
"""
Dialogue riding: a voice evened out phrase by phrase - the quiet ones up,
the loud ones down - as a volume curve, the way a mixer rides a fader. Like
ducking.py it's the Curve panel's keys (seen, changed, then Apply), worked
out from the page's copy of the timeline and Buddy's own measurement. No
Qt, no Resolve.

    ride_keys(clip, audio, fps, amount, most) -> keys as heard, or []

The phrases are where the clip itself is talking (ducking.speech on it alone,
pauses under PHRASE_PAUSE_S joined). Each phrase's loudness (BS.1770, gated
like Match) is set against the whole clip's: the gain that takes it there,
times `amount` (0-1: how far to even it out), never past `most` dB either
way. The gain holds through a phrase and changes in the pause before the
next (over RAMP_S, or the pause if that's shorter), so no word is ridden
mid-way. Measured on the file as it is - the clip's volume, keys and fades
aren't what's evened out; they stay on top.
"""

import numpy as np

from .ducking import _heard_blocks, speech
from .keys import clean, curve_db
from .levels import gate, keyed_in_resolve

PHRASE_PAUSE_S = 0.3
RAMP_S = 0.25
DEFAULTS = {"amount": 0.7, "most": 6.0}
MOST_RANGE = (1.0, 15.0)


def settings(raw):
    out = dict(DEFAULTS)
    for key, (lo, hi) in (("amount", (0.0, 1.0)), ("most", MOST_RANGE)):
        try:
            value = float((raw or {}).get(key, out[key]))
        except (TypeError, ValueError):
            continue
        if np.isfinite(value):
            out[key] = min(hi, max(lo, value))
    return out


def _plain(clip):
    """The clip as its file sounds: no volume, keys or fades."""
    return dict(clip, volume=0.0, keys=None, curve=None, fade_in=0, fade_out=0, enabled=True)


def phrases(clip, audio, fps):
    """[(first, last, LUFS)] - each phrase of the clip, in timeline frames, and its loudness."""
    plain = _plain(clip)
    spans, _missing = speech([plain], lambda _c: audio, fps)
    joined = []
    for a, b in spans:
        if joined and a - joined[-1][1] <= PHRASE_PAUSE_S * fps:
            joined[-1][1] = b
        else:
            joined.append([a, b])
    at, energy = _heard_blocks(plain, audio, fps)
    block = audio["block"]
    out = []
    for a, b in joined:
        i = int(np.floor(a / fps / block)) - at
        j = int(np.ceil(b / fps / block)) - at
        part = energy[max(0, i):max(0, j)]
        four = np.convolve(part, np.ones(4) / 4, mode="valid") if len(part) >= 4 else part.mean(keepdims=True) if len(part) else part
        level = gate(four) if len(four) else None
        if level is not None:
            out.append((a, b, level))
    return out


def ride_keys(clip, audio, fps, amount=DEFAULTS["amount"], most=DEFAULTS["most"]):
    """The clip's keys as heard, riding each phrase towards the clip's own
    loudness. [] when there's nothing to even out (one phrase, or none)."""
    found = phrases(clip, audio, fps)
    if len(found) < 2:
        return []
    plain = _plain(clip)
    at, energy = _heard_blocks(plain, audio, fps)
    four = np.convolve(energy, np.ones(4) / 4, mode="valid")
    whole = gate(four) if len(four) else None
    if whole is None:
        return []
    gains = [float(np.clip((whole - level) * amount, -most, most)) for _a, _b, level in found]
    offset = clip.get("offset", 0.0)
    t_of = lambda frame: offset + (frame - clip["start"]) / fps
    own = clip.get("keys") or []
    if clip.get("curve"):
        base = lambda t: float(curve_db(own, t)) + (clip.get("volume") or 0.0)
    elif keyed_in_resolve(clip):
        base = lambda t: float(curve_db(own, t))
    else:
        base = lambda _t: float(clip.get("volume") or 0.0)
    points = []                        # (frame, gain)
    ramp = int(round(RAMP_S * fps))
    for i, (a, b, _level) in enumerate(found):
        if i == 0:
            points.append((clip["start"], gains[0]))
        else:
            pause = a - found[i - 1][1]
            change = min(ramp, max(1, pause))
            points.append((a - change, gains[i - 1]))
        points += [(a, gains[i]), (b, gains[i])]
    points.append((clip["end"], gains[-1]))
    keys = []
    for frame, gain in points:
        frame = min(clip["end"], max(clip["start"], frame))
        t = t_of(frame)
        keys.append({"t": round(t, 6), "db": round(base(t) + gain, 2), "ease": False})
    return _thin(clean(keys))


def _thin(keys):
    from .ducking import _thin as thin
    return thin(keys)
