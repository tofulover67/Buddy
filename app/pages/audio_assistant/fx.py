#!/usr/bin/env python3
"""
The Effects panel's long jobs, off the UI thread: Preview (a stretch of the
clip decoded, run through the chain, written as two short WAVs - before and
after - for the page to play) and Apply's processing (render.render: the
whole clip into a WAV beside the media). Placing that WAV in Resolve is
resolve_ext.apply_effects, on the page's worker, once this is done.

What a clip's effects are made from (source()) comes from the page's copy of
the timeline - no Resolve call: a processed clip is remade from its WAV's
source, never from the WAV, so effects don't stack on effects.
"""

import os
import threading
import uuid
import wave

import numpy as np
from PySide6.QtCore import QThread, Signal

from core.settings_store import BUDDY_DIR

from . import dsp, render
from .keys import frame_duration
from .peaks import decode_range

PREVIEW_DIR = os.path.join(BUDDY_DIR, "audio_assistant", "preview")
PREVIEW_S = 12.0         # how much Preview plays
PREVIEW_ROOM_S = 1.0     # a playhead this near the clip's end previews from earlier
_STILL_RUNNING = []      # jobs that didn't stop at quitting: kept, never destroyed running


def source(clip, fps):
    """{"path", "start_s", "end_s", "channels", "chain"} - what the clip's
    effects are made from: its file, or for a processed clip the recording
    its WAV was made from (the sidecar's, via resolve_ext._fx_of) and the
    effects it has. None for a clip with no file."""
    media = clip.get("media") or {}
    if not media.get("path"):
        return None
    length = (clip["end"] - clip["start"]) / float(fps or 24.0)
    fx = media.get("fx")
    if fx and not fx.get("fit"):          # fitted music is a file of its own
        start = float(clip.get("offset") or 0.0) + fx["file_start"]
        return {"path": fx["source"], "start_s": start, "end_s": start + length,
                "channels": fx.get("channels_used"), "chain": fx.get("chain") or []}
    start = float(clip.get("offset") or 0.0)
    return {"path": media["path"], "start_s": start, "end_s": start + length,
            "channels": clip.get("channels"), "chain": []}


def preview_span(clip, src, fps, playhead):
    """(start_s, end_s, start_frame): the stretch Preview plays - PREVIEW_S
    from the playhead when it's on the clip (with room to hear something),
    else from the clip's start - and the timeline frame that starts on."""
    fps = float(fps or 24.0)
    at = clip["start"]
    room = int(round(PREVIEW_ROOM_S * fps))
    if playhead is not None and clip["start"] <= playhead < clip["end"] - room:
        at = int(playhead)
    start = src["start_s"] + (at - clip["start"]) / fps
    return start, min(src["end_s"], start + PREVIEW_S), at


def write_preview(path, samples, rate):
    """16-bit PCM WAV (what the page's <audio> plays everywhere), clipped at full scale."""
    x = np.asarray(samples, dtype=np.float64)
    if x.ndim == 1:
        x = x[:, None]
    ints = np.clip(np.round(x * 32767), -32768, 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(x.shape[1])
        w.setsampwidth(2)
        w.setframerate(int(rate))
        w.writeframes(ints.tobytes())


def clear_previews(folder=None):
    folder = folder or PREVIEW_DIR
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for name in names:
        if name.endswith(".wav"):
            try:
                os.remove(os.path.join(folder, name))
            except OSError:
                pass          # still playing: the next clear gets it


class Job(QThread):
    """work(progress, cancelled) -> (result, error), on a thread of its own.
    progressed(0-1); done(result, error) once it's over (error "Stopped." when
    it was stopped)."""

    progressed = Signal(float)
    done = Signal(object, object)

    def __init__(self, work, kind, clip_id, parent=None):
        super().__init__(parent)
        self.work, self.kind, self.clip_id = work, kind, clip_id
        self._stop = threading.Event()

    def cancel(self):
        self._stop.set()

    def run(self):
        try:
            result, error = self.work(self.progressed.emit, self._stop.is_set)
        except Exception as exc:  # noqa: BLE001 - said on the page
            result, error = None, str(exc) or type(exc).__name__
        if self._stop.is_set():
            result, error = None, "Stopped."
        self.done.emit(result, error)

    def stop_for_quit(self):
        self.cancel()
        if not self.wait(3000):
            self.setParent(None)
            _STILL_RUNNING.append(self)


def preview_work(src, span, chain, cached):
    """Preview's job: the stretch decoded (or `cached`'s, when it's the same
    one: {"key", "samples", "rate"}), the chain run on it, both written.
    Result: {"before", "after" (paths), "hum", "decoded" (for the cache)}."""
    start_s, end_s, _frame = span
    key = (src["path"], round(start_s, 4), round(end_s, 4), tuple(src["channels"] or ()))

    def work(progress, cancelled):
        if cached and cached.get("key") == key:
            samples, rate = cached["samples"], cached["rate"]
        else:
            got, error = decode_range(src["path"], start_s, end_s, lambda f: progress(f * 0.7), cancelled,
                                      channels=src["channels"])
            if error:
                return None, error
            samples, rate = got
        chain_ = dsp.clean_chain(chain)
        analysis = dsp.analyse(samples, rate) if dsp.needs_analysis(chain_) else None
        after = dsp.process(samples, rate, chain_, cancelled=cancelled, analysis=analysis)
        if after is None:
            return None, "Stopped."
        os.makedirs(PREVIEW_DIR, exist_ok=True)
        clear_previews()
        tag = uuid.uuid4().hex[:8]
        before_path = os.path.join(PREVIEW_DIR, f"before-{tag}.wav")
        after_path = os.path.join(PREVIEW_DIR, f"after-{tag}.wav")
        write_preview(before_path, samples, rate)
        write_preview(after_path, after, rate)
        progress(1.0)
        return {"before": before_path, "after": after_path, "hum": (analysis or {}).get("hum"),
                "decoded": {"key": key, "samples": samples, "rate": rate}}, None

    return work


def render_work(src, chain, fps, project):
    """Apply's processing: render.render() of the clip's whole stretch, with
    a handle of whole frames either side. Result: render.render()'s, plus
    "source": the recording's path (resolve_ext.apply_effects checks it)."""

    def work(progress, cancelled):
        handle = render.frame_handle(src["start_s"], frame_duration(fps))
        result, error = render.render(src["path"], src["start_s"], src["end_s"], chain, decode_range,
                                      handle_s=handle, channels=src["channels"], project=project,
                                      progress=progress, cancelled=cancelled)
        if error:
            return None, error
        return dict(result, source=src["path"]), None

    return work
