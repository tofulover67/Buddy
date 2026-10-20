#!/usr/bin/env python3
"""
Beat markers: where the beats of a piece of music fall, to cut on. numpy
only (no Qt, no Resolve), so it's tested on made-up music.

    find(samples, rate) -> {"bpm", "beats": [seconds], "bars": [indices into beats]}

How (the tempo after Percival and Tzanetakis, "Streamlined tempo estimation",
2014; the beats after Ellis, "Beat tracking by dynamic programming", 2007):
  onsets   spectral flux on a mel scale: how many dB louder each of MEL_BANDS
           bands (spaced as the ear hears pitch) got since the last window,
           rises only, averaged - each band counts the same, so a kick and a
           snare count as much as the hats over them. One value per HOP.
  tempo    the candidates: the peaks of the onset curve's autocorrelation
           between MIN_BPM and MAX_BPM. Each is tried as a pulse: of all the
           ways to lay beats that far apart over the music, how far the best
           one stands out of the rest (and, at half weight, the same for its
           eighths) - stretch by stretch, so a song's small drifts don't blur
           it. A tempo a dotted beat (4:3) or a triplet (3:2) off puts half
           its beats between the hits, and loses. A leaning to PREFERRED_BPM
           breaks near ties.
  octave   a tempo and its double fit the same hits; which one a musician
           counts can't be heard from the audio alone. It's said between
           FOLD_BPM and twice that, where most music is counted. Measured on
           10-40 s excerpts of 27 released songs: the published tempo, or
           half or double it, on 91% of them, the very tempo on 79%, no tempo
           on 3% (the way before this: 54%, 44%, and none on a quarter). The
           DAW has x2 and ÷2 for a song counted the other way.
  pulse    a steady beat stands clear of chance: the best pulse's beats are
           PULSE_Z spreads (and more for a longer clip - music's grows with
           its length, speech's doesn't) over the average way of laying them.
           And it holds: of the clip's stretches of about AGREE_S, heard on
           their own, at least half find the same tempo - a few laughs or
           footsteps can line up by chance once, not stretch after stretch.
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
MEL_BANDS = 40
MEL_LO_HZ, MEL_HI_HZ = 30.0, 16000.0
TOP_DB = 80.0             # quieter than this under the loudest is left out (no hiss counted)
MIN_BPM, MAX_BPM = 50.0, 210.0
FOLD_BPM = 72.0           # the tempo said: from this to twice it
PREFERRED_BPM = 120.0
PREFER_OCTAVES = 1.5      # how gently: a log-normal this many octaves wide
CANDIDATES = 10
EIGHTHS = 0.5             # how much a tempo's eighths count, against its beats
STRETCH_S = 20.0          # a pulse is tried stretch by stretch, this long
PULSE_Z = 4.5             # a steady beat's pulse stands out at least this much...
PULSE_Z_GROWTH = 2.0      # ... plus this, times the square root of its length in tens of seconds
AGREE_S = 10.0            # ... and stretches about this long, heard on their own...
AGREE_SHARE = 0.5         # ... this share of them at least, find the same tempo...
AGREE_WITHIN = 0.04       # ... to within this
SHORT_Z = 8.0             # a clip too short to split: its pulse stands out this much
QUIET_DB = 0.02           # an onset curve flatter than this (dB a window, on average) has no hits at all
TIGHTNESS = 100.0         # how hard the beats are held to the tempo
BEATS_PER_BAR = 4
# A hit's flux peaks before the hit reaches the window's centre: measured on
# made-up kicks, hats and plucks at 44.1 and 48 kHz, it's heard about this many
# samples after the centre of the window that found it.
ONSET_LAG = 290


def _mel_bank(rate):
    """MEL_BANDS triangles over the FFT's bins, and each one's centre (Hz)."""
    hi = min(MEL_HI_HZ, rate / 2)
    to_mel = lambda f: 2595.0 * np.log10(1 + f / 700.0)
    edges = 700.0 * (10 ** (np.linspace(to_mel(MEL_LO_HZ), to_mel(hi), MEL_BANDS + 2) / 2595.0) - 1)
    freqs = np.fft.rfftfreq(N_FFT, 1.0 / rate)
    bank = np.zeros((MEL_BANDS, len(freqs)))
    for b in range(MEL_BANDS):
        lo, mid, top = edges[b:b + 3]
        bank[b] = np.maximum(0.0, np.minimum((freqs - lo) / (mid - lo), (top - freqs) / (top - mid)))
    return bank, edges[1:-1]


def _onsets(mono, rate):
    """(all bands, bass) onset strength per HOP, both scaled to unit spread -
    or (None, None) when nothing in it starts at all (silence, a held tone)."""
    if len(mono) < N_FFT:
        mono = np.concatenate((mono, np.zeros(N_FFT - len(mono))))
    frames = np.lib.stride_tricks.sliding_window_view(mono, N_FFT)[::HOP]
    window = np.hanning(N_FFT)
    bank, centres = _mel_bank(rate)
    mel = np.zeros((len(frames), MEL_BANDS))
    for first in range(0, len(frames), 512):
        power = np.abs(np.fft.rfft(frames[first:first + 512] * window, axis=-1)) ** 2
        mel[first:first + len(power)] = power @ bank.T
    db = 10 * np.log10(np.maximum(mel, 1e-10))
    db = np.maximum(db, db.max() - TOP_DB)
    rise = np.maximum(np.diff(db, axis=0, prepend=db[:1]), 0.0)
    flux, low = rise.mean(axis=1), rise[:, centres < 200].mean(axis=1)
    if flux.mean() < QUIET_DB:
        return None, None
    out = []
    for curve in (flux, low):
        curve = curve - np.convolve(curve, np.ones(16) / 16, mode="same")     # the local level away
        curve = np.maximum(curve, 0.0)
        spread = curve.std()
        out.append(curve / spread if spread > 0 else curve)
    return out


def _phases(onset, period):
    """For each way of laying beats `period` windows apart (each phase, in
    windows): the mean onset on its beats - a window either side counted, as
    a hit's peak can fall in the next - and how many beats there are."""
    n = len(onset)
    near = onset.copy()
    near[1:] = np.maximum(near[1:], onset[:-1])
    near[:-1] = np.maximum(near[:-1], onset[1:])
    k = np.arange(int((n - 1) / period) + 1)
    at = np.round(np.arange(int(np.ceil(period)))[:, None] + k[None, :] * period).astype(int)
    inside = at < n
    sums = np.where(inside, near[np.minimum(at, n - 1)], 0.0).sum(axis=1)
    return sums / np.maximum(inside.sum(axis=1), 1), len(k)


