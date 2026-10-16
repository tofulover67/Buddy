#!/usr/bin/env python3
"""
Sound Design's ear: a piece of music's key and tempo, and the moments in it
a sound belongs on - a riser into a drop, an impact on it, a whoosh where a
new section starts. numpy only (no Qt, no Resolve), so it's tested on
made-up music; beats.py finds the beats and bars, fit.py's _features() what
each beat sounds like.

    analyse(samples, rate) -> {"bpm", "beat_s", "bar_s", "bars": [seconds],
                               "levels": [dB per bar], "key", "moments", "length_s",
                               "grid": {"bpm", "first"}}

The grid: the steady beat that fits the beats found best (a straight line
through them, by least squares) - its tempo to the thousandth of a BPM, and
where on it the first bar falls. A beat found on its own can be ten
milliseconds out and a tempo rounded to a tenth drifts a fifth of a second
over a song; what plays along with the music (the DAW) counts on the grid.

The key (after Krumhansl and Kessler's key profiles, 1982): how much of each
of the 12 notes the whole song has (a chroma, each frame's counted the same
however loud it is), set against the profile of each major and minor key -
the best match is the key. Close seconds are said: a song can sit between
a key and its relative (A minor and C major share every note), and "unsure"
is shown rather than a guess dressed up as an answer.

The moments, bar by bar (4/4, as beats.py counts):
  drop     the music gets much louder on a bar's first beat than the bars
           before it - a riser leads into it, an impact lands on it
  change   what's played changes (its notes and tone) though its level
           doesn't, at a local peak of that change - a new section: a whoosh
  break    the music falls away - a whoosh or a downlifter
  end      the last hit before a song that stops (not one that fades out)
Only the strongest are kept, at least MIN_APART_BARS apart.
"""

import numpy as np

from . import beats as beat_finder
from . import fit

# Krumhansl-Kessler key profiles: how well each of the 12 notes, from the
# tonic up, fits a major or a minor key.
MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
# Spelled as a key signature would: flats for F and the flat keys, sharps otherwise.
MAJOR_NAMES = ["C", "D♭", "D", "E♭", "E", "F", "F♯", "G", "A♭", "A", "B♭", "B"]
MINOR_NAMES = ["C", "C♯", "D", "E♭", "E", "F", "F♯", "G", "G♯", "A", "B♭", "B"]

N_FFT = 4096
HOP = 2048
LOW_HZ, HIGH_HZ = 55.0, 5000.0
SURE = 0.6               # the best key's correlation, at least, to be "clear"...
MARGIN = 0.04            # ... and this far ahead of the next key that isn't its relative

DROP_DB = 4.0            # a drop: this much louder than the bars before
BREAK_DB = 6.0           # a break: this much quieter
BEFORE_BARS = 2          # the bars a bar is compared with
CHANGE_Z = 1.5           # a change: this many spreads over the song's usual
MIN_APART_BARS = 2
MOST_MOMENTS = 12
PRIORITY = {"drop": 0, "end": 1, "break": 2, "change": 3}


def _mono(samples):
    x = np.asarray(samples, dtype=np.float64)
    return x.mean(axis=1) if x.ndim == 2 else x


def chroma(mono, rate):
    """The song's 12 notes, C first: each frame's own chroma (unit length,
    so a quiet verse counts as much as a loud chorus), summed."""
    if len(mono) < N_FFT:
        mono = np.concatenate((mono, np.zeros(N_FFT - len(mono))))
    frames = np.lib.stride_tricks.sliding_window_view(mono, N_FFT)[::HOP]
    window = np.hanning(N_FFT)
    freqs = np.fft.rfftfreq(N_FFT, 1.0 / rate)
    pitched = (freqs >= LOW_HZ) & (freqs <= HIGH_HZ)
    pitch_class = np.round(12 * np.log2(freqs[pitched] / 440.0) + 9).astype(np.int64) % 12
    total = np.zeros(12)
    for first in range(0, len(frames), 256):
        power = np.abs(np.fft.rfft(frames[first:first + 256] * window, axis=-1))[:, pitched] ** 2
        c = np.zeros((len(power), 12))
        for k in range(12):
            c[:, k] = np.sqrt(power[:, pitch_class == k].sum(axis=1))
        norms = np.linalg.norm(c, axis=1)
        heard = norms > 1e-6
        total += (c[heard] / norms[heard, None]).sum(axis=0)
    return total


