#!/usr/bin/env python3
"""
Audit's sound check: the timeline's mix, as it would be delivered, measured
for its peaks, clipping and loudness.

Resolve can't hand a script its mix, so it's rendered - Transcribe's
render_timeline_audio (an audio-only 16-bit QuickTime; the person's Deliver
settings, render mode and render queue are put back as they were) - then
decoded here (Audio Assistant's decoder, peaks._stream) and measured.
Checked live on Resolve 21.1 (2026-10-06) against ffmpeg's ebur128 on the
same render: loudness and true peak agree to 0.05.

  true peak   each sample's level, and where it's loud, the level between
              samples too - the signal 4x oversampled, as BS.1770 measures
              true peak (a windowed-sinc filter of OVERSAMPLE x TAPS taps).
  clipping    CLIP_RUN samples or more in a row at full scale: the mix went
              over and was cut off (a render can't hold more than full scale).
  loudness    BS.1770 integrated, the same as Audio Assistant's
              (peaks.LoudnessAccumulator + integrated_lufs).

measure() keeps only what checks.py needs - each frame where it peaks
above KEEP_DB, where it clips, the loudness and the loudest peak - so the
target can change without measuring again. Qt is needed only to decode.
"""

import os

import numpy as np

from pages.audio_assistant.peaks import LoudnessAccumulator, _stream, integrated_lufs

# Delivery targets: integrated loudness, and the true peak not to pass.
# "strict": a broadcaster rejects a mix off target; a streaming service
# just turns it up or down (but never up past its ceiling).
TARGETS = [
    {"id": "streaming", "label": "Online video (YouTube, Spotify): −14 LUFS", "lufs": -14.0, "ceiling": -1.0,
     "strict": False},
    {"id": "podcast", "label": "Podcasts and Apple: −16 LUFS", "lufs": -16.0, "ceiling": -1.0, "strict": False},
    {"id": "ebu", "label": "Broadcast, EBU R128: −23 LUFS", "lufs": -23.0, "ceiling": -1.0, "strict": True},
    {"id": "atsc", "label": "Broadcast, US ATSC A/85: −24 LUFS", "lufs": -24.0, "ceiling": -2.0, "strict": True},
]
TARGET_BY_ID = {t["id"]: t for t in TARGETS}
DEFAULT_TARGET = "streaming"
# Loudness this close to the target is on it (EBU R128's own tolerance).
TOLERANCE_LU = 1.0

# Frames peaking above this are kept: under every target's ceiling, so any
# target can be checked against one measurement.
KEEP_DB = -3.0
# Only buffers with a sample above this are oversampled: what lies between
# samples can't be more than a few dB over the samples around it.
OVERSAMPLE_FROM_DB = KEEP_DB - 6.0
OVERSAMPLE = 4
TAPS = 12                  # per phase
CLIP_LEVEL = 0.9999        # full scale, give or take the last bit of a 16-bit sample
CLIP_RUN = 3
# The most frames measure() keeps of each kind: a mix that's loud all through
# needs no more to say so.
MAX_FRAMES = 20000


def _filter():
    """The oversampling filter's phases: (OVERSAMPLE, TAPS), each phase
    summing to 1 - a Kaiser-windowed sinc cut at the original Nyquist."""
    n = OVERSAMPLE * TAPS
    t = (np.arange(n) - (n - 1) / 2) / OVERSAMPLE
    h = np.sinc(t) * np.kaiser(n, 8.0)
    phases = h.reshape(TAPS, OVERSAMPLE).T[:, ::-1]
    return phases / phases.sum(axis=1, keepdims=True)


_PHASES = _filter()


def _db(amplitude):
    return float(20.0 * np.log10(max(float(amplitude), 1e-12)))


