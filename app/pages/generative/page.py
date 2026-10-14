#!/usr/bin/env python3
"""
Generative - pictures changed by models, on this computer, nothing
uploaded. Three tasks (`tab`), one picked before anything's opened - the
page starts on the choice, and a drop before it is turned away (the user
found the two tabs that came before hard to tell apart, 2026-10-08):

  remove     paint over what shouldn't be there and Remove fills it with
             what would be behind it (LaMa, fill.py).
  cutout     the subject, found by itself as the picture opens (BiRefNet,
             matte.py), shown on transparency - its edge fixed by painting
             Keep / Take away. Saves are the cut-out.
  separate   the same subject, plus the picture with the subject taken out
             (LaMa again): two layers to stack in Resolve.

The task can be changed with a picture open: it stays, and so does its
cut-out. The view is the picture, tools on it, and a short panel beside it
saying what's next (the layout the user picked from mock-ups, 2026-10-08).

A web page (core/web_page.py): the view is web/index.html + generative.js.

Pictures come from a file (Open, or dropped on the page) or from Resolve
(the still under the playhead, else the one selected in the Media Pool:
resolve_ext.find - or a clip dragged off Resolve's timeline onto the page:
the one selected there, resolve_ext.find(selected=True)). The picture, its undo history and its cut-out live in
the engine's process (worker.py, through engine_client.py); this page
keeps what the view shows and sends it previews to draw. While the engine
works, the view says what it's doing - its stage and step, as the worker
reports them - over the picture.

Footage opens too (a video file, or a clip from Resolve - the part the
timeline uses, at the playhead's frame), for any of the three tasks. Then
the view is the clip's frame with a scrubber under it, in steps (the user found one button doing
it all hard to follow, 2026-10-07): paint on any frames (keyframes); Track
carries the paint through the rest (track.py, in the engine) and shows it
over the picture, to be checked and switched off; then Remove / Cut out /
Separate runs as a job of its own (video_job.py through video_runner.py) using that
tracking as it is - asked for again if the paint or the part changed. The
job writes its frames into a "Buddy Generative" folder beside the clip, a
frame at a time - stopped and carried on with, looked at frame by frame
(the engine shows any frame: worker.py's video_frame) - and makes them into
a video file in the format picked (video_formats.py: ProRes, DNxHR, H.264,
with the source's audio and timecode), or leaves them as a sequence. Add
to Media Pool imports it into the original's bin, and shows that bin.

Saving never touches the original: files go beside it, named after it
(_clean, _subject, _background), in its format and bit depth where the
format allows (a cut-out is PNG, TIFF or EXR - formats with transparency).
A cleaned still from Resolve can be swapped in (its Media Pool clip points
at the cleaned file, everywhere it's used); anything saved can be added to
the Media Pool beside the original.

The engine (engine_setup.py) is installed from the page, each model when
its task first needs it, every download pinned and hash-checked.

Quality (2026-10-07): with the High quality add-on (an NVIDIA RTX, PyTorch
and two models - engine_setup.install_hq), Remove and Separate can be High:
a picture's removal is FLUX.2 klein's (klein_fill.py) instead of LaMa's,
footage is drawn again by ROSE (rose_fill.py) instead of filled from the
frames round it. Standard stays, and is all there is without the add-on.

Protocol:
    to the view    state, toast
    from the view  tab, install, install_hq, cancel_install, open_file, from_resolve, close, hq, hq_effects, hq_fine,
                   dismiss_hq_hint,
                   remove, undo, redo, save, save_as, swap, add_to_pool, reveal,
                   matte_model, find_subject, use_outline,
                   matte_edit, make_background, save_layers, add_layers,
                   video_frame, video_range, video_track, video_run, video_resume, video_stop, video_show, video_add,
                   video_format, video_tracker, video_direction, margin,
                   video_from_timeline
"""

import json
import os
import sys
import time
from fractions import Fraction

from PySide6.QtCore import QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog

from core import settings_form as sf
from core.i18n import tr, tr_filter
from core.message_dialog import confirm
from core.resolve_bridge import ResolveConnectionError
from core.resolve_reader import ResolveCalls
from core.resolve_worker import ResolveWorker
from core.settings_dialog import refresh_open
from core.web_page import WebToolPage

from . import engine_setup as es
from . import resolve_ext
from . import video_formats
from .engine_client import EngineClient
from .video_runner import VideoRun

OPEN_FILTER = "Pictures (*.jpg *.jpeg *.png *.tif *.tiff *.webp *.bmp *.exr *.jp2)"
VIDEO_EXTS = {".mov", ".mp4", ".m4v", ".mxf", ".avi", ".mkv", ".webm", ".mts", ".m2ts", ".mpg", ".mpeg", ".wmv"}
OPEN_ANY = ("Pictures and footage (*.jpg *.jpeg *.png *.tif *.tiff *.webp *.bmp *.exr *.jp2 "
            + " ".join("*" + e for e in sorted(VIDEO_EXTS)) + ")")
FOLDER = "Buddy Generative"     # beside the footage: where its jobs write
TABS = ("remove", "cutout", "separate")
MAX_MARGIN = 200            # px: Margin's most (fill.MAX_MARGIN, in the engine)
DEFAULTS = {"margin": 0, "matte_model": "", "video_format": video_formats.DEFAULT, "tracker": "standard",
            "use_gpu": True, "gpu_hint_dismissed": False, "track_direction": "both",
            "use_hq": True, "hq_hint_dismissed": False, "hq_effects": True, "hq_fine": False}
DIRECTIONS = ("backward", "both", "forward")    # Track, from the painted frame(s): to the in point, both, to the out
TRACKERS = ("standard", "object")      # Track: the paint as it moves / SAM 2's object under it ("sam2-tiny")
# What each task needs downloaded ("matte": either subject model).
NEEDS = {"remove": ["lama"], "cutout": ["matte"], "separate": ["matte", "lama"]}
JOB_STAGES = {"remove": "Removing", "cutout": "Cutting out", "separate": "Separating"}


class SetupJob(QThread):
    """engine_setup.install() on a thread: progressed(text, pct or -1), done(error or "")."""

    progressed = Signal(str, int)
    done = Signal(str)

    def __init__(self, models, parent=None):
        super().__init__(parent)
        self.models = list(models)
        self._stop = False

    def cancel(self):
        self._stop = True

    def run(self):
        try:
            es.install(self.models,
                       lambda text, pct=None: self.progressed.emit(str(text)[:300], -1 if pct is None else int(pct)),
                       lambda: self._stop)
        except es.SetupCancelled:
            self.done.emit("Stopped.")
        except es.SetupError as exc:
            self.done.emit(str(exc))
        except Exception as exc:  # noqa: BLE001 - said on the page
            self.done.emit(f"{type(exc).__name__}: {exc}")
        else:
            self.done.emit("")


class GpuSetupJob(SetupJob):
    """engine_setup.install_gpu() on a thread - SetupJob's signals."""

    def run(self):
        try:
            es.install_gpu(lambda text, pct=None: self.progressed.emit(str(text)[:300], -1 if pct is None else int(pct)),
                           lambda: self._stop)
        except es.SetupCancelled:
            self.done.emit("Stopped.")
        except es.SetupError as exc:
            self.done.emit(str(exc))
        except Exception as exc:  # noqa: BLE001 - said on the page
            self.done.emit(f"{type(exc).__name__}: {exc}")
        else:
            self.done.emit("")


class HqSetupJob(SetupJob):
    """engine_setup.install_hq() on a thread - SetupJob's signals."""

    def run(self):
        try:
            es.install_hq(self.models or None,
                          lambda text, pct=None: self.progressed.emit(str(text)[:300], -1 if pct is None else int(pct)),
                          lambda: self._stop)
        except es.SetupCancelled:
            self.done.emit("Stopped.")
        except es.SetupError as exc:
            self.done.emit(str(exc))
        except Exception as exc:  # noqa: BLE001 - said on the page
            self.done.emit(f"{type(exc).__name__}: {exc}")
        else:
            self.done.emit("")


class StatusJob(QThread):
    """engine_setup.status() off the UI thread: the first time starts the
    engine's Python and hashes the models (a second or two). With the sizes
    on disk too, for Settings' Model library - the environment is tens of
    thousands of files, too many to add up each time Settings opens."""

    done = Signal(object)

    def run(self):
        try:
            status = es.status()
            if status.get("env"):
                status["sizes"] = es.disk_size()
            self.done.emit(status)
        except Exception as exc:  # noqa: BLE001
            self.done.emit({"env": False, "detail": str(exc), "models": {}})


def _url(path):
    return QUrl.fromLocalFile(path).toString() if path else ""


def _duration(seconds):
    seconds = max(0, int(round(seconds)))
    if seconds < 90:
        return f"{seconds} s"
    if seconds < 5400:
        return f"{round(seconds / 60)} min"
    return f"{seconds / 3600:.1f} h"


