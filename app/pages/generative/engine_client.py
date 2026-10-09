#!/usr/bin/env python3
"""
Buddy's side of Generative's engine process (worker.py, run with the
engine environment's Python): starts it when first asked, sends one
request at a time, hands the replies back on the UI thread.

    client.ask("fill", {"strokes": [...]}, done, progress)
        done(result, error)  - error a str, or None
        progress(step)       - while it works: {"stage", "detail", "progress"
                               (0-1, or absent while there's no telling)}

The process holds the picture and its undo history, so it's kept running
while a picture is open, and stopped when Buddy quits (stop()). If it
dies, the request in hand fails with what it printed last, and the next
one starts a fresh process - with no picture open.
"""

import collections
import json
import os
import subprocess
import threading

from PySide6.QtCore import QObject, Signal

from . import engine_setup as es

MARK = "@@BUDDY@@"


class EngineClient(QObject):
    _line = Signal(object)          # a parsed reply, or {"ended": proc id}

    def __init__(self, parent=None, preload=()):
        super().__init__(parent)
        self.preload = list(preload)    # models worth loading as the process starts
        self.use_gpu = True             # the graphics card, when there's one (gpu.py) - restart to change
        self._proc = None
        self._next_id = 0
        self._pending = None        # (id, done, progress)
        self._tail = collections.deque(maxlen=8)
        self._line.connect(self._on_line)

    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def busy(self) -> bool:
        return self._pending is not None

    def ask(self, do, args, done, progress=None) -> bool:
        """False (done never called) while a request is in hand."""
        if self._pending is not None:
            return False
        problem = self._start()
        if problem:
            done(None, problem)
            return True
        self._next_id += 1
        self._pending = (self._next_id, done, progress)
        try:
            self._proc.stdin.write(json.dumps({"id": self._next_id, "do": do, "args": args or {}}) + "\n")
            self._proc.stdin.flush()
        except (OSError, ValueError):
            self._ended()
        return True

    def stop(self):
        proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            try:
                proc.stdin.write(json.dumps({"do": "quit"}) + "\n")
                proc.stdin.flush()
                proc.wait(2)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                try:
                    proc.kill()
                except OSError:
                    pass

    # ------------------------------------------------------------ process --

    def _start(self) -> str:
        """"" once a process is running, else why not."""
        if self.running():
            return ""
        self._proc = None
        python = es.venv_python()
        if not python.exists():
            return "Generative's engine isn't installed."
        _clear_work()
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)          # Resolve's, not the engine's
        env["BUDDY_GENERATIVE_GPU"] = "1" if self.use_gpu else "0"     # gpu.py
        try:
            proc = subprocess.Popen(
                [str(python), str(es.WORKER), "--models", str(es.MODELS_DIR), "--work", str(es.WORK_DIR),
                 "--preload", ",".join(m for m in self.preload if es.model_path(m).exists())],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                encoding="utf-8", errors="replace", bufsize=1, env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as exc:
            return f"Couldn't start Generative's engine: {exc}"
        self._proc = proc
        self._tail.clear()
        threading.Thread(target=self._read, args=(proc,), daemon=True, name="generative-engine").start()
        return ""

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
        try:
            self._line.emit({"ended": id(proc)})
        except RuntimeError:
            pass                    # Buddy closing

    def _on_line(self, data):
        if "say" in data:
            if data["say"]:
                self._tail.append(data["say"])
            return
        if "ended" in data:
            if self._proc is not None and id(self._proc) == data["ended"]:
                self._ended()
            return
        if self._pending is None or data.get("id") != self._pending[0]:
            return
        if "stage" in data:
            if self._pending[2] is not None:
                self._pending[2](data)
            return
        _rid, done, _progress = self._pending
        self._pending = None
        if data.get("ok"):
            done(data.get("result") or {}, None)
        else:
            done(None, data.get("error") or "The engine couldn't do that.")

    def _ended(self):
        proc, self._proc = self._proc, None
        if proc is not None:
            try:
                proc.kill()
            except OSError:
                pass
        pending, self._pending = self._pending, None
        if pending is not None:
            why = "Generative's engine stopped unexpectedly."
            tail = "\n".join(self._tail)
            if tail:
                why += "\n\n" + tail[-600:]
            pending[1](None, why)


def _clear_work():
    """Previews (and footage's tracked masks) a process before this one left behind (it was killed)."""
    try:
        names = os.listdir(es.WORK_DIR)
    except OSError:
        return
    for name in names:
        if name.endswith((".jpg", ".png", ".bmp")):
            try:
                os.remove(os.path.join(es.WORK_DIR, name))
            except OSError:
                pass
