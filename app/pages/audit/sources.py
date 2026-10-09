#!/usr/bin/env python3
"""
Audit's recording checks: each audio clip's own stretch of its file,
listened to for what went wrong when it was recorded - what a check of the
mix can't tell apart from the mix, and what no setting in Resolve puts
right. Made with Check sound, after the mix (audio.py).

  clipped    the recording was too loud and is cut off: CLIP_RUN samples
             in a row at full scale, or FLAT_RUN at one level (a recording
             clipped, then turned down)
  dropout    digital silence (every channel under SILENT) for DROPOUT_S to
             DROPOUT_MOST_S, with sound either side: the recorder lost samples
  one side   a stereo clip on a stereo track with sound on one side only
             (a lav on channel 1, nothing on 2): heard on one side
  phase      left and right cancel out (correlation under PHASE_LIMIT):
             played in mono - a phone, a TV - it nearly vanishes
  pops       a burst of low rumble (20-90 Hz) louder than the voice's own
             band: a "p" too close to the mic, the mic knocked
  wind       the same rumble held for WIND_S or more: wind, or handling
             Speech only (POP_SPEECH): music has its bass on purpose.

Each file is decoded once, from the top (QAudioDecoder can't seek) up to
the end of the last clip that uses it, and each buffer handed to the clips
it falls in (Listener). Qt is needed only to decode.
"""

import os

import numpy as np

from pages.audio_assistant.peaks import _stream

from .checks import stood_in

CLIP_LEVEL = 0.9999
CLIP_RUN = 3
FLAT_RUN = 8
FLAT_FROM = 0.3              # a flat run counts only this loud
SILENT = 1e-6
DROPOUT_S = 0.008
DROPOUT_MOST_S = 0.25        # longer digital silence is meant: an edit, a gate
AROUND_S = 0.1               # a dropout has sound this close either side
AROUND_DB = -70.0            # ... this loud (RMS): a room's own noise is
SOUND_DB = -50.0             # a clip with sound in it
BLOCK_S = 0.05               # the level is kept per this long
ONE_SIDE_DB = 30.0
PHASE_LIMIT = -0.5
FFT = 1024
LOW_HZ, VOICE_HZ = (20.0, 90.0), (300.0, 3000.0)
POP_OVER_DB = 6.0            # the low band this far over the voice band
POP_FROM_DB = 12.0           # ... and no more than this under the clip's loud level
POP_MOST_S = 0.15            # longer than this, it's wind
WIND_OVER_DB = 3.0
WIND_FROM_DB = 20.0
WIND_S = 1.0
POP_SPEECH = 0.15            # a clip is speech when this much of it is pauses (15 dB under its loud level)
MOST_EVENTS = 200            # per check per clip


def _runs(mask):
    """[(start, end)] of the True runs, end exclusive."""
    if not len(mask):
        return []
    edges = np.diff(np.concatenate(([0], mask.astype(np.int8), [0])))
    return list(zip(np.flatnonzero(edges == 1).tolist(), np.flatnonzero(edges == -1).tolist()))


