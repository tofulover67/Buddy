#!/usr/bin/env python3
"""
Who is talking, block by block, on a podcast's mics - and, where people
share a mic, which of them. numpy only: no Qt, no Resolve.

    mics_talking(mics, audio_of, fps, hold)  -> Talk (each mic, every BLOCK_S)
    file_spans(talk, row, clips, fps)        -> where a mic talks, in each clip's file
    voices_on_mic(talk, row, clips, windows, labels, fps, hold) -> rows, one per voice

One mic per person is Audio Assistant's automixer's question (its
automix.py), asked the same way and with its numbers: each mic's
K-weighted loudness (peaks.py's) against its own integrated loudness, a mic
talking where it's within MARGIN_DB of the one most so and above its own
speech floor, pauses up to `hold` bridged and sounds under MIN_TALK_S
dropped. The camera then follows the mic, so bleed (the same voice heard
quieter on the other mics) never moves it. The automixer goes on to make
gain keys; here the talk itself is the answer, so plan()'s first half is
asked for directly through its own pieces.

Several voices on one mic: the voice prints of that mic's talk
(voice_worker.py, grouped by cluster.py) say who - each block goes to the
voice of the prints covering it.
"""

from dataclasses import dataclass, field

import numpy as np

from pages.audio_assistant.automix import MARGIN_DB, MIN_TALK_S, SMOOTH_BLOCKS, _clean, _runs
from pages.audio_assistant.ducking import FLOOR_LUFS, SENSITIVITY_LU, _heard_blocks
from pages.audio_assistant.levels import gate

BLOCK_S = 0.1           # peaks.LOUD_BLOCK_S: one reading per block
SPAN_PAD_S = 0.3        # a mic's talk handed to the voice worker with this either side
WINDOW_REACH_S = 0.75   # a block takes the voice of a print this near it, when none covers it


@dataclass
class Talk:
    """Each mic's talk: talk[row, b] for block `first + b` of the timeline
    (blocks of `block` s counted from frame 0); rows in `order` (track index)."""
    first: int = 0
    block: float = BLOCK_S
    order: list = field(default_factory=list)
    talk: np.ndarray = field(default_factory=lambda: np.zeros((0, 0), dtype=bool))
    missing: list = field(default_factory=list)

    @property
    def length(self):
        return self.talk.shape[1] if self.talk.ndim == 2 else 0

    def seconds(self, b):
        """Block b's start, in timeline seconds from frame 0."""
        return (self.first + b) * self.block


def mics_talking(mics, audio_of, fps, hold=0.5):
    """mics: [{"index", "clips"}] (Audio Assistant's clips: id, start, end
    frames, offset s into the file; volume and fades are heard too).
    audio_of(clip): {"loud", "block"} - its file's loudness - or None."""
    heard, missing, block = {}, [], BLOCK_S
    for track in mics:
        got = []
        for clip in track["clips"]:
            if clip.get("transition") or not clip.get("enabled", True):
                continue
            audio = audio_of(clip)
            if audio is None:
                missing.append(clip["id"])
                continue
            block = audio["block"]
            at, energy = _heard_blocks(clip, audio, fps)
            if len(energy):
                got.append((at, energy))
        heard[track["index"]] = got
    order = [t["index"] for t in mics]
    parts = [p for got in heard.values() for p in got]
    if not parts:
        return Talk(0, block, order, np.zeros((len(order), 0), dtype=bool), missing)
    first = min(at for at, _e in parts)
    n = max(at + len(e) for at, e in parts) - first
    energy = np.zeros((len(order), n))
    for row, index in enumerate(order):
        for at, e in heard[index]:
            energy[row, at - first:at - first + len(e)] += e
    with np.errstate(divide="ignore"):
        level = -0.691 + 10 * np.log10(np.maximum(
            np.array([np.convolve(e, np.ones(SMOOTH_BLOCKS) / SMOOTH_BLOCKS, mode="same") for e in energy]), 1e-20))
    own = []
    for e in energy:
        four = np.convolve(e, np.ones(4) / 4, mode="valid") if len(e) >= 4 else e
        own.append(gate(four) if len(four) else None)
    own = np.array([np.nan if v is None else v for v in own], dtype=float)
    floor = np.maximum(FLOOR_LUFS, own - SENSITIVITY_LU)
    above = level > np.where(np.isnan(floor), np.inf, floor)[:, None]
    against = np.where(above, level - np.nan_to_num(own)[:, None], -np.inf)
    best = against.max(axis=0)
    talk = above & (against >= best - MARGIN_DB)
    hold_blocks = int(round(hold / block))
    shortest = max(1, int(round(MIN_TALK_S / block)))
    talk = np.array([_clean(row, hold_blocks, shortest) for row in talk], dtype=bool).reshape(len(order), n)
    return Talk(first, block, order, talk, missing)


