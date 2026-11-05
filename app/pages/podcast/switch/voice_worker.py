#!/usr/bin/env python3
"""
Runs INSIDE the transcription venv (~/.buddy/transcribe/venv), never in
Buddy's own interpreter: it needs onnxruntime and PyAV, which only the
speech engine has. Auto Switch (page.py, jobs.py) launches it to tell
voices apart on a mic that more than one person spoke into.

    python voice_worker.py embed --job JOB.json --out OUT.json

JOB: {"model": the voice model's folder,
      "clips": [{"id", "path", "spans": [[start, end], ...] seconds into the
                 file - where the mic's track is talking (talk.py); none:
                 the whole file}]}

For each clip: the spans are decoded (16 kHz mono), Silero VAD (the one
faster-whisper ships, as Subtitles uses) keeps the speech in them, and the
speech is cut into WINDOW_S windows every STEP_S. Each window becomes a
voice print - WeSpeaker's ResNet34 (voxceleb_resnet34_LM.onnx: 80 Kaldi
filterbanks in, 256 numbers out). Buddy groups the prints into voices
(cluster.py) - not done here, so a change of "how many voices" needs no
second pass.

OUT: {"dim": 256, "windows": [[clip index, start, end], ...] (file seconds),
      "prints": base64 float16 (windows x dim), "speech": {clip id: [[s, e]]}}

Protocol on stdout, as transcribe/worker.py's: one "@@BUDDY " + JSON per
line - status, progress {done, total} (seconds of audio), done, error.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import traceback

MARK = "@@BUDDY "
RATE = 16000
WINDOW_S = 1.5          # one voice print's length: long enough to know a voice by,
STEP_S = 0.75           # short enough not to straddle many turns
SHORTEST_S = 0.5        # speech shorter than this makes no print
BATCH = 16
VAD = dict(min_silence_duration_ms=250, speech_pad_ms=100)

# Kaldi's filterbank, as WeSpeaker computes its input (torchaudio's
# kaldi.fbank with 25 ms frames every 10 ms, 80 mel bins, Hamming window,
# no dither, on samples at 16-bit scale), then each window's mean taken off.
FRAME = 400
SHIFT = 160
FFT = 512
MELS = 80
PREEMPH = 0.97


def emit(kind, **fields):
    print(MARK + json.dumps({"type": kind, **fields}), flush=True)


def _mel_banks(np):
    """(MELS, FFT // 2 + 1) Kaldi triangular mel filters, 20 Hz to Nyquist."""
    def mel(f):
        return 1127.0 * np.log(1.0 + f / 700.0)
    low, high = mel(20.0), mel(RATE / 2.0)
    step = (high - low) / (MELS + 1)
    bins = mel(np.arange(FFT // 2) * RATE / FFT)
    banks = np.zeros((MELS, FFT // 2 + 1), dtype=np.float32)
    for m in range(MELS):
        left, centre, right = low + m * step, low + (m + 1) * step, low + (m + 2) * step
        up = (bins - left) / (centre - left)
        down = (right - bins) / (right - centre)
        banks[m, :FFT // 2] = np.maximum(0.0, np.minimum(up, down))
    return banks


_BANKS = None
_WINDOW = None


def fbank(samples, np):
    """(frames, MELS) log filterbank energies of 16 kHz float samples (-1..1)."""
    global _BANKS, _WINDOW
    if _BANKS is None:
        _BANKS = _mel_banks(np)
        _WINDOW = (0.54 - 0.46 * np.cos(2 * np.pi * np.arange(FRAME) / (FRAME - 1))).astype(np.float32)
    x = np.asarray(samples, dtype=np.float32) * 32768.0
    if len(x) < FRAME:
        x = np.pad(x, (0, FRAME - len(x)))
    n = 1 + (len(x) - FRAME) // SHIFT
    idx = np.arange(FRAME)[None, :] + SHIFT * np.arange(n)[:, None]
    frames = x[idx]
    frames = frames - frames.mean(axis=1, keepdims=True)
    frames = np.concatenate((frames[:, :1] * (1 - PREEMPH), frames[:, 1:] - PREEMPH * frames[:, :-1]), axis=1)
    frames *= _WINDOW
    power = np.abs(np.fft.rfft(frames, n=FFT, axis=1)) ** 2
    energy = power @ _BANKS.T
    return np.log(np.maximum(energy, np.finfo(np.float32).eps)).astype(np.float32)


def windows(speech, total):
    """[(start, end)] sample windows over the speech spans (sample indices)."""
    win, step, least = int(WINDOW_S * RATE), int(STEP_S * RATE), int(SHORTEST_S * RATE)
    out = []
    for a, b in speech:
        b = min(b, total)
        if b - a < least:
            continue
        if b - a <= win:
            out.append((a, b))
            continue
        at = a
        while at + win <= b:
            out.append((at, at + win))
            at += step
        if b - (at - step + win) >= step // 2:     # the end of the span, so its last words count
            out.append((b - win, b))
    return out


def _fit(piece, np):
    """A window WINDOW_S long: a short one repeated to fill it."""
    win = int(WINDOW_S * RATE)
    if len(piece) >= win:
        return piece[:win]
    return np.resize(piece, win)


def decode_range(path, start, end):
    """16 kHz mono float32 of the file's first audio stream, from start to end seconds."""
    import av
    import numpy as np
    from av.stream import Discard
    out, got = [], 0
    first = int(round(start * RATE))
    wanted = max(0, int(round((end - start) * RATE)))
    resampler = av.audio.resampler.AudioResampler(format="s16", layout="mono", rate=RATE)
    with av.open(path) as container:
        stream = container.streams.audio[0]
        for other in container.streams:
            if other.index != stream.index:
                other.discard = Discard.all
        if start > 1.0:
            try:
                container.seek(int(max(0.0, start - 0.5) / stream.time_base), stream=stream, backward=True)
            except Exception:  # noqa: BLE001 - a file that can't seek is read from the top
                pass
        at = None
        for frame in container.decode(stream):
            if at is None:
                at = int(round(float(frame.time or 0.0) * RATE)) if frame.time is not None else 0
            for piece in resampler.resample(frame):
                data = piece.to_ndarray().reshape(-1)
                lo = max(0, first - at)
                if lo < len(data):
                    keep = data[lo:lo + wanted - got]
                    out.append(keep)
                    got += len(keep)
                at += len(data)
            if got >= wanted:
                break
    if not out:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(out).astype(np.float32) / 32768.0


def embed(args) -> int:
    import numpy as np
    import onnxruntime as ort
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    with open(args.job, encoding="utf-8") as f:
        job = json.load(f)
    model = os.path.join(job["model"], "voxceleb_resnet34_LM.onnx")
    if not os.path.isfile(model):
        emit("error", message="The voice model isn't downloaded.")
        return 1
    options = ort.SessionOptions()
    options.log_severity_level = 3
    session = ort.InferenceSession(model, options, providers=["CPUExecutionProvider"])
    clips = job.get("clips") or []
    total = sum(sum(max(0.0, b - a) for a, b in c.get("spans") or [[0.0, c.get("duration", 0.0)]]) for c in clips)
    done, found, prints, speech_out = 0.0, [], [], {}

    def flush(batch):
        if not batch:
            return
        feats = np.stack([f - f.mean(axis=0, keepdims=True) for f in (fbank(_fit(p, np), np) for p in batch)])
        prints.append(session.run(None, {"feats": feats})[0].astype(np.float32))

    for index, clip in enumerate(clips):
        emit("status", message=f"Listening to {os.path.basename(clip['path'])}")
        spans = clip.get("spans") or [[0.0, float(clip.get("duration") or 0.0)]]
        spoken = []
        for a, b in spans:
            if b - a < SHORTEST_S:
                done += max(0.0, b - a)
                continue
            try:
                audio = decode_range(clip["path"], a, b)
            except Exception as exc:  # noqa: BLE001 - one unreadable file: said, the rest go on
                emit("status", message=f"Couldn't read {os.path.basename(clip['path'])}: {exc}")
                done += b - a
                continue
            speech = [(s["start"], s["end"]) for s in get_speech_timestamps(audio, VadOptions(**VAD))]
            batch, places = [], []
            for s, e in windows(speech, len(audio)):
                batch.append(audio[s:e])
                places.append((index, round(a + s / RATE, 3), round(a + e / RATE, 3)))
                if len(batch) == BATCH:
                    flush(batch)
                    found.extend(places)
                    batch, places = [], []
            flush(batch)
            found.extend(places)
            spoken.extend([round(a + s / RATE, 3), round(a + e / RATE, 3)] for s, e in speech)
            done += b - a
            emit("progress", done=done, total=total)
        speech_out[clip["id"]] = spoken
    matrix = np.concatenate(prints) if prints else np.zeros((0, 256), dtype=np.float32)
    result = {"dim": int(matrix.shape[1]) if matrix.ndim == 2 else 256, "windows": found,
              "prints": base64.b64encode(matrix.astype(np.float16).tobytes()).decode("ascii"),
              "speech": speech_out}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f)
    emit("done", out=args.out, windows=len(found))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="mode", required=True)
    e = sub.add_parser("embed")
    e.add_argument("--job", required=True)
    e.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    try:
        return embed(args)
    except Exception as exc:  # noqa: BLE001 - reported, not a traceback on the user's screen
        traceback.print_exc(file=sys.stderr)
        emit("error", message=f"Telling the voices apart failed: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
