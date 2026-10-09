#!/usr/bin/env python3
"""
Generative's footage: what a clip is (size, frame rate, how many frames,
its depth), its frames one at a time at their own depth, and image
sequences written a frame at a time. Runs in the engine's environment
(engine_setup.py) - NumPy and OpenCV - and uses the person's own ffmpeg
when there is one (the same search as Project Setup's).

Two ways to read a clip:

  ffmpeg   decoded straight to 16-bit BGR when the clip is deeper than
           8 bits (a 10-bit camera file keeps its 10 bits), 8-bit
           otherwise. Any format ffmpeg reads.
  OpenCV   its own FFmpeg, built in: 8-bit only. Always there.

Frames are numbered from 0 at the clip's first frame, whatever its
timecode. A frame's time is its number over the frame rate, and ffmpeg is
asked for that time rounded down to the microsecond: it starts at the
frame nearest the time asked for (seen with ffmpeg 9: half a frame early
gave the frame before), and rounded down it's never past the frame either.

Sequences are what Resolve imports and plays as one clip: name_000000.png
and on, PNG (8 or 16-bit, with transparency) or TIFF (8 or 16-bit). Each
frame is written to a temporary name and moved into place, so a frame
that's there is a whole frame - a job stopped part-way picks up at the
first one missing.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
import threading
from fractions import Fraction

import numpy as np

import cv2

import video_formats  # beside it: footage is imported by bare name, from its own folder

# On Windows: no console window, and no busy cursor, for each ffmpeg run.
_QUIET = {}
if sys.platform == "win32":
    _si = subprocess.STARTUPINFO()
    _si.dwFlags |= 0x00000080                     # STARTF_FORCEOFFFEEDBACK
    _QUIET = {"startupinfo": _si, "creationflags": subprocess.CREATE_NO_WINDOW}


def imread(path: str, flags: int = cv2.IMREAD_UNCHANGED) -> np.ndarray | None:
    """cv2.imread through Python's own file reading: OpenCV's can't take
    every Windows path (one outside the ANSI code page). None when the
    file isn't there or isn't a picture, as imread gives."""
    try:
        data = np.fromfile(path, np.uint8)
    except OSError:
        return None
    return cv2.imdecode(data, flags) if data.size else None


def imwrite(path: str, img: np.ndarray) -> bool:
    """cv2.imwrite through Python's own file writing (see imread)."""
    ok, data = cv2.imencode(os.path.splitext(path)[1] or ".png", img)
    if not ok:
        return False
    try:
        data.tofile(path)
    except OSError:
        return False
    return True


class FootageError(RuntimeError):
    """A clip that can't be read, said as it is."""


# ----------------------------------------------------------------- ffmpeg --