def name_of(tonic, mode):
    return f"{(MAJOR_NAMES if mode == 'major' else MINOR_NAMES)[tonic % 12]} {mode}"


def camelot(tonic, mode):
    """The DJ wheel's code: 8B is C major, 8A A minor - keys a step apart mix."""
    major = tonic if mode == "major" else (tonic + 3) % 12
    return f"{(7 * major % 12 + 7) % 12 + 1}{'B' if mode == 'major' else 'A'}"


def relative(tonic, mode):
    return ((tonic + 3) % 12, "major") if mode == "minor" else ((tonic + 9) % 12, "minor")


def key_of(notes):
    """{"tonic" (0 = C), "mode", "name", "camelot", "relative", "clear",
    "also"} for a 12-note chroma - or None when there are no notes in it.
    "also": the next likeliest key's name when it's close, else None."""
    notes = np.asarray(notes, dtype=np.float64)
    if notes.sum() <= 0 or np.ptp(notes) <= 1e-9:
        return None
    scores = []
    for mode, profile in (("major", MAJOR), ("minor", MINOR)):
        for tonic in range(12):
            r = np.corrcoef(notes, np.roll(profile, tonic))[0, 1]
            scores.append((float(r), tonic, mode))
    scores.sort(reverse=True)
    best, tonic, mode = scores[0]
    rel = relative(tonic, mode)
    # The relative key shares every note: it's no rival, just the other reading.
    rival = next((s for s in scores[1:] if (s[1], s[2]) != rel), None)
    clear = best >= SURE and (rival is None or best - rival[0] >= MARGIN)
    also = name_of(rival[1], rival[2]) if rival is not None and best - rival[0] < MARGIN else None
    return {"tonic": tonic, "mode": mode, "name": name_of(tonic, mode), "camelot": camelot(tonic, mode),
            "relative": name_of(*rel), "clear": bool(clear), "also": also}


def _bars(found, length_s):
    """Each bar's (start, end) in seconds."""
    beats = found["beats"]
    starts = [beats[i] for i in found["bars"]]
    bar_s = 60.0 / found["bpm"] * beat_finder.BEATS_PER_BAR
    return [(s, min(length_s, starts[i + 1] if i + 1 < len(starts) else s + bar_s)) for i, s in enumerate(starts)]


def grid(found):
    """{"bpm", "first"}: the straight beat grid through found's beats, and the
    time of its first bar - or None with too few beats to fit. Each beat is
    counted from the last by the gap (a beat the finder skipped still counts),
    and beats far off the line (a stray hit) are left out of the fit."""
    t = np.asarray(found["beats"], dtype=np.float64)
    if len(t) < 8 or not found["bars"]:
        return None
    period = 60.0 / found["bpm"]
    n = np.concatenate(([0.0], np.cumsum(np.maximum(1.0, np.round(np.diff(t) / period)))))
    keep = np.ones(len(t), dtype=bool)
    for _ in range(3):
        slope, at0 = np.polyfit(n[keep], t[keep], 1)
        off = np.abs(t - (at0 + slope * n))
        keep = off <= max(0.015, 3.0 * float(np.median(off[keep])) * 1.4826)
        if keep.sum() < 8:
            return None
    slope, at0 = np.polyfit(n[keep], t[keep], 1)
    if slope <= 0:
        return None
    return {"bpm": round(60.0 / slope, 3), "first": round(float(at0 + slope * n[found["bars"][0]]), 4)}


def _level_db(mono, rate, a, b):
    part = mono[int(a * rate):max(int(a * rate) + 1, int(b * rate))]
    return float(10 * np.log10(max(np.mean(part ** 2) if len(part) else 0.0, 1e-12)))


