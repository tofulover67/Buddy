#!/usr/bin/env python3
"""
Buddy's own audio effects - the ones Resolve's scripting can't reach (Fairlight
FX have no call), so they're baked into a new file (render.py). No Qt, no
Resolve: numpy only, tested directly.

numpy has no IIR filter, and a per-sample Python loop over minutes of audio is
out of the question, so everything happens in the frequency domain: the audio
is cut into overlapping windows (N_FFT samples, a new one every HOP), each
window's spectrum is multiplied by the chain's gains, and the windows are
added back together (weighted overlap-add). A square-root Hann window on the
way in and again on the way out sums to a constant at 75% overlap, so with
every gain at 1 what comes out is what went in, to float rounding. One pass
runs the whole chain: stacking effects doesn't stack windowing.

An effect's gain is either fixed per frequency (a high-pass, a notch, an EQ -
worked out once) or changes window by window (a de-esser, a noise gate - from
the spectrum itself). Either way it's magnitude only, so nothing is shifted
in time: a filter here has no phase delay, unlike its analogue original.

A chain is a list of {"id", "on", "strength" 0-1}, in the order they run -
what the page sends and render.py's sidecar keeps. EFFECTS is every effect
there is; clean_chain() makes a chain from the page safe to run.
"""

from __future__ import annotations

import numpy as np

N_FFT = 2048          # 43 ms at 48 kHz: 23 Hz a bin, fine enough for a hum notch
HOP = N_FFT // 4      # 75% overlap - what the square-root Hann pair needs
CHUNK_FRAMES = 256    # windows transformed at once: bounds the memory, not the result


def _window():
    # Periodic, not symmetric: that's the one whose overlaps sum flat.
    return np.sqrt(0.5 - 0.5 * np.cos(2.0 * np.pi * np.arange(N_FFT) / N_FFT))


def _rate_freqs(rate):
    return np.fft.rfftfreq(N_FFT, 1.0 / float(rate))


# --------------------------------------------------------------- effects --

class _Rumble:
    """A high-pass for rumble, handling noise and wind: a 4th-order (24 dB an
    octave) Butterworth's magnitude, from 50 Hz at no strength to 100 Hz at
    full. Default 0.6, so 80 Hz - under a voice's lowest notes."""

    def __init__(self, rate, strength):
        self.cutoff = 50.0 + 50.0 * strength
        f = _rate_freqs(rate)
        with np.errstate(divide="ignore"):
            ratio = np.where(f > 0, self.cutoff / np.maximum(f, 1e-9), np.inf)
        self._gain = 1.0 / np.sqrt(1.0 + ratio ** 8)

    def gain(self, _spectrum):
        return self._gain



# ---------------------------------------------------------------- analysis --
#
# Some effects need to know the recording first: its noise (the quietest
# stretches' spectrum) and whether it hums. analyse() finds out from the whole
# stretch being processed, before any window is changed - so Preview, which
# analyses the stretch it plays, can differ a little from Apply, which
# analyses the whole clip.

QUIET_SHARE = 0.1          # the quietest tenth of the windows is taken for the noise
HUM_FFT = 32768            # 1.5 Hz a bin at 48 kHz: fine enough to tell 50 from 60 Hz hum
HUM_HARMONICS = 6
HUM_FOUND_DB = 8.0         # a hum's harmonics stand this far above their neighbours
HUM_MAX_SEGMENTS = 120
_SILENT = 1e-14            # a window this quiet is digital silence, not noise


def _frames(x, size, hop):
    """Non-copying (windows, channels, size) views of x (frames, channels)."""
    if len(x) < size:
        x = np.concatenate((x, np.zeros((size - len(x), x.shape[1]))))
    return np.lib.stride_tricks.sliding_window_view(x, size, axis=0)[::hop]


