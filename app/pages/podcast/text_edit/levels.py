#!/usr/bin/env python3
"""
Which mic each word was said into - Text Edit's bleed rejection. Pure numpy.

Every mic hears everyone: a two-person podcast transcribed mic by mic says
most things twice, once clearly and once faintly. Per mic, the worker
measures the level every 50 ms (worker.py --levels); here every mic's level
is laid on the timeline, and for each pair of mics the difference between
them is taken over the moments either is in use. Those differences fall in
two heaps - x speaking (x much louder) and y speaking - and the threshold
between the heaps (Otsu's) is that pair's even point: it absorbs a hot mic
or a quiet guest, so "louder" means louder than usual, not louder in dB.

A word is then:
- one of a pair heard twice (the same word on two mics within DUP_WINDOW):
  kept on the mic it was spoken into;
- dropped where another mic was clearly the one in use (by DECIDE_DB) -
  unless that mic's own transcript has nothing at that moment (the word was
  missed there): then it is kept and given to that person, so no word is
  lost to bleed rejection;
- otherwise kept - two people talking over each other both stay.
"""

import bisect
import math

import numpy as np

from .model import DUP_WINDOW, clip_window, normalise, source_key

HOP = 0.05
ACTIVE_DB = 12.0        # above a mic's floor (its room tone): someone is talking
SEPARATE_DB = 8.0       # a pair's two heaps this far apart: the threshold between them counts
DECIDE_DB = 3.0         # this much past the even point: clearly the other mic
MIN_SAMPLES = 40
WIDEN = 0.5


def track_levels(timeline, tracks, levels, span_end):
    """{track: float32 dB over the timeline, every HOP (nan with no clip)}.
    levels: {source_key: (hop, dB array)}."""
    n = int(math.ceil(max(span_end, 0.0) / HOP)) + 2
    grid = np.arange(n, dtype=np.float64) * HOP
    out = {}
    for item in timeline.get("items") or []:
        if item["kind"] != "audio" or item["track"] not in tracks or not item.get("path"):
            continue
        found = levels.get(source_key(item["path"], item.get("channels")))
        if not found:
            continue
        hop, data = found
        a, b, rec0 = clip_window(item, timeline)
        i0 = max(0, int(math.ceil(rec0 / HOP)))
        i1 = min(n, int(math.floor((rec0 + (b - a)) / HOP)) + 1)
        if i1 <= i0:
            continue
        arr = out.setdefault(item["track"], np.full(n, np.nan, dtype=np.float32))
        idx = ((a + grid[i0:i1] - rec0) / hop).astype(np.int64)
        ok = (idx >= 0) & (idx < len(data))
        seg = arr[i0:i1]
        seg[ok] = data[idx[ok]]
    return out


def _otsu(diff):
    """The threshold best splitting `diff` (dB) in two, and the two means."""
    values = np.clip(np.round(diff), -80, 80).astype(np.int64) + 80
    hist = np.bincount(values, minlength=161).astype(np.float64)
    bins = np.arange(161, dtype=np.float64) - 80
    total = hist.sum()
    if total <= 0:
        return 0.0, 0.0, 0.0
    w0 = np.cumsum(hist)
    w1 = total - w0
    m0 = np.cumsum(hist * bins)
    mean_all = m0[-1]
    with np.errstate(divide="ignore", invalid="ignore"):
        mu0 = m0 / w0
        mu1 = (mean_all - m0) / w1
        between = w0 * w1 * (mu0 - mu1) ** 2
    between[~np.isfinite(between)] = -1
    k = int(np.argmax(between))
    lo = float(mu0[k]) if w0[k] else 0.0
    hi = float(mu1[k]) if w1[k] else 0.0
    return float(bins[k]) + 0.5, lo, hi