def find_ffmpeg(override: str | None = None) -> str | None:
    """The person's ffmpeg: the one given, on PATH, or where installers put
    it (as pages/project_setup/ffmpeg_utils.find_ffmpeg - that one isn't
    importable from the engine's own Python)."""
    if override and os.path.isfile(override):
        return override
    found = shutil.which("ffmpeg")
    if found:
        return found
    if sys.platform == "win32":
        for c in (os.path.expandvars(r"%ProgramFiles%\ffmpeg\bin\ffmpeg.exe"),
                  os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe")):
            if os.path.isfile(c):
                return c
        root = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages")
        try:
            for entry in os.listdir(root):
                if entry.lower().startswith("gyan.ffmpeg"):
                    for sub in os.listdir(os.path.join(root, entry)):
                        c = os.path.join(root, entry, sub, "bin", "ffmpeg.exe")
                        if os.path.isfile(c):
                            return c
        except OSError:
            pass
        return None
    for c in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg"):
        if os.path.isfile(c):
            return c
    return None


def _ffprobe(ffmpeg: str) -> str | None:
    folder, name = os.path.split(ffmpeg)
    probe = os.path.join(folder, name.lower().replace("ffmpeg", "ffprobe"))
    return probe if os.path.isfile(probe) else shutil.which("ffprobe")


# ------------------------------------------------------------------ probe --

def _depth_of(pix_fmt: str, bits: str | int | None) -> int:
    """8, or 16 for anything deeper (10, 12 and 16-bit all read as 16)."""
    try:
        if int(bits or 0) > 8:
            return 16
    except ValueError:
        pass
    return 16 if any(t in (pix_fmt or "") for t in ("10", "12", "14", "16", "p010", "p210", "p410")) else 8


def probe(path: str, ffmpeg: str | None = None) -> dict:
    """The clip's facts: {width, height, fps (a Fraction as "num/den"),
    frames, depth (8 | 16), codec, alpha, reader}. ffprobe when there's an
    ffmpeg, else OpenCV's own reading."""
    if not os.path.isfile(path):
        raise FootageError(f"There's no file at {path}.")
    probe_exe = _ffprobe(ffmpeg) if ffmpeg else None
    if probe_exe:
        out = subprocess.run([probe_exe, "-v", "error", "-select_streams", "v:0", "-count_packets",
                              "-show_entries", "stream=width,height,r_frame_rate,avg_frame_rate,nb_frames,"
                              "nb_read_packets,pix_fmt,codec_name,bits_per_raw_sample,color_space,color_range,"
                              "color_primaries,color_transfer"
                              ":stream_side_data=rotation:stream_tags=rotate:format=duration",
                              "-of", "json", path], capture_output=True, text=True, timeout=120, **_QUIET)
        try:
            data = json.loads(out.stdout or "{}")
            s = data["streams"][0]
        except (ValueError, KeyError, IndexError):
            raise FootageError(f"ffmpeg can't read {os.path.basename(path)} as video.") from None
        fps = Fraction(s.get("r_frame_rate") or s.get("avg_frame_rate") or "0/1")
        if not fps:
            fps = Fraction(s.get("avg_frame_rate") or "0/1")
        frames = 0
        for key in ("nb_frames", "nb_read_packets"):
            try:
                frames = int(s.get(key) or 0)
            except ValueError:
                frames = 0
            if frames:
                break
        if not frames and fps:
            frames = int(round(float(data.get("format", {}).get("duration") or 0) * fps))
        pix = s.get("pix_fmt") or ""
        w, h = int(s["width"]), int(s["height"])
        if _quarter_turn(s):
            w, h = h, w                          # stood up as it's decoded: frames come out the other way round
        return {"path": path, "width": w, "height": h,
                "fps": f"{fps.numerator}/{fps.denominator}", "frames": frames,
                "depth": _depth_of(pix, s.get("bits_per_raw_sample")), "codec": s.get("codec_name") or "",
                "alpha": pix.startswith(("yuva", "rgba", "bgra", "argb", "abgr", "gbrap", "ya")),
                "reader": "ffmpeg", "matrix": s.get("color_space") or "", "full_range": s.get("color_range") == "pc",
                "range": s.get("color_range") or "", "primaries": s.get("color_primaries") or "",
                "transfer": s.get("color_transfer") or "",
                **_extras(probe_exe, path)}
    cap = cv2.VideoCapture(path)
    try:
        if not cap.isOpened():
            raise FootageError(f"Can't read {os.path.basename(path)} as video.")
        fps = Fraction(cap.get(cv2.CAP_PROP_FPS) or 0).limit_denominator(1001)
        return {"path": path, "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                "fps": f"{fps.numerator}/{fps.denominator}", "frames": int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),
                "depth": 8, "codec": "", "alpha": False, "reader": "opencv"}
    finally:
        cap.release()


def _quarter_turn(stream: dict) -> bool:
    """A clip stored on its side, to be shown turned a quarter (a phone's
    portrait video: 1920 x 1080 with "rotate 90"). ffmpeg turns its frames
    upright as it decodes them, so they come out h x w, not the w x h
    ffprobe reports - read as w x h they were stripes of nonsense (seen
    2026-10-07, a vertical clip)."""
    turns = []
    for side in stream.get("side_data_list") or []:
        if "rotation" in side:
            turns.append(side["rotation"])
    turns.append((stream.get("tags") or {}).get("rotate"))
    for r in turns:
        try:
            if round(abs(float(r))) % 180 == 90:
                return True
        except (TypeError, ValueError):
            continue
    return False


def _extras(probe_exe: str, path: str) -> dict:
    """{"timecode": the first frame's ("" when none), "audio": how many audio tracks}."""
    out = subprocess.run([probe_exe, "-v", "error", "-show_entries",
                          "stream=codec_type:stream_tags=timecode:format_tags=timecode", "-of", "json", path],
                         capture_output=True, text=True, timeout=60, **_QUIET)
    try:
        data = json.loads(out.stdout or "{}")
    except ValueError:
        return {"timecode": "", "audio": 0}
    streams = data.get("streams") or []
    tc = (data.get("format") or {}).get("tags", {}).get("timecode") or next(
        (st.get("tags", {}).get("timecode") for st in streams if st.get("tags", {}).get("timecode")), "")
    return {"timecode": tc or "", "audio": sum(1 for st in streams if st.get("codec_type") == "audio")}


def fps_of(info: dict) -> Fraction:
    return Fraction(info["fps"])


# ----------------------------------------------------------------- frames --

class Reader:
    """A clip's frames, from a given one on, at its own depth (BGR, uint8 or
    uint16). frames(start, count) reads them in order - one decode of the
    whole run, the fast way; frame(i) is one on its own."""

    def __init__(self, info: dict, ffmpeg: str | None = None, alpha: bool = False):
        self.info = info
        # BGRA when asked for and the clip has transparency (a job's ProRes 4444 subject).
        self.channels = 4 if alpha and info.get("alpha") and info.get("reader") == "ffmpeg" else 3
        self.ffmpeg = ffmpeg if info.get("reader") == "ffmpeg" else None
        self.fps = fps_of(info)
        self.w, self.h = info["width"], info["height"]
        self.depth = info.get("depth", 8) if self.ffmpeg else 8

    def frame(self, i: int) -> np.ndarray:
        run = self.frames(i, 1)
        try:
            for _, f in run:
                return f
        finally:
            run.close()                          # its ffmpeg ends now, not when it's collected
        raise FootageError(f"There's no frame {i} in {os.path.basename(self.info['path'])}.")

    def frames(self, start: int, count: int):
        """(number, frame) for start .. start + count - 1, fewer at the clip's end."""
        start, count = max(0, int(start)), max(0, int(count))
        if self.info["frames"]:                  # 0: not known - read until the clip ends
            count = min(count, self.info["frames"] - start)
        if count <= 0:
            return
        if self.ffmpeg:
            yield from self._ffmpeg(start, count)
        else:
            yield from self._opencv(start, count)

    def _ffmpeg(self, start, count):
        deep = self.depth > 8
        at = math.floor(float(start / self.fps) * 1e6) / 1e6 if start else 0.0
        cmd = [self.ffmpeg, "-v", "error", "-nostdin"]
        if at:
            cmd += ["-ss", f"{at:.6f}"]
        c = self.channels
        fmt = ("bgra64le" if deep else "bgra") if c == 4 else ("bgr48le" if deep else "bgr24")
        cmd += ["-i", self.info["path"], "-map", "0:v:0", "-frames:v", str(count),
                "-vf", video_formats.decode_filter(self.info), "-f", "rawvideo", "-pix_fmt", fmt, "-"]
        size = self.w * self.h * c * (2 if deep else 1)
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **_QUIET)
        try:
            for n in range(count):
                buf = _read_exactly(proc.stdout, size)
                if buf is None:
                    break
                f = np.frombuffer(buf, np.uint16 if deep else np.uint8).reshape(self.h, self.w, c)
                yield start + n, f.copy()
        finally:
            proc.stdout.close()
            if proc.poll() is None:
                proc.kill()
            proc.wait()

    def _opencv(self, start, count):
        cap = cv2.VideoCapture(self.info["path"])
        try:
            if not cap.isOpened():
                raise FootageError(f"Can't read {os.path.basename(self.info['path'])} as video.")
            if start:
                cap.set(cv2.CAP_PROP_POS_FRAMES, start)
                got = int(round(cap.get(cv2.CAP_PROP_POS_FRAMES)))
                if got != start:
                    # A seek that missed: from the start, the slow way.
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    for _ in range(start):
                        if not cap.grab():
                            return
            for n in range(count):
                ok, f = cap.read()
                if not ok:
                    break
                yield start + n, f
        finally:
            cap.release()


