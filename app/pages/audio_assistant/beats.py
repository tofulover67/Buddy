#!/usr/bin/env python3
"""
Beat markers: where the beats of a piece of music fall, to cut on. numpy
only (no Qt, no Resolve), so it's tested on made-up music.

    find(samples, rate) -> {"bpm", "beats": [seconds], "bars": [indices into beats]}

How (after Ellis, "Beat tracking by dynamic programming", 2007):
  onsets   spectral flux - how much louder each band got since the last
           window (log magnitude, rises only), summed: peaks where notes and
           hits start. One value per HOP samples.
  tempo    the onset curve's autocorrelation, weighted towards 120 BPM (a
           log-normal over an octave) so a half or double tempo doesn't win
           on a tie; between MIN_BPM and MAX_BPM.
  beats    dynamic programming: each window's best score is its onset plus
           the best earlier score one beat back, less a penalty for being off
           the tempo - traced back from the best place near the end. Beats
           follow a song's small drifts, and land on its hits.
  bars     4/4 assumed: of the four ways to count the beats, the one whose
           first beats hit hardest in the bass (under 200 Hz) - the downbeats.
"""

import numpy as np

N_FFT = 2048
HOP = 512
MIN_BPM, MAX_BPM = 60.0, 200.0
PREFERRED_BPM = 120.0
TIGHTNESS = 100.0         # how hard the beats are held to the tempo
BEATS_PER_BAR = 4
PULSE_MIN = 0.15          # the beat's repeat, against the onset curve's own energy
PULSE_OVER = 6.0          # ... and against the median over the tempo range - or, short of that:
STEADY_S = 8.0            # stretches this long, half overlapping...
STEADY_SHARE = 0.8        # ... at least this share of them finding the same beat (or its double or half)
STEADY_LEAST = 4          # ... out of this many, at least
# A hit's flux peaks before the hit reaches the window's centre: measured on
# made-up kicks, hats and plucks at 44.1 and 48 kHz, it's heard about this many
# samples after the centre of the window that found it.
ONSET_LAG = 400


def _onsets(mono, rate):
    """(all bands, bass) onset strength per HOP, both scaled to unit spread."""
    if len(mono) < N_FFT:
        mono = np.concatenate((mono, np.zeros(N_FFT - len(mono))))
    frames = np.lib.stride_tricks.sliding_window_view(mono, N_FFT)[::HOP]
    window = np.hanning(N_FFT)
    freqs = np.fft.rfftfreq(N_FFT, 1.0 / rate)
    bass = freqs < 200
    flux, low = np.zeros(len(frames)), np.zeros(len(frames))
    prev = None
    for first in range(0, len(frames), 512):
        mag = np.log1p(1000.0 * np.abs(np.fft.rfft(frames[first:first + 512] * window, axis=-1)))
        before = np.concatenate(([prev if prev is not None else mag[0]], mag[:-1]))
        rise = np.maximum(mag - before, 0.0)
        flux[first:first + len(mag)] = rise.sum(axis=1)
        low[first:first + len(mag)] = rise[:, bass].sum(axis=1)
        prev = mag[-1]
    out = []
    for curve in (flux, low):
        curve = curve - np.convolve(curve, np.ones(16) / 16, mode="same")     # the local level away
        curve = np.maximum(curve, 0.0)
        spread = curve.std()
        out.append(curve / spread if spread > 0 else curve)
    return out


def _tempo(onset, per_second):
    """The beat's length in onset windows (a float), or None for no pulse."""
    lo, hi = int(per_second * 60 / MAX_BPM), int(np.ceil(per_second * 60 / MIN_BPM))
    if len(onset) < hi * 4:
        return None
    x = onset - onset.mean()
    corr = np.correlate(x, x, mode="full")[len(x) - 1:]
    if corr[0] <= 0:
        return None
    lags = np.arange(lo, hi + 1)
    plain = corr[lo:hi + 1] / corr[0]
    # A pulse repeats clearly: its lag stands well out of the rest (measured:
    # sparse drums 14-25x the median, noise, speech and a held tone 2-4x). A full
    # mix with busy hats and offbeats can sit at 4x too - its beat is told from
    # speech's by holding: the same beat, stretch after stretch.
    if plain.max() < PULSE_MIN:
        return None
    if plain.max() < PULSE_OVER * np.median(np.abs(plain)) and not _steady(onset, lo + int(np.argmax(plain)), lo, hi, per_second):
        return None
    bpm = 60 * per_second / lags
    weight = np.exp(-0.5 * (np.log2(bpm / PREFERRED_BPM) / 1.0) ** 2)
    score = corr[lo:hi + 1] * weight
    best = int(np.argmax(score))
    period = float(lags[best])
    if 0 < best < len(score) - 1:                 # between the lags: a parabola's top
        a, b, c = score[best - 1], score[best], score[best + 1]
        denom = a - 2 * b + c
        if denom < 0:
            period += 0.5 * (a - c) / denom
    return period


