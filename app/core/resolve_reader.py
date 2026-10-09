"""
Reads from Resolve that never freeze Buddy.

While the timeline plays, Resolve holds every scripting call until playback
stops, and fusionscript keeps Python's GIL for as long as a call waits - so a
read made inside Buddy, even on a ResolveWorker thread, froze every Buddy
window until playback stopped (measured on 21.1: a second thread made no
progress during a 500 ms call; a user's Buddy sat "loading" through a whole
play). A ResolveReader asks a process of its own instead
(resolve_reader_child.py, started once and kept), one read at a time, and
waits on its pipe on a thread of its own - a pipe read lets the GIL go - so
Buddy carries on, and a page can say Resolve is busy.

The reads it can make are the child's JOBS. If the child can't be started or
can't reach Resolve (no interpreter to run it with, external scripting off),
available() is False for a while and the page reads the old way.

Most pages use ResolveCalls (below): one child shared by all of them
(shared_reader(), which takes reads from several at once and answers them in
turn), with the page's ResolveWorker as the old way.
"""

import functools
import json
import os
import subprocess
import sys
import threading
import time

from PySide6.QtCore import QCoreApplication, QEventLoop, QObject, Signal

from core.resolve_bridge import ResolveConnectionError
from core.resolve_worker import ResolveWorker

CHILD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resolve_reader_child.py")
RETRY_AFTER_S = 30          # a child that couldn't reach Resolve: tried again after this


class ReadError(RuntimeError):
    """A read the child couldn't make: `kind` is the exception's class name there."""

    def __init__(self, message, kind=""):
        super().__init__(message)
        self.kind = kind


@functools.lru_cache(maxsize=1)
def _python():
    """The interpreter to run the child with: inside Resolve, sys.executable
    is its script host (fuscript), which won't do for a helper. On a Mac the
    Python it embeds (core.python_exe, as every helper there); on Windows
    Audio Assistant's choice (child_python)."""
    try:
        if sys.platform == "darwin":
            from core.python_exe import standalone_python
            return standalone_python()
        else:
            from pages.audio_assistant.page import child_python
            return child_python()
    except Exception:
        return None if os.path.basename(sys.executable).lower().startswith("fuscript") else sys.executable


class ResolveReader(QObject):
    """ask(name, args, done): done(result, error) on the UI thread - error a
    ReadError, or None. One read at a time - or, with queue, any number,
    answered in the order asked."""

    _line = Signal(object)          # a line from the child, parsed (or None: it ended)

    def __init__(self, parent=None, queue=False):
        super().__init__(parent)
        self._proc = None
        self._queue = queue
        self._pending = {}          # id -> (done, started), oldest first
        self._next_id = 0
        self._ready = False
        self._failed_at = 0.0
        self.problem = ""
        self._line.connect(self._on_line)

    # ---------------------------------------------------------------- use --

    def available(self) -> bool:
        """Worth asking: a child's running, or it may be started. Never with
        BUDDY_RESOLVE_READER=off (the tests: their fake Resolve is in-process)."""
        if os.environ.get("BUDDY_RESOLVE_READER") == "off":
            return False
        return (self._proc is not None or time.monotonic() - self._failed_at > RETRY_AFTER_S) and _python() is not None

    def busy(self) -> bool:
        return bool(self._pending)

    def waiting_for(self) -> float:
        """Seconds the oldest read in hand has waited; 0 with none."""
        return time.monotonic() - next(iter(self._pending.values()))[1] if self._pending else 0.0

    def ask(self, name, args, done) -> bool:
        """Sends a read. False (and done never called) when no child can be
        started, or - without queue - one's already in hand."""
        if (self._pending and not self._queue) or not self._start():
            return False
        self._next_id += 1
        self._pending[self._next_id] = (done, time.monotonic())
        try:
            self._proc.stdin.write(json.dumps({"id": self._next_id, "do": name, "args": args or {}}) + "\n")
            self._proc.stdin.flush()
        except (OSError, ValueError):
            # False promises this request's callback was never called:
            # ResolveCalls will run its fallback. Older queued reads still
            # need their error callbacks, but this one must not run twice.
            self._pending.pop(self._next_id, None)
            self._ended("The Resolve reader stopped.")
            return False
        return True

    def stop(self):
        proc, self._proc = self._proc, None
        if proc is not None:
            try:
                proc.kill()
            except OSError:
                pass

    # ------------------------------------------------------------ process --

    def _start(self) -> bool:
        if self._proc is not None:
            if self._proc.poll() is None:
                return True
            # Gone before its "ended" line got here: the reads it had in hand
            # fail now, not never (that line, when it comes, names a child
            # that's no more, and is ignored).
            self._ended("The Resolve reader stopped.")
        if not self.available():
            return False
        python = _python()
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            proc = subprocess.Popen([python, CHILD], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1,
                                    cwd=os.path.dirname(os.path.dirname(CHILD)), creationflags=flags)
        except OSError as exc:
            self._failed_at, self.problem = time.monotonic(), str(exc)
            return False
        self._proc, self._ready = proc, False
        threading.Thread(target=self._read, args=(proc,), daemon=True, name="resolve-reader").start()
        return True

    def _read(self, proc):
        """The child's lines, handed to the UI thread - on a thread of its own,
        where waiting on the pipe holds nothing up."""
        try:
            for line in proc.stdout:
                try:
                    self._line.emit(json.loads(line))
                except ValueError:
                    continue
        except (OSError, ValueError):
            pass
        try:
            self._line.emit({"ended": True, "proc": id(proc)})
        except RuntimeError:
            pass                    # Buddy closing: nobody left to tell

    def _on_line(self, data):
        if not isinstance(data, dict):
            return
        if data.get("ended"):
            if self._proc is not None and id(self._proc) == data.get("proc"):
                self._ended("The Resolve reader stopped.")
            return
        if "ready" in data:
            self._ready = bool(data["ready"])
            if not self._ready:
                self.problem = data.get("error") or "The Resolve reader couldn't reach Resolve."
                self._failed_at = time.monotonic()
            return
        pending = self._pending.pop(data.get("id"), None)
        if pending is None:
            return
        done = pending[0]
        if data.get("ok"):
            done(data.get("result"), None)
        else:
            done(None, ReadError(data.get("error") or "Resolve didn't answer.", data.get("type") or ""))

    def _ended(self, why):
        """The child's gone: the read in hand fails, and the next one starts a new child."""
        proc, self._proc = self._proc, None
        if proc is not None:
            try:
                proc.kill()
            except OSError:
                pass
        if not self._ready:
            self._failed_at = time.monotonic()
        pending, self._pending = self._pending, {}
        for done, _started in pending.values():
            done(None, ReadError(self.problem or why, "Ended"))