def _read_exactly(stream, size):
    chunks, got = [], 0
    while got < size:
        b = stream.read(size - got)
        if not b:
            return None
        chunks.append(b)
        got += len(b)
    return b"".join(chunks)


# --------------------------------------------------------------- sequences --

class Sequence:
    """An image sequence in `folder`: name_000000.ext, numbered from `first`
    (the clip's own frame numbers, so a part of a clip comes back where it
    was). PNG or TIFF; 8 or 16-bit; BGR, or BGRA with transparency."""

    def __init__(self, folder: str, name: str, ext: str = ".png", digits: int = 6):
        self.folder, self.name, self.ext, self.digits = folder, name, ext.lower(), digits
        if self.ext not in (".png", ".tif", ".tiff"):
            raise FootageError(f"Sequences are PNG or TIFF, not {ext}.")
        os.makedirs(folder, exist_ok=True)

    def path(self, i: int) -> str:
        return os.path.join(self.folder, f"{self.name}_{i:0{self.digits}d}{self.ext}")

    def has(self, i: int) -> bool:
        return os.path.isfile(self.path(i))

    def write(self, i: int, img: np.ndarray) -> str:
        if img.dtype not in (np.uint8, np.uint16):
            raise FootageError("Sequences take 8 or 16-bit frames.")
        final = self.path(i)
        tmp = final[: -len(self.ext)] + ".part" + self.ext
        if not imwrite(tmp, img):
            raise FootageError(f"Couldn't write {os.path.basename(final)}.")
        os.replace(tmp, final)
        return final

    def read(self, i: int) -> np.ndarray | None:
        return imread(self.path(i)) if self.has(i) else None

    def first_missing(self, start: int, end: int) -> int:
        """The first frame in start .. end - 1 not written yet (end when all are)."""
        for i in range(start, end):
            if not self.has(i):
                return i
        return end

    def pattern(self) -> str:
        """As ffmpeg and Resolve name a sequence: name_%06d.png."""
        return os.path.join(self.folder, f"{self.name}_%0{self.digits}d{self.ext}")


