#!/usr/bin/env python3
"""
A media file as the pieces media search embeds.

  a still    itself, PICTURE_SIDE pixels on its longer side (Pillow).
  footage    a frame every FRAME_STEP seconds - further apart in a long clip,
             so none costs more than MAX_FRAMES - each read by an ffmpeg of
             its own, SEEKERS at a time, running ahead of the model. What's
             read is the keyframe at or before the moment wanted: ffmpeg
             lands on it and nothing more is decoded, where the exact frame
             means decoding on from there (4K, 2026-10-12: 60 ms a frame
             against 100 in H.264, 110 against 500 in long-GOP HEVC), and
             only that part of the file is read - a pass over the whole clip
             was quicker still from a fast disk, but reads every byte of it.
             ffmpeg says when the keyframe is from, and that's where the
             piece starts. Cameras, phones and drones write a keyframe a
             second or so, software encoders one at each cut; where the
             nearest is more than MAX_KEY_GAP seconds back (or there's no
             ffprobe to say when the file's picture starts), the exact frame
             is read after all. A frame that looks like the last one kept (a
             locked-off interview) isn't embedded again; the kept one stands
             for its time too.
  sound      SOUND_WINDOW-second windows as 16 kHz mono WAV, the whole of a
             short sound, MAX_WINDOWS spread over a long one. Windows of
             silence are left out.

Footage and compressed sound need ffmpeg - the person's own, found as
Project Setup finds it (not bundled). Without it stills are still read, and
plain WAV files, which Python reads itself.

No Qt; safe on any thread. Each generator can be abandoned halfway.
"""

from __future__ import annotations

import concurrent.futures
import io
import os
import re
import subprocess
import wave

from .model import PICTURE_SIDE, SOUND_RATE

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".gif", ".webp"}
AUDIO_EXTS = {".mp3", ".wav", ".aac", ".flac", ".m4a", ".ogg", ".aif", ".aiff"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".mxf", ".m4v", ".webm", ".mpg", ".mts", ".m2ts"}
IMAGE, AUDIO, VIDEO = "image", "audio", "video"

FRAME_STEP = 2.0            # seconds between frames looked at
MAX_FRAMES = 300            # of one clip: an hour's is looked at every 12 seconds
SAME_FRAME = 4.0            # mean difference (of 255) under which two frames are one picture
MAX_KEY_GAP = 10.0          # a keyframe this many seconds before the moment wanted still stands for it
SEEKERS = 4                 # frames read at once
SOUND_WINDOW = 30.0
MAX_WINDOWS = 6
SILENCE = 1e-4              # RMS under which a window has nothing to hear
GRAB_TIMEOUT = 60


class NeedsFfmpeg(Exception):
    """The file can only be read with ffmpeg, and there's none."""


class Unreadable(Exception):
    """Worded for the person: why nothing could be made of the file."""


def kind_of(path) -> str | None:
    ext = os.path.splitext(str(path))[1].lower()
    return IMAGE if ext in IMAGE_EXTS else AUDIO if ext in AUDIO_EXTS else VIDEO if ext in VIDEO_EXTS else None


def find_ffmpeg() -> str | None:
    """The person's ffmpeg: the one Project Setup's Settings names, else
    wherever Project Setup would find one."""
    import json
    from pages.project_setup import data_manager
    from pages.project_setup.ffmpeg_utils import find_ffmpeg as find
    try:
        with open(data_manager.SETTINGS_PATH, encoding="utf-8") as f:
            override = json.load(f).get("ffmpeg_path")
    except (OSError, ValueError, AttributeError):
        override = None
    return find(override or None)


def _quiet() -> dict:
    from pages.project_setup.ffmpeg_utils import _no_feedback_cursor_kwargs
    return _no_feedback_cursor_kwargs()


# ----------------------------------------------------------------- stills --

