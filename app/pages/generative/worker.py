#!/usr/bin/env python3
"""
Generative's engine process, run with the engine environment's Python
(engine_setup.py): holds the picture being worked on, its undo history
and its cut-out, and does the work - removal (fill.py's LaMa, or FLUX.2
klein - klein_fill.py - with the High quality add-on) and finding the
subject (matte.py). Nothing of Buddy's or Qt's.

fill and background take "hq": true (the page sends it when the add-on and
its model are there and Quality is High): removal is klein's then. If
klein can't load (no room on the card, a broken install), LaMa does it and
the reply says why ("note").

    worker.py --models <folder> --work <folder for previews>

One JSON line in, one out - the reply marked so stray library output on
stdout can't be mistaken for it:

    in   {"id": 3, "do": "fill", "args": {"strokes": [...]}}
    out  @@BUDDY@@{"id": 3, "stage": "Removing what you painted", "detail": "Part 1 of 2", "progress": 0.0}
         @@BUDDY@@{"id": 3, "ok": true, "result": {...}}
         @@BUDDY@@{"id": 3, "ok": false, "error": "..."}

Requests:
    open            {path}            the picture's facts and preview
    fill            {strokes, hq?}    Clean Up: the new preview, the box changed, the model used
    undo / redo                       the preview then
    save            {path}            the picture, at its own depth
    matte           {model, region?}  Cut Out: the subject found - matte and subject previews;
                                      with region (strokes drawn round it), looked for in there only
    outline         {region}          the strokes drawn round the subject as its matte, as they are
    matte_edit      {strokes}         the person's fixes to the matte ("keep" or not)
    background      {hq?}             Separate: the picture with the subject removed
    video_open      {path}            footage: its facts (footage.probe) and its first frame
    video_frame     {index, show?, pattern?, first?}  a frame to look at: the clip's ("source"), or one
                                      a job made ("clean" / "subject" / "background": its sequence
                                      pattern, or its video file starting at the clip's frame `first`)
    video_track     {keyframes, start, end, tracker?}   where the masks painted on keyframes are carried, frame
                                      by frame (track.carry_masks): a small mask picture per frame
    save_cutout     {path}            the subject on transparency (PNG / TIFF / EXR)
    save_background {path}            the clean background
    close                             drops the picture

A stroke: {"size": diameter, "erase" | "keep": bool, "feather": 0-100,
"points": [x0, y0, x1, ...]} in the PICTURE's pixels (not the preview's).
Or a shape: "shape": "rect" | "ellipse" with two corners as its points
(feather a share of half its shorter side), or "lasso" with its outline's
points (closed back to the first).
Feather is how much of the brush's radius fades: a removal blends that far
into the picture, a cut-out fix has that soft an edge.

The previews the view draws are 8-bit, at most PREVIEW_MAX on their longer
side ("scale" says how much smaller than the picture): JPEGs, and the
matte as a PNG whose alpha is the matte. A new file each time (the view's
cache never shows an old one); older ones are deleted.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import traceback

# Before cv2 loads: OpenEXR reading is off by default.
os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")

import numpy as np  # noqa: E402

import cv2  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fill  # noqa: E402
import footage  # noqa: E402
import gpu  # noqa: E402
import matte  # noqa: E402
import track  # noqa: E402
import track_sam  # noqa: E402

MARK = "@@BUDDY@@"
PREVIEW_MAX = 8192
KEEP_PREVIEWS = 3
# A frame of footage is shown at most this big on its longer side: 4K as it is. At 1920 a 4K frame was
# stretched back up on a large screen and looked soft beside Resolve's viewer (2026-10-08).
VIDEO_PREVIEW = 4096
# Previews as JPEG at full colour resolution (4:4:4) and quality 100: the default 4:2:0 smeared coloured
# edges, and at quality 95 a soft dark gradient zoomed in showed JPEG's 8 x 8 blocks (2026-10-08; 3x the
# camera file's own, against none added at 100). A frame of footage is a BMP instead - not compressed at
# all, and the quickest to make (6 ms for 4K, against 150-350 for PNG).
VIEW_JPEG = [cv2.IMWRITE_JPEG_QUALITY, 100, cv2.IMWRITE_JPEG_SAMPLING_FACTOR, cv2.IMWRITE_JPEG_SAMPLING_FACTOR_444]
WRITE_PARAMS = {".jpg": VIEW_JPEG, ".png": [cv2.IMWRITE_PNG_COMPRESSION, 1], ".bmp": []}
RESULTS = ("clean", "subject", "background")      # what video_job.py makes, as video_frame shows them
MAX_UNDO = 40
MODEL_FILES = {"lama": "inpainting_lama_2025jan.onnx", "birefnet-lite": "birefnet_lite_fp16.onnx",
               "birefnet": "birefnet_fp16.onnx"}
SAM_FOLDER = "sam2.1-tiny-video"                 # engine_setup.MODELS["sam2-tiny"]: Track's "Object"
KLEIN_FOLDER = "flux2-klein-4b"                  # engine_setup.MODELS["flux2-klein"]: removal, High quality
# What can be read (OpenCV's decoders) and what's written back in the
# same format; anything else is saved as PNG (or TIFF past 8 bits).
READS = {".jpg", ".jpeg", ".jpe", ".png", ".tif", ".tiff", ".bmp", ".dib", ".webp", ".exr", ".jp2",
         ".hdr", ".pic", ".pbm", ".pgm", ".ppm", ".pnm", ".pxm", ".sr", ".ras"}
WRITES_8BIT = {".jpg", ".jpeg", ".jpe", ".bmp", ".dib", ".webp"}
WRITES_ANY = {".png", ".tif", ".tiff"}


class Fail(Exception):
    """A request that can't be done, said as it is."""