# ------------------------------------------------------------------ depth --

def to_float(f: np.ndarray) -> np.ndarray:
    """A frame as float32 0..1, whatever its depth."""
    if f.dtype == np.uint8:
        return f.astype(np.float32) / 255.0
    if f.dtype == np.uint16:
        return f.astype(np.float32) / 65535.0
    return f.astype(np.float32)


def from_float(f: np.ndarray, dtype) -> np.ndarray:
    scale = 255.0 if dtype == np.uint8 else 65535.0
    return np.clip(np.rint(f * scale), 0, scale).astype(dtype)


# ----------------------------------------------------------------- encode --

def encode(cmd: list, count: int, out: str, progress=None) -> str:
    """Runs video_formats.encode_command()'s ffmpeg, saying
    progress(frames done) as it goes. Written to a temporary name and moved
    into place: a file that's there is a whole one."""
    final = out
    stem, ext = os.path.splitext(final)
    tmp = stem + ".part" + ext
    cmd = list(cmd)
    cmd[-1] = tmp
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                            text=True, encoding="utf-8", errors="replace", **_QUIET)
    tail = []

    def errors():
        for line in proc.stderr:
            tail.append(line.rstrip())
            del tail[:-12]

    reader = threading.Thread(target=errors, daemon=True)
    reader.start()
    try:
        for line in proc.stdout:
            key, _, value = line.strip().partition("=")
            if key == "frame" and progress is not None:
                try:
                    progress(min(count, int(value)))
                except ValueError:
                    pass
    finally:
        proc.stdout.close()
        proc.wait()
        reader.join(5)
        proc.stderr.close()
    if proc.returncode != 0 or not os.path.isfile(tmp):
        try:
            os.remove(tmp)
        except OSError:
            pass
        why = "\n".join(t for t in tail if t)[-600:]
        raise FootageError(f"ffmpeg couldn't write {os.path.basename(final)}." + (f"\n\n{why}" if why else ""))
    os.replace(tmp, final)
    return final
