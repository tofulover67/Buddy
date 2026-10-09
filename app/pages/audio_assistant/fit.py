#!/usr/bin/env python3
"""
Fit music to a length: a song made shorter (or longer) so that its own
ending lands exactly where the video ends - whole bars taken out (or played
again) at a place where the music sounds the same either side of the cut,
so the edit isn't heard. numpy only (no Qt, no Resolve), so it's tested on
made-up music; beats.py finds the beats and bars.

    song_end(samples, rate)                 seconds: where the music stops (its silent tail off)
    plans(samples, rate, want_s, found)     the ways to do it, best first
    build(samples, rate, plan)              the fitted song, as samples
    save(source, start_s, channels, plan, samples, rate)   ... as a WAV with its sidecar

A plan is one jump: play up to beat `a`, then carry on from beat `b`. A jump
forward (b after a) takes out the bars between; one back (b before a) plays
them again - `times` over, for a long way to go. Both beats are the first of
a bar, so the music keeps its count. Where it jumps is chosen by how alike
the music is around the two beats - BEATS_AROUND beats either side, each
beat's harmony (a chroma: how much of each of the 12 notes) and tone (the
level in a few bands) - and by how near the result comes to the length
wanted. Whole bars can't make every length: what's left over is made up
around it - first a moment of silence after the song ends (up to END_ROOM_S),
then the song starting a moment late (silence first), or, by up to a beat,
a moment into its intro (faded in). Its ending is never touched: the fitted
song is built back from its last sound.

The jump is then fine-tuned on the waveform (to the sample where the two
sides line up best, within ALIGN_S) and crossfaded over XFADE_S, a moment
before the beat so the new bar's first hit comes in whole.
"""

import os
from datetime import datetime

import numpy as np

from core.atomic_io import write_json

from . import render
from .beats import BEATS_PER_BAR

N_FFT = 4096
HOP = 2048
BEATS_AROUND = 4
XFADE_S = 0.03
LEAD_S = 0.01            # the crossfade ends this long before the beat
ALIGN_S = 0.012
FADE_IN_S = 0.02
QUIET_DB = -60.0         # the song's tail under this (50 ms RMS) is silence
END_ROOM_S = 0.5         # silence after the song's end, at the most, before it starts late instead
TAIL_TRIM_S = 0.05        # off the very end of its last sound (below QUIET_DB by then), rather than its start
MOST_TIMES = 8
KEEP_BARS = 1            # bars kept whole at the start and end, at the least
MOST_PLANS = 5
END_COST = 0.02          # per beat of silence after the end
LATE_COST = 0.08         # per beat the song starts late
REPEAT_COST = 0.02       # per time a stretch is played again past the first
EARLY_COST = 0.25        # per beat it starts into its intro
PHRASE_BONUS = 0.03      # a jump of whole 4-bar phrases
SEAMLESS, SMOOTH = 0.12, 0.25     # how alike the cut's two sides are: "seamless", "smooth", else "may be heard"


def _mono(samples):
    x = np.asarray(samples, dtype=np.float64)
    return x.mean(axis=1) if x.ndim == 2 else x


def song_end(samples, rate):
    """Seconds from the start where the music stops: the end of the last
    50 ms over QUIET_DB - or the whole length when it never goes quiet."""
    mono = _mono(samples)
    step = max(1, int(rate * 0.05))
    n = len(mono) // step
    if not n:
        return len(mono) / rate
    rms = np.sqrt(np.mean(mono[:n * step].reshape(n, step) ** 2, axis=1))
    loud = np.flatnonzero(rms > 10 ** (QUIET_DB / 20))
    if not len(loud):
        return 0.0
    return min(len(mono) / rate, (loud[-1] + 1) * step / rate)