# ------------------------------------------------------------- files --


def read_image(path: str) -> np.ndarray:
    ext = os.path.splitext(path)[1].lower()
    if ext not in READS:
        raise Fail(f"Generative can't open {ext or 'this kind of'} files. It opens JPEG, PNG, TIFF, WebP, "
                   "BMP, EXR and JPEG 2000 pictures.")
    try:
        data = np.fromfile(path, np.uint8)     # not imread: it can't take every Windows path
    except OSError as exc:
        raise Fail(f"Couldn't read the file: {exc}") from exc
    if ext in (".jpg", ".jpeg", ".jpe"):
        # Turned the way the camera says (EXIF), as everything else shows it.
        img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    else:
        img = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise Fail("Couldn't read that picture – the file may be damaged, or a kind OpenCV can't decode.")
    if img.dtype not in (np.uint8, np.uint16, np.float32):
        img = img.astype(np.float32)
    if img.ndim == 3 and img.shape[2] == 1:
        img = img[:, :, 0]
    return img


def save_path_for(img: np.ndarray, wanted: str, alpha: bool = False) -> str:
    """The format a picture is saved in: the one asked for when it can hold
    the picture; past 8 bits, PNG/TIFF/EXR only - and with transparency,
    PNG/TIFF/EXR whatever the depth."""
    ext = os.path.splitext(wanted)[1].lower()
    deep = img.dtype != np.uint8
    if ext in WRITES_ANY or (ext == ".exr" and img.dtype == np.float32):
        return wanted
    if ext in WRITES_8BIT and not deep and not alpha:
        return wanted
    stem = os.path.splitext(wanted)[0]
    return stem + (".tif" if deep else ".png")


def write_image(path: str, img: np.ndarray) -> None:
    ext = os.path.splitext(path)[1].lower()
    params = []
    out = img
    if ext in (".jpg", ".jpeg", ".jpe"):
        params = [cv2.IMWRITE_JPEG_QUALITY, 95]
    elif ext == ".webp":
        params = [cv2.IMWRITE_WEBP_QUALITY, 95]
    elif ext == ".png":
        params = [cv2.IMWRITE_PNG_COMPRESSION, 3]
    if img.dtype == np.float32 and ext != ".exr":
        out = np.clip(np.rint(img * 65535), 0, 65535).astype(np.uint16)
    ok, buf = cv2.imencode(ext, out, params)
    if not ok:
        raise Fail(f"Couldn't write a {ext} file.")
    tmp = path + ".buddy-saving"
    try:
        buf.tofile(tmp)
        os.replace(tmp, path)
    except OSError as exc:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise Fail(f"Couldn't save: {exc}") from exc


_NOISE = {}