def _jpeg(image) -> bytes:
    image = image.convert("RGB")
    image.thumbnail((PICTURE_SIDE, PICTURE_SIDE))
    out = io.BytesIO()
    image.save(out, "JPEG", quality=85)
    return out.getvalue()


def picture(path) -> bytes:
    """A still as the JPEG that's embedded."""
    from PIL import Image, UnidentifiedImageError
    try:
        with Image.open(path) as image:
            image.seek(0)                       # an animated GIF: its first frame
            return _jpeg(image)
    except (OSError, UnidentifiedImageError, ValueError, Image.DecompressionBombError) as exc:
        raise Unreadable(f"Couldn't read the picture ({exc}).") from exc


def _look(jpeg: bytes):
    """A frame small and grey: what two frames are compared by."""
    import numpy as np
    from PIL import Image
    with Image.open(io.BytesIO(jpeg)) as image:
        return np.asarray(image.convert("L").resize((16, 16)), dtype=np.float32)


# ---------------------------------------------------------------- footage --

def duration(ffmpeg: str, path) -> float:
    """How long a file runs, in seconds; 0 when ffmpeg can't say."""
    from pages.project_setup.ffmpeg_utils import _ffprobe_path
    probe = _ffprobe_path(ffmpeg)
    try:
        if probe:
            done = subprocess.run([probe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                                  capture_output=True, timeout=GRAB_TIMEOUT, **_quiet())
            return max(0.0, float((done.stdout or b"0").decode("ascii", "replace").strip() or 0))
        done = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path)], capture_output=True, timeout=GRAB_TIMEOUT,
                              **_quiet())
        found = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", (done.stderr or b"").decode("utf-8", "replace"))
        return int(found.group(1)) * 3600 + int(found.group(2)) * 60 + float(found.group(3)) if found else 0.0
    except (subprocess.SubprocessError, OSError, ValueError):
        return 0.0


def frame_times(seconds: float) -> tuple[list[float], float]:
    """(when to look, how far apart): the middle of each FRAME_STEP of the
    clip - of a longer step in a clip that would take more than MAX_FRAMES."""
    if seconds <= 0:
        return [0.0], FRAME_STEP
    step = max(FRAME_STEP, seconds / MAX_FRAMES)
    count = max(1, int(seconds / step + 0.5))
    return [min(seconds, (i + 0.5) * step) for i in range(count)], step


def frame(ffmpeg: str, path, at: float, side: int = PICTURE_SIDE) -> bytes | None:
    """The frame `at` seconds into a clip as a JPEG, `side` pixels on its
    longer side; None when there's none there (past the end, or no picture)."""
    cmd = [ffmpeg, "-v", "error", "-nostdin"]
    if at >= 0.001:
        cmd += ["-ss", f"{at:.6f}"]
    cmd += ["-i", str(path), "-map", "0:v:0", "-frames:v", "1",
            "-vf", f"scale={side}:{side}:force_original_aspect_ratio=decrease:force_divisible_by=2",
            "-f", "image2pipe", "-c:v", "mjpeg", "-q:v", "5", "-"]
    try:
        done = subprocess.run(cmd, capture_output=True, timeout=GRAB_TIMEOUT, **_quiet())
    except (subprocess.SubprocessError, OSError):
        return None
    return done.stdout if done.returncode == 0 and done.stdout[:2] == b"\xff\xd8" else None


def picture_start(ffmpeg: str, path) -> float | None:
    """The timestamp of a clip's first frame (a camera file's needn't be 0);
    None when there's no ffprobe, or it can't say."""
    from pages.project_setup.ffmpeg_utils import _ffprobe_path
    probe = _ffprobe_path(ffmpeg)
    if not probe:
        return None
    try:
        done = subprocess.run([probe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=start_time",
                               "-of", "csv=p=0", str(path)], capture_output=True, timeout=GRAB_TIMEOUT, **_quiet())
        return float((done.stdout or b"").decode("ascii", "replace").strip().split(",")[0])
    except (subprocess.SubprocessError, OSError, ValueError):
        return None


