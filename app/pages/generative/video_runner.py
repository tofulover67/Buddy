#!/usr/bin/env python3
"""
Buddy's side of a footage job (video_job.py, run with the engine's Python
in a process of its own - not the engine's: a job runs for minutes, while
the engine stays free to show frames).

    run = VideoRun(spec, parent)
    run.progressed.connect(lambda step: ...)   {"stage", "detail", "frame", "of", "progress"}
    run.finished.connect(lambda result, error: ...)
    run.start()                                # False: couldn't start (finished says why)
    run.stop()                                 # ends the process; started again with the same
                                               # spec, the job picks up where it was

The spec is written beside the job's output (out/.work/job.json), so a job
can be looked at - or run again by hand - after.
"""

import collections
import json
import os
import shutil
import subprocess
import threading

from PySide6.QtCore import QObject, Signal

from core import ai_card

from . import engine_setup as es

MARK = "@@BUDDY@@"
JOB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "video_job.py")


class VideoRun(QObject):
    progressed = Signal(object)
    finished = Signal(object, object)       # result or None, error (str) or None
    _line = Signal(object)

    def __init__(self, spec, parent=None):
        super().__init__(parent)
        self.spec = dict(spec)
        self._proc = None
        self._stopped = False
        self._result = None
        self._error = None
        self._tail = collections.deque(maxlen=8)
        self._line.connect(self._on_line)

    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> bool:
        python = es.venv_python()
        if not python.exists():
            self.finished.emit(None, "Generative's engine isn't installed.")
            return False
        work = os.path.join(self.spec["out"], ".work")
        try:
            os.makedirs(work, exist_ok=True)
            self._keep_tracking(work)
            spec_path = os.path.join(work, "job.json")
            with open(spec_path, "w", encoding="utf-8") as fh:
                json.dump(self.spec, fh, indent=1)
        except OSError as exc:
            self.finished.emit(None, f"Can't write to {self.spec['out']}: {exc.strerror or exc}")
            return False
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)          # Resolve's, not the engine's
        env["BUDDY_GENERATIVE_GPU"] = "1" if self.spec.get("gpu", True) else "0"     # gpu.py
        ai_card.cuda_env(env)                # on Settings > AI's card, if one was chosen
        try:
            self._proc = subprocess.Popen(
                [str(python), JOB, spec_path], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", bufsize=1, env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as exc:
            self.finished.emit(None, f"Couldn't start the job: {exc}")
            return False
        threading.Thread(target=self._read, args=(self._proc,), daemon=True, name="generative-video-job").start()
        return True

    def _keep_tracking(self, work):
        """Track's masks are the engine's, gone when it tracks again or
        stops: copied in beside the job (.work/tracked), so a job carried on
        later - the clip closed, Buddy restarted - follows what it started with."""
        masks = (self.spec.get("tracked") or {}).get("masks") or {}
        if not masks:
            return
        keep = os.path.join(work, "tracked")
        os.makedirs(keep, exist_ok=True)
        kept = {}
        for k, path in masks.items():
            dest = os.path.join(keep, f"{int(k):06d}.png")
            try:
                if os.path.normcase(os.path.abspath(path)) != os.path.normcase(dest):
                    shutil.copyfile(path, dest)
            except OSError:
                continue                     # not all there: the job tracks again itself
            kept[str(k)] = dest
        self.spec["tracked"] = dict(self.spec["tracked"], masks=kept)

    def stop(self):
        """Ends the job where it is; what's written stays."""
        self._stopped = True
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass

    def _read(self, proc):
        try:
            for line in proc.stdout:
                if line.startswith(MARK):
                    try:
                        self._line.emit(json.loads(line[len(MARK):]))
                        continue
                    except ValueError:
                        pass
                self._line.emit({"say": line.rstrip()})
        except (OSError, ValueError):
            pass
        proc.wait()
        try:
            proc.stdout.close()
        except OSError:
            pass
        try:
            self._line.emit({"ended": proc.returncode})
        except RuntimeError:
            pass                    # Buddy closing

    def _on_line(self, data):
        if "say" in data:
            if data["say"]:
                self._tail.append(data["say"])
        elif "stage" in data:
            self.progressed.emit(data)
        elif data.get("done"):
            self._result = data
        elif "error" in data:
            self._error = data["error"]
        elif "ended" in data:
            self._proc = None
            if self._stopped:
                self.finished.emit(None, "Stopped.")
            elif self._result is not None:
                self.finished.emit(self._result, None)
            else:
                why = self._error or "The job stopped unexpectedly."
                if not self._error and self._tail:
                    why += "\n\n" + "\n".join(self._tail)[-600:]
                self.finished.emit(None, why)