def _features(mono, rate, beats):
    """(chroma, tone) per beat: unit-length chroma (12) and the level in
    six bands (dB, each band's spread across the song taken out)."""
    if len(mono) < N_FFT:
        mono = np.concatenate((mono, np.zeros(N_FFT - len(mono))))
    frames = np.lib.stride_tricks.sliding_window_view(mono, N_FFT)[::HOP]
    window = np.hanning(N_FFT)
    freqs = np.fft.rfftfreq(N_FFT, 1.0 / rate)
    pitched = (freqs >= 55) & (freqs <= 5000)
    pitch_class = np.zeros(len(freqs), dtype=np.int64)
    pitch_class[pitched] = np.round(12 * np.log2(freqs[pitched] / 440.0) + 69).astype(np.int64) % 12
    edges = [0, 150, 400, 1000, 2500, 6000, rate / 2 + 1]
    bands = np.searchsorted(edges, freqs, side="right") - 1
    chroma = np.zeros((len(frames), 12))
    tone = np.zeros((len(frames), 6))
    for first in range(0, len(frames), 256):
        power = np.abs(np.fft.rfft(frames[first:first + 256] * window, axis=-1)) ** 2
        for k in range(12):
            chroma[first:first + len(power), k] = power[:, pitched & (pitch_class == k)].sum(axis=1)
        for k in range(6):
            tone[first:first + len(power), k] = power[:, bands == k].sum(axis=1)
    centre = (np.arange(len(frames)) * HOP + N_FFT / 2) / rate
    bounds = list(beats) + [beats[-1] + (beats[-1] - beats[-2] if len(beats) > 1 else 0.5)]
    at = np.searchsorted(centre, bounds)
    c_out, t_out = np.zeros((len(beats), 12)), np.zeros((len(beats), 6))
    for i in range(len(beats)):
        a, b = at[i], max(at[i] + 1, at[i + 1])
        c = np.sqrt(chroma[a:b].mean(axis=0)) if b <= len(frames) and a < len(frames) else np.zeros(12)
        norm = np.linalg.norm(c)
        c_out[i] = c / norm if norm > 0 else c
        t_out[i] = 10 * np.log10(np.maximum(tone[a:b].mean(axis=0), 1e-12)) if a < len(frames) else -120.0
    spread = t_out.std(axis=0)
    t_out = (t_out - t_out.mean(axis=0)) / np.where(spread > 1e-9, spread, 1.0)
    return c_out, t_out


def _unlike(chroma, tone, a, b):
    """How unlike the music is around beats a and b: 0 the same, ~1 nothing alike."""
    n = len(chroma)
    js = [j for j in range(-BEATS_AROUND, BEATS_AROUND) if 0 <= a + j < n and 0 <= b + j < n]
    if not js:
        return 1.0
    ca, cb = chroma[[a + j for j in js]], chroma[[b + j for j in js]]
    harmony = 1.0 - np.mean(np.sum(ca * cb, axis=1))
    level = np.mean(np.abs(tone[[a + j for j in js]] - tone[[b + j for j in js]])) / 4.0
    return float(min(1.0, 0.6 * harmony + 0.4 * min(1.0, level)))


def _fit(song_s, want_s, change_s, beat_s, times):
    """(start_s, end_s, cost of what's left over) - start_s: where in the
    result the song starts (> 0: late, after silence) or, < 0, how far into
    the song it starts; end_s: the silence after its end. None when what's
    left over is too much to make up. end_s < 0: that much of the end of
    the last sound goes (TAIL_TRIM_S at the most)."""
    left = want_s - (song_s + times * change_s)          # > 0: the song is short of it
    if left >= 0:
        end = min(left, END_ROOM_S)
        late = left - end
        if late > BEATS_PER_BAR * beat_s * 1.01:
            return None
        return late, end, (END_COST * end + LATE_COST * late) / beat_s
    if -left <= TAIL_TRIM_S:                 # the last of its fading tail: not heard
        return 0.0, left, END_COST * -left / beat_s
    return (left, 0.0, EARLY_COST * -left / beat_s) if -left <= beat_s else None