def moments(mono, rate, found, bars, levels, song_s):
    """The moments (see the top), strongest kept, in time order:
    [{"t": seconds, "bar": index, "kind", "strength": 0-1}]."""
    n = len(bars)
    if n < BEFORE_BARS + 2:
        return []
    levels = np.asarray(levels)
    loud = np.median(levels)
    found_moments = []
    for i in range(BEFORE_BARS, n):
        before = levels[i - BEFORE_BARS:i].mean()
        rise, fall = levels[i] - before, before - levels[i]
        if rise >= DROP_DB and levels[i] >= loud - 3:
            found_moments.append({"bar": i, "kind": "drop", "strength": min(1.0, rise / 12.0)})
        elif fall >= BREAK_DB and before >= loud - 3 and (levels[i + 1:] >= loud - 3).any():
            # ... and the music comes back after it: not a fade-out.
            found_moments.append({"bar": i, "kind": "break", "strength": min(1.0, fall / 15.0)})
    # What's played: the notes and the tone of each bar, against the bars before.
    beats = found["beats"]
    chroma_b, tone_b = fit._features(mono, rate, beats)
    per_bar_c = np.array([chroma_b[found["bars"][i]:(found["bars"][i + 1] if i + 1 < n else len(beats))].mean(axis=0)
                          for i in range(n)])
    per_bar_t = np.array([tone_b[found["bars"][i]:(found["bars"][i + 1] if i + 1 < n else len(beats))].mean(axis=0)
                          for i in range(n)])
    novelty = np.zeros(n)
    for i in range(BEFORE_BARS, n - 1):
        a_c, b_c = per_bar_c[i - BEFORE_BARS:i].mean(axis=0), per_bar_c[i:i + BEFORE_BARS].mean(axis=0)
        cos = float(a_c @ b_c / max(np.linalg.norm(a_c) * np.linalg.norm(b_c), 1e-9))
        tone = float(np.abs(per_bar_t[i - BEFORE_BARS:i].mean(axis=0) - per_bar_t[i:i + BEFORE_BARS].mean(axis=0)).mean())
        novelty[i] = (1 - cos) * 4 + tone
    inner = novelty[BEFORE_BARS:n - 1]
    if len(inner) > 2 and inner.std() > 1e-9:
        z = (novelty - inner.mean()) / inner.std()
        for i in range(BEFORE_BARS, n - 1):
            if z[i] >= CHANGE_Z and z[i] == z[max(0, i - 2):i + 3].max():
                found_moments.append({"bar": i, "kind": "change", "strength": min(1.0, z[i] / 4.0)})
    # A song that stops on a hit, not one that fades away: its last bar is as loud as the rest.
    last = max((i for i, (a, _b) in enumerate(bars) if a <= song_s - 0.25), default=None)
    if last is not None and last > 0 and levels[last] >= loud - 6:
        found_moments.append({"bar": last, "kind": "end", "strength": 0.6})
    kept = []
    for m in sorted(found_moments, key=lambda m: (PRIORITY[m["kind"]], -m["strength"])):
        if all(abs(m["bar"] - k["bar"]) >= MIN_APART_BARS for k in kept):
            kept.append(m)
    kept = sorted(kept, key=lambda m: -m["strength"] - (PRIORITY[m["kind"]] == 0))[:MOST_MOMENTS]
    return sorted(({"t": round(bars[m["bar"]][0], 4), "bar": m["bar"], "kind": m["kind"],
                    "strength": round(float(m["strength"]), 3)} for m in kept), key=lambda m: m["t"])


def analyse(samples, rate):
    """The song (see the top). Without a steady beat there's still a key:
    "bpm" is None then, and there are no bars and no moments."""
    mono = _mono(samples)
    length_s = len(mono) / rate
    key = key_of(chroma(mono, rate))
    found = beat_finder.find(samples, rate)
    out = {"bpm": None, "beat_s": None, "bar_s": None, "bars": [], "levels": [], "key": key,
           "moments": [], "length_s": round(length_s, 3), "grid": None}
    if not found or len(found["bars"]) < 2:
        return out
    bars = _bars(found, length_s)
    levels = [_level_db(mono, rate, a, b) for a, b in bars]
    song_s = fit.song_end(samples, rate)
    beat_s = 60.0 / found["bpm"]
    out.update(bpm=found["bpm"], beat_s=round(beat_s, 5), bar_s=round(beat_s * beat_finder.BEATS_PER_BAR, 5),
               bars=[round(a, 4) for a, _b in bars], levels=[round(v, 1) for v in levels],
               moments=moments(mono, rate, found, bars, levels, song_s), grid=grid(found))
    return out