def key_frame(ffmpeg: str, path, at: float, origin: float, side: int = PICTURE_SIDE) -> tuple[float, bytes] | None:
    """(when it's from, JPEG) of the keyframe at or before `at` seconds into
    a clip whose picture starts at timestamp `origin` - where ffmpeg lands,
    nothing decoded past it. None when it gave no frame, or didn't say when
    the frame is from."""
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "info", "-nostdin", "-noaccurate_seek", "-ss", f"{origin + at:.6f}",
           "-copyts", "-i", str(path), "-map", "0:v:0", "-frames:v", "1",
           "-vf", f"showinfo,scale={side}:{side}:force_original_aspect_ratio=decrease:force_divisible_by=2",
           "-f", "image2pipe", "-c:v", "mjpeg", "-q:v", "5", "-"]
    try:
        done = subprocess.run(cmd, capture_output=True, timeout=GRAB_TIMEOUT, **_quiet())
    except (subprocess.SubprocessError, OSError):
        return None
    said = re.search(rb"showinfo[^\n]*?pts_time:\s*(-?[\d.]+)", done.stderr or b"")
    if done.returncode != 0 or done.stdout[:2] != b"\xff\xd8" or not said:
        return None
    return float(said.group(1)) - origin, done.stdout


def _grab(ffmpeg: str, path, at: float, origin: float | None) -> tuple[float, bytes] | None:
    """(moment, JPEG) for the moment `at`: the keyframe at or before it,
    or - when that's too far back, or can't be had - the frame at `at` itself."""
    if origin is not None:
        key = key_frame(ffmpeg, path, at, origin)
        if key is not None and -0.5 <= at - key[0] <= MAX_KEY_GAP:
            return max(0.0, key[0]), key[1]
    jpeg = frame(ffmpeg, path, at)
    return None if jpeg is None else (at, jpeg)


def _grabs(ffmpeg: str, path, times, origin: float | None):
    """_grab for each of `times`, in order, SEEKERS running ahead of what's
    been taken; moments nothing could be read for are left out."""
    pool = concurrent.futures.ThreadPoolExecutor(SEEKERS, thread_name_prefix="media-search-frame")
    try:
        ahead = []
        for at in [*times, None]:
            if at is not None:
                ahead.append(pool.submit(_grab, ffmpeg, path, at, origin))
            while ahead and (at is None or len(ahead) >= SEEKERS * 2):
                grabbed = ahead.pop(0).result()
                if grabbed is not None:
                    yield grabbed
    finally:
        pool.shutdown(wait=False, cancel_futures=True)      # abandoned halfway: no more are started


def frames(ffmpeg: str | None, path, seconds: float = 0.0):
    """(start, span, JPEG) for each picture a clip shows: the moment the
    frame is from, and the seconds from there it stands for - until the next
    picture, or the clip's end. `seconds`: the clip's length if it's known."""
    import numpy as np
    if not ffmpeg:
        raise NeedsFfmpeg()
    seconds = seconds or duration(ffmpeg, path)
    times, step = frame_times(seconds)
    kept = None                 # [moment, jpeg, look] of the last picture kept
    for moment, jpeg in _grabs(ffmpeg, path, times, picture_start(ffmpeg, path)):
        if kept is not None and moment <= kept[0] + 1e-3:
            continue                                    # the same keyframe again (or one out of order)
        look = _look(jpeg)
        if kept is not None and float(np.abs(look - kept[2]).mean()) < SAME_FRAME:
            continue                                    # the same picture still: the kept one stands for this time too
        if kept is not None:
            yield kept[0], moment - kept[0], kept[1]
        kept = [moment, jpeg, look]
    if kept is None:
        raise Unreadable("ffmpeg found no picture in it.")
    yield kept[0], max(seconds - kept[0], step / 2), kept[1]