def saved_name(source, suffix="_clean", ext=None, taken=os.path.exists):
    """Where a save goes first: "<name><suffix><ext>" beside the source,
    or <suffix>2, <suffix>3 ... when that's taken."""
    stem, own = os.path.splitext(source)
    ext = ext or own
    n = 1
    while True:
        path = f"{stem}{suffix}{'' if n == 1 else n}{ext}"
        if not taken(path):
            return path
        n += 1


def clean_name(source, taken=os.path.exists):
    return saved_name(source, "_clean", None, taken)


def is_footage(path):
    return os.path.splitext(path)[1].lower() in VIDEO_EXTS


def job_folder(path, op, first, end, spec, root=None, exists=os.path.exists, read=None):
    """Where a footage job writes: "<FOLDER>/<clip> <op> <first>-<last>"
    beside the clip (or in root) - the same folder again when the job asked
    for is the one it holds (stopped part-way: carried on with), else the
    next free "... 2", "... 3": a different job never writes over one done."""
    stem = os.path.splitext(os.path.basename(path))[0]
    base = os.path.join(root or os.path.join(os.path.dirname(path), FOLDER), f"{stem} {op} {first}-{end - 1}")
    read = read or _read_spec
    n = 1
    while True:
        folder = base if n == 1 else f"{base} {n}"
        if not exists(folder) or _same_job(read(folder), spec):
            return folder
        n += 1