def _steady(onset, lag, lo, hi, per_second):
    """Whether stretch after stretch of the onset curve repeats at `lag` windows
    (or twice or half that) - a beat that holds, not a chance repeat."""
    n = int(STEADY_S * per_second)
    agree = total = 0
    for a in range(0, len(onset) - n + 1, n // 2):
        x = onset[a:a + n] - onset[a:a + n].mean()
        corr = np.correlate(x, x, mode="full")[n - 1:]
        if corr[0] <= 0:
            continue
        total += 1
        best = lo + int(np.argmax(corr[lo:hi + 1]))
        agree += any(abs(best * m - lag) <= 0.04 * lag for m in (0.5, 1.0, 2.0))
    return total >= STEADY_LEAST and agree >= STEADY_SHARE * total


def _track(onset, period):
    """Beat windows by dynamic programming over the onset curve."""
    n = len(onset)
    score = onset.astype(np.float64).copy()
    back = np.full(n, -1)
    lo, hi = int(round(period / 2)), int(round(period * 2))
    offsets = np.arange(lo, hi + 1)
    penalty = -TIGHTNESS * np.log(offsets / period) ** 2
    for t in range(lo, n):
        prev = t - offsets
        ok = prev >= 0
        if not ok.any():
            continue
        cand = score[prev[ok]] + penalty[ok]
        best = int(np.argmax(cand))
        if cand[best] > 0:
            score[t] = onset[t] + cand[best]
            back[t] = prev[ok][best]
    # The best place near the end - one of the last beat's worth.
    tail = max(0, n - int(round(period)))
    t = tail + int(np.argmax(score[tail:]))
    beats = [t]
    while back[t] >= 0:
        t = back[t]
        beats.append(t)
    return np.array(beats[::-1])


def scaled(found, tempo):
    """find()'s beats at the tempo asked: "double" puts one between each
    pair, "half" keeps every other (the bars' first beats among them)."""
    beats_, bars = list(found["beats"]), list(found["bars"])
    if tempo == "double" and len(beats_) > 1:
        out = []
        for a, b in zip(beats_, beats_[1:]):
            out += [a, round((a + b) / 2, 4)]
        out.append(beats_[-1])
        return {"bpm": round(found["bpm"] * 2, 1), "beats": out,
                "bars": list(range(2 * bars[0], len(out), BEATS_PER_BAR)) if bars else []}
    if tempo == "half":
        first = bars[0] % 2 if bars else 0
        out = beats_[first::2]
        return {"bpm": round(found["bpm"] / 2, 1), "beats": out,
                "bars": list(range(0, len(out), BEATS_PER_BAR))}
    return found


def find(samples, rate):
    """{"bpm", "beats": [seconds from the start of samples], "bars": [indices
    into beats of each bar's first beat]} - or None when there's no pulse to
    find (speech, silence, too short)."""
    x = np.asarray(samples, dtype=np.float64)
    mono = x.mean(axis=1) if x.ndim == 2 else x
    if len(mono) < rate * 3:
        return None
    onset, bass = _onsets(mono, rate)
    per_second = rate / HOP
    period = _tempo(onset, per_second)
    if period is None:
        return None
    windows = _track(onset, period)
    if len(windows) < 2:
        return None
    # Where its onset is heard: a little after the window's centre.
    seconds = (windows * HOP + N_FFT / 2 + ONSET_LAG) / rate
    phase = max(range(BEATS_PER_BAR), key=lambda k: bass[windows[k::BEATS_PER_BAR]].sum())
    return {"bpm": round(60 * per_second / period, 1), "beats": [round(float(s), 4) for s in seconds],
            "bars": list(range(phase, len(windows), BEATS_PER_BAR))}
