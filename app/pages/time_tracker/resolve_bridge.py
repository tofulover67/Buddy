#!/usr/bin/env python3
"""
Resolve Bridge for Time Tracker.
Asynchronously checks which project (if any) is currently open in DaVinci
Resolve, via a short-lived helper subprocess (see resolve_poll_worker.py) -
or, when Buddy was launched from Resolve's Scripts menu on a Mac, through
the connection Resolve handed it (see poll()), since the free Resolve won't
let that subprocess connect.
See resolve_poll_worker.py's docstring for why this stays deliberately
separate from core/resolve_bridge.py's shared one-shot connection.

QProcess (not subprocess.run) keeps each poll off the Qt main thread so a
slow or hung check never freezes the UI; the actual subprocess spawn+wait
runs on a plain background thread instead (see poll()), since
_no_feedback_cursor_kwargs() below needs subprocess.run/Popen kwargs QProcess
doesn't expose.

There is deliberately no tasklist-based "is Resolve.exe even running"
pre-check gating whether poll() spawns the worker subprocess. It would only
be a micro-optimization (skip the heavier worker when Resolve is obviously
closed), but `tasklist` resolving via a bare command name depends on PATH,
and when Buddy runs from inside Resolve's own script host that lookup can
fail to find a genuinely-running Resolve.exe - short-circuiting every poll
to "offline" even though the shell's own shared connection
(core/resolve_probe_worker.py, which has no tasklist dependency at all)
connects fine. Every poll goes straight to the real worker subprocess,
exactly like that shared probe does on every reconnect - one extra
short-lived process per poll interval, but correct rather than
fast-and-occasionally-wrong.
"""

import os
import subprocess
import sys
import threading

from PySide6.QtCore import QObject, QTimer, Signal

from core import resolve_bridge as shell_bridge
from core.python_exe import standalone_python

_WORKER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resolve_poll_worker.py")

# Win32 STARTUPINFO.dwFlags bit - not exposed as a named constant by
# Python's own subprocess module, but the numeric value is documented and
# stable. Suppresses Windows' "an application is starting" busy-cursor
# animation for this specific child process launch - without it, spawning a
# brand new OS process on every poll (as often as every 2s, per Settings'
# poll interval) re-triggers that animation faster than Windows' own
# built-in ~2s display window for it can finish, which reads to the user as
# an almost permanently stuck loading spinner next to the mouse cursor.
_STARTF_FORCEOFFFEEDBACK = 0x00000080

# Substrings of the scripting library's own startup banner ("DaVinci Resolve
# Script Interpreter (Python 3.x)" / "Copyright (C) 2005 - 2026 Blackmagic
# Design Pty. Ltd. ..."), so it can be told apart from the real project name
# regardless of which line the banner lands on - see _extract_name() below.
_BANNER_MARKERS = ("Blackmagic Design", "Script Interpreter")


def _no_feedback_cursor_kwargs():
    """Extra subprocess.run()/Popen() kwargs that keep a short-lived,
    windowless helper process from triggering Windows' busy-cursor
    animation. Windows-only - a no-op dict everywhere else."""
    if sys.platform != "win32":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= _STARTF_FORCEOFFFEEDBACK
    return {"startupinfo": startupinfo, "creationflags": subprocess.CREATE_NO_WINDOW}


def _extract_name(stdout: str) -> str | None:
    """The real project name out of the worker's captured stdout, dropping
    the scripting library's own startup banner - not just its last line.

    resolve_poll_worker.py writes only the name, with no trailing newline,
    expecting it to be the last line in the capture: the banner prints once
    a process's connection to fusionscript.dll is established, ahead of the
    name in the ordinary case. But that banner is native code's own doing,
    not something this worker controls the timing of - when Resolve is
    mid-shutdown (project undefined, or Resolve closed while Buddy keeps
    running in the tray) it can print late enough to land AFTER the name
    instead, and a plain "take the last line" would read it as the project
    name itself, logging time against a project literally called
    "Copyright (C) 2005 - 2026 Blackmagic Design Pty. Ltd. ...". Filtering out any line that looks like the banner,
    wherever it falls, is robust to either ordering."""
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    lines = [line for line in lines if not any(marker in line for marker in _BANNER_MARKERS)]
    return lines[-1] if lines else None


def current_project_name(resolve):
    """The open project's name, or None - resolve_poll_worker.py's rules
    (see its main()), on a connection this process already holds. Keep the
    two in step."""
    project_manager = resolve.GetProjectManager()
    project = project_manager.GetCurrentProject() if project_manager else None
    name = project.GetName() if project else None
    # Resolve keeps an "Untitled Project" current while sitting on the
    # Project Manager screen - see the worker's comment.
    if not name or name == "Untitled Project":
        return None
    return name


class ResolveBridge(QObject):
    # str project name (or None if no project/Resolve unreachable), and
    # separately whether the check itself completed at all (False only for
    # a hung/crashed worker subprocess, e.g. a timeout) - a plain "no
    # project" or "Resolve isn't open" result both come back as
    # (None, True), since resolve_poll_worker.py's own exit code can't tell
    # those two apart any more cheaply than actually trying to connect.
    project_detected = Signal(object, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._busy = False

    def poll(self):
        """Kicks off an async check; project_detected fires once it resolves.
        No-ops while a previous poll is still in flight, so a slow or hung
        check can't pile up subprocesses. Runs the actual subprocess spawn
        + wait on a plain background thread (not Qt's QProcess, which offers
        no way to apply _no_feedback_cursor_kwargs() above) - emitting a
        Signal from a non-Qt thread is safe here, Qt queues the delivery to
        this object's own (main) thread automatically."""
        if self._busy:
            return
        self._busy = True
        if shell_bridge.live_host_resolve() is not None:
            # Launched from Resolve's Scripts menu on a Mac: ask through the
            # connection Resolve handed Buddy - the free Resolve won't let
            # the worker subprocess connect at all. On the main thread, a
            # tick from now: it's one quick call, and this way it never runs
            # alongside a tool's own calls on that connection.
            QTimer.singleShot(0, self._poll_host)
            return
        threading.Thread(target=self._poll_worker, daemon=True).start()

    def _poll_host(self):
        name = None
        try:
            host = shell_bridge.live_host_resolve()
            if host is not None:
                name = current_project_name(host)
        except Exception:
            name = None
        finally:
            self._busy = False
        self.project_detected.emit(name, True)

    def _poll_worker(self):
        name = None
        completed = False
        try:
            result = subprocess.run(
                # On a Mac sys.executable is Resolve's script host, which
                # can't report the worker's result (core/python_exe.py).
                [standalone_python() if sys.platform == "darwin" else sys.executable, _WORKER_PATH],
                capture_output=True, text=True, timeout=15,
                **_no_feedback_cursor_kwargs(),
            )
            completed = True
            if result.returncode == 0:
                name = _extract_name(result.stdout)
        except Exception:
            name = None  # a hung/crashed worker just comes back as "no project detected"
        finally:
            self._busy = False
            self.project_detected.emit(name, completed)