def _hum(x, rate):
    """50 or 60 (Hz) when the recording hums at it, else None. Welch's average
    spectrum of the mono mix; each harmonic's peak against the median of its
    neighbourhood (4-12 Hz either side), the median over the harmonics."""
    mono = x.mean(axis=1)
    size = HUM_FFT if len(mono) >= HUM_FFT else max(4096, 1 << int(np.log2(max(len(mono), 2))))
    segments = _frames(mono[:, None], size, size)[:, 0, :]
    if len(segments) > HUM_MAX_SEGMENTS:
        segments = segments[np.linspace(0, len(segments) - 1, HUM_MAX_SEGMENTS).astype(int)]
    power = (np.abs(np.fft.rfft(segments * np.hanning(size), axis=-1)) ** 2).mean(axis=0)
    freqs = np.fft.rfftfreq(size, 1.0 / rate)
    best, best_score = None, HUM_FOUND_DB
    for f0 in (50.0, 60.0):
        scores = []
        for k in range(1, HUM_HARMONICS + 1):
            f = k * f0
            if f + 12 >= rate / 2:
                break
            peak = power[(freqs > f - 1.6) & (freqs < f + 1.6)]
            around = power[((freqs > f - 12) & (freqs < f - 4)) | ((freqs > f + 4) & (freqs < f + 12))]
            if len(peak) and len(around):
                scores.append(10 * np.log10((peak.max() + 1e-30) / (np.median(around) + 1e-30)))
        score = float(np.median(scores)) if scores else 0.0
        if score > best_score:
            best, best_score = f0, score
    return best