def plans(samples, rate, want_s, found, song_s=None):
    """The ways to fit the song (samples from where the clip starts in its
    file) into want_s seconds, best first: [{"a", "b" (seconds into
    samples: play to a, carry on from b; None for no jump), "times",
    "start_s" (> 0: the song starts that late; < 0: that far into it),
    "end_s" (the silence after it ends), "length_s" (want_s), "song_s",
    "bars" (how many are taken out or played again),
    "removed_s" (> 0 taken out, < 0 added), "unlike", "sound" ("seamless" |
    "smooth" | "heard")}]. `found`: beats.find()'s. [] when it can't be
    done (too short to keep a bar either end, or no beat)."""
    if not found or len(found["bars"]) < 2 * KEEP_BARS + 2:
        return []
    song_s = song_end(samples, rate) if song_s is None else song_s
    beats_s = np.array(found["beats"], dtype=np.float64)
    beat_s = float(np.median(np.diff(beats_s))) if len(beats_s) > 1 else 0.5
    bars = [i for i in found["bars"] if beats_s[i] < song_s]
    if len(bars) < 2 * KEEP_BARS + 2:
        return []
    chroma, tone = _features(_mono(samples), rate, beats_s)
    first_ok, last_ok = bars[KEEP_BARS], bars[-1 - KEEP_BARS]
    out = []
    plain = _fit(song_s, want_s, 0.0, beat_s, 0)           # no jump: only the start moved
    if plain is not None:
        out.append((plain[2], None, None, 0, plain[:2], 0.0))
    longer = want_s > song_s
    for a in bars:
        for b in bars:
            if a == b or not (first_ok <= a <= last_ok and first_ok <= b <= last_ok) or longer != (b < a):
                continue
            change = beats_s[a] - beats_s[b]                     # < 0 for a jump forward
            if longer:
                about = (want_s - song_s) / change
                choices = {max(1, int(np.floor(about))), max(1, int(np.ceil(about)))}
            else:
                choices = {1}
            for times in sorted(choices):
                if times > MOST_TIMES:
                    continue
                fit = _fit(song_s, want_s, change, beat_s, times)
                if fit is None:
                    continue
                unlike = _unlike(chroma, tone, a, b)
                bars_moved = abs(b - a) // BEATS_PER_BAR
                score = unlike + fit[2] + REPEAT_COST * (times - 1) - (PHRASE_BONUS if bars_moved % 4 == 0 else 0.0)
                out.append((score, a, b, times, fit[:2], unlike))
    out.sort(key=lambda o: o[0])
    chosen = []
    for option in out:
        _score, a, b = option[:3]
        # Different enough from the ones kept: not the same cut a bar or so along.
        if a is not None and any(c[1] is not None and abs(a - c[1]) < 2 * BEATS_PER_BAR
                                 and abs(b - c[2]) < 2 * BEATS_PER_BAR for c in chosen):
            continue
        chosen.append(option)
        if len(chosen) >= MOST_PLANS:
            break
    plans_ = []
    for _score, a, b, times, (start_s, end_s), unlike in chosen:
        change = 0.0 if a is None else float(beats_s[a] - beats_s[b])
        plans_.append({
            "a": None if a is None else round(float(beats_s[a]), 4),
            "b": None if b is None else round(float(beats_s[b]), 4),
            "times": times, "start_s": round(float(start_s), 4), "end_s": round(float(end_s), 4),
            "length_s": round(float(want_s), 4),
            "song_s": round(float(song_s), 4), "removed_s": round(-change * times, 4),
            "bars": 0 if a is None else abs(b - a) // BEATS_PER_BAR,
            "unlike": round(unlike, 3),
            "sound": "seamless" if unlike < SEAMLESS else "smooth" if unlike < SMOOTH else "heard"})
    return plans_


def _align(mono, rate, a, b):
    """b moved (by up to ALIGN_S) to where the waveform after it best
    carries on from what's before a: seconds."""
    w = int(rate * 0.05)
    reach = int(rate * ALIGN_S)
    ia, ib = int(round(a * rate)), int(round(b * rate))
    if ia - w < 0 or ib - w - reach < 0 or ia + w > len(mono) or ib + w + reach > len(mono):
        return b
    ref = mono[ia - w:ia + w]
    if not np.any(ref):
        return b
    best, best_d = -np.inf, 0
    for d in range(-reach, reach + 1):
        seg = mono[ib - w + d:ib + w + d]
        denom = np.linalg.norm(seg) * np.linalg.norm(ref)
        if denom <= 0:
            continue
        score = float(np.dot(seg, ref) / denom)
        if score > best:
            best, best_d = score, d
    return (ib + best_d) / rate


def segments(plan, samples=None, rate=None):
    """[(from_s, to_s)] of the song, in order, that make the fitted song -
    the jump fine-tuned on the waveform when samples are given."""
    a, b = plan["a"], plan["b"]
    first = max(0.0, -plan["start_s"])
    end = plan["song_s"]
    if a is None:
        return [(first, end)]
    if samples is not None:
        b = _align(_mono(samples), rate, a, b)
    if b > a:
        return [(first, a), (b, end)]
    return [(first, a)] + [(b, a)] * (plan["times"] - 1) + [(b, end)]