class Listener:
    """One clip's stretch of its file, fed in order (add) - what it found
    once it's all in (result)."""

    def __init__(self, start_s, end_s, stereo_track):
        self.start_s, self.end_s = start_s, end_s
        self.stereo_track = stereo_track
        self.rate = None
        self.seen = 0                      # samples of the clip so far
        self.clipped, self.drops = [], []  # (first, last) seconds into the clip
        self._full = None                  # each channel's full-scale run going into this buffer
        self._flat = None                  # (value, run) per channel
        self._quiet = 0                    # the silent run going into this buffer
        self.energy = None                 # per channel
        self.cross = 0.0                   # sum of left x right
        self.blocks = []                   # RMS per BLOCK_S, all channels
        self._block = None
        self.low, self.voice = [], []      # dB per FFT, the two bands
        self._fft = None

    def add(self, samples):
        x = np.asarray(samples, dtype=np.float64)
        n = len(x)
        if not n:
            return
        ch = x.shape[1]
        if self.energy is None:
            self.energy = np.zeros(ch)
            self._full = np.zeros(ch, dtype=np.int64)
            self._flat = [(None, 0)] * ch
            self._block = np.zeros((0, ch))
            self._fft = np.zeros(0)
        self.energy += (x * x).sum(axis=0)
        if ch >= 2:
            self.cross += float(np.dot(x[:, 0], x[:, 1]))
        self._clipping(x)
        self._dropouts(x)
        self._levels(x)
        self.seen += n

    def _at(self, i):
        return (self.seen + i) / self.rate

    def _clipping(self, x):
        for c in range(x.shape[1]):
            col = x[:, c]
            # Full scale.
            full = np.abs(col) >= CLIP_LEVEL
            if full.any():
                runs = _runs(full)
                for a, b in runs:
                    length = b - a + (self._full[c] if a == 0 else 0)
                    if length >= CLIP_RUN:
                        self._mark(self.clipped, self._at(a), self._at(b))
                self._full[c] = (runs[-1][1] - runs[-1][0] + (self._full[c] if runs[-1][0] == 0 else 0)
                                 if runs[-1][1] == len(col) else 0)
            else:
                self._full[c] = 0
            # Flat at one level: the same value FLAT_RUN times in a row, loud.
            value, run = self._flat[c]
            same = np.concatenate(([col[0] == value if value is not None else False], col[1:] == col[:-1]))
            loud = np.abs(col) >= FLAT_FROM
            flat = same & loud
            runs = _runs(flat)
            for a, b in runs:
                length = b - a + 1 + (run if a == 0 else 0)
                if length >= FLAT_RUN:
                    self._mark(self.clipped, self._at(a), self._at(b))
            if runs and runs[-1][1] == len(col):
                a, b = runs[-1]
                self._flat[c] = (col[-1], b - a + (run if a == 0 else 0))
            else:
                self._flat[c] = (col[-1], 0)

    def _dropouts(self, x):
        silent = (np.abs(x) <= SILENT).all(axis=1)
        runs = _runs(silent)
        carry = self._quiet
        for a, b in runs:
            first = a - (carry if a == 0 else 0)
            if b == len(silent):
                self._quiet = b - first
                break
            if DROPOUT_S <= (b - first) / self.rate <= DROPOUT_MOST_S:
                self.drops.append((self._at(first), self._at(b)))
        else:
            self._quiet = 0

    def _levels(self, x):
        step = int(self.rate * BLOCK_S)
        self._block = np.concatenate((self._block, x))
        whole = len(self._block) // step
        if whole:
            part = self._block[:whole * step].reshape(whole, step, -1)
            self.blocks += np.sqrt((part ** 2).mean(axis=(1, 2))).tolist()
            self._block = self._block[whole * step:]
        mono = x.mean(axis=1)
        self._fft = np.concatenate((self._fft, mono))
        whole = len(self._fft) // FFT
        if whole:
            frames = self._fft[:whole * FFT].reshape(whole, FFT) * np.hanning(FFT)
            power = np.abs(np.fft.rfft(frames, axis=1)) ** 2
            freqs = np.fft.rfftfreq(FFT, 1.0 / self.rate)
            band = lambda lo_hi: power[:, (freqs >= lo_hi[0]) & (freqs < lo_hi[1])].sum(axis=1)
            self.low += (10 * np.log10(np.maximum(band(LOW_HZ), 1e-20))).tolist()
            self.voice += (10 * np.log10(np.maximum(band(VOICE_HZ), 1e-20))).tolist()
            self._fft = self._fft[whole * FFT:]

    @staticmethod
    def _mark(events, first, last):
        if events and first - events[-1][1] <= 0.5:
            events[-1] = (events[-1][0], max(events[-1][1], last))
        elif len(events) < MOST_EVENTS:
            events.append((first, last))

    def result(self):
        """{"clipped", "dropouts", "pops", "wind": [(first, last) seconds
        into the clip]; "side": "left" | "right" | None; "phase": the
        left-right correlation when it's under PHASE_LIMIT, else None}."""
        out = {"clipped": self.clipped, "dropouts": [], "pops": [], "wind": [], "side": None, "phase": None}
        if self.rate is None:
            return out
        blocks = np.array(self.blocks)
        with np.errstate(divide="ignore"):
            level = 20 * np.log10(np.maximum(blocks, 1e-12))
        for first, last in self.drops:
            # The blocks round it, the ones it starts and ends in too.
            before = level[max(0, int((first - AROUND_S) / BLOCK_S)):max(0, int(first / BLOCK_S) + 1)]
            after = level[min(len(level), int(last / BLOCK_S)):min(len(level), int((last + AROUND_S) / BLOCK_S) + 1)]
            if len(before) and len(after) and before.max() > AROUND_DB and after.max() > AROUND_DB:
                self._mark(out["dropouts"], first, last)
        if len(self.energy) == 2 and self.stereo_track:
            left, right = self.energy[0], self.energy[1]
            loud = max(left, right) / max(1, self.seen)
            if loud > 10 ** (SOUND_DB / 10):
                gap = 10 * np.log10(max(left, 1e-30) / max(right, 1e-30))
                if gap > ONE_SIDE_DB:
                    out["side"] = "left"
                elif gap < -ONE_SIDE_DB:
                    out["side"] = "right"
                elif left > 0 and right > 0:
                    corr = self.cross / np.sqrt(left * right)
                    if corr < PHASE_LIMIT:
                        out["phase"] = round(float(corr), 2)
        out["pops"], out["wind"] = self._rumble(level)
        return out

    def _rumble(self, level):
        """(pops, wind) - on speech only."""
        low, voice = np.array(self.low), np.array(self.voice)
        if not len(low) or not len(level):
            return [], []
        top = np.percentile(level, 90)
        if top < SOUND_DB or np.mean(level < top - 15) < POP_SPEECH:
            return [], []
        loud_band = np.percentile(np.maximum(low, voice), 90)
        per = FFT / self.rate
        over = low - voice
        pops, wind = [], []
        popping = (over > POP_OVER_DB) & (low > loud_band - POP_FROM_DB)
        for a, b in _runs(popping):
            if (b - a) * per <= POP_MOST_S:
                self._mark(pops, a * per, b * per)
        windy = (over > WIND_OVER_DB) & (low > loud_band - WIND_FROM_DB)
        # Short breaks in a gust are the same gust.
        gap = int(0.3 / per)
        filled = windy.copy()
        runs = _runs(windy)
        for (_a, b), (c, _d) in zip(runs, runs[1:]):
            if c - b <= gap:
                filled[b:c] = True
        for a, b in _runs(filled):
            if (b - a) * per >= WIND_S:
                self._mark(wind, a * per, b * per)
        return pops, wind