def _read_spec(folder):
    try:
        with open(os.path.join(folder, ".work", "job.json"), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _same_job(a, b):
    keys = ("op", "source", "start", "end", "keyframes", "format", "model", "video_format")
    # Made another way (Quality, Margin), the frames written aren't this job's to carry on with.
    return bool(a) and all(a.get(k) == b.get(k) for k in keys) \
        and float(a.get("margin") or 0) == float(b.get("margin") or 0) \
        and a.get("engine", "standard") == b.get("engine", "standard") \
        and bool(a.get("effects")) == bool(b.get("effects")) and bool(a.get("fine")) == bool(b.get("fine"))


JOB_KINDS = {"remove": ("clean",), "cutout": ("subject",), "separate": ("subject", "background")}


def job_progress(spec, listdir=os.listdir, isfile=os.path.isfile):
    """How far a footage job got, from what's on disk: (frames done, frames
    in it, finished). Finished once its video files are made - or, for an
    image sequence, every frame of every kind is written."""
    kinds = JOB_KINDS.get(spec.get("op"), ())
    start, end = int(spec.get("start", 0)), int(spec.get("end", 0))
    of = max(0, end - start)
    out = spec.get("out") or ""
    name = spec.get("name") or ""
    fmt = video_formats.known(spec.get("video_format") or "sequence")
    if video_formats.is_video(fmt):
        files = [os.path.join(out, f"{name}_{k}{spec.get('tag', '')}"
                                   f"{video_formats.FORMATS[video_formats.for_kind(fmt, k)]['ext']}") for k in kinds]
        if kinds and all(isfile(f) for f in files):
            return of, of, True
        folder, ext = os.path.join(out, ".work", "frames"), ".png"
    else:
        folder, ext = out, (spec.get("format") or ".png").lower()
    try:
        there = set(listdir(folder))
    except OSError:
        there = set()
    counts = [sum(f"{name}_{k}_{i:06d}{ext}" in there for i in range(start, end)) for k in kinds]
    done = sum(counts) // len(counts) if counts else 0
    return done, of, bool(counts) and not video_formats.is_video(fmt) and all(c == of for c in counts)


def stopped_job(path, root=None, read=None):
    """The newest footage job on `path` stopped part-way (in FOLDER beside
    it, or in root): {"spec", "done", "of"}, or None."""
    folder = root or os.path.join(os.path.dirname(path), FOLDER)
    read = read or _read_spec
    try:
        names = os.listdir(folder)
    except OSError:
        return None
    same = os.path.normcase(os.path.abspath(path))
    found = []
    for n in names:
        spec_path = os.path.join(folder, n, ".work", "job.json")
        spec = read(os.path.join(folder, n))
        if not spec or spec.get("op") not in JOB_KINDS or not spec.get("source") \
                or os.path.normcase(os.path.abspath(spec["source"])) != same:
            continue
        spec["out"] = os.path.join(folder, n)            # where it is now, if the folder was moved
        done, of, finished = job_progress(spec)
        if not finished:
            try:
                found.append((os.path.getmtime(spec_path), {"spec": spec, "done": done, "of": of}))
            except OSError:
                continue
    return max(found, key=lambda f: f[0])[1] if found else None


def layer_ext(source, depth):
    """A cut-out's format: one with transparency, deep enough for the picture."""
    own = os.path.splitext(source)[1].lower()
    if own == ".exr":
        return ".exr"
    if own in (".tif", ".tiff") or depth != "8-bit":
        return ".tif"
    return ".png"


class GenerativePage(WebToolPage):
    tool_id = "generative"
    display_name = "Generative"
    category = "Generative"
    web_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
    file_drops = True
    resolve_clip_drops = True

    def build_state(self):
        self.settings = self.host.tool_settings(self.tool_id, DEFAULTS)
        self.tab = None             # the task: picked on the page each time, before anything's opened
        self.engine = {"checking": True, "env": False, "models": {}, "detail": ""}
        self.install = None         # {"models", "line", "pct"} while installing
        self.install_error = ""
        self.doc = None             # the open picture: what worker.py's open said, and more
        self.busy = None            # {"kind", "stage", "detail", "progress", "started"}
        self.setup_job = None
        self.status_job = None
        self.client = EngineClient(self, preload=["lama"])
        self.client.use_gpu = bool(self.settings.get("use_gpu", True))
        self.run = None             # a footage job running (video_runner.VideoRun)
        self._frame_wanted = None   # footage: the frame to show once the engine's free
        self._worker = ResolveWorker(self)
        self._calls = ResolveCalls(self, self._worker, {"CleanupError": resolve_ext.CleanupError})

    def working_on(self):
        if self.busy is not None:
            return self.busy.get("stage") or "Working"
        return "Installing an engine" if self.setup_job is not None else None

    def web_ready(self):
        if self.engine["checking"] and self.status_job is None:
            self._check_engine()
        self._push()

    def on_app_quitting(self):
        if self.setup_job is not None:
            self.setup_job.cancel()
            self.setup_job.wait(5000)
        if self.status_job is not None:
            self.status_job.wait(5000)      # sizing the engine's folder: a QThread too
        if self.run is not None:
            self.run.stop()                 # what's written stays; Run again carries on
        self.client.stop()

    def on_tab(self, payload):
        """The task picked - or none (back to the choice), with nothing open.
        With something open it stays: a picture's subject is found for Cut
        out / Split as soon as it's switched to."""
        tab = (payload or {}).get("id")
        if tab == self.tab or (tab not in TABS and (tab is not None or self.doc)):
            return
        if self.busy or self.run is not None:
            return
        self.tab = tab
        self._push()
        self._auto_find()

    def accepts_drops(self):
        return self.tab is not None

    def _auto_find(self):
        """Cut out / Split with a picture open and no subject yet: found now, as the task's first step."""
        doc = self.doc
        if self.tab in ("cutout", "separate") and doc and doc.get("kind") != "video" and not doc.get("matte") \
                and not self.busy and self._models_ready():
            self.on_find_subject()

    # ------------------------------------------------------------ busy --

    def _start(self, kind, stage, detail=""):
        self.busy = {"kind": kind, "stage": stage, "detail": detail, "progress": None,
                     "started": time.time()}
        self._push()

    def _step(self, step):
        """A step the engine reported."""
        if self.busy is None:
            return
        self.busy.update(stage=step.get("stage") or self.busy["stage"], detail=step.get("detail") or "",
                         progress=step.get("progress"))
        self._push()

    def _done(self):
        self.busy = None

    def _ask(self, do, args, done, progress=None):
        """client.ask - refused while the engine's still on something else
        (a frame being drawn, the clip just closed): said, not left on
        "Opening …" for good."""
        if self.client.ask(do, args, done, progress):
            return True
        self._done()
        self.emit("toast", {"text": "Generative is still busy – try again in a moment."})
        self._push()
        return False

    # ---------------------------------------------------------- engine --

    def _check_engine(self):
        job = StatusJob(self)
        job.done.connect(self._on_status)
        job.finished.connect(job.deleteLater)
        self.status_job = job
        job.start()

    def _on_status(self, status):
        self.status_job = None
        self.engine = {"checking": False, "env": bool(status.get("env")),
                       "models": dict(status.get("models") or {}), "detail": status.get("detail") or "",
                       "gpu": dict(status.get("gpu") or {}), "hq": dict(status.get("hq") or {}),
                       "sizes": dict(status.get("sizes") or {})}
        self._push()
        refresh_open(self.tool_id)              # an open Settings window shows the same
        self._auto_find()                       # just downloaded what the task needed

    def _matte_model(self):
        """The subject model to use: the one picked, if it's downloaded,
        else the best one that is - None with neither."""
        have = [m for m in es.MATTE_MODELS if self.engine["models"].get(m)]
        picked = self.settings.get("matte_model")
        return picked if picked in have else (have[0] if have else None)

    def _missing(self, tab=None):
        """The models a task still needs ("matte": either subject model)."""
        out = []
        for need in NEEDS.get(tab or self.tab) or []:
            if need == "matte":
                if self._matte_model() is None:
                    out.append("matte")
            elif not self.engine["models"].get(need):
                out.append(need)
        return out

    def _models_ready(self, tab=None):
        return self.engine["env"] and not self._missing(tab)

    def on_install(self, payload=None):
        """Installs the engine (if need be) and the models asked for -
        "matte" meaning the subject model picked ("standard" / "best")."""
        if self.setup_job is not None:
            return
        wanted = []
        for m in (payload or {}).get("models") or NEEDS.get(self.tab) or []:
            if m == "matte":
                m = "birefnet" if (payload or {}).get("quality") == "best" else "birefnet-lite"
            if m in es.MODELS and m not in wanted:
                wanted.append(m)
        if not wanted:
            return
        if any(m in es.MATTE_MODELS for m in wanted):
            self.settings["matte_model"] = next(m for m in wanted if m in es.MATTE_MODELS)
            self.settings.save()
        self.install = {"models": wanted, "line": "Starting …", "pct": 0}
        self.install_error = ""
        job = SetupJob(wanted, self)
        job.progressed.connect(self._on_setup_progress)
        job.done.connect(self._on_setup_done)
        job.finished.connect(job.deleteLater)
        self.setup_job = job
        job.start()
        self._push()

    def on_install_gpu(self, _payload=None):
        """The graphics card add-on (engine_setup.install_gpu) - the engine
        starts again after, to use it."""
        if self.setup_job is not None or not (self.engine.get("gpu") or {}).get("supported"):
            return
        if not self.engine["env"]:
            return self.emit("toast", {"text": "Download Generative's engine first."})
        self.install = {"models": ["gpu"], "line": "Starting …", "pct": 0, "gpu": True}
        self.install_error = ""
        job = GpuSetupJob([], self)
        job.progressed.connect(self._on_setup_progress)
        job.done.connect(self._on_setup_done)
        job.finished.connect(job.deleteLater)
        self.setup_job = job
        job.start()
        self._push()

    def on_install_hq(self, payload=None):
        """The High quality add-on (engine_setup.install_hq): PyTorch and its
        models - or just the models asked for."""
        if self.setup_job is not None or not (self.engine.get("hq") or {}).get("supported"):
            return
        if not self.engine["env"]:
            return self.emit("toast", {"text": "Download Generative's engine first."})
        models = [m for m in ((payload or {}).get("models") or []) if m in es.HQ_MODELS]
        self.install = {"models": models or list(es.HQ_MODELS), "line": "Starting …", "pct": 0, "hq": True}
        self.install_error = ""
        job = HqSetupJob(models, self)
        job.progressed.connect(self._on_setup_progress)
        job.done.connect(self._on_setup_done)
        job.finished.connect(job.deleteLater)
        self.setup_job = job
        job.start()
        self._push()

    def on_dismiss_hq_hint(self, _payload=None):
        self.settings["hq_hint_dismissed"] = True
        self.settings.save()
        self._push()

    def on_hq(self, payload):
        """Quality: High (the add-on's models) or Standard - asked for with
        each removal, so nothing restarts."""
        self.settings["use_hq"] = bool((payload or {}).get("on"))
        self.settings.save()
        self._push()

    def on_hq_effects(self, payload):
        """"Also its shadow and reflection" (High, footage): what ROSE
        changed round what's removed goes in too."""
        self.settings["hq_effects"] = bool((payload or {}).get("on"))
        self.settings.save()
        self._push()

    def on_hq_fine(self, payload):
        """"Finer detail" (High, footage): ROSE at about 720p - sharper on a
        big part of a 4K frame, about five times as long."""
        self.settings["hq_fine"] = bool((payload or {}).get("on"))
        self.settings.save()
        self._push()

    def _effects(self):
        return bool(self.settings.get("hq_effects", True))

    def on_margin(self, payload):
        """Margin: how far past what's painted (or the subject's edge) a removal reaches, in picture pixels."""
        try:
            px = int(round(float((payload or {}).get("px", 0))))
        except (TypeError, ValueError):
            return
        self.settings["margin"] = max(0, min(MAX_MARGIN, px))
        self.settings.save()
        self._push()

    def _margin(self):
        try:
            return max(0, min(MAX_MARGIN, int(self.settings.get("margin") or 0)))
        except (TypeError, ValueError):
            return 0

    def _hq(self, kind):
        """High quality for `kind` ("pictures": klein, "footage": ROSE):
        the add-on and that model are there, and Quality is High."""
        h = self.engine.get("hq") or {}
        mid = "flux2-klein" if kind == "pictures" else "rose"
        return bool(self.settings.get("use_hq", True) and h.get("installed") and (h.get("models") or {}).get(mid))

    def on_dismiss_gpu_hint(self, _payload=None):
        self.settings["gpu_hint_dismissed"] = True
        self.settings.save()
        self._push()

    def _set_gpu(self, on):
        """Settings' "Use the graphics card": the engine starts again, as
        it's next needed, on the card or not (a running job keeps what it has)."""
        self.settings["use_gpu"] = bool(on)
        self.settings.save()
        if self.client.use_gpu != bool(on):
            self.client.use_gpu = bool(on)
            if not self.busy:
                self.client.stop()
                if self.doc and self.doc.get("kind") != "video":
                    self.doc = None             # a picture lives in the engine; footage opens again from its file
        self._push()

    def on_cancel_install(self, _payload=None):
        if self.setup_job is not None:
            self.setup_job.cancel()

    def _on_setup_progress(self, text, pct):
        if self.install is None:
            return
        self.install["line"] = text
        # pip's own lines (PyTorch is 2 GB of them) say nothing of how far it is: the bar slides then.
        self.install["known"] = pct >= 0
        if pct >= 0:
            self.install["pct"] = pct
        self._push()

    def _on_setup_done(self, error):
        self.setup_job = None
        gpu = bool((self.install or {}).get("gpu"))
        hq = bool((self.install or {}).get("hq"))
        self.install = None
        self.install_error = "" if error == "Stopped." else error
        if not error:
            self.emit("toast", {"text": "The graphics card is ready – each model is prepared for it the first time "
                                        "it's used (a minute or two, once)" if gpu else
                                        "High quality is ready – pick it under Quality" if hq else
                                        "Downloaded – ready to use"})
            if gpu and not self.busy:
                self.client.stop()                  # the engine starts again with it
        self.engine["checking"] = True
        self._check_engine()
        self._push()

    def on_matte_model(self, payload):
        mid = (payload or {}).get("id")
        if mid in es.MATTE_MODELS:
            self.settings["matte_model"] = mid
            self.settings.save()
            self._push()

    # ---------------------------------------------------------- opening --

    def on_open_file(self, _payload=None):
        if not self._can_open():
            return
        folder = os.path.dirname(self.doc["path"]) if self.doc else ""
        path, _ = QFileDialog.getOpenFileName(self, tr("Open a picture or footage"), folder, tr_filter(OPEN_ANY))
        if path:
            self._open_any(path, None)

    def on_files_dropped(self, paths):
        if paths and self._can_open():
            self._open_any(paths[0], None)

    def _open_any(self, path, source, at=None):
        if is_footage(path):
            self._open_video(path, source, at or {})
        else:
            self._open(path, source)

    def on_from_resolve(self, _payload=None, selected=False):
        if not self._can_open():
            return
        try:
            controller = self.host.ensure_connected()
        except ResolveConnectionError as exc:
            return self.emit("toast", {"text": str(exc)})
        args = {"selected": True} if selected else {}
        if not self._calls.call("generative.find", args, lambda: resolve_ext.find(controller, **args), self._on_found):
            return self.emit("toast", {"text": "Resolve is busy – try again in a moment."})
        self._start("resolve", "Asking Resolve for the clip")

    def on_resolve_clip_dropped(self):
        """A clip dragged off Resolve's timeline: the one selected there."""
        self.on_from_resolve(selected=True)

    def _on_found(self, found, error):
        self._done()
        if error is not None:
            self.emit("toast", {"text": str(error)})
            return self._push()
        if not found.get("path") or not os.path.isfile(found["path"]):
            self.emit("toast", {"text": f"Can't find “{found.get('name', '')}”'s file – it may be offline."})
            return self._push()
        source = {"id": found["id"], "name": found["name"], "where": found["where"]}
        if found.get("others"):
            more = found["others"]
            self.emit("toast", {"text": f"{more + 1} clips were selected on the timeline – opened “{found['name']}”."})
        if found.get("still"):
            self._open(found["path"], source)
        else:
            self._open_video(found["path"], source, {"range": found.get("range"), "frame": found.get("frame")})

    def on_video_from_timeline(self, _payload=None):
        """In and out set to the part of the open clip the timeline uses - the
        clip under Resolve's playhead, when it's this one - and the frame at the
        playhead shown. Nothing reopened: the paint stays."""
        if not self._video() or self.busy or self.run is not None:
            return
        try:
            controller = self.host.ensure_connected()
        except ResolveConnectionError as exc:
            return self.emit("toast", {"text": str(exc)})
        path = self.doc["path"]

        def found(got, error):
            self._done()
            if error is not None:
                self.emit("toast", {"text": str(error)})
                return self._push()
            if not self._video() or self.doc["path"] != path:
                return self._push()
            same = os.path.normcase(os.path.abspath(got.get("path") or "")) == os.path.normcase(os.path.abspath(path))
            if not same or got.get("still"):
                self.emit("toast", {"text": f"The clip under Resolve's playhead is “{got.get('name', '')}”, not this one – "
                                            "put the playhead over this clip, or open that one with From Resolve."})
                return self._push()
            if not got.get("range"):
                self.emit("toast", {"text": "Resolve didn't say which part of the clip the timeline uses."})
                return self._push()
            if not self.doc.get("source"):
                self.doc["source"] = {"id": got["id"], "name": got["name"], "where": got["where"]}
            self.on_video_range({"range": got["range"]})
            a, b = self.doc["range"]
            self.emit("toast", {"text": f"In and out set to what the timeline uses – frames {a}–{b - 1}"})
            if got.get("frame") is not None:
                self._ask_frame(int(got["frame"]))
            self._push()

        self._start("resolve", "Asking Resolve for the timeline's in and out")
        if not self._calls.call("generative.find", {}, lambda: resolve_ext.find(controller), found):
            self._done()
            self.emit("toast", {"text": "Resolve is busy – try again in a moment."})
            self._push()

    def _can_open(self):
        if self.tab is None:
            self.emit("toast", {"text": "Pick what you want to do first."})
            return False
        if not self.engine["env"]:
            self.emit("toast", {"text": "Download Generative's engine first."})
            return False
        if self.busy or self.run is not None:
            return False
        if self._unsaved() and not confirm(
                self, tr("Leave this picture?"),
                tr("Its changes aren't saved. Open another picture anyway?"), ok=tr("Open another"), danger=True):
            return False
        return True

    def _unsaved(self):
        return bool(self.doc and self.doc.get("dirty"))

    def _open(self, path, source):
        self._start("opening", "Opening the picture", os.path.basename(path))

        def done(result, error):
            self._done()
            if error is not None:
                self.emit("toast", {"text": error})
            else:
                self.doc = {**result, "name": os.path.basename(path), "source": source, "saved": "",
                            "dirty": False, "matte": None, "background": None, "layers": {}}
            self._push()
            self._auto_find()

        self._ask("open", {"path": path}, done)

    def on_close(self, _payload=None):
        if self.busy or not self.doc or self.run is not None:
            return
        if self._unsaved() and not confirm(self, tr("Close the picture?"), tr("Its changes aren't saved."),
                                           ok=tr("Close"), danger=True):
            return
        self.client.ask("close", {}, lambda _r, _e: None)
        self.doc = None
        self._push()

    def _engine_failed(self, error):
        """A request failed: said, and if the engine went with it, so did the picture."""
        self.emit("toast", {"text": error})
        if not self.client.running():
            self.doc = None

    # ---------------------------------------------------------- footage --

    def _video(self):
        return bool(self.doc and self.doc.get("kind") == "video")

    def _open_video(self, path, source, at):
        self._start("opening", "Opening the footage", os.path.basename(path))

        def done(result, error):
            self._done()
            if error is not None:
                self.emit("toast", {"text": error})
                return self._push()
            frames = int(result["frames"])
            first, end = 0, frames
            rng = at.get("range")
            if rng and 0 <= int(rng[0]) < int(rng[1]):
                first, end = int(rng[0]), min(frames, int(rng[1]))
            self.doc = {
                "kind": "video", "path": path, "name": os.path.basename(path), "source": source,
                "width": result["width"], "height": result["height"], "fps": result["fps"], "frames": frames,
                "depth": result["depth"], "reader": result["reader"], "ffmpeg": result.get("ffmpeg", False),
                "frame": 0, "preview": result["preview"], "scale": result["scale"], "range": [first, end],
                "show": "source", "results": {}, "results_range": None, "out": "", "tracked": {}, "track": None,
                "saved": "", "dirty": False, "matte": None, "background": None, "layers": {},
                "undo": 0, "redo": 0, "original": "", "follow": False, "spec": None,
                "stopped": stopped_job(path),     # a job on it stopped part-way: Carry on
            }
            want = at.get("frame")
            self._push()
            if want:
                self._ask_frame(int(want))

        self._ask("video_open", {"path": path}, done)

    def on_video_frame(self, payload):
        if self._video():
            self.doc["follow"] = False          # looking for themselves: the job's newest frame isn't followed
            self._ask_frame(int((payload or {}).get("index", self.doc["frame"])))

    def _ask_frame(self, index):
        """Shows frame `index` - asked of the engine once it's free; while
        it's busy only the last frame wanted is kept (a scrub asks a lot)."""
        doc = self.doc
        index = max(0, min(int(index), doc["frames"] - 1))
        self._frame_wanted = index
        if self.client.busy():
            return
        self._frame_wanted = None
        show = doc["show"] if doc["results"].get(doc["show"]) else "source"
        rng = doc.get("results_range") or [0, 0]
        if show != "source" and not (rng[0] <= index < rng[1]):
            show = "source"                    # outside what the job made: the clip as it is

        def done(result, error):
            if not self._video():
                return
            if error is None:
                self.doc.update(frame=result["index"], preview=result["preview"], scale=result["scale"],
                                shown="missing" if result.get("missing") else result["show"])
            self._push()
            if self._frame_wanted is not None:
                self._ask_frame(self._frame_wanted)

        if not self.client.ask("video_frame", {"index": index, "show": show, "pattern": doc["results"].get(show, ""),
                                               "first": rng[0]}, done):
            self._frame_wanted = index

    def on_video_range(self, payload):
        if not self._video() or self.busy or self.run is not None:
            return
        rng = (payload or {}).get("range") or []
        if len(rng) == 2:
            first = max(0, min(int(rng[0]), self.doc["frames"] - 1))
            end = max(first + 1, min(int(rng[1]), self.doc["frames"]))
            self.doc["range"] = [first, end]
            self.doc["tracked"] = {}
            self.doc["track"] = None
            self._push()

    def on_video_show(self, payload):
        show = (payload or {}).get("show")
        if self._video() and (show == "source" or show in self.doc["results"]):
            self.doc["show"] = show
            self._ask_frame(self.doc["frame"])

    def on_video_format(self, payload):
        fmt = (payload or {}).get("id")
        if fmt in video_formats.FORMATS:
            self.settings["video_format"] = fmt
            self.settings.save()
            self._push()

    def on_video_tracker(self, payload):
        tracker = (payload or {}).get("id")
        if tracker in TRACKERS:
            self.settings["tracker"] = tracker
            self.settings.save()
            self._push()

    def on_video_direction(self, payload):
        direction = (payload or {}).get("id")
        if direction in DIRECTIONS:
            self.settings["track_direction"] = direction
            self.settings.save()
            self._push()

    def _direction(self):
        d = self.settings.get("track_direction")
        return d if d in DIRECTIONS else "both"

    def _span(self, keys):
        """The frames Track follows the paint through: from the painted
        frames forward to the out point, back to the in point, or both -
        [first, end). None with nothing painted inside the part."""
        first, end = self.doc["range"]
        ks = sorted(int(k) for k in keys if first <= int(k) < end)
        if not ks:
            return None
        d = self._direction()
        if d == "forward":
            return [ks[0], end]
        if d == "backward":
            return [first, ks[-1] + 1]
        return [first, end]

    def _tracker(self):
        return self.settings.get("tracker") if self.settings.get("tracker") in TRACKERS else "standard"

    def _video_format(self):
        fmt = video_formats.known(self.settings.get("video_format"))
        if video_formats.is_video(fmt) and self._video() and not self.doc.get("ffmpeg"):
            return "sequence"                  # a video file needs ffmpeg
        return fmt

    def on_video_track(self, payload):
        """Track: where the paint goes, frame by frame, through the part to
        work on. What it makes is kept for Remove / Separate, as long as the
        paint and the part are what was tracked (`sig`, the view's own
        summary of them)."""
        keys = self._keyframes(payload)
        if not self._video() or self.busy or self.run is not None or not keys:
            return
        span = self._span(keys)
        if span is None:
            return self.emit("toast", {"text": "Paint on a frame inside the part to work on first."})
        whole = list(self.doc["range"])
        first, end = span
        sig = str((payload or {}).get("sig") or "")
        tracker = self._tracker()
        if tracker == "object" and not self.engine["models"].get("sam2-tiny"):
            return self.emit("toast", {"text": "Download SAM 2 first – or pick Standard."})
        self._start("track", "Tracking")

        def done(result, error):
            self._done()
            if error is not None:
                self._engine_failed(error)
            elif self._video() and self.doc["range"] == whole:
                self.doc["tracked"] = {int(k): v for k, v in (result.get("masks") or {}).items()}
                self.doc["track"] = {"sig": sig, "range": whole, "span": [first, end],
                                     "exact": {int(k): v for k, v in (result.get("exact") or {}).items()}}
                missed = sorted(int(k) for k, v in (result.get("objects") or {}).items() if v == "none")
                if missed:
                    frames = ", ".join(str(k) for k in missed)
                    self.emit("toast", {"text": f"SAM 2 couldn't find one object under the paint on frame {frames} – "
                                                "that paint was followed as Standard. Scrub through to check it."})
                else:
                    self.emit("toast", {"text": "Followed – scrub through to check it, then "
                                        + {"remove": "Remove it", "cutout": "Cut it out"}.get(self.tab, "Separate")})
            self._push()

        self._ask("video_track", {"keyframes": keys, "start": first, "end": end, "tracker": tracker},
                        done, self._step)

    def _tracked_for(self, sig):
        """Track's masks when they're for this paint and part, else None."""
        track = self.doc.get("track")
        if not track or not sig or track["sig"] != sig or track["range"] != self.doc["range"]:
            return None
        return track

    def _keyframes(self, payload):
        """{frame: strokes} from the view - only frames with paint, inside the clip."""
        out = {}
        for k, v in ((payload or {}).get("keyframes") or {}).items():
            try:
                k = int(k)
            except (TypeError, ValueError):
                continue
            if v and self.doc and 0 <= k < self.doc.get("frames", 0):
                out[str(k)] = v
        return out

    def on_video_run(self, payload):
        """Remove / Separate through the part to work on, as a job - with the
        tracking Track made for this paint (`sig`)."""
        if not self._video() or self.busy or self.run is not None:
            return
        doc = self.doc
        op = self.tab
        if op not in TABS:
            return
        keys = self._keyframes(payload)
        first, end = doc["range"]
        keys = {k: v for k, v in keys.items() if first <= int(k) < end}
        if op == "remove" and not keys:
            return self.emit("toast", {"text": "Paint over what to remove on a frame inside the part to work on."})
        # Painted on every frame of the part: nothing to carry, so nothing to track (the user painted a
        # long run of frames by hand and was still asked to Track first, 2026-10-08).
        every = bool(keys) and all(str(i) in keys for i in range(first, end))
        tracked = self._tracked_for(str((payload or {}).get("sig") or "")) if keys and not every else None
        if keys and not every and tracked is None:
            return self.emit("toast", {"text": "Track it first – the paint or the part changed since it was tracked."
                                       if doc.get("track") else "Track it first."})
        if not self._models_ready(op):
            return self.emit("toast", {"text": "Download the models first."})
        if tracked is not None and tracked.get("span"):
            first, end = tracked["span"]            # only the frames it was tracked through (Track's direction)
            keys = {k: v for k, v in keys.items() if first <= int(k) < end}
        spec = {"op": op, "source": doc["path"], "start": first, "end": end, "keyframes": keys,
                "name": os.path.splitext(doc["name"])[0], "format": ".tif" if doc["depth"] > 8 else ".png",
                "video_format": self._video_format(), "models": str(es.MODELS_DIR), "filler": "lama",
                "tracker": self._tracker(), "gpu": bool(self.settings.get("use_gpu", True)),
                "engine": "rose" if self._hq("footage") else "standard",
                "effects": self._hq("footage") and self._effects(),
                "fine": self._hq("footage") and bool(self.settings.get("hq_fine", False)),
                "margin": self._margin()}
        if tracked is not None:
            spec["tracked"] = {"masks": {str(k): v for k, v in tracked["exact"].items()}}
        if op in ("cutout", "separate"):
            spec["model"] = self._matte_model()
        root = None
        beside = os.path.dirname(doc["path"])
        if not os.access(beside or ".", os.W_OK):
            root = QFileDialog.getExistingDirectory(self, tr("Where should the frames go?"), "")
            if not root:
                return
        spec["out"] = job_folder(doc["path"], op, first, end, spec, root)
        # The files are named for the part they're of, as the folder is ("A001_clean_120-239.mov"):
        # two jobs' clips side by side in the Media Pool aren't called the same.
        spec["tag"] = "_" + os.path.basename(spec["out"])[len(f"{spec['name']} {op} "):].replace(" ", "_")
        self._start_run(spec)

    def on_video_resume(self, _payload=None):
        """Carry on: the job that stopped part-way, as it was started - its
        paint, tracking, quality and format - from the first frame not
        written. Needs nothing from the view (it may have been closed since)."""
        stopped = self.doc.get("stopped") if self._video() else None
        if not stopped or self.busy or self.run is not None:
            return
        spec = dict(stopped["spec"], gpu=bool(self.settings.get("use_gpu", True)))
        self._start_run(spec, f"Carrying on – {stopped['done']} of {stopped['of']} frames were done")

    def _start_run(self, spec, detail=None):
        op = spec["op"]
        run = VideoRun(spec, self)
        run.progressed.connect(self._job_step)
        run.finished.connect(self._job_done)
        self.run = run
        self.doc.update(spec=spec, out=spec["out"], follow=True, finished=False, results={}, results_range=None,
                        stopped=None)
        self.busy = {"kind": "job", "stage": JOB_STAGES.get(op, "Working"),
                     "detail": detail or ("Starting – High quality (ROSE)" if spec.get("engine") == "rose"
                                          else "Starting …"),
                     "progress": 0.0, "started": time.time(), "op": op}
        self._push()
        if not run.start():
            self.run = None
        else:
            self.doc["spec"] = run.spec          # with the tracking kept beside the job

    def _job_step(self, step):
        if self.busy is None or self.busy.get("kind") != "job":
            return
        done = step.get("progress")
        detail = step.get("detail") or ""
        if step.get("frame") is not None and step.get("of"):
            first = (self.doc.get("spec") or {}).get("start", self.doc["range"][0]) if self._video() else 0
            detail = f"Frame {int(step['frame']) - first + 1} of {step['of']}"
            # Timed from the first frame it reported: a job carried on starts part-way through.
            if done is not None and "base" not in self.busy:
                self.busy["base"] = (done, time.time())
            base, since = self.busy.get("base", (0.0, self.busy["started"]))
            if done and done > base:
                left = (time.time() - since) / (done - base) * (1 - done)
                detail += f" · about {_duration(left)} left"
        self.busy.update(stage=step.get("stage") or self.busy["stage"], detail=detail, progress=done)
        doc = self.doc
        # Watching it happen: the newest frame done, when nothing else is asked for.
        if self._video() and doc.get("follow") and step.get("frame") is not None and step.get("pattern") \
                and step.get("kind") in ("clean", "subject", "background"):
            kind = step["kind"]
            doc["results"][kind] = step["pattern"]
            doc["results_range"] = [(doc.get("spec") or {}).get("start", doc["range"][0]), int(step["frame"]) + 1]
            doc["show"] = kind
            if not self.client.busy() and time.time() - doc.get("_followed", 0) > 0.8:
                doc["_followed"] = time.time()
                self._ask_frame(int(step["frame"]))
        self._push()

    def _job_done(self, result, error):
        run, self.run = self.run, None
        self._done()
        if run is not None:
            run.deleteLater()
        if not self._video():
            return self._push()
        doc = self.doc
        if error is not None and doc.get("spec"):
            done, of, finished = job_progress(doc["spec"])
            doc["stopped"] = None if finished else {"spec": doc["spec"], "done": done, "of": of}
        if error == "Stopped.":
            self.emit("toast", {"text": "Stopped – what's done is kept. Carry on picks it up from there."})
        elif error is not None:
            self.emit("toast", {"text": error})
        else:
            outs = result.get("outputs") or {}
            doc["results"] = {k: v for k, v in outs.items() if k in ("clean", "subject", "background")}
            doc["results_range"] = list(doc["spec"] and [doc["spec"]["start"], doc["spec"]["end"]] or doc["range"])
            doc["show"] = "clean" if "clean" in doc["results"] else "subject"
            doc["finished"] = True
            mins = _duration(result.get("seconds", 0))
            files = [os.path.basename(v) for v in doc["results"].values() if "%" not in v]
            self.emit("toast", {"text": f"Done in {mins} – saved as {' and '.join(files)}" if files
                                else f"Done in {mins} – scrub through to check it"})
            self._ask_frame(doc["frame"] if doc["results_range"][0] <= doc["frame"] < doc["results_range"][1]
                            else doc["results_range"][0])
        self._push()

    def on_video_stop(self, _payload=None):
        if self.run is not None:
            self.run.stop()

    def on_video_add(self, payload=None):
        """Adds what the finished job made to the Media Pool, in the original
        clip's bin (or the current one), and shows that bin: one clip each
        - its video file, or its sequence."""
        if not self._video() or self.busy or self.run is not None:
            return
        doc = self.doc
        kinds = [k for k in ((payload or {}).get("kinds") or ("clean", "background", "subject")) if k in doc["results"]]
        if not kinds or not doc.get("results_range") or not doc.get("finished"):
            return
        first, end = doc["results_range"]
        try:
            controller = self.host.ensure_connected()
        except ResolveConnectionError as exc:
            return self.emit("toast", {"text": str(exc)})
        clip_id = (doc.get("source") or {}).get("id", "")
        names, bins = [], []

        def next_one(result=None, error=None):
            if error is not None:
                self._done()
                self.emit("toast", {"text": str(error)})
                return self._push()
            if result is not None:
                names.append(result.get("name", ""))
                bins.append(result.get("bin", ""))
            if len(names) == len(kinds):
                self._done()
                where = f"the “{bins[-1]}” bin" if bins and bins[-1] else "the Media Pool"
                self.emit("toast", {"text": f"Added {' and '.join(names)} to {where}"})
                return self._push()
            path = doc["results"][kinds[len(names)]]
            if "%" in path:
                args = {"clip_id": clip_id, "pattern": path, "first": first, "last": end - 1}
                call, run = "generative.add_sequence", (
                    lambda: resolve_ext.add_sequence(controller, clip_id, path, first, end - 1))
            else:
                args = {"clip_id": clip_id, "path": path}
                call, run = "generative.add", lambda: resolve_ext.add(controller, clip_id, path)
            if not self._calls.call(call, args, run, next_one):
                self._done()
                self.emit("toast", {"text": "Resolve is busy – try again in a moment."})
                self._push()

        self._start("resolve", "Adding to the Media Pool")
        next_one()

    # ---------------------------------------------------------- Clean Up --

    def on_remove(self, payload):
        strokes = (payload or {}).get("strokes") or []
        if not self.doc or self.busy or not strokes:
            return
        if not self._models_ready("remove"):
            return self.emit("toast", {"text": "Download the removal model first."})
        self._start("removing", "Removing what you painted")
        self._ask("fill", {"strokes": strokes, "hq": self._hq("pictures"), "margin": self._margin()},
                  self._edited, self._step)

    def on_undo(self, _payload=None):
        if self.doc and not self.busy and self.doc.get("undo"):
            self._start("undo", "Undoing")
            self._ask("undo", {}, self._edited)

    def on_redo(self, _payload=None):
        if self.doc and not self.busy and self.doc.get("redo"):
            self._start("redo", "Redoing")
            self._ask("redo", {}, self._edited)

    def _edited(self, result, error):
        kind = (self.busy or {}).get("kind")
        self._done()
        if error is not None:
            self._engine_failed(error)
        elif self.doc is not None:
            self.doc.update(preview=result["preview"], scale=result["scale"], undo=result["undo"],
                            redo=result["redo"], dirty=True, background=None, layers={})
            if self.doc.get("matte"):
                # The cut-out keeps its matte; its colours are the edited picture's now.
                self.doc["matte"] = {**self.doc["matte"], "subject": result["preview"]}
            if kind == "removing":
                self.emit("toast", {"text": result.get("note") or
                                    f"Removed in {result.get('seconds', 0)} s – hold Before to compare"})
        self._push()

    def _cutout(self):
        """Cut out, the subject found: its saves are the cut-out."""
        return bool(self.tab == "cutout" and self.doc and self.doc.get("matte"))

    def on_save(self, payload=None):
        if not self.doc or self.busy:
            return
        if self._cutout():
            return self.on_save_cutout(payload)
        target = self.doc.get("saved") or clean_name(self.doc["path"])
        if not os.access(os.path.dirname(target) or ".", os.W_OK):
            return self.on_save_as(payload)
        self._save(target, (payload or {}).get("then"))

    def on_save_as(self, payload=None):
        if not self.doc or self.busy:
            return
        if self._cutout():
            start = (self.doc.get("layers") or {}).get("subject") or self._layer_paths()["subject"]
            path, _ = QFileDialog.getSaveFileName(self, tr("Save the cut-out"), start,
                                                  tr_filter("Pictures with transparency (*.png *.tif *.tiff *.exr)"))
            if path:
                self._save_layers(["subject"], (payload or {}).get("then"), {"subject": path})
            return
        start = self.doc.get("saved") or clean_name(self.doc["path"])
        path, _ = QFileDialog.getSaveFileName(self, tr("Save the cleaned picture"), start, tr_filter(OPEN_FILTER))
        if path:
            if os.path.normcase(os.path.abspath(path)) == os.path.normcase(os.path.abspath(self.doc["path"])) \
                    and not confirm(self, tr("Replace the original?"),
                                    tr("This saves over the original picture. It can't be undone."),
                                    ok=tr("Replace"), danger=True):
                return
            self._save(path, (payload or {}).get("then"))

    def _save(self, path, then=None):
        self._start("saving", "Saving", os.path.basename(path))

        def done(result, error):
            self._done()
            if error is not None:
                self.emit("toast", {"text": error})
                return self._push()
            self.doc.update(saved=result["path"], dirty=False)
            self.achieve("cleaned", 1)
            self.emit("toast", {"text": f"Saved {os.path.basename(result['path'])}"})
            self._push()
            if then == "swap":
                self.on_swap()
            elif then == "add":
                self.on_add_to_pool()

        self._ask("save", {"path": path}, done)

    def on_reveal(self, _payload=None):
        doc = self.doc or {}
        if doc.get("kind") == "video":
            folder = doc.get("out") if doc.get("out") and os.path.isdir(doc["out"]) else os.path.dirname(doc["path"])
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
            return
        layers = doc.get("layers") or {}
        path = (layers.get("subject") if self.tab in ("cutout", "separate") else "") \
            or doc.get("saved") or doc.get("path")
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))

    def on_swap(self, _payload=None):
        doc = self.doc
        if not doc or self.busy or not doc.get("source"):
            return
        if self._cutout():
            subject = (doc.get("layers") or {}).get("subject")
            if not subject:
                return self.on_save_cutout({"then": "swap"})
            name = doc["source"]["name"]
            if not confirm(self, tr("Swap the cut-out in?"),
                           tr("Every use of “{name}” in this project will show the cut-out, on transparency. "
                              "The original file stays where it is.").format(name=name),
                           ok=tr("Swap in")):
                return
            return self._resolve_write("generative.swap", resolve_ext.swap, [subject], "Swapping into Resolve",
                                       lambda r: f"Swapped in – “{r.get('name') or name}” now uses the cut-out")
        if doc.get("dirty") or not doc.get("saved"):
            return self.on_save({"then": "swap"})
        name = doc["source"]["name"]
        if not confirm(self, tr("Swap the cleaned picture in?"),
                       tr("Every use of “{name}” in this project will show the cleaned picture. "
                          "The original file stays where it is.").format(name=name),
                       ok=tr("Swap in")):
            return
        self._resolve_write("generative.swap", resolve_ext.swap, [doc["saved"]], "Swapping into Resolve",
                            lambda r: f"Swapped in – “{r.get('name') or name}” now uses the cleaned picture")

    def on_add_to_pool(self, _payload=None):
        doc = self.doc
        if not doc or self.busy:
            return
        if self._cutout():
            return self.on_add_layers()
        if doc.get("dirty") or not doc.get("saved"):
            return self.on_save({"then": "add"})
        self._resolve_write("generative.add", resolve_ext.add, [doc["saved"]], "Adding to the Media Pool",
                            lambda r: f"Added “{r.get('name', '')}” to the Media Pool")

    def _resolve_write(self, name, fn, paths, stage, said):
        try:
            controller = self.host.ensure_connected()
        except ResolveConnectionError as exc:
            return self.emit("toast", {"text": str(exc)})
        clip_id = ((self.doc or {}).get("source") or {}).get("id", "")
        results = []

        def next_one(result=None, error=None):
            if error is not None:
                self._done()
                self.emit("toast", {"text": str(error)})
                return self._push()
            if result is not None:
                results.append(result)
            if len(results) == len(paths):
                self._done()
                self.emit("toast", {"text": said(results[0] if len(results) == 1 else
                                                 {"name": ", ".join(r.get("name", "") for r in results)})})
                return self._push()
            path = paths[len(results)]
            if not self._calls.call(name, {"clip_id": clip_id, "path": path},
                                    lambda: fn(controller, clip_id, path), next_one):
                self._done()
                self.emit("toast", {"text": "Resolve is busy – try again in a moment."})
                self._push()

        self._start("resolve", stage)
        next_one()

    # ---------------------------------------------------------- Cut Out --

    def on_find_subject(self, payload=None):
        """Finds the subject: Cut out's and Split's first step (as the picture
        opens), and Find again - in the whole picture, or only inside a
        region drawn round it ({region: strokes})."""
        if not self.doc or self.busy:
            return
        cutout = self.tab == "cutout"
        region = (payload or {}).get("region") or None
        model = self._matte_model()
        if model is None or not self.engine["env"]:
            return self.emit("toast", {"text": "Download a subject model first."})
        self._start("matte", "Finding the subject")

        def done(result, error):
            self._subject_found(result, error, cutout,
                                f"Found the subject {'inside what you drew ' if region else ''}"
                                f"in {(result or {}).get('seconds', 0)} s")

        self._ask("matte", {"model": model, "region": region} if region else {"model": model},
                        done, self._step)

    def on_use_outline(self, payload=None):
        """Separate by hand: what was drawn round the subject is its cut-out, as it is."""
        region = (payload or {}).get("region")
        if not self.doc or self.busy or not region:
            return
        self._start("matte", "Cutting out what you drew")
        self._ask("outline", {"region": region},
                        lambda result, error: self._subject_found(result, error, self.tab == "cutout",
                                                                  "Cut out what you drew"), self._step)

    def _subject_found(self, result, error, cutout, said):
        self._done()
        if error is not None:
            self._engine_failed(error)
        elif self.doc is not None:
            self.doc.update(matte=result, background=None, layers={})
            self.emit("toast", {"text": f"{said} – paint Keep or Take away to fix its edge" if cutout else said})
        self._push()

    def on_matte_edit(self, payload):
        strokes = (payload or {}).get("strokes")
        if not self.doc or self.busy or not (self.doc.get("matte")) or strokes is None:
            return
        self._start("matte_edit", "Updating the cut-out")

        def done(result, error):
            self._done()
            if error is not None:
                self._engine_failed(error)
            elif self.doc is not None:
                self.doc.update(matte=result, background=None, layers={})
            self._push()

        self._ask("matte_edit", {"strokes": strokes}, done, self._step)

    def _layer_paths(self):
        doc = self.doc
        return {"subject": saved_name(doc["path"], "_subject", layer_ext(doc["path"], doc["depth"])),
                "background": saved_name(doc["path"], "_background")}

    def on_save_cutout(self, payload=None):
        """Cut out's Save: the subject on transparency, beside the original."""
        if not self.doc or self.busy or not self.doc.get("matte"):
            return
        self._save_layers(["subject"], (payload or {}).get("then"))

    def on_make_background(self, _payload=None):
        if not self.doc or self.busy or not self.doc.get("matte"):
            return
        if not self.engine["models"].get("lama"):
            return self.emit("toast", {"text": "Download the removal model first."})
        self._start("background", "Making the clean background")

        def done(result, error):
            self._done()
            if error is not None:
                self._engine_failed(error)
            elif self.doc is not None:
                self.doc.update(background=result, layers={})
                self.emit("toast", {"text": result.get("note") or
                                    f"Clean background made in {result.get('seconds', 0)} s"})
            self._push()

        self._ask("background", {"hq": self._hq("pictures"), "margin": self._margin()}, done, self._step)

    def on_save_layers(self, payload=None):
        """Separate's Save: the subject and the clean background."""
        if not self.doc or self.busy or not self.doc.get("background"):
            return
        self._save_layers(["subject", "background"], (payload or {}).get("then"))

    def _save_layers(self, which, then=None, chosen=None):
        doc = self.doc
        targets = chosen or {k: v for k, v in self._layer_paths().items() if k in which}
        folder = os.path.dirname(doc["path"])
        if not chosen and not os.access(folder or ".", os.W_OK):
            folder = QFileDialog.getExistingDirectory(self, tr("Save to"), "")
            if not folder:
                return
            targets = {k: os.path.join(folder, os.path.basename(v)) for k, v in targets.items()}
        saved = {}
        order = [k for k in ("subject", "background") if k in targets]

        def next_one(result=None, error=None):
            if error is not None:
                self._done()
                self.emit("toast", {"text": error})
                return self._push()
            if result is not None:
                saved[order[len(saved)]] = result["path"]
            if len(saved) == len(order):
                self._done()
                doc["layers"] = saved
                self.achieve("cleaned", 1)
                names = " and ".join(os.path.basename(p) for p in saved.values())
                self.emit("toast", {"text": f"Saved {names}"})
                self._push()
                if then == "add":
                    self.on_add_layers()
                elif then == "swap":
                    self.on_swap()
                return
            kind = order[len(saved)]
            self.busy.update(detail=os.path.basename(targets[kind]))
            self._push()
            self._ask("save_cutout" if kind == "subject" else "save_background", {"path": targets[kind]},
                            next_one)

        self._start("saving", "Saving")
        next_one()

    def on_add_layers(self, _payload=None):
        doc = self.doc
        if not doc or self.busy:
            return
        want = ["subject"] + (["background"] if self.tab == "separate" else [])
        if any(k not in (doc.get("layers") or {}) for k in want):
            if self.tab == "separate":
                return self.on_save_layers({"then": "add"})
            return self.on_save_cutout({"then": "add"})
        paths = [doc["layers"][k] for k in ("background", "subject") if k in want]
        self._resolve_write("generative.add", resolve_ext.add, paths, "Adding to the Media Pool",
                            lambda r: f"Added {r.get('name', '')} to the Media Pool")

    # ---------------------------------------------------------- settings --

    def ai_models(self):
        sizes = self.engine.get("sizes") or {}  # measured by the last StatusJob
        rows = []
        hq_ok = bool((self.engine.get("hq") or {}).get("supported"))
        for mid, m in es.MODELS.items():
            if m.get("hq") and not hq_ok:
                continue
            have = bool(self.engine["models"].get(mid))
            rows.append({
                "id": f"generative-{mid}", "label": m["label"], "kind": "Model",
                "sub": [m["use"], m["license"]],
                "size": f"{(sizes.get(mid) or m['size']) / 1e6:.0f} MB", "bytes": sizes.get(mid, 0) if have else 0,
                "where": "local" if have else "missing", "note": str(es.model_path(mid)) if have else "",
                "chip": {"text": "Verified", "tone": "ok", "tip": "Checked against its published hash."}
                if have else {"text": "Not downloaded", "tone": ""},
                "actions": [{"label": "Remove", "action": f"remove:{mid}", "kind": "danger",
                             "disabled": self.setup_job is not None or bool(self.busy)}] if have else
                           [{"label": "Download", "action": f"download:{mid}", "disabled": self.setup_job is not None}],
            })
        return rows

    def _gpu_view(self):
        g = self.engine.get("gpu") or {}
        return {"card": g.get("card", ""), "supported": bool(g.get("supported")), "installed": bool(g.get("installed")),
                "on": bool(g.get("installed")) and bool(self.settings.get("use_gpu", True)), "mb": es.GPU_MB,
                "hint": bool(g.get("supported")) and not g.get("installed")
                and not self.settings.get("gpu_hint_dismissed")}

    def settings_pages(self):
        g = self.engine.get("gpu") or {}
        busy = self.setup_job is not None
        fields = [sf.heading("Graphics card")]
        if self.engine.get("checking"):
            fields.append(sf.hint("Checking this computer…"))
        elif not g.get("card"):
            fields.append(sf.status("Graphics card", "Not used", "",
                                    "Generative runs on the processor. Graphics card support is for NVIDIA RTX "
                                    "cards on Windows." if sys.platform == "darwin" else
                                    "Generative runs on the processor. An NVIDIA RTX graphics card (RTX 30 or later) "
                                    "would make it many times faster."))
        elif not g.get("supported"):
            fields.append(sf.status("Graphics card", g["card"], "",
                                    "This card is older than the RTX 30 series that Generative's graphics card "
                                    "support needs – it runs on the processor."))
        elif g.get("installed"):
            fields.append(sf.status("Graphics card", g["card"], "success",
                                    "NVIDIA TensorRT for RTX is installed. Each model is prepared for the card the "
                                    "first time it's used – a minute or two, once – and kept "
                                    f"({es.gpu_cache_size() / 1e9:.1f} GB so far)."))
            fields.append(sf.check("use_gpu", "Use the graphics card", self.settings.get("use_gpu", True),
                                   "Off: everything runs on the processor, as before."))
            fields.append(sf.buttons(("Remove the add-on", "gpu_remove", {"disabled": busy})))
        else:
            fields.append(sf.status("Graphics card", g["card"], "",
                                    "Not used yet. NVIDIA's TensorRT for RTX makes Generative many times faster on "
                                    "it – measured on an RTX 5070 Ti: finding a subject 8 s → 0.5 s, following an "
                                    "object 1.5 s → 0.25 s a frame."))
            fields.append(sf.hint(f"A one-time download of {es.GPU_MB} MB into Generative's engine, checked against "
                                  "its published hash and scanned by Windows Defender. It runs on this computer – "
                                  "nothing is uploaded."))
            fields.append(sf.buttons((f"Download ({es.GPU_MB} MB)", "gpu_install",
                                      {"disabled": busy or not self.engine.get("env")})))
        if self.install and self.install.get("gpu"):
            fields.append(sf.progress(self.install.get("line") or "Working…", self.install.get("pct")))
        fields += self._hq_fields(busy)
        return [sf.page("ai_generative", "ai", "Generative", fields,
                        "Removing things, cutting out subjects and following them through footage.")]

    def _hq_fields(self, busy):
        h = self.engine.get("hq") or {}
        fields = [sf.heading("High quality")]
        if self.engine.get("checking"):
            return fields + [sf.hint("Checking this computer…")]
        if not h.get("supported"):
            return fields + [sf.status("High quality", "Not available", "",
                                       (h.get("why") or "") + " Generative uses LaMa and its own fill from the "
                                       "frames round what's removed.")]
        models = h.get("models") or {}
        if h.get("installed") and all(models.get(m) for m in es.HQ_MODELS):
            fields.append(sf.status("High quality", "Installed", "success",
                                    "FLUX.2 klein redraws what's behind something removed from a picture; ROSE "
                                    "redraws a shot without it – its shadow and reflection too. On the graphics card, "
                                    "beside Resolve (3–5 GB of its memory)."))
            fields.append(sf.check("use_hq", "Use High quality", self.settings.get("use_hq", True),
                                   "Off: Standard (LaMa, and the fill from the frames round it). Remove and Separate "
                                   "also have a Quality switch."))
            fields.append(sf.buttons(("Remove the add-on", "hq_remove", {"disabled": busy})))
        else:
            fields.append(sf.status("High quality", h.get("card", ""), "",
                                    "Removal that redraws what's behind: FLUX.2 klein for pictures (where LaMa leaves "
                                    "a smear, it puts back the street, the doorway, the kerb) and ROSE for footage "
                                    "(the whole shot redrawn without it, shadows and reflections too)."))
            fields.append(sf.hint(f"A one-time download of about {es.hq_mb(h) / 1000:.1f} GB: PyTorch and diffusers into "
                                  "Generative's engine, and the two models (Apache 2.0) – every file checked against "
                                  "its published hash, scanned by Windows Defender. Nothing is uploaded."))
            fields.append(sf.buttons((f"Download ({es.hq_mb(h) / 1000:.1f} GB)", "hq_install",
                                      {"disabled": busy or not self.engine.get("env")})))
        if self.install and self.install.get("hq"):
            fields.append(sf.progress(self.install.get("line") or "Working…", self.install.get("pct")))
        return fields

    def on_setting(self, key, value, ui):
        if key == "use_gpu":
            self._set_gpu(bool(value))
        elif key == "use_hq":
            self.on_hq({"on": bool(value)})

    def ai_jobs(self):
        return [{"label": "Removing things, cutting out subjects", "model": "LaMa, BiRefNet", "where": "local",
                 "detail": "Generative works on this computer. Nothing is uploaded."},
                {"label": "Removing things – High quality", "model": "FLUX.2 klein 4B, ROSE", "where": "local",
                 "detail": "On this computer's graphics card. Nothing is uploaded."},
                {"label": "Following an object through footage (Track: Object)", "model": "SAM 2.1 Tiny",
                 "where": "local", "detail": "On this computer. Nothing is uploaded."}]

    def models_busy(self):
        if self.busy or self.setup_job is not None or self.run is not None or self.status_job is not None:
            return "Generative is busy – try again when it's done."
        if self.doc and self.doc.get("kind") != "video":
            return "Close the picture open in Generative first – its engine holds it, and stops while the models move."
        return ""

    def models_moving(self):
        self.client.stop()                      # it holds the model files open

    def models_moved(self):
        self.engine["checking"] = True
        self._check_engine()
        self._push()

    def on_settings_action(self, action, ui):
        name, _, mid = action.partition(":")
        if name == "gpu_install":
            self.on_install_gpu()
            return ui.status("Downloading – see Generative for progress.", "")
        if name == "hq_install":
            self.on_install_hq()
            return ui.status("Downloading – see Generative for progress.", "")
        if name == "hq_remove":
            if self.busy or self.setup_job is not None or self.run is not None:
                return ui.status("Generative is busy – try again when it's done.", "")
            if ui.confirm("Remove High quality?",
                          f"PyTorch comes out of Generative's engine and its two models are deleted "
                          f"({(es.size_mb('flux2-klein') + es.size_mb('rose')) / 1000:.1f} GB). Remove and Separate go "
                          "back to Standard.", ok="Remove", danger=True):
                self.client.stop()
                if self.doc and self.doc.get("kind") != "video":
                    self.doc = None             # a picture lives in the engine
                es.remove_hq()
                self.engine["checking"] = True
                self._check_engine()
                self._push()
            return
        if name == "gpu_remove":
            if self.busy or self.setup_job is not None or self.run is not None:
                return ui.status("Generative is busy – try again when it's done.", "")
            if ui.confirm("Remove the graphics card add-on?",
                          "Generative goes back to running on the processor. The models prepared for the card "
                          f"({es.gpu_cache_size() / 1e9:.1f} GB) are removed too.", ok="Remove", danger=True):
                self.client.stop()
                es.remove_gpu()
                self.engine.setdefault("gpu", {})["installed"] = False
                self._push()
            return
        if mid not in es.MODELS:
            return
        if name == "download":
            if es.MODELS[mid].get("hq"):
                self.on_install_hq({"models": [mid]})
            else:
                self.on_install({"models": [mid]})
            ui.status("Downloading – see Generative for progress.", "")
        elif name == "remove":
            if self.busy or self.setup_job is not None:
                return ui.status("Generative is busy – try again when it's done.", "")
            if ui.confirm(f"Remove {es.MODELS[mid]['label']}?",
                          f"It takes {es.size_mb(mid)} MB. Generative offers to download it again when it's needed.",
                          ok="Remove", danger=True):
                self.client.stop()                  # it may hold the file open
                self.doc = None
                es.remove(mid)
                self.engine["models"][mid] = False
                self.engine.get("sizes", {}).pop(mid, None)
                self._push()

    # -------------------------------------------------------------- view --

    def _push(self):
        doc = self.doc
        view = None
        if doc and doc.get("kind") == "video":
            fps = Fraction(doc["fps"])
            view = {
                "kind": "video", "name": doc["name"], "path": doc["path"], "width": doc["width"],
                "height": doc["height"], "depth": f"{doc['depth']}-bit", "fps": float(fps), "frames": doc["frames"],
                "frame": doc["frame"], "range": doc["range"], "preview": _url(doc["preview"]), "original": "",
                "scale": doc["scale"], "undo": 0, "redo": 0, "dirty": False, "saved": "", "source": doc.get("source"),
                "matte": None, "background": "", "layers": {}, "deep": doc["depth"] > 8,
                "ffmpeg": doc.get("ffmpeg", False), "show": doc["show"], "shown": doc.get("shown", "source"),
                "results": sorted(doc["results"]), "results_range": doc.get("results_range"),
                "tracked": {str(k): _url(v) for k, v in doc["tracked"].items()},
                "track_sig": (doc.get("track") or {}).get("sig", ""),
                "track_span": (doc.get("track") or {}).get("span"), "track_direction": self._direction(),
                "out": doc.get("out", ""), "running": self.run is not None, "finished": bool(doc.get("finished")),
                "stopped": {"op": doc["stopped"]["spec"]["op"], "done": doc["stopped"]["done"],
                            "of": doc["stopped"]["of"], "first": doc["stopped"]["spec"]["start"],
                            "last": doc["stopped"]["spec"]["end"] - 1} if doc.get("stopped") else None,
                "files": {k: os.path.basename(v) for k, v in doc["results"].items() if "%" not in v},
                "video_format": self._video_format(), "formats": video_formats.choices(),
                "tracker": self._tracker(), "sam_ready": bool(self.engine["models"].get("sam2-tiny")),
                "sam_mb": es.size_mb("sam2-tiny"),
            }
        elif doc:
            matte = doc.get("matte")
            bg = doc.get("background")
            view = {
                "name": doc["name"], "path": doc["path"], "width": doc["width"], "height": doc["height"],
                "depth": doc["depth"], "preview": _url(doc["preview"]), "original": _url(doc["original"]),
                "scale": doc["scale"], "undo": doc["undo"], "redo": doc["redo"], "dirty": doc["dirty"],
                "saved": os.path.basename(doc["saved"]) if doc.get("saved") else "",
                "source": doc.get("source"),
                "matte": {"matte": _url(matte["matte"]), "subject": _url(matte["subject"]),
                          "model": matte.get("model"), "coverage": matte.get("coverage"),
                          "strokes": matte.get("strokes", 0)} if matte else None,
                "background": _url(bg["background"]) if bg else "",
                "layers": {k: os.path.basename(v) for k, v in (doc.get("layers") or {}).items()},
            }
        missing = {tab: self._missing(tab) for tab in TABS}
        self.emit("state", {
            "tab": self.tab,
            "engine": {**self.engine, "missing": missing, "matte_model": self._matte_model(),
                       "sizes": {mid: es.size_mb(mid) for mid in es.MODELS}, "env_mb": es.ENV_MB},
            "install": self.install,
            "install_error": self.install_error,
            "doc": view,
            "busy": self.busy,
            "connected": bool(getattr(self.host, "connected", False)),
            "gpu": self._gpu_view(),
            "hq": self._hq_view(),
            "margin": self._margin(),
        })

    def _hq_view(self):
        h = self.engine.get("hq") or {}
        models = h.get("models") or {}
        return {"supported": bool(h.get("supported")), "installed": bool(h.get("installed")),
                "pictures": bool(h.get("installed") and models.get("flux2-klein")),
                "footage": bool(h.get("installed") and models.get("rose")),
                "on": bool(self.settings.get("use_hq", True)), "card": h.get("card", ""),
                "effects": self._effects(), "fine": bool(self.settings.get("hq_fine", False)),
                "gb": round(es.hq_mb(h) / 1000, 1) if h.get("supported") and not h.get("installed") else 0,
                "hint": bool(h.get("supported")) and not h.get("installed") and not self.settings.get("hq_hint_dismissed")}