def _clip_window(clip, fps):
    """(first, last) timeline seconds a clip plays."""
    return clip["start"] / fps, clip["end"] / fps


def file_spans(talk, row, clips, fps, pad=SPAN_PAD_S):
    """{clip id: [[start, end], ...] seconds into its file} where mic `row`
    talks - what the voice worker listens to (nothing else need be heard)."""
    out = {}
    runs = [(talk.seconds(a), talk.seconds(b)) for a, b in _runs(talk.talk[row])] if talk.length else []
    for clip in clips:
        if clip.get("transition") or not clip.get("enabled", True) or not clip.get("media"):
            continue
        lo, hi = _clip_window(clip, fps)
        offset = clip.get("offset", 0.0)
        spans = []
        for a, b in runs:
            a, b = max(lo, a - pad), min(hi, b + pad)
            if b - a <= 0:
                continue
            fa, fb = offset + (a - lo), offset + (b - lo)
            if spans and fa <= spans[-1][1]:
                spans[-1][1] = round(fb, 3)
            else:
                spans.append([round(fa, 3), round(fb, 3)])
        if spans:
            out[clip["id"]] = spans
    return out


def voices_on_mic(talk, row, clips, windows, labels, fps, hold=0.5):
    """(voices, n) rows of talk for the people sharing mic `row`: windows
    [(clip id, start, end)] in file seconds (the worker's), labels their
    voice (cluster.group's). A block goes to the voice of the prints
    covering it - the one most of them say - while the mic talks."""
    n = talk.length
    voices = int(max(labels)) + 1 if len(labels) else 0
    if not voices or not n:
        return np.zeros((max(voices, 1), n), dtype=bool)
    by_id = {c["id"]: c for c in clips}
    votes = np.zeros((voices, n))
    for (clip_id, start, end), label in zip(windows, labels):
        clip = by_id.get(clip_id)
        if clip is None:
            continue
        zero = clip["start"] / fps - clip.get("offset", 0.0)     # timeline seconds of the file's time 0
        a = max(0, int(np.floor((zero + start) / talk.block)) - talk.first)
        b = min(n, int(np.ceil((zero + end) / talk.block)) - talk.first)
        if a < b:
            votes[label, a:b] += 1
    covered = votes.max(axis=0) > 0
    who = np.where(covered, np.argmax(votes, axis=0), -1)
    # Talk no print covers (a word between windows) takes the voice of the
    # nearest print, if one is within WINDOW_REACH_S.
    idx = np.arange(n)
    before = np.maximum.accumulate(np.where(covered, idx, -1))
    after = np.minimum.accumulate(np.where(covered, idx, n)[::-1])[::-1]
    reach = WINDOW_REACH_S / talk.block
    gap_before = np.where(before >= 0, idx - before, np.inf)
    gap_after = np.where(after < n, after - idx, np.inf)
    use_before = (gap_before <= gap_after) & (gap_before <= reach)
    use_after = ~use_before & (gap_after <= reach)
    who = np.where(covered, who, np.where(use_before, who[np.clip(before, 0, n - 1)],
                                          np.where(use_after, who[np.clip(after, 0, n - 1)], -1)))
    speaking = talk.talk[row]
    rows = np.zeros((voices, n), dtype=bool)
    for v in range(voices):
        rows[v] = speaking & (who == v)
    hold_blocks = int(round(hold / talk.block))
    shortest = max(1, int(round(MIN_TALK_S / talk.block)))
    return np.array([_clean(r, hold_blocks, shortest) for r in rows], dtype=bool).reshape(voices, n)


def runs_seconds(talk, row_values):
    """[(start, end)] timeline seconds of a row's True runs."""
    return [(round(talk.seconds(a), 3), round(talk.seconds(b), 3)) for a, b in _runs(np.asarray(row_values))]