class Mics:
    """Every chosen mic's level on the timeline, and pairwise comparisons."""

    def __init__(self, arrays):
        self.arrays = arrays
        self.floor = {t: float(np.nanpercentile(a, 20)) if np.isfinite(a).any() else -100.0
                      for t, a in arrays.items()}
        self._pairs = {}

    def __contains__(self, track):
        return track in self.arrays

    def _active(self, x, y, lo=0, hi=None):
        a, b = self.arrays[x][lo:hi], self.arrays[y][lo:hi]
        both = np.isfinite(a) & np.isfinite(b)
        return both & ((a > self.floor[x] + ACTIVE_DB) | (b > self.floor[y] + ACTIVE_DB)), a, b

    def even_point(self, x, y):
        """Where x and y count as equally loud (dB, x - y), for this pair."""
        key = (x, y)
        if key not in self._pairs:
            mask, a, b = self._active(x, y)
            diff = (a - b)[mask]
            theta = 0.0
            if diff.size > MIN_SAMPLES:
                _split, lo, hi = _otsu(diff)
                if hi - lo >= SEPARATE_DB:
                    # Halfway between the heaps: Otsu's split itself can sit
                    # anywhere in an empty stretch between them.
                    theta = (lo + hi) / 2.0
            self._pairs[key] = theta
            self._pairs[(y, x)] = -theta
        return self._pairs[key]

    def dominance(self, x, y, s, e):
        """How clearly x, not y, was the mic in use over [s, e] seconds - dB
        past their even point (positive: x) - or None when neither was."""
        if x not in self.arrays or y not in self.arrays:
            return None
        theta = self.even_point(x, y)
        for pad in (0.0, WIDEN):
            lo = max(0, int(math.floor((s - pad) / HOP)))
            hi = int(math.ceil((e + pad) / HOP)) + 1
            mask, a, b = self._active(x, y, lo, hi)
            if mask.any():
                return float(np.median((a - b)[mask])) - theta
        return None


def _heard(spans, s, e):
    """Any of `spans` (sorted by start) overlaps [s, e]."""
    i = bisect.bisect_left(spans, (e, float("inf")))
    for k in range(i - 1, max(-1, i - 40), -1):
        if spans[k][1] > s:
            return True
        if spans[k][0] < s - 30:
            break
    return False


def filter_words(words, mics):
    """`words` (model.collect_words) with bleed taken out - a new list, in
    the same order. Without levels for at least two mics, only exact
    doubles are taken out (the less sure of the two)."""
    tracks = sorted({w["tr"] for w in words} | (set(mics.arrays) if mics is not None else set()))
    if len(tracks) < 2 or not words:
        return list(words)
    have = mics is not None and sum(1 for t in tracks if t in mics) >= 2
    drop = set()
    norm = [normalise(w["t"]) for w in words]
    # Heard twice: the same word on another mic, starting within DUP_WINDOW.
    for i, w in enumerate(words):
        if i in drop:
            continue
        for j in range(i + 1, len(words)):
            v = words[j]
            if v["s"] - w["s"] > DUP_WINDOW:
                break
            if j in drop or v["tr"] == w["tr"] or norm[j] != norm[i]:
                continue
            d = mics.dominance(w["tr"], v["tr"], min(w["s"], v["s"]), max(w["e"], v["e"])) if have else None
            if d is None or abs(d) < DECIDE_DB:
                loser = j if ((w.get("p") or 0), -w["tr"]) >= ((v.get("p") or 0), -v["tr"]) else i
            else:
                loser = j if d > 0 else i
            drop.add(loser)
            if loser == i:
                break
    if not have:
        return [w for i, w in enumerate(words) if i not in drop]
    # Said into another mic: dropped - or, where that mic heard nothing then, moved to it.
    by_track = {t: [(w["s"], w["e"]) for k, w in enumerate(words) if w["tr"] == t and k not in drop]
                for t in tracks}
    out = []
    for i, w in enumerate(words):
        if i in drop:
            continue
        owner = None
        for y in tracks:
            if y == w["tr"]:
                continue
            d = mics.dominance(w["tr"], y, w["s"], w["e"])
            if d is not None and d < -DECIDE_DB:
                owner = y
                break
        if owner is None:
            out.append(w)
            continue
        if not _heard(by_track[owner], w["s"] - 0.2, w["e"] + 0.2):
            moved = dict(w)
            moved["tr"] = owner
            out.append(moved)
    return out