# ------------------------------------------------------------------ sound --

def _wav(samples) -> bytes:
    """Mono float samples at SOUND_RATE as a 16-bit WAV file's bytes."""
    import numpy as np
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SOUND_RATE)
        w.writeframes((np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes())
    return out.getvalue()


def _plain_wav(path):
    """A PCM WAV file read without ffmpeg: (mono float samples at SOUND_RATE)
    or None for one Python can't read (float or compressed WAVs)."""
    import numpy as np
    try:
        with wave.open(str(path), "rb") as w:
            channels, width, rate, count = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            if width not in (1, 2, 3, 4) or not rate or not channels:
                return None
            raw = w.readframes(min(count, int(rate * SOUND_WINDOW * MAX_WINDOWS * 4)))
    except (wave.Error, OSError, EOFError):
        return None
    if width == 1:
        data = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128) / 128
    elif width == 3:
        b = np.frombuffer(raw[:len(raw) - len(raw) % 3], dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        data = ((b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)) << 8 >> 8).astype(np.float32) / (1 << 23)
    else:
        data = np.frombuffer(raw, dtype="<i2" if width == 2 else "<i4").astype(np.float32) / float(1 << (8 * width - 1))
    data = data[:len(data) - len(data) % channels].reshape(-1, channels).mean(axis=1)
    if rate != SOUND_RATE and len(data):
        new = np.arange(int(len(data) * SOUND_RATE / rate)) * (rate / SOUND_RATE)
        data = np.interp(new, np.arange(len(data)), data).astype(np.float32)
    return data


def window_starts(seconds: float) -> list[float]:
    """Where a sound's windows start: end to end through a short one,
    MAX_WINDOWS spread evenly over a long one."""
    if seconds <= SOUND_WINDOW:
        return [0.0]
    count = int(-(-seconds // SOUND_WINDOW))
    if count <= MAX_WINDOWS:
        return [i * SOUND_WINDOW for i in range(count)]
    return [i * (seconds - SOUND_WINDOW) / (MAX_WINDOWS - 1) for i in range(MAX_WINDOWS)]


def sounds(ffmpeg: str | None, path, seconds: float = 0.0):
    """(start, span, WAV bytes) for each window of a sound that has something
    in it."""
    import numpy as np
    loud = lambda s: len(s) and float(np.sqrt(np.mean(np.square(s, dtype=np.float64)))) >= SILENCE   # noqa: E731
    size = int(SOUND_WINDOW * SOUND_RATE)
    if not ffmpeg:
        samples = _plain_wav(path) if os.path.splitext(str(path))[1].lower() == ".wav" else None
        if samples is None:
            raise NeedsFfmpeg()
        for start in window_starts(len(samples) / SOUND_RATE):
            part = samples[int(start * SOUND_RATE):int(start * SOUND_RATE) + size]
            if loud(part):
                yield start, len(part) / SOUND_RATE, _wav(part)
        return
    from pages.project_setup.ffmpeg_utils import NoAudioStreamError, decode_mono_pcm
    seconds = seconds or duration(ffmpeg, path)
    for start in window_starts(seconds):
        try:
            raw = decode_mono_pcm(ffmpeg, str(path), SOUND_RATE, max_seconds=SOUND_WINDOW, start_seconds=start,
                                  timeout=GRAB_TIMEOUT)
        except NoAudioStreamError as exc:
            raise Unreadable("There's no sound in it.") from exc
        except (RuntimeError, subprocess.SubprocessError, OSError) as exc:
            if start:
                continue                    # past the end of a file shorter than it said
            raise Unreadable(f"ffmpeg couldn't read its sound ({str(exc)[:160]}).") from exc
        part = np.frombuffer(raw[:len(raw) - len(raw) % 4], dtype="<f4")
        if loud(part):
            yield start, len(part) / SOUND_RATE, _wav(part)