def build(samples, rate, plan):
    """The fitted song: plan["length_s"] long exactly, (frames, channels)
    float - silence first when it starts late, each jump crossfaded."""
    x = np.asarray(samples, dtype=np.float32)
    if x.ndim == 1:
        x = x[:, None]
    parts = segments(plan, x, rate)
    xf = max(2, int(round(XFADE_S * rate)))
    lead = int(round(LEAD_S * rate))
    half = xf // 2
    up = np.sin(np.linspace(0, np.pi / 2, xf))[:, None].astype(np.float32)
    down = np.cos(np.linspace(0, np.pi / 2, xf))[:, None].astype(np.float32)
    out = None
    for i, (s, e) in enumerate(parts):
        si, ei = int(round(s * rate)), int(round(e * rate))
        # Each cut sits LEAD_S before its beat; the crossfade is centred on it.
        if i > 0:
            si -= lead
        if i < len(parts) - 1:
            ei -= lead
        lo = max(0, si - (half if i > 0 else 0))
        hi = min(len(x), ei + (xf - half if i < len(parts) - 1 else 0))
        piece = x[lo:hi].copy()
        if out is None:
            out = piece
            continue
        n = min(xf, len(out), len(piece))
        mixed = out[-n:] * down[-n:] + piece[:n] * up[:n]
        out = np.concatenate((out[:-n], mixed, piece[n:]))
    out = out if out is not None else np.zeros((0, x.shape[1]), dtype=np.float32)
    # Placed back from its end - the song's last sound end_s before the
    # length's - so the fine-tuning's few samples come off the start, never the end.
    want = int(round(plan["length_s"] * rate))
    at = want - int(round(plan.get("end_s", 0.0) * rate)) - len(out)
    cut = plan["start_s"] < 0 or at < 0           # it starts into the song: faded in
    if at < 0:
        out = out[-at:]
        at = 0
    if cut:
        n = min(len(out), int(round(FADE_IN_S * rate)))
        out[:n] *= np.linspace(0, 1, n, dtype=np.float32)[:, None]
    result = np.zeros((want, x.shape[1]), dtype=np.float32)
    result[at:at + len(out)] = out[:want - at]
    return result


def jump_at(plan):
    """Seconds into the fitted song where its (first) jump is heard - or,
    with no jump, where the song starts."""
    if plan["a"] is None:
        return max(0.0, plan["start_s"])
    return max(0.0, plan["start_s"]) + plan["a"] - max(0.0, -plan["start_s"])


def _kept(plan):
    return {key: plan[key] for key in ("a", "b", "times", "start_s", "end_s", "length_s", "song_s", "bars")}


def save(source_path, start_s, channels, plan, samples, rate, project=None,
         fallback=render.FALLBACK_DIR, writable=render._writable):
    """The fitted song (build()) as a WAV where render.py puts processed
    audio - beside the source, in "Buddy Audio" - with a sidecar that says
    it's a fit ("fit": the plan), so it's known again (resolve_ext._fx_of),
    tidied like any of Buddy's WAVs, and the same fit is never made twice.
    start_s: where the clip starts in its file (samples start there).
    (result, None) or (None, why); result: {"path", "rate", "frames",
    "channels", "beside", "clipped", "reused", "source"}."""
    try:
        source = render._source(source_path)
    except OSError:
        return None, "The clip's file is offline."
    channels = [int(c) for c in channels] if channels else None
    end_s = start_s + plan["song_s"]
    folder, beside = render.output_folder(source_path, fallback, writable)
    path = os.path.join(folder, render.file_name(source, start_s, end_s, [{"fit": _kept(plan)}], channels, 0.0))
    known = render.read_sidecar(path) if os.path.isfile(path) else None
    if known and known.get("source") == source and known.get("fit") == _kept(plan):
        render._claim(path, known, project)
        return {"path": path, "rate": known["rate"], "frames": known["frames"], "channels": known["channels"],
                "beside": beside, "clipped": known.get("clipped", 0), "reused": True, "source": source_path}, None
    fitted = build(samples, rate, plan)
    try:
        clipped = render.write_wav(path, fitted, rate)
    except (OSError, ValueError) as exc:
        return None, f"Couldn't write the fitted music: {exc}"
    info = {"version": render.SIDECAR_VERSION, "source": source, "range": [start_s, end_s], "file_start": 0.0,
            "rate": int(rate), "frames": int(len(fitted)), "channels": int(fitted.shape[1]),
            "channels_used": channels, "chain": [], "fit": _kept(plan), "clipped": clipped,
            "projects": [project] if project else [], "made": datetime.now().isoformat(timespec="seconds")}
    try:
        write_json(render.sidecar_path(path), info)
    except OSError:
        pass
    return {"path": path, "rate": int(rate), "frames": int(len(fitted)), "channels": int(fitted.shape[1]),
            "beside": beside, "clipped": clipped, "reused": False, "source": source_path}, None
