#!/usr/bin/env python3
"""
One of Generative's footage jobs, start to finish, in a process of its own
(the engine's Python): what to do is in a JSON file, progress comes out as
worker.py's replies do. Frames are written as image sequences, a frame at
a time; with a video format they're made into one file at the end (with
the source's audio and timecode) and then let go of. Stopping it is ending
the process; started again with the same file it picks up at the first
frame not written yet.

    video_job.py <job.json>

    {"op": "remove" | "cutout" | "separate",
     "source": "C:/.../A001.mov", "start": 120, "end": 240,   (frames, end not included)
     "keyframes": {"120": [strokes], "180": [strokes]},
         remove: what's painted on those frames (worker.py's strokes), carried to the rest;
         cutout / separate: optional - drawn round the subject, to find it only in there
     "tracked": {"masks": {"120": "C:/.../mask.png", ...}}   optional: Track's masks for
         every frame of the part (worker.do_video_track, 16-bit), used instead of tracking again
     "out": "C:/.../Buddy Generative/A001 120-239", "name": "A001",
     "video_format": video_formats.FORMATS's id ("sequence" when left out),
     "format": ".png" | ".tif"   (a sequence's frames),
     "models": "~/.buddy/generative/models", "model": "birefnet-lite" | "birefnet",
     "ffmpeg": optional path, "radius": 6, "filler": "lama" | "klein" | "opencv",
     "tracker": "standard" | "object"   (when there's no "tracked": how to track - "object" follows
         SAM 2's object under the paint, track_sam.py),
     "engine": "standard" | "rose"   (how what's removed is filled: "standard" - video_fill.py, the
         frames round it, then the filler; "rose" - ROSE draws the whole part again, rose_fill.py,
         the High quality add-on's),
     "effects": bool   (with "rose": what ROSE changed round the hole goes in too - its shadow, its
         reflection: rose_fill.effect_masks),
     "fine": bool   (with "rose": drawn at about 720p instead of 480p where the cut's bigger - sharper on
         4K, about 5x slower),
     "margin": px   (what's removed taken this far past what's painted - or past the subject, for
         separate's background: fill.widen)}

Out (in "out"), with a video format:
    remove     name_clean.mov                       the footage with it removed
    cutout     name_subject.mov                     the subject on transparency (ProRes 4444)
    separate   name_subject.mov                     the same
               name_background.mov                  what's behind it, the subject removed
  frames on the way in .work/frames/ (gone once the files are made); with "sequence":
    remove     name_clean_000120.png ...
    separate   name_subject_000120.png ...          (PNG, 8 or 16-bit, with transparency)
               name_background_000120.png ...
  and        .work/name_matte_000120.png ...      the steadied mattes (16-bit), for the background pass

Replies, one JSON line each after @@BUDDY@@ (as worker.py's):
    {"stage": "Tracking the mask", "frame": 130, "of": 120, "progress": 0.1}
    {"stage": "Removing", "frame": 130, "of": 120, "progress": 0.1, "kind": "clean", "pattern": "..._%06d.png"}
    {"done": true, "outputs": {"clean": "C:/.../A001_clean.mov"}, "seconds": 812.4}
    {"error": "..."}
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import shutil
import sys
import time
import traceback

os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")

import numpy as np  # noqa: E402

import cv2  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fill  # noqa: E402
import footage  # noqa: E402
import gpu  # noqa: E402
import matte as matting  # noqa: E402
import track  # noqa: E402
import track_sam  # noqa: E402
import video_fill  # noqa: E402
import video_formats  # noqa: E402
import worker  # noqa: E402

MARK = "@@BUDDY@@"


def say(data):
    sys.stdout.write(MARK + json.dumps(data) + "\n")
    sys.stdout.flush()


class Frames:
    """A clip's frames, read in order and kept while they're near the frame
    being worked on: asking for one further on reads on to it; asking for
    one already let go of starts reading again from there."""

    def __init__(self, reader: footage.Reader):
        self.reader = reader
        self.kept = {}
        self._run = None
        self._next = None

    def get(self, i):
        if i in self.kept:
            return self.kept[i]
        if i < 0 or (self.reader.info["frames"] and i >= self.reader.info["frames"]):
            return None
        if self._run is None or self._next is None or i < self._next:
            if self._run is not None:
                self._run.close()                  # its ffmpeg ends with it
            self._run = self.reader.frames(i, (self.reader.info["frames"] or i + 100000) - i)
            self._next = i
        for n, f in self._run:
            self.kept[n] = f
            self._next = n + 1
            if n >= i:
                break
        return self.kept.get(i)

    def keep(self, lo, hi):
        for i in [i for i in self.kept if i < lo or i > hi]:
            del self.kept[i]

    def close(self):
        if self._run is not None:
            self._run.close()
            self._run = None


class Job:
    def __init__(self, spec: dict):
        self.spec = spec
        self.op = spec["op"]
        if self.op not in ("remove", "cutout", "separate"):
            raise footage.FootageError(f"Unknown job {self.op!r}.")
        self.ffmpeg = footage.find_ffmpeg(spec.get("ffmpeg"))
        self.info = footage.probe(spec["source"], self.ffmpeg)
        self.reader = footage.Reader(self.info, self.ffmpeg)
        total = self.info["frames"]
        self.start = max(0, int(spec.get("start", 0)))
        self.end = min(total, int(spec.get("end", total))) if total else int(spec.get("end", 0))
        if self.end <= self.start:
            raise footage.FootageError("There are no frames in that part of the clip.")
        self.radius = int(spec.get("radius", video_fill.RADIUS))
        self.h, self.w = self.info["height"], self.info["width"]
        self.out = spec["out"]
        self.name = spec.get("name") or os.path.splitext(os.path.basename(spec["source"]))[0]
        self.video_format = video_formats.known(spec.get("video_format") or "sequence")
        self.work = os.path.join(self.out, ".work")
        os.makedirs(self.work, exist_ok=True)
        if video_formats.is_video(self.video_format):
            if not self.ffmpeg:
                raise footage.FootageError("Making a video file needs ffmpeg – pick Image sequence, or install ffmpeg.")
            self.frames_dir, self.ext = os.path.join(self.work, "frames"), ".png"
        else:
            self.frames_dir, self.ext = self.out, spec.get("format", ".png")
        self.keyframes = {int(k): v for k, v in (spec.get("keyframes") or {}).items()
                          if self.start <= int(k) < self.end and v}
        self._exact = {}
        self.small_masks = {}
        self.mask_files = {}
        self.flow = track.Flow()
        self.margin = float(spec.get("margin") or 0)
        self.started = time.monotonic()

    # -- models

    def _model_path(self, mid):
        folder = os.path.expanduser(self.spec.get("models") or "~/.buddy/generative/models")
        return os.path.join(folder, worker.MODEL_FILES[mid])

    def _model_folder(self, name):
        return os.path.join(os.path.expanduser(self.spec.get("models") or "~/.buddy/generative/models"), name)

    def filler(self):
        if self.spec.get("filler") == "opencv":
            return video_fill.inpaint_filler
        if self.spec.get("filler") == "klein":
            import klein_fill
            say({"stage": "Loading FLUX.2 klein", "detail": "About 15 seconds", "progress": 0.0})
            lama = self._model_path("lama")
            prefill = klein_fill.lama_prefill(fill_model(lama)) if os.path.isfile(lama) else None
            return video_fill.model_filler(klein_fill.KleinFiller(self._model_folder("flux2-klein-4b"), prefill))
        path = self._model_path("lama")
        if gpu.devices() and not gpu.compiled(path):
            say(gpu.preparing_text("the removal model"))
        return video_fill.model_filler(fill_model(path))

    # -- masks painted on keyframes, carried to every frame

    def exact_mask(self, i):
        """A keyframe's own mask, full size."""
        if i not in self._exact:
            self._exact[i] = worker.mask_from(self.keyframes[i], (self.h, self.w))
        return self._exact[i]

    def track_masks(self, stage="Tracking the mask"):
        """Carries the keyframes' masks to every frame of the part (small) -
        following SAM 2's object too when the spec's "tracker" is "object"."""
        if not self.keyframes:
            return
        sam = None
        if self.spec.get("tracker") == "object":
            folder = os.path.join(os.path.expanduser(self.spec.get("models") or "~/.buddy/generative/models"),
                                  worker.SAM_FOLDER)
            if os.path.isfile(os.path.join(folder, "constants.json")):
                if gpu.devices() and not gpu.compiled(os.path.join(folder, "vision_encoder.onnx")):
                    say(gpu.preparing_text("SAM 2"))
                sam = track_sam.Sam(folder)
        self.small_masks, _ = track_sam.follow(
            self.reader.frames(self.start, self.end - self.start),
            {k: self.exact_mask(k) for k in self.keyframes}, self.start, self.end, (self.h, self.w), sam,
            lambda detail, p: say({"stage": stage, "detail": detail, "progress": p}), self.flow)

    def use_tracked(self):
        """Track's masks (spec "tracked"), when there's one for every frame of
        the part - read as they're needed. False: they aren't all there."""
        got = {}
        for k, path in ((self.spec.get("tracked") or {}).get("masks") or {}).items():
            try:
                got[int(k)] = path
            except (TypeError, ValueError):
                continue
        if not all(i in got and os.path.isfile(got[i]) for i in range(self.start, self.end)):
            return False
        self.mask_files = got
        say({"stage": "Using the tracking", "progress": 0.0})
        return True

    def follow(self, stage="Tracking the mask"):
        if self.keyframes and all(i in self.keyframes for i in range(self.start, self.end)):
            return                       # painted on every frame: each one's own paint, nothing to carry
        if not self.use_tracked():
            self.track_masks(stage)

    def widened(self, mask_of):
        """mask_of with the spec's margin round each frame's mask (as it is without one)."""
        if self.margin <= 0:
            return mask_of
        return lambda i: (lambda m: None if m is None else fill.widen(m, self.margin))(mask_of(i))

    def mask(self, i):
        """Frame i's mask, full size: a keyframe's own, else carried."""
        if i in self.keyframes:
            return self.exact_mask(i)
        if i in self.mask_files:
            m = footage.imread(self.mask_files[i])
            if m is None:
                # Never a frame left as it was without a word (2026-10-07: the engine's copies went mid-job,
                # and all but the painted frame came out untouched). Carry on tracks it again.
                raise footage.FootageError(f"Frame {i}'s tracking is gone – Carry on tracks it again.")
            m = m.astype(np.float32) / (65535.0 if m.dtype == np.uint16 else 255.0)
            return track.to_size(m, self.h, self.w)
        m = self.small_masks.get(i)
        return None if m is None else track.to_size(m, self.h, self.w)

    # -- what's made

    def final(self, kind):
        """The video file `kind` is made into, or None for a sequence."""
        fmt = video_formats.for_kind(self.video_format, kind)
        if not video_formats.is_video(fmt):
            return None
        return os.path.join(self.out, f"{self.name}_{kind}{self.spec.get('tag', '')}{video_formats.FORMATS[fmt]['ext']}")

    def made(self, kinds):
        return all(self.final(k) and os.path.isfile(self.final(k)) for k in kinds)

    def finish(self, seq, kind):
        """The sequence made into its video file (once); a sequence as it is."""
        out = self.final(kind)
        if out is None:
            return seq.pattern()
        if os.path.isfile(out):
            return out
        n = self.end - self.start
        fps = footage.fps_of(self.info)
        cmd = video_formats.encode_command(
            self.ffmpeg, seq.pattern(), self.start, n, self.info["fps"], out, self.video_format, kind=kind,
            source=self.spec["source"] if self.info.get("audio") else None, at=float(self.start / fps),
            timecode=video_formats.tc_add(self.info.get("timecode", ""), self.start, fps),
            info=self.info)
        label = video_formats.FORMATS[video_formats.for_kind(self.video_format, kind)]["label"]
        footage.encode(cmd, n, out, lambda d: say({"stage": "Writing the video file", "kind": kind,
                                                   "detail": f"{label} · frame {d} of {n}", "progress": d / n}))
        return out

    def tidy(self):
        """Once the files are made: the frames they were made from go."""
        if video_formats.is_video(self.video_format):
            shutil.rmtree(self.frames_dir, ignore_errors=True)
            for f in os.listdir(self.work):
                if f.startswith(self.name + "_matte_"):
                    try:
                        os.remove(os.path.join(self.work, f))
                    except OSError:
                        pass

    # -- remove

    def remove(self):
        if not self.keyframes:
            raise footage.FootageError("Paint over what to remove on at least one frame.")
        if self.made(["clean"]):
            return {"clean": self.final("clean")}
        self.follow()
        seq = footage.Sequence(self.frames_dir, self.name + "_clean", self.ext)
        hole = self.widened(self.mask)
        if self.spec.get("engine") == "rose":
            self.rose_run(seq, hole, "Removing", "clean")
        else:
            self.fill_run(seq, hole, "Removing", self.filler(), "clean")
        out = {"clean": self.finish(seq, "clean")}
        self.tidy()
        return out

    def fill_run(self, seq, mask_of, stage, filler, kind):
        frames = Frames(self.reader)
        clip = video_fill.Clip(frames.get, lambda i: mask_of(i) if self.start <= i < self.end else None)
        first = seq.first_missing(self.start, self.end)
        previous = None
        backdrop = video_fill.Backdrop()     # what's invented, kept on its own frame (video_fill.Backdrop)
        if first > self.start:
            before = seq.read(first - 1)
            if before is not None:
                previous = (first - 1, footage.to_float(before[:, :, :3]))
        n = self.end - self.start
        # A frame's written while the next is worked out (a 16-bit 4K PNG was
        # 0.37 s of each frame's 3.5); said once it's there, so the page never
        # asks for one that isn't yet.
        writing = concurrent.futures.ThreadPoolExecutor(1)
        pending = None

        def written(job):
            t, said = job[1], job[2]
            job[0].result()
            say({"stage": stage, "frame": t, "of": n, "progress": (t - self.start + 1) / n, "from": said,
                 "kind": kind, "pattern": seq.pattern()})

        try:
            for t in range(first, self.end):
                out, said = video_fill.fill_frame(clip, t, previous, filler, self.flow, self.radius,
                                                  lo=self.start, hi=self.end - 1, backdrop=backdrop)
                dtype = frames.get(t).dtype
                if pending is not None:
                    written(pending)
                pending = (writing.submit(seq.write, t, footage.from_float(out, dtype)), t, said)
                previous = (t, out)
                clip.keep(t - self.radius, t + self.radius)
                frames.keep(t - self.radius, t + self.radius)
            if pending is not None:
                written(pending)
        finally:
            writing.shutdown(wait=True)
            frames.close()

    def rose_run(self, seq, mask_of, stage, kind):
        """The part drawn again by ROSE (rose_fill.Remover), a run of frames
        at a time - written as they're finished, carried on with from the
        first frame not written (its run started over)."""
        import rose_fill
        say({"stage": "Loading ROSE", "detail": "About 10 seconds", "progress": 0.0})
        model = rose_fill.Rose(self._model_folder("rose"))
        first = seq.first_missing(self.start, self.end)
        if first >= self.end:
            return
        n = self.end - self.start
        dtype = [None]
        writing = concurrent.futures.ThreadPoolExecutor(1)
        pending = [None]
        # How far along: the model's steps are most of each run's time and its frames are written in a burst
        # after - so a frame written says no less than the steps did (frame 1 of 40 after a minute's drawing
        # read as "about 43 min left").
        done = [(first - self.start) / n]

        def frames_of(a, b):
            for i, f in self.reader.frames(a, b - a):
                dtype[0] = f.dtype
                yield i, f

        def written(job):
            job[0].result()
            t = job[1]
            done[0] = max(done[0], (t - self.start + 1) / n)
            say({"stage": stage, "frame": t, "of": n, "progress": done[0], "kind": kind, "pattern": seq.pattern()})

        def write(i, f):
            if pending[0] is not None:
                written(pending[0])
            pending[0] = (writing.submit(seq.write, i, footage.from_float(f, dtype[0] or np.uint8)), i)

        def done_frame(i):
            r = seq.read(i)
            return None if r is None else footage.to_float(r)[:, :, :3]

        remover = rose_fill.Remover(model, effects=bool(self.spec.get("effects")), fine=bool(self.spec.get("fine")))

        def progress(k, total, step, steps):
            a, b = remover.runs[k]
            # This run's frames not written yet, drawn this far.
            start = max(done[0], (a - self.start) / n)
            done[0] = max(done[0], min(0.99, start + ((b - self.start) / n - start) * 0.9 * step / steps))
            # After the last step the run's frames are read again and put in (its shadow and reflection found
            # first): said, not left on "step 20 of 20" as if it had stopped.
            detail = (f"Part {k + 1} of {total} drawn – putting it into the frames" if step >= steps
                      else f"Drawing part {k + 1} of {total} – step {step} of {steps}")
            say({"stage": stage, "detail": detail, "progress": done[0]})

        try:
            remover.remove(frames_of, lambda i: mask_of(i) if self.start <= i < self.end else None,
                           self.start, self.end, write, done_frame, progress, first=first)
            if pending[0] is not None:
                written(pending[0])
        finally:
            writing.shutdown(wait=True)

    # -- separate

    def separate(self, background=True):
        """The subject on its own - and, unless only the cut-out's wanted, the background without it."""
        kinds = ["subject", "background"] if background else ["subject"]
        if self.made(kinds):
            return {k: self.final(k) for k in kinds}
        if self.keyframes:
            self.follow("Following what you drew")
        model = self.spec.get("model") or "birefnet-lite"
        if gpu.devices() and not gpu.compiled(self._model_path(model)):
            say(gpu.preparing_text("the subject model"))
        found = matting.Matter(self._model_path(model))
        mattes = footage.Sequence(self.work, self.name + "_matte", ".png")
        subject = footage.Sequence(self.frames_dir, self.name + "_subject", ".png")
        n = self.end - self.start
        first = min(mattes.first_missing(self.start, self.end), subject.first_missing(self.start, self.end))
        previous, prev_gray = None, None
        if first > self.start and mattes.has(first - 1):
            previous = mattes.read(first - 1).astype(np.float32) / 65535.0
            prev_gray = track.gray_small(self.reader.frame(first - 1))
        for t, f in self.reader.frames(first, self.end - first):
            m = self._matte(found, f, self.mask(t) if self.keyframes else None)
            gray = track.gray_small(f)
            if previous is not None:
                m = track.steady(m, track.line_up(previous, prev_gray, gray, self.flow))
            mattes.write(t, np.clip(np.rint(m * 65535), 0, 65535).astype(np.uint16))
            subject.write(t, cutout(f, m))
            previous, prev_gray = m, gray
            say({"stage": "Finding the subject", "frame": t, "of": n, "progress": (t - self.start + 1) / n,
                 "kind": "subject", "pattern": subject.pattern()})
        if not background:
            out = {"subject": self.finish(subject, "subject")}
            self.tidy()
            return out
        # The background: the subject removed, filled from the frames round each.
        background = footage.Sequence(self.frames_dir, self.name + "_background", self.ext)

        def subject_at(i):
            a = mattes.read(i)
            return None if a is None else np.where(a > 0.03 * 65535, 1.0, 0.0).astype(np.float32)

        hole = self.widened(subject_at)

        if self.spec.get("engine") == "rose":
            self.rose_run(background, hole, "Making the clean background", "background")
        else:
            self.fill_run(background, hole, "Making the clean background", self.filler(), "background")
        out = {"subject": self.finish(subject, "subject"), "background": self.finish(background, "background")}
        self.tidy()
        return out

    def _matte(self, found, frame, within):
        """The subject in one frame: the whole frame looked at, or only round
        what was drawn (as worker.do_matte with a region)."""
        view = worker.to_8bit(frame)
        x0, y0, x1, y1 = 0, 0, self.w, self.h
        if within is not None:
            if not (within > video_fill.FAINT).any():
                return np.zeros((self.h, self.w), np.float32)
            x0, y0, x1, y1 = worker.region_box(within)
        part = np.ascontiguousarray(view[y0:y1, x0:x1])
        ph, pw = part.shape[:2]
        if max(ph, pw) > matting.SIZE:
            small = found.find(cv2.resize(part, (matting.SIZE, matting.SIZE), interpolation=cv2.INTER_AREA))
            got = matting.refine(part, cv2.resize(small, (pw, ph), interpolation=cv2.INTER_LINEAR))
        else:
            got = found.find(part)
        out = np.zeros((self.h, self.w), np.float32)
        out[y0:y1, x0:x1] = np.clip(got, 0, 1)
        return out * within if within is not None else out

    def run(self):
        result = self.remove() if self.op == "remove" else self.separate(background=self.op == "separate")
        return {"done": True, "outputs": result, "seconds": round(time.monotonic() - self.started, 1),
                "frames": self.end - self.start, "reader": self.info["reader"], "depth": self.reader.depth,
                "video_format": self.video_format}


def cutout(frame, alpha):
    """The subject's own colours (matte.foreground) with the matte as
    transparency, as a 16-bit (deep footage) or 8-bit BGRA frame."""
    deep = frame.dtype == np.uint16
    f = footage.to_float(frame)
    color = np.clip(matting.foreground(f, alpha), 0, 1)
    out = np.dstack([color, alpha[:, :, None]])
    return footage.from_float(out, np.uint16 if deep else np.uint8)


def fill_model(path):
    import fill
    return fill.Filler(path)


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    try:
        with open(sys.argv[1], encoding="utf-8") as fh:
            spec = json.load(fh)
        say(Job(spec).run())
        return 0
    except (footage.FootageError, worker.Fail, ValueError) as exc:
        say({"error": str(exc)})
        return 1
    except Exception as exc:  # noqa: BLE001 - said, with where, for the crash trail
        say({"error": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc()[-2000:]})
        return 1


if __name__ == "__main__":
    sys.exit(main())