def _clips(scan):
    """[(clip, track)] - every audio clip that's heard, with a file. A clip
    under one of Audio Assistant's curves is heard through it - the curve
    clip is a timeline, with no file of its own - so it's the one listened
    to, though it's turned off (checks.stood_in). A processed WAV is
    listened to itself."""
    under = stood_in(scan)
    out = []
    for track in scan["tracks"]:
        if track["kind"] != "audio" or not track.get("enabled", True):
            continue
        for clip in track["items"]:
            heard = clip.get("enabled", True) or under.get((track["index"], clip["start"], clip["end"])) == "curve"
            if heard and clip.get("path") and clip.get("online", True) and clip["end"] > clip["start"]:
                out.append((clip, track))
    return out


def listen(scan, progress=None, cancelled=lambda: False, stream=_stream):
    """(events, skipped): every clip's Listener.result() - [{"where",
    "clip", "start", "end", "source_s" (where the clip starts in its file),
    "found"}] - and the files that couldn't be read ([path, why])."""
    fps = scan["fps"] or 24.0
    by_file = {}
    for clip, track in _clips(scan):
        key = (os.path.normcase(os.path.abspath(clip["path"])), tuple(clip.get("channels") or ()))
        by_file.setdefault(key, []).append((clip, track))
    # Each file is decoded up to its last clip's end: that's the work.
    work = {key: max(float(c.get("source_s") or 0.0) + (c["end"] - c["start"]) / fps for c, _t in group)
            for key, group in by_file.items()}
    total = sum(work.values()) or 1.0
    done = 0.0
    events, skipped = [], []
    for (path, channels), group in by_file.items():
        if cancelled():
            break
        listeners = []
        for clip, track in group:
            start = float(clip.get("source_s") or 0.0)
            end = start + (clip["end"] - clip["start"]) / fps
            listeners.append((clip, track, Listener(start, end, track.get("subtype") == "stereo")))
        last_s = max(lst.end_s for _c, _t, lst in listeners)
        state = {"seen": 0, "rate": None, "error": None}
        chosen = list(channels) or None

        def take(samples, rate, listeners=listeners, state=state, chosen=chosen, last_s=last_s):
            if state["rate"] is None:
                state["rate"] = rate
                for _c, _t, lst in listeners:
                    lst.rate = rate
            if chosen:
                if max(chosen) >= samples.shape[1]:
                    have = samples.shape[1]
                    state["error"] = (f"Buddy can read only the first {'channel' if have == 1 else f'{have} channels'} "
                                      f"of this file, and the clip plays channel {max(chosen) + 1}.")
                    return True
                samples = samples[:, chosen]
            seen, n = state["seen"], len(samples)
            state["seen"] = seen + n
            for _c, _t, lst in listeners:
                a, b = max(int(round(lst.start_s * rate)), seen), min(int(round(lst.end_s * rate)), seen + n)
                if a < b:
                    lst.add(samples[a - seen:b - seen])
            return state["seen"] >= last_s * rate

        share, base = work[(path, channels)] / total, done / total
        error = stream(group[0][0]["path"], take,
                       (lambda f, base=base, share=share: progress(min(1.0, base + f * share))) if progress else None,
                       cancelled, until_ms=last_s * 1000.0) or state["error"]
        done += work[(path, channels)]
        if error:
            if error != "Stopped.":
                skipped.append([group[0][0]["path"], error])
            continue
        for clip, track, lst in listeners:
            events.append({"where": "A" + str(track["index"]), "clip": clip["name"], "start": clip["start"],
                           "end": clip["end"], "source_s": lst.start_s, "found": lst.result()})
    return events, skipped