def analyse(samples, rate):
    """What the effects that adapt need to know: {"noise": the quietest
    windows' magnitude per bin (None when it's all silence), "noise_level":
    their mean energy, "hum": 50 / 60 / None}. Windows as process() makes
    them, but not overlapping - the noise is an average anyway."""
    x = np.asarray(samples, dtype=np.float64)
    if x.ndim == 1:
        x = x[:, None]
    window = _window()
    views = _frames(x, N_FFT, N_FFT)                               # (windows, channels, N_FFT)
    energy = np.empty(len(views))
    for first in range(0, len(views), CHUNK_FRAMES):
        spectrum = np.fft.rfft(views[first:first + CHUNK_FRAMES] * window, axis=-1)
        energy[first:first + CHUNK_FRAMES] = (np.abs(spectrum) ** 2).mean(axis=1).sum(axis=-1)
    audible = energy > _SILENT
    noise, level = None, 0.0
    if audible.any():
        cut = np.quantile(energy[audible], QUIET_SHARE)
        quiet = np.flatnonzero(audible & (energy <= cut))
        total = np.zeros(N_FFT // 2 + 1)
        for first in range(0, len(quiet), CHUNK_FRAMES):
            picked = views[quiet[first:first + CHUNK_FRAMES]]
            total += (np.abs(np.fft.rfft(picked * window, axis=-1)) ** 2).mean(axis=1).sum(axis=0)
        noise = np.sqrt(total / len(quiet))
        level = float(energy[quiet].mean())
    return {"noise": noise, "noise_level": level, "hum": _hum(x, rate) if len(x) else None}


# Gains that follow the recording window by window are smoothed over time:
# straight from window to window they flicker ("musical noise", a gate's
# chatter). One window is HOP samples: 10.7 ms at 48 kHz.

def _per_second(rate, seconds):
    """The share a smoothed value keeps from one window to the next, for it to
    get most of the way (1 - 1/e) to a new value in `seconds`."""
    return float(np.exp(-HOP / (float(rate) * max(seconds, 1e-6))))


def _smooth_bins(g, width=3):
    """A moving average across frequency (the last axis): no lone bin left standing."""
    kernel = np.ones(width) / width
    pad = width // 2
    padded = np.concatenate((g[..., :1].repeat(pad, -1), g, g[..., -1:].repeat(pad, -1)), axis=-1)
    out = np.zeros_like(g)
    for i in range(width):
        out += kernel[i] * padded[..., i:i + g.shape[-1]]
    return out


class _Denoise:
    """Noise reduction: each bin turned down by how much of it is the noise
    the quietest stretches have (a Wiener-style gain, over-subtracted by
    strength), never below a floor - 6 dB down at no strength, 24 at full - so
    what's left still sounds like the room, not underwater. Smoothed across
    frequency and, rising fast and falling slowly, over time."""

    analysis = True

    def __init__(self, rate, strength, analysis):
        self.noise = analysis.get("noise") if analysis else None
        self.over = 1.0 + 2.0 * strength
        self.floor = 10 ** (-(6.0 + 18.0 * strength) / 20)
        self.fall = _per_second(rate, 0.08)
        self._last = None

    def gain(self, spectrum):
        if self.noise is None:
            return 1.0
        power = np.abs(spectrum) ** 2
        g = np.sqrt(np.clip(1.0 - self.over * self.noise ** 2 / np.maximum(power, 1e-30), 0.0, 1.0))
        g = np.maximum(_smooth_bins(g), self.floor)
        out = np.empty_like(g)
        last = self._last if self._last is not None else g[:, 0]
        for w in range(g.shape[1]):
            last = np.maximum(g[:, w], last * self.fall + g[:, w] * (1 - self.fall))
            out[:, w] = last
        self._last = last
        return out


class _Hum:
    """Hum removal: at the hum's frequency and its harmonics (up to 1 kHz),
    the hum's own share taken out of each window - the same subtraction as
    noise reduction, but only there and harder - so a voice on those
    frequencies stays. 50 or 60 Hz, as analyse() heard it; nothing when it
    heard none."""

    analysis = True

    def __init__(self, rate, strength, analysis):
        self.f0 = (analysis or {}).get("hum")
        noise = (analysis or {}).get("noise")
        self._gain = None
        if self.f0 is None or noise is None:
            return
        f = _rate_freqs(rate)
        width = f[1]
        mask = np.zeros(len(f), dtype=bool)
        top = min(1000.0, rate / 2 - width)
        for k in range(1, int(top // self.f0) + 1):
            mask |= np.abs(f - k * self.f0) <= width
        self.mask = mask
        self.noise = noise[mask]
        self.over = 2.0 + 4.0 * strength
        self.floor = 10 ** (-(10.0 + 30.0 * strength) / 20)
        self._gain = np.ones(len(f))

    def gain(self, spectrum):
        if self._gain is None:
            return 1.0
        power = np.abs(spectrum[..., self.mask]) ** 2
        g = np.ones(spectrum.shape)
        g[..., self.mask] = np.maximum(np.sqrt(np.clip(
            1.0 - self.over * self.noise ** 2 / np.maximum(power, 1e-30), 0.0, 1.0)), self.floor)
        return g


class _DeEss:
    """De-esser: when the sibilance band (4.5-10 kHz) is louder than the body
    of the voice (300 Hz-4 kHz) by more than the threshold, that band is
    turned down by the excess - up to 6 dB at no strength, 16 at full. Down
    at once, back up over 60 ms."""

    def __init__(self, rate, strength):
        f = _rate_freqs(rate)
        self.band = (f >= 4500) & (f <= min(10000, rate / 2))
        self.body = (f >= 300) & (f < 4000)
        self.threshold_db = 3.0 - 9.0 * strength          # sibilance this much over the body counts
        self.most_db = 6.0 + 10.0 * strength
        # Soft edges: the band's gain fades in over half an octave either side.
        edge = np.clip(np.minimum(np.log2(np.maximum(f, 1) / 3200) / 0.5, np.log2(14000 / np.maximum(f, 1)) / 0.5),
                       0, 1)
        self.shape = np.where(self.band, 1.0, edge)
        self.rise = _per_second(rate, 0.06)
        self._last = None

    def gain(self, spectrum):
        power = np.abs(spectrum) ** 2
        s = power[..., self.band].sum(axis=-1)
        v = power[..., self.body].sum(axis=-1)
        excess = 10 * np.log10((s + 1e-30) / (v + 1e-30)) - self.threshold_db
        cut_db = np.clip(excess, 0.0, self.most_db) * (s > 1e-12)            # (channels, windows)
        smooth = np.empty_like(cut_db)
        last = self._last if self._last is not None else np.zeros(cut_db.shape[0])
        for w in range(cut_db.shape[1]):
            last = np.maximum(cut_db[:, w], last * self.rise)
            smooth[:, w] = last
        self._last = last
        return 10 ** (-(smooth[..., None] * self.shape) / 20)


class _Clarity:
    """Voice clarity, an EQ: mud at 300 Hz down (up to 4 dB), presence at
    4 kHz up (up to 4 dB) and air above 10 kHz up (up to 3 dB), each a
    smooth bell (or shelf) on a log scale, scaled by strength."""

    def __init__(self, rate, strength):
        f = np.maximum(_rate_freqs(rate), 1.0)
        bell = lambda fc, width: np.exp(-0.5 * (np.log2(f / fc) / width) ** 2)
        shelf = 1 / (1 + (10000 / f) ** 4)
        db = strength * (-4.0 * bell(300, 0.6) + 4.0 * bell(4000, 0.7) + 3.0 * shelf)
        self._gain = 10 ** (db / 20)

    def gain(self, _spectrum):
        return self._gain


class _Gate:
    """Gate: between the words - a window only a little louder than the
    noise analyse() heard - the sound goes down (6 dB at no strength, 30 at
    full), back up at once when the voice comes back, down over 60 ms after
    an 80 ms hold so word ends aren't cut off."""

    analysis = True

    def __init__(self, rate, strength, analysis):
        level = (analysis or {}).get("noise_level") or 0.0
        self.threshold = level * 10 ** (8.0 / 10) if level > 0 else None      # 8 dB over the noise
        self.range_db = 6.0 + 24.0 * strength
        self.step_db = self.range_db * HOP / (float(rate) * 0.06)        # all the way down in 60 ms, in dB
        self.hold = max(1, int(round(0.08 * rate / HOP)))
        self._last, self._held = 0.0, 0                                  # dB down

    def gain(self, spectrum):
        if self.threshold is None:
            return 1.0
        energy = (np.abs(spectrum) ** 2).mean(axis=0).sum(axis=-1)            # (windows,) - all channels as one
        out = np.empty(len(energy))
        last, held = self._last, self._held
        for w, e in enumerate(energy):
            if e > self.threshold:
                last, held = 0.0, self.hold
            elif held > 0:
                held -= 1
            else:
                last = min(self.range_db, last + self.step_db)
            out[w] = last
        self._last, self._held = last, held
        return 10 ** (-out[None, :, None] / 20)


# In the order they run. name: shown in the page; default: the strength a new
# chain gets; about: what the page says it does. An effect whose class has
# `analysis` is made with analyse()'s findings too: make(rate, strength, analysis).
EFFECTS = {
    "rumble": {"name": "Rumble filter", "default": 0.6, "make": _Rumble,
               "about": "Takes out low rumble, handling noise and wind under the voice."},
    "hum": {"name": "Hum remover", "default": 0.6, "make": _Hum,
            "about": "Finds 50 or 60 Hz mains hum and takes it and its harmonics out."},
    "denoise": {"name": "Noise reduction", "default": 0.5, "make": _Denoise,
                "about": "Learns the noise from the quietest moments and turns it down everywhere."},
    "deess": {"name": "De-esser", "default": 0.5, "make": _DeEss,
              "about": "Softens harsh S and T sounds."},
    "clarity": {"name": "Voice clarity", "default": 0.5, "make": _Clarity,
                "about": "Less mud, more presence and air – an EQ for speech."},
    "gate": {"name": "Gate", "default": 0.4, "make": _Gate,
             "about": "Turns the sound down between words, where only the noise is left."},
}


def _make(effect_id, rate, strength, analysis):
    make = EFFECTS[effect_id]["make"]
    return make(rate, strength, analysis) if getattr(make, "analysis", False) else make(rate, strength)


def needs_analysis(chain):
    """An effect that's on in a clean chain adapts to the recording."""
    return any(getattr(EFFECTS[e["id"]]["make"], "analysis", False) for e in active(chain))


def clean_chain(chain):
    """A chain from the page or a sidecar made safe: unknown ids and junk
    dropped, each effect at most once, strength clamped to 0-1."""
    out, seen = [], set()
    for entry in chain if isinstance(chain, list) else []:
        if not isinstance(entry, dict) or entry.get("id") not in EFFECTS or entry["id"] in seen:
            continue
        seen.add(entry["id"])
        try:
            strength = float(entry.get("strength", EFFECTS[entry["id"]]["default"]))
        except (TypeError, ValueError):
            strength = EFFECTS[entry["id"]]["default"]
        if not np.isfinite(strength):
            strength = EFFECTS[entry["id"]]["default"]
        out.append({"id": entry["id"], "on": bool(entry.get("on", True)),
                    "strength": round(min(1.0, max(0.0, strength)), 3)})
    return out


def active(chain):
    """The effects that are on, from a clean chain - none means nothing to render."""
    return [e for e in chain if e["on"]]


# ---------------------------------------------------------------- engine --

def process(samples, rate, chain, progress=None, cancelled=lambda: False, analysis=None):
    """samples (frames, channels) or (frames,), float, full scale 1.0 -> the
    same shape through the chain's effects that are on, float32. Returns None
    if cancelled. A value can come out over full scale (an EQ boost): that's
    render.py's to handle. analysis: analyse()'s, made here from these
    samples when an effect needs it and none is given."""
    x = np.asarray(samples, dtype=np.float64)
    mono = x.ndim == 1
    if mono:
        x = x[:, None]
    frames, channels = x.shape
    chain = clean_chain(chain)
    if analysis is None and needs_analysis(chain):
        analysis = analyse(x, rate)
    effects = [_make(e["id"], rate, e["strength"], analysis) for e in active(chain)]
    if not effects or not frames:
        out = x.astype(np.float32)
        return out[:, 0] if mono else out

    # Padded so every real sample sits under all four of its windows.
    lead = N_FFT - HOP
    count = -(-(frames + lead) // HOP)                 # windows needed to reach the end
    padded = np.zeros((count * HOP + N_FFT, channels))
    padded[lead:lead + frames] = x
    out = np.zeros_like(padded)
    window = _window()
    views = np.lib.stride_tricks.sliding_window_view(padded, N_FFT, axis=0)[::HOP]   # (windows, channels, N_FFT)
    scale = HOP / (N_FFT * 0.5)                        # the Hann overlaps sum to N_FFT / (2 HOP)
    per_phase = N_FFT // HOP

    for first in range(0, count, CHUNK_FRAMES):
        if cancelled():
            return None
        last = min(count, first + CHUNK_FRAMES)
        spectrum = np.fft.rfft(views[first:last] * window, axis=-1).transpose(1, 0, 2)   # (channels, windows, bins)
        for effect in effects:
            spectrum = spectrum * effect.gain(spectrum)
        chunk = np.fft.irfft(spectrum.transpose(1, 0, 2), n=N_FFT, axis=-1) * (window * scale)
        # Windows a whole N_FFT apart don't overlap: add each of the four sets in one go.
        for phase in range(per_phase):
            part = chunk[phase::per_phase]
            if not len(part):
                continue
            at = (first + phase) * HOP
            flat = part.transpose(0, 2, 1).reshape(-1, channels)
            out[at:at + len(flat)] += flat
        if progress:
            progress(last / count)

    result = out[lead:lead + frames].astype(np.float32)
    return result[:, 0] if mono else result