def to_view(img: np.ndarray) -> np.ndarray:
    """The picture to look at, as to_8bit - a deep one rounded with a
    faint dither rather than cut down: cut to 8 bits, a soft gradient (a
    dark, out-of-focus 10-bit shot) showed bands Resolve's viewer doesn't.
    OpenCV's calls, the noise kept for the next frame: ~20 ms on a 4K
    frame, against ~110 ms in NumPy."""
    if img.dtype != np.uint16:
        return to_8bit(img)
    noise = _NOISE.get(img.shape)
    if noise is None:
        rng = np.random.default_rng(7)
        tile = rng.random((256, 256), np.float32) - rng.random((256, 256), np.float32)   # -1..1, triangular
        h, w = img.shape[:2]
        noise = np.tile(tile, (-(-h // 256), -(-w // 256)))[:h, :w]
        if img.ndim == 3:
            noise = np.repeat(noise[:, :, None], img.shape[2], axis=2)
        _NOISE.clear()
        noise = _NOISE[img.shape] = np.ascontiguousarray(noise)
    x = img.astype(np.float32)
    cv2.scaleAdd(x, 1 / 257, noise, dst=x)
    return to_8bit(cv2.convertScaleAbs(cv2.max(x, 0.0)))      # rounded, held to 0..255


def to_8bit(img: np.ndarray) -> np.ndarray:
    """The picture to look at: 8-bit BGR. Float (EXR) is taken as linear."""
    if img.dtype == np.uint16:
        out = (img >> 8).astype(np.uint8)
    elif img.dtype == np.float32:
        lin = np.clip(img, 0, 1)
        out = np.where(lin <= 0.0031308, lin * 12.92, 1.055 * np.power(lin, 1 / 2.4) - 0.055)
        out = np.clip(out * 255 + 0.5, 0, 255).astype(np.uint8)
    else:
        out = img
    if out.ndim == 2:
        out = cv2.cvtColor(out, cv2.COLOR_GRAY2BGR)
    elif out.shape[2] == 4:
        out = out[:, :, :3]
    return out


def mask_from(strokes, shape, outward: bool = True) -> np.ndarray:
    """Remove's paint as one mask, 0..1 - how much of the fill goes where:
    brush strokes, rectangles and ellipses in order, an erasing one taking
    paint off, each as soft as its feather (as the view draws them) - the
    feather fading out past each shape (outward), all of it replaced
    inside; outward False: half in, half out (a drawn outline's edge)."""
    paint = [dict(s, keep=not s.get("erase")) for s in strokes or () if isinstance(s, dict)]
    return matte.apply_strokes(np.zeros(shape[:2], np.float32), paint, soft=0.0, outward=outward)


def region_box(within: np.ndarray) -> tuple[int, int, int, int]:
    """The part of the picture to look for the subject in: round what was
    drawn, with room on every side - an eighth of its longer side, at least
    16 px - so the model sees the subject's surroundings too."""
    ys, xs = np.nonzero(within > fill.FAINT)
    h, w = within.shape
    x0, x1, y0, y1 = int(xs.min()), int(xs.max()) + 1, int(ys.min()), int(ys.max()) + 1
    pad = max(16, (max(x1 - x0, y1 - y0) + 7) // 8)
    return max(0, x0 - pad), max(0, y0 - pad), min(w, x1 + pad), min(h, y1 + pad)


def _scale_of(fl: np.ndarray) -> float:
    return 255.0 if fl.dtype == np.uint8 else 65535.0 if fl.dtype == np.uint16 else 1.0


# ------------------------------------------------------------ engine --


class Engine:
    def __init__(self, models: str, work: str, say):
        self.models_dir, self.work, self.say = models, work, say
        self.loaded = {}                       # model id -> Filler / Matter
        self._loading = threading.Lock()
        self.img = None
        self.path = ""
        self.undo, self.redo = [], []          # (x, y, before, after) patches
        self.matte_base = None                 # the model's matte (refined), H x W float
        self.matte_strokes = []
        self.matte = None                      # with the person's fixes
        self.matte_model = ""
        self._fg = None                        # subject colours, for the current picture and matte
        self.background = None                 # Separate's plate, for the current picture and matte
        self.files = {}                        # kind -> [preview paths], newest last
        self.n = 0
        self.video = None                      # footage open: a footage.Reader
        self.tracked = []                      # video_track's mask pictures
        self.result_readers = {}               # a job's video file -> footage.Reader, to scrub through
        self.hq_failed = ""                    # why klein couldn't load: LaMa removes instead
        os.makedirs(work, exist_ok=True)

    # -- models

    def _removal(self, hq):
        """The removal model to use: klein when High quality is asked for (and
        until it fails to load), else LaMa."""
        if hq and not self.hq_failed and os.path.isfile(os.path.join(self.models_dir, KLEIN_FOLDER, "transformer",
                                                                          "config.json")):
            return "flux2-klein"
        return "lama"

    def _model(self, mid):
        with self._loading:
            if mid == "flux2-klein" and self.hq_failed:
                mid = "lama"
            if mid == "flux2-klein" and mid not in self.loaded:
                try:
                    import importlib
                    importlib.invalidate_caches()      # the add-on may have come since this process started
                    import klein_fill
                    lama = os.path.join(self.models_dir, MODEL_FILES["lama"])
                    prefill = None
                    if os.path.isfile(lama):
                        self.loaded.setdefault("lama", fill.Filler(lama))
                        prefill = klein_fill.lama_prefill(self.loaded["lama"])
                    self.loaded[mid] = klein_fill.KleinFiller(os.path.join(self.models_dir, KLEIN_FOLDER), prefill)
                except Exception as exc:  # noqa: BLE001 - LaMa then, and said why
                    traceback.print_exc(file=sys.stderr)
                    self.hq_failed = (str(exc).strip().splitlines() or [type(exc).__name__])[0][:240]
                    mid = "lama"
            if mid not in self.loaded:
                path = os.path.join(self.models_dir, MODEL_FILES[mid])
                if not os.path.isfile(path):
                    raise Fail("That model isn't downloaded.")
                self.loaded[mid] = (fill.Filler(path) if mid == "lama" else matte.Matter(path))
        return self.loaded[mid]

    def _first_load_note(self, rid, mid):
        """What to say while a model loads for the first time: compiling it
        for the graphics card (a minute or two, once), or the engine starting."""
        path = os.path.join(self.models_dir, MODEL_FILES[mid]) if mid in MODEL_FILES else ""
        if mid == "flux2-klein":
            return {"id": rid, "stage": "Loading FLUX.2 klein", "detail": "The first time takes about 15 seconds"}
        label = {"lama": "the removal model", "birefnet": "the subject model",
                 "birefnet-lite": "the subject model"}.get(mid, "SAM 2")
        if gpu.devices() and path and not gpu.compiled(path):
            return {"id": rid, **gpu.preparing_text(label)}
        if mid == "sam2-tiny" and gpu.devices() and not all(
                gpu.compiled(os.path.join(self.models_dir, SAM_FOLDER, n + ".onnx"))
                for n in ("vision_encoder", "mask_decoder", "memory_encoder", "memory_attention", "pointer_tpos")):
            return {"id": rid, **gpu.preparing_text(label)}
        return {"id": rid, "stage": "Starting the engine", "detail": "The first time takes a few seconds"}

    def preload(self, mid):
        """Loads a model on a thread of its own (about a second) while the
        picture opens, so the first use doesn't wait for it."""
        def load():
            try:
                self._model(mid)
            except Exception:  # noqa: BLE001 - the first use says what's wrong
                pass
        threading.Thread(target=load, daemon=True).start()

    def _starting(self, rid, mid, stage):
        """Says the engine is starting while a model loads for the first time."""
        if mid not in self.loaded:
            self.say(self._first_load_note(rid, mid))
        self._model(mid)
        self.say({"id": rid, "stage": stage, "progress": 0.0})

    # -- previews

    def _write(self, kind, view, ext=".jpg", params=None):
        self.n += 1
        path = os.path.join(self.work, f"{kind}_{os.getpid()}_{self.n}{ext}")
        ok, buf = cv2.imencode(ext, view, params or WRITE_PARAMS[ext])
        if not ok:
            raise Fail("Couldn't draw the preview.")
        buf.tofile(path)
        old = self.files.setdefault(kind, [])
        old.append(path)
        keep = 1 if kind == "original" else KEEP_PREVIEWS
        for p in old[:-keep]:
            try:
                os.remove(p)
            except OSError:
                pass
        self.files[kind] = old[-keep:]
        return path

    def _scale(self):
        h, w = self.img.shape[:2]
        return min(1.0, PREVIEW_MAX / max(h, w))

    def _small(self, view, interpolation=cv2.INTER_AREA):
        h, w = view.shape[:2]
        s = self._scale()
        if s < 1:
            view = cv2.resize(view, (max(1, round(w * s)), max(1, round(h * s))), interpolation=interpolation)
        return view

    def _preview(self, kind="preview", img=None):
        return {kind: self._write(kind, self._small(to_view(self.img if img is None else img))),
                "scale": self._scale()}

    def _state(self):
        return {"undo": len(self.undo), "redo": len(self.redo)}

    def _picture_changed(self):
        """The picture was edited: the cut-out's colours and the clean
        background were made from the old one."""
        self._fg = None
        self.background = None

    # -- open / close

    def do_open(self, path):
        img = read_image(path)
        self.close_files()
        self.video = None
        self.img, self.path = img, path
        self.undo, self.redo = [], []
        self._matte_reset()
        h, w = img.shape[:2]
        depth = {np.dtype(np.uint8): "8-bit", np.dtype(np.uint16): "16-bit"}.get(img.dtype, "32-bit float")
        channels = 1 if img.ndim == 2 else img.shape[2]
        return {"width": w, "height": h, "depth": depth, "channels": channels, "path": path,
                "original": self._preview("original")["original"], **self._preview(), **self._state()}

    def _matte_reset(self):
        self.matte_base = self.matte = None
        self.matte_strokes = []
        self.matte_model = ""
        self._fg = None
        self.background = None

    def close_files(self):
        for paths in self.files.values():
            for p in paths:
                try:
                    os.remove(p)
                except OSError:
                    pass
        self.files = {}
        self.result_readers = {}

    def do_close(self):
        self.close_files()
        self.img, self.path, self.undo, self.redo = None, "", [], []
        self.video = None
        self._matte_reset()
        return {}

    # -- footage: looking at it and checking the tracking (the work itself is video_job.py's)

    def do_video_open(self, path):
        if os.path.splitext(path)[1].lower() in READS:
            raise Fail("That's a picture, not footage.")
        ff = footage.find_ffmpeg()
        try:
            info = footage.probe(path, ff)
        except footage.FootageError as exc:
            raise Fail(str(exc)) from None
        if not info["frames"]:
            raise Fail(f"Can't tell how long {os.path.basename(path)} is.")
        self.close_files()
        self.img, self.path, self.undo, self.redo = None, path, [], []
        self._matte_reset()
        self.video = footage.Reader(info, ff)
        return {**info, "ffmpeg": bool(ff), **self.do_video_frame(0)}

    def do_video_frame(self, index, show="source", pattern="", first=0):
        """Frame `index` of the clip - or of what a job made: a sequence
        (`pattern`, numbered as the clip is) or a video file starting at the
        clip's frame `first`."""
        if self.video is None:
            raise Fail("Open the footage first.")
        index = max(0, min(int(index), self.video.info["frames"] - 1))
        if show == "source":
            try:
                f = self.video.frame(index)
            except footage.FootageError as exc:
                raise Fail(str(exc)) from None
        elif show in RESULTS and pattern:
            if "%06d" in pattern:
                path = pattern.replace("%06d", f"{index:06d}")
                f = footage.imread(path) if os.path.isfile(path) else None
            else:
                f = self._result_frame(pattern, index - int(first))
            if f is None:                       # not made (yet): the clip's own frame, said so
                return {**self.do_video_frame(index), "missing": True}
        else:
            raise Fail("Nothing to show there.")
        h, w = f.shape[:2]
        scale = min(1.0, VIDEO_PREVIEW / max(h, w))
        alpha = f.ndim == 3 and f.shape[2] == 4
        view = to_view(f) if not alpha else (
            f if f.dtype == np.uint8 else np.clip((f.astype(np.float32) + 128) * (1 / 257), 0, 255).astype(np.uint8))
        if scale < 1:
            view = cv2.resize(view, (max(1, round(w * scale)), max(1, round(h * scale))), interpolation=cv2.INTER_AREA)
        path = self._write("frame", view, ".png" if alpha else ".bmp")
        return {"preview": path, "scale": scale, "index": index, "show": show}

    def _result_frame(self, path, i):
        """Frame i of a video file a job made (with its transparency, if it has
        some), or None when it isn't there or hasn't that frame."""
        reader = self.result_readers.get(path)
        if reader is None:
            if not os.path.isfile(path):
                return None
            try:
                info = footage.probe(path, footage.find_ffmpeg())
            except footage.FootageError:
                return None
            reader = self.result_readers[path] = footage.Reader(info, footage.find_ffmpeg(), alpha=True)
        if not 0 <= i < (reader.info["frames"] or i + 1):
            return None
        try:
            return reader.frame(i)
        except footage.FootageError:
            return None

    def sam(self):
        """SAM 2 (track_sam.Sam), loaded once - None when it isn't downloaded."""
        with self._loading:
            if "sam2-tiny" not in self.loaded:
                folder = os.path.join(self.models_dir, SAM_FOLDER)
                if not os.path.isfile(os.path.join(folder, "constants.json")):
                    return None
                self.loaded["sam2-tiny"] = track_sam.Sam(folder)
        return self.loaded["sam2-tiny"]

    def do_video_track(self, rid, keyframes, start, end, tracker="standard"):
        """Carries the masks painted on keyframes ({frame: strokes}) through
        start .. end - 1: a small mask picture per frame, white where it's painted and
        see-through where it isn't (and the masks themselves, for the job).
        tracker "object": SAM 2's object under the paint is followed
        (track_sam) - "objects" says how it took each keyframe."""
        if self.video is None:
            raise Fail("Open the footage first.")
        h, w = self.video.info["height"], self.video.info["width"]
        start, end = max(0, int(start)), min(self.video.info["frames"], int(end))
        keys = {int(k): v for k, v in (keyframes or {}).items() if start <= int(k) < end and v}
        if not keys:
            raise Fail("Paint on a frame inside the part to work on first.")
        sam = None
        if tracker == "object":
            if "sam2-tiny" not in self.loaded:
                self.say(self._first_load_note(rid, "sam2-tiny"))
            sam = self.sam()
            if sam is None:
                raise Fail("SAM 2 isn't downloaded.")
        for p in self.tracked:
            try:
                os.remove(p)
            except OSError:
                pass
        self.tracked = []
        try:
            carried, how = track_sam.follow(
                self.video.frames(start, end - start), {k: mask_from(v, (h, w)) for k, v in keys.items()},
                start, end, (h, w), sam, lambda detail, p: self.say(
                    {"id": rid, "stage": "Tracking", "detail": detail, "progress": p}))
        except footage.FootageError as exc:
            raise Fail(str(exc)) from None
        out, exact = {}, {}
        self.n += 1
        for i, m in carried.items():
            path = os.path.join(self.work, f"track_{os.getpid()}_{self.n}_{i}.png")
            # White, as transparent as it isn't painted: the view tints it.
            a = np.clip(m * 255 + 0.5, 0, 255).astype(np.uint8)
            ok, buf = cv2.imencode(".png", np.dstack([np.full_like(a, 255)] * 3 + [a]),
                                   [cv2.IMWRITE_PNG_COMPRESSION, 1])
            if ok:
                buf.tofile(path)
                self.tracked.append(path)
                out[i] = path
            # The mask itself, 16-bit, for the job to use as it is (video_job "tracked").
            path = os.path.join(self.work, f"trackmask_{os.getpid()}_{self.n}_{i}.png")
            ok, buf = cv2.imencode(".png", np.clip(np.rint(m * 65535), 0, 65535).astype(np.uint16),
                                   [cv2.IMWRITE_PNG_COMPRESSION, 1])
            if ok:
                buf.tofile(path)
                self.tracked.append(path)
                exact[i] = path
        return {"masks": out, "exact": exact, "start": start, "end": max(carried) + 1, "objects": how}

    def _need_picture(self):
        if self.img is None:
            raise Fail("Open a picture first.")

    # -- Clean Up

    def do_fill(self, rid, strokes, hq=False, margin=0):
        self._need_picture()
        mask = fill.widen(mask_from(strokes, self.img.shape), margin)      # Margin: past what's painted
        if not (mask > fill.FAINT).any():
            raise Fail("Paint over what to remove first.")
        started = time.monotonic()
        mid = self._removal(hq)
        self._starting(rid, mid, "Removing what you painted")
        filler = self._model(mid)
        said = lambda n, total: self.say(
            {"id": rid, "stage": "Removing what you painted",
             "detail": f"Part {n + 1} of {total}" if total > 1 and mid == "lama" else "",
             "progress": n / max(total, 1)})
        out, box = filler.fill(self.img, mask, said)
        if box is None:
            raise Fail("Nothing to remove there.")
        self.say({"id": rid, "stage": "Removing what you painted", "detail": "Drawing the result", "progress": 1.0})
        x, y, w, h = box
        self.undo.append((x, y, self.img[y:y + h, x:x + w].copy(), out[y:y + h, x:x + w].copy()))
        self.undo = self.undo[-MAX_UNDO:]
        self.redo = []
        self.img = out
        self._picture_changed()
        return {"box": list(box), "seconds": round(time.monotonic() - started, 1), **self._used(mid, hq),
                **self._preview(), **self._state()}

    def _used(self, mid, hq):
        """Which model removed it - and, when klein was wanted but LaMa did it, why."""
        used = "flux2-klein" if mid == "flux2-klein" and not self.hq_failed else "lama"
        out = {"model": used}
        if hq and used == "lama" and self.hq_failed:
            out["note"] = f"High quality couldn't start ({self.hq_failed}) – removed with LaMa instead."
        return out

    def _step(self, from_list, to_list, undoing):
        self._need_picture()
        if not from_list:
            raise Fail("Nothing to undo." if undoing else "Nothing to redo.")
        x, y, before, after = from_list.pop()
        patch = before if undoing else after
        self.img[y:y + patch.shape[0], x:x + patch.shape[1]] = patch
        to_list.append((x, y, before, after))
        self._picture_changed()
        return {**self._preview(), **self._state()}

    def do_undo(self):
        return self._step(self.undo, self.redo, True)

    def do_redo(self):
        return self._step(self.redo, self.undo, False)

    def do_save(self, path):
        self._need_picture()
        path = save_path_for(self.img, path)
        write_image(path, self.img)
        return {"path": path}

    # -- Cut Out

    def do_matte(self, rid, model, region=None):
        self._need_picture()
        if model not in ("birefnet", "birefnet-lite"):
            raise Fail("Unknown subject model.")
        within = self._region(region) if region else None
        started = time.monotonic()
        self._starting(rid, model, "Finding the subject")
        view = to_8bit(self.img)
        h, w = view.shape[:2]
        x0, y0, x1, y1 = 0, 0, w, h
        if within is not None:
            # Only inside what was drawn round it: the model looks at that
            # part (and a little round it), nothing outside it is kept.
            x0, y0, x1, y1 = region_box(within)
            self.say({"id": rid, "stage": "Finding the subject", "detail": "Looking inside what you drew"})
        else:
            self.say({"id": rid, "stage": "Finding the subject", "detail": "Looking at the whole picture"})
        part = np.ascontiguousarray(view[y0:y1, x0:x1])
        found = self._model(model)
        ph, pw = part.shape[:2]
        small = found.find(cv2.resize(part, (matte.SIZE, matte.SIZE), interpolation=cv2.INTER_AREA)) \
            if max(ph, pw) > matte.SIZE else None
        if small is not None:
            # The model sees 1024 px either way: found small, then sharpened
            # against the full picture - said as its own step.
            self.say({"id": rid, "stage": "Finding the subject", "detail": "Sharpening the edges", "progress": 0.6})
            found_part = matte.refine(part, cv2.resize(small, (pw, ph), interpolation=cv2.INTER_LINEAR))
        else:
            found_part = found.find(part)
        base = np.zeros((h, w), np.float32)
        base[y0:y1, x0:x1] = np.clip(found_part, 0, 1)
        if within is not None:
            base *= within
        self._new_matte(base, model)
        self.say({"id": rid, "stage": "Finding the subject", "detail": "Cleaning the edge colours", "progress": 0.85})
        return {"seconds": round(time.monotonic() - started, 1), **self._matte_previews()}

    def do_outline(self, rid, region):
        """What was drawn round the subject, as its matte - no model."""
        self._need_picture()
        within = self._region(region)
        started = time.monotonic()
        self.say({"id": rid, "stage": "Cutting out what you drew", "detail": "Cleaning the edge colours",
                  "progress": 0.4})
        self._new_matte(within, "outline")
        return {"seconds": round(time.monotonic() - started, 1), **self._matte_previews()}

    def _region(self, region) -> np.ndarray:
        """Strokes drawn round the subject (an erasing one takes away) as
        0..1, as soft as their feathers."""
        within = mask_from(region if isinstance(region, list) else [], self.img.shape, outward=False)
        if not (within > fill.FAINT).any():
            raise Fail("Draw round the subject first.")
        return within

    def _new_matte(self, base, model):
        self.matte_base = np.clip(base, 0, 1).astype(np.float32)
        self.matte_model = model
        self.matte_strokes = []
        self.matte = self.matte_base
        self._fg = None
        self.background = None

    def do_matte_edit(self, rid, strokes):
        self._need_picture()
        if self.matte_base is None:
            raise Fail("Find the subject first.")
        self.say({"id": rid, "stage": "Updating the cut-out", "progress": 0.3})
        self.matte_strokes = list(strokes or [])
        self.matte = matte.apply_strokes(self.matte_base, [s for s in self.matte_strokes if isinstance(s, dict)])
        self._fg = None
        self.background = None
        return self._matte_previews()

    def _foreground(self):
        if self._fg is None:
            fl = self.img.astype(np.float32) / _scale_of(self.img)
            color = fl if fl.ndim == 2 else fl[:, :, :3]
            self._fg = matte.foreground(color, self.matte)
        return self._fg

    def _matte_previews(self):
        a = self.matte
        small_a = self._small(a, cv2.INTER_AREA)
        h, w = small_a.shape
        rgba = np.full((h, w, 4), 255, np.uint8)
        rgba[:, :, 3] = np.clip(small_a * 255 + 0.5, 0, 255).astype(np.uint8)
        f = self._foreground()
        fg = f if self.img.dtype == np.float32 else np.clip(f, 0, 1)
        subject = to_8bit((fg * _scale_of(self.img)).astype(self.img.dtype) if self.img.dtype != np.float32
                          else fg.astype(np.float32))
        return {"matte": self._write("matte", rgba, ".png"),
                "subject": self._write("subject", self._small(subject)),
                "scale": self._scale(), "coverage": round(float(a.mean()), 4),
                "model": self.matte_model, "strokes": len(self.matte_strokes)}

    def _cutout(self):
        """The subject with the matte as its alpha, at the picture's depth."""
        f = self._foreground()
        scale = _scale_of(self.img)
        a = self.matte
        if self.img.ndim == 3 and self.img.shape[2] == 4:      # its own transparency too
            a = a * (self.img[:, :, 3].astype(np.float32) / scale)
        color = f if f.ndim == 3 else np.repeat(f[:, :, None], 3, axis=2)
        out = np.dstack([color, a[:, :, None]])
        if self.img.dtype == np.float32:
            return out.astype(np.float32)
        return np.clip(np.rint(np.clip(out, 0, 1) * scale), 0, scale).astype(self.img.dtype)

    def do_save_cutout(self, path):
        self._need_picture()
        if self.matte is None:
            raise Fail("Find the subject first.")
        path = save_path_for(self.img, path, alpha=True)
        write_image(path, self._cutout())
        return {"path": path}

    # -- Separate

    def do_background(self, rid, hq=False, margin=0):
        self._need_picture()
        if self.matte is None:
            raise Fail("Find the subject first.")
        hole = self.matte > 0.03
        if not hole.any():
            raise Fail("There's no subject to take out.")
        if margin:
            hole = fill.widen(hole, margin)                                  # Margin: past the subject's edge
        started = time.monotonic()
        mid = self._removal(hq)
        self._starting(rid, mid, "Making the clean background")
        filler = self._model(mid)
        said = lambda n, total: self.say(
            {"id": rid, "stage": "Making the clean background",
             "detail": f"Filling where the subject was – part {n + 1} of {total}" if total > 1
             else "Filling where the subject was", "progress": n / max(total, 1)})
        out, _box = filler.fill(self.img, hole, said)
        self.background = out
        return {"seconds": round(time.monotonic() - started, 1), **self._used(mid, hq),
                **self._preview("background", out)}

    def do_save_background(self, path):
        self._need_picture()
        if self.background is None:
            raise Fail("Make the clean background first.")
        path = save_path_for(self.background, path)
        write_image(path, self.background)
        return {"path": path}


def make_say():
    lock = threading.Lock()

    def say(data):
        with lock:
            sys.stdout.write(MARK + json.dumps(data) + "\n")
            sys.stdout.flush()
    return say


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--preload", default="")
    a = ap.parse_args()
    say = make_say()
    engine = Engine(a.models, a.work, say)
    for mid in filter(None, a.preload.split(",")):
        engine.preload(mid)
    say({"ready": True})
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            continue
        rid, do, args = req.get("id"), req.get("do"), req.get("args") or {}
        try:
            if do == "fill":
                result = engine.do_fill(rid, args.get("strokes"), bool(args.get("hq")),
                                        margin=int(args.get("margin") or 0))
            elif do == "matte":
                result = engine.do_matte(rid, str(args.get("model") or ""), args.get("region"))
            elif do == "outline":
                result = engine.do_outline(rid, args.get("region"))
            elif do == "matte_edit":
                result = engine.do_matte_edit(rid, args.get("strokes"))
            elif do == "background":
                result = engine.do_background(rid, bool(args.get("hq")), margin=int(args.get("margin") or 0))
            elif do in ("open", "save", "save_cutout", "save_background"):
                result = getattr(engine, "do_" + do)(str(args.get("path") or ""))
            elif do == "video_open":
                result = engine.do_video_open(str(args.get("path") or ""))
            elif do == "video_frame":
                result = engine.do_video_frame(int(args.get("index") or 0), str(args.get("show") or "source"),
                                               str(args.get("pattern") or ""), int(args.get("first") or 0))
            elif do == "video_track":
                result = engine.do_video_track(rid, args.get("keyframes"), int(args.get("start") or 0),
                                               int(args.get("end") or 0), str(args.get("tracker") or "standard"))
            elif do in ("undo", "redo", "close"):
                result = getattr(engine, "do_" + do)()
            elif do == "quit":
                break
            else:
                raise Fail(f"Unknown request {do!r}.")
            say({"id": rid, "ok": True, "result": result})
        except Fail as exc:
            say({"id": rid, "ok": False, "error": str(exc)})
        except MemoryError:
            say({"id": rid, "ok": False, "error": "Not enough memory for a picture this big."})
        except Exception as exc:  # noqa: BLE001 - said in Buddy
            traceback.print_exc(file=sys.stderr)
            say({"id": rid, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
    engine.close_files()


if __name__ == "__main__":
    main()