class Meter:
    """Folds decoded buffers, in order, into what measure() returns."""

    def __init__(self, fps):
        self.fps = float(fps or 24.0)
        self.loud = LoudnessAccumulator()
        self.rate = None
        self.channels = 0
        self._seen = 0                   # sample frames so far
        self._history = None             # the last TAPS - 1 samples, for the filter
        self._runs = None                # each channel's full-scale run going into this buffer
        self.peak = 0.0
        self.frame_peaks = {}            # frame -> loudest amplitude, where above KEEP_DB
        self.clipped = set()             # frames where it clips

    def add(self, samples, rate):
        samples = np.asarray(samples, dtype=np.float32)
        if samples.ndim == 1:
            samples = samples[:, None]
        n = len(samples)
        if not n or not rate:
            return
        if self.rate is None:
            self.rate, self.channels = int(rate), samples.shape[1]
            self._history = np.zeros((TAPS - 1, self.channels), dtype=np.float32)
            self._runs = np.zeros(self.channels, dtype=np.int64)
        if samples.shape[1] != self.channels:      # a decoder that changes mid-file: as peaks._fit
            fixed = np.zeros((n, self.channels), dtype=np.float32)
            keep = min(self.channels, samples.shape[1])
            fixed[:, :keep] = samples[:, :keep]
            samples = fixed
        self.loud.add(samples, rate)
        level = np.abs(samples).max(axis=1)
        if level.max() >= 10 ** (OVERSAMPLE_FROM_DB / 20):
            level = np.maximum(level, self._between(samples))
        self._history = np.concatenate((self._history, samples))[-(TAPS - 1):]
        self._clips(samples)
        self.peak = max(self.peak, float(level.max()))
        self._keep(level)
        self._seen += n

    def _between(self, samples):
        """The level between each sample and the next few, oversampled: (n,).
        The filter's delay (half its length) is under a millisecond - left in."""
        x = np.concatenate((self._history, samples))
        out = np.zeros(len(samples), dtype=np.float32)
        for ch in range(self.channels):
            for phase in _PHASES[1:]:
                y = np.convolve(x[:, ch], phase, mode="valid")
                np.maximum(out, np.abs(y[:len(samples)]), out=out)
        return out

    def _frames(self, offsets):
        return ((self._seen + np.asarray(offsets, dtype=np.int64)) * self.fps / self.rate).astype(np.int64)

    def _keep(self, level):
        loud = np.flatnonzero(level >= 10 ** (KEEP_DB / 20))
        if not len(loud) or len(self.frame_peaks) >= MAX_FRAMES:
            return
        frames = self._frames(loud)
        for frame, value in zip(frames.tolist(), level[loud].tolist()):
            if value > self.frame_peaks.get(frame, 0.0):
                self.frame_peaks[frame] = value

    def _clips(self, samples):
        """Marks the frame of each run of CLIP_RUN+ full-scale samples. A run
        going on from the last buffer counts what it had there."""
        full = np.abs(samples) >= CLIP_LEVEL
        for ch in range(self.channels):
            col = full[:, ch]
            if not col.any():
                self._runs[ch] = 0
                continue
            edges = np.diff(np.concatenate(([0], col.astype(np.int8), [0])))
            starts, ends = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
            lengths = ends - starts
            if starts[0] == 0:
                lengths[0] += self._runs[ch]
            long_enough = starts[lengths >= CLIP_RUN]
            if len(long_enough) and len(self.clipped) < MAX_FRAMES:
                self.clipped.update(self._frames(long_enough).tolist())
            self._runs[ch] = lengths[-1] if ends[-1] == len(col) else 0

    def result(self):
        energies = self.loud.per_channel().sum(axis=1) if self.rate else np.zeros(0)
        lufs = integrated_lufs(energies) if len(energies) else None
        return {
            "lufs": None if lufs is None else round(float(lufs), 2),
            "peak_db": round(_db(self.peak), 2) if self.rate else None,
            "seconds": round(self._seen / self.rate, 2) if self.rate else 0.0,
            "peaks": sorted([f, round(_db(v), 2)] for f, v in self.frame_peaks.items()),
            "clipped": sorted(self.clipped),
        }


def measure(path, fps, progress=None, cancelled=lambda: False):
    """(result, None) or (None, why) for a rendered mix: its loudness, its
    loudest true peak, and where it peaks above KEEP_DB or clips - frames
    from the render's start (the timeline's first frame). Runs its own event
    loop to decode, so it belongs on a worker thread."""
    meter = Meter(fps)
    error = _stream(path, meter.add, progress, cancelled)
    if error:
        return None, error
    if meter.rate is None:
        return None, "The render has no sound Buddy can read."
    return meter.result(), None


def remove(path):
    try:
        os.remove(path)
    except OSError:
        pass
