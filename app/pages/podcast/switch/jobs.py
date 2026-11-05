#!/usr/bin/env python3
"""
Auto Switch's background work, each on a QThread so Buddy keeps drawing:

    VoiceJob     voice_worker.py in the speech engine's venv: voice prints
                 of the mics that more than one person spoke into
    GroupJob     those prints grouped into voices (cluster.py) - again
                 whenever "people on this mic" changes, without listening again
    DownloadJob  the voice model, pinned and checked like every speech model
                 (transcribe/env_setup.py download_model)

The voice model and the engine it runs in belong to Subtitles' speech
engine (Settings > AI > Transcription); Auto Switch only adds a model to
its folder.
"""

import base64
import collections
import json
import os
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PySide6.QtCore import QThread, Signal

from pages.transcribe import env_setup as es
from pages.transcribe import plan as transcribe_plan

from . import cluster

MARK = "@@BUDDY "
WORKER = Path(__file__).with_name("voice_worker.py")
MODEL_ID = es.VOICE_IDS[0]


def model_folder():
    return es.buddy_model_dir(MODEL_ID)


def model_installed():
    folder = model_folder()
    return all((folder / name).is_file() for name in es.model_pins().get(MODEL_ID, {}).get("files", {}))


def engine_installed():
    return es.venv_python().exists()


class Cancelled(Exception):
    pass


class VoiceJob(QThread):
    """clips: {track index: [{"id", "path", "spans"}]}. succeeded({track
    index: {"windows": [(clip id, start, end)], "prints": array}})."""

    progressed = Signal(str, float)
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, clips, parent=None):
        super().__init__(parent)
        self.clips = clips
        self.proc = None
        self.cancelled = False

    def cancel(self):
        self.cancelled = True
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()

    def run(self):
        try:
            self.succeeded.emit(self._run())
        except Cancelled:
            self.failed.emit("")
        except Exception as exc:  # noqa: BLE001 - said in the view, not a crash
            self.failed.emit(str(exc) or "Telling the voices apart failed.")

    def _run(self):
        flat, owner = [], []
        for track, clips in self.clips.items():
            for clip in clips:
                flat.append({"id": clip["id"], "path": clip["path"], "spans": clip["spans"]})
                owner.append(track)
        folder = tempfile.mkdtemp(prefix="buddy_voices_")
        job, out = os.path.join(folder, "job.json"), os.path.join(folder, "out.json")
        with open(job, "w", encoding="utf-8") as f:
            json.dump({"model": str(model_folder()), "clips": flat}, f)
        error = self._launch(["embed", "--job", job, "--out", out])
        if error:
            raise RuntimeError(error)
        with open(out, encoding="utf-8") as f:
            result = json.load(f)
        dim = int(result.get("dim") or 256)
        prints = np.frombuffer(base64.b64decode(result.get("prints") or ""), dtype=np.float16)
        prints = prints.reshape(-1, dim).astype(np.float32) if prints.size else np.zeros((0, dim), np.float32)
        tracks = {t: {"windows": [], "rows": []} for t in self.clips}
        for row, (index, start, end) in enumerate(result.get("windows") or []):
            track = owner[int(index)]
            tracks[track]["windows"].append((flat[int(index)]["id"], float(start), float(end)))
            tracks[track]["rows"].append(row)
        for entry in tracks.values():
            entry["prints"] = prints[entry.pop("rows")] if len(prints) else np.zeros((0, dim), np.float32)
        try:
            for name in (job, out):
                os.remove(name)
            os.rmdir(folder)
        except OSError:
            pass
        return tracks

    def _launch(self, args):
        if self.cancelled:
            raise Cancelled()
        env = dict(os.environ)
        self.proc = subprocess.Popen(
            [str(es.venv_python()), "-I", str(WORKER), *args],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
            bufsize=1, env=env, **es._no_window())
        error, tail = None, collections.deque(maxlen=8)
        for line in self.proc.stdout:
            if self.cancelled:
                break
            if not line.startswith(MARK):
                tail.append(line.strip())
                continue
            try:
                msg = json.loads(line[len(MARK):])
            except ValueError:
                continue
            kind = msg.get("type")
            if kind == "error":
                error = msg.get("message") or "The voice engine reported an error."
            elif kind == "progress" and msg.get("total"):
                self.progressed.emit("Telling the voices apart", min(1.0, msg["done"] / msg["total"]))
            elif kind == "status":
                self.progressed.emit(msg.get("message") or "", -1.0)
        self.proc.wait()
        if self.cancelled:
            raise Cancelled()
        if error is None and self.proc.returncode != 0:
            text = "\n".join(t for t in tail if t)
            error = "The voice engine stopped unexpectedly." + (f"\n\n{text}" if text else "")
        return error


class GroupJob(QThread):
    """{track: {"windows", "prints"}} and {track: voices or None (find out)}
    -> succeeded({track: labels})."""

    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, prints, counts, parent=None):
        super().__init__(parent)
        self.prints, self.counts = prints, counts

    def run(self):
        try:
            out = {}
            for track, entry in self.prints.items():
                labels, _sizes = cluster.group(entry["prints"], self.counts.get(track))
                out[track] = labels
            self.succeeded.emit(out)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc) or "Grouping the voices failed.")


class DownloadJob(QThread):
    progressed = Signal(str)
    succeeded = Signal(str)
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cancelled = False

    def cancel(self):
        self.cancelled = True

    def _line(self, text):
        line = transcribe_plan.setup_line(str(text))
        if line is None:
            return
        self.progressed.emit(line[2] if line[0] == "progress" else line[1])

    def run(self):
        try:
            folder = es.download_model(MODEL_ID, self._line, lambda: self.cancelled)
            self.succeeded.emit(folder)
        except Exception as exc:  # noqa: BLE001 - es.SetupError's words, or the cancel
            self.failed.emit("" if self.cancelled else (str(exc) or "The download failed."))