_SHARED = None


def shared_reader():
    """The one reader process the pages' ResolveCalls share, started on its
    first read and stopped when Buddy quits."""
    global _SHARED
    if _SHARED is None:
        # The application's child: a parentless QObject left for Python to
        # delete at exit, after Qt has gone, crashed the exit.
        app = QCoreApplication.instance()
        _SHARED = ResolveReader(app, queue=True)
        if app is not None:
            app.aboutToQuit.connect(_SHARED.stop)
    return _SHARED


class ResolveCalls(QObject):
    """A page's Resolve calls: call(name, args, job, done) asks the shared
    reader's process for its job `name` when there is one - Buddy never waits
    on Resolve, even while the timeline plays - else runs job() on the page's
    ResolveWorker (`worker`, which the page's own writes use too).
    done(result, error) on the UI thread either way; a child's error comes
    back as the same exception class the job would raise here, for those in
    `errors` ({class name: class}; ResolveConnectionError always)."""

    def __init__(self, parent=None, worker=None, errors=None):
        super().__init__(parent)
        self.reader = shared_reader()
        self.worker = worker if worker is not None else ResolveWorker(self)
        self._errors = {"ResolveConnectionError": ResolveConnectionError, **(errors or {})}
        self._asked = {}            # this page's reads in the reader: token -> started

    def call(self, name, args, job, done) -> bool:
        """False (done never called) when nothing could be started: the
        worker's still busy with an earlier job."""
        token = object()

        def back(result, error):
            self._asked.pop(token, None)
            if isinstance(error, ReadError):
                if error.kind == "Ended" and not self.reader.available() and self.worker.start(job, done):
                    return          # the reader couldn't run here: the old way
                kind = self._errors.get(error.kind)
                if kind is not None:
                    error = kind(str(error))
            done(result, error)

        if self.reader.available():
            self._asked[token] = time.monotonic()
            if self.reader.ask(name, args, back):
                return True
            self._asked.pop(token, None)
        return self.worker.start(job, done)

    def busy(self) -> bool:
        """One of this page's calls is still waiting on Resolve."""
        return bool(self._asked) or self.worker.busy()

    def waiting_for(self) -> float:
        """Seconds this page's oldest call has waited; 0 with none."""
        oldest = time.monotonic() - min(self._asked.values()) if self._asked else 0.0
        return max(oldest, self.worker.running_for())

    def wait_idle(self, timeout: float) -> bool:
        """Up to `timeout` seconds for this page's calls to be answered,
        before an action of its own talks to Resolve - True once they are.
        The page keeps drawing meanwhile; what the user does waits."""
        deadline = time.monotonic() + timeout
        if not self.worker.wait_idle(timeout):
            return False
        while self._asked:
            if time.monotonic() >= deadline:
                return False
            QCoreApplication.processEvents(QEventLoop.ExcludeUserInputEvents, 10)
            time.sleep(0.005)
        return True