def _stretches(n, per_second):
    m = int(STRETCH_S * per_second)
    bounds = np.linspace(0, n, max(1, int(round(n / m))) + 1).astype(int)
    return list(zip(bounds, bounds[1:]))


def _stands_out(onset, period, per_second):
    """How far the best way of laying beats `period` apart stands out of the
    average way, in the onset's own spread - stretch by stretch, averaged."""
    out = []
    for a, b in _stretches(len(onset), per_second):
        part = onset[a:b]
        if len(part) >= 2 * period and part.std() > 0:
            means, _ = _phases(part, period)
            out.append((means.max() - means.mean()) / part.std())
    return float(np.mean(out)) if out else 0.0


def _pulse_z(onset, period, per_second):
    """The same, against chance: in spreads of a mean of that many beats.
    Stretch by stretch, so the more there are, the surer."""
    zs = []
    for a, b in _stretches(len(onset), per_second):
        part = onset[a:b]
        if len(part) >= 2 * period and part.std() > 0:
            means, beats_ = _phases(part, period)
            zs.append((means.max() - means.mean()) / (part.std() / np.sqrt(beats_)))
    return float(np.mean(zs) * np.sqrt(len(zs))) if zs else 0.0


def _tempo(onset, per_second):
    """The beat's length in onset windows (a float), or None for no pulse."""
    period = _likeliest(onset, per_second)
    if period is None:
        return None
    seconds = len(onset) / per_second
    z = _pulse_z(onset, period, per_second)
    folded = _folded(period, per_second)
    parts = max(2, int(round(seconds / AGREE_S)))
    n = len(onset)
    if not _long_enough(n // parts, per_second):
        return folded if z >= SHORT_Z else None             # too short to split: only a clearer pulse will do
    if z < PULSE_Z + PULSE_Z_GROWTH * np.sqrt(seconds / 10):
        return None
    # The stretches vote: the tempo most of them find wins, even over the
    # whole clip's (a busy song can lead it to a 4:5 or 3:4 of its beat).
    heard = [_folded(p, per_second) for p in
             (_likeliest(onset[i * n // parts:(i + 1) * n // parts], per_second) for i in range(parts)) if p]
    near = lambda a, b: abs(a / b - 1) < AGREE_WITHIN
    best = max(heard + [folded], key=lambda h: (sum(near(q, h) for q in heard), near(h, folded)))
    with_it = [q for q in heard if near(q, best)]
    if len(with_it) < AGREE_SHARE * parts or (len(with_it) < 2 and not near(best, folded)):
        return None
    return folded if near(best, folded) else float(np.median(with_it))


def _folded(period, per_second):
    """The period between FOLD_BPM and twice it: halved or doubled."""
    while 60 * per_second / period < FOLD_BPM:
        period /= 2
    while 60 * per_second / period > 2 * FOLD_BPM:
        period *= 2
    return period


def _long_enough(n, per_second):
    """Whether an onset curve n windows long holds four beats at MIN_BPM."""
    return n >= int(np.ceil(per_second * 60 / MIN_BPM)) * 4


def _likeliest(onset, per_second):
    """The likeliest beat's length in onset windows, before folding - or None
    for a curve too short or too flat to say."""
    lo, hi = int(per_second * 60 / MAX_BPM), int(np.ceil(per_second * 60 / MIN_BPM))
    if not _long_enough(len(onset), per_second):
        return None
    # The autocorrelation, its spectrum's magnitude squashed (a square root)
    # so a few loud hits don't make every peak.
    x = onset - onset.mean()
    corr = np.fft.irfft(np.abs(np.fft.rfft(x, 2 * len(x))), 2 * len(x))[:len(x)]
    if corr[0] <= 0:
        return None
    peaks = [t for t in range(max(lo, 1), hi + 1) if corr[t] >= corr[t - 1] and corr[t] >= corr[t + 1]]
    best = None
    for t in sorted(peaks, key=lambda t: -corr[t])[:CANDIDATES]:
        a, b, c = corr[t - 1], corr[t], corr[t + 1]
        denom = a - 2 * b + c
        period = t + (0.5 * (a - c) / denom if denom < 0 else 0.0)        # between the lags: a parabola's top
        bpm = 60 * per_second / period
        if not MIN_BPM <= bpm <= MAX_BPM:
            continue
        score = _stands_out(onset, period, per_second) + EIGHTHS * _stands_out(onset, period / 2, per_second)
        score *= np.exp(-0.5 * (np.log2(bpm / PREFERRED_BPM) / PREFER_OCTAVES) ** 2)
        if best is None or score > best[0]:
            best = (score, period)
    return best[1] if best else None


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
    if onset is None:
        return None
    per_second = rate / HOP
    period = _tempo(onset, per_second)
    if period is None:
        return None
    if 60 * per_second / (period / 2) <= MAX_BPM:
        # Tracked at twice the tempo, then every other beat kept (the way that
        # hits harder): with the eighths to hold on to, the beats don't slip
        # to the "and"s between them - a fast song said at half its tempo
        # kept to a thousandth of a BPM, measured, not a quarter of one.
        fine = _track(onset, period / 2)
        on, off = fine[0::2], fine[1::2]
        windows = on if len(off) == 0 or onset[on].mean() >= onset[off].mean() else off
    else:
        windows = _track(onset, period)
    if len(windows) < 2:
        return None
    # Where its onset is heard: a little after the window's centre.
    seconds = (windows * HOP + N_FFT / 2 + ONSET_LAG) / rate
    phase = max(range(BEATS_PER_BAR), key=lambda k: bass[windows[k::BEATS_PER_BAR]].sum())
    return {"bpm": round(60 * per_second / period, 1), "beats": [round(float(s), 4) for s in seconds],
            "bars": list(range(phase, len(windows), BEATS_PER_BAR))}
