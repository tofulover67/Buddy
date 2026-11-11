#!/usr/bin/env python3
"""
How Buddy ends once it has really decided to quit - not hidden to the tray.

By the time the event loop returns, everything that matters is written:
each page's on_app_quitting() has flushed its state (Time Tracker's open
entry), the desktop's windows are saved, settings are on disk. What's left
is Python and Qt taking every object apart - dozens of web views, their
Chromium processes, the Resolve connection - and that teardown is where a
quit can stall: the window gone, a tray icon that no longer answers, and a
process only Task Manager could end. A Buddy stuck like that still holds
its single-instance pipe, too, so opening it again from Resolve's Scripts
menu did nothing at all.

So once the loop returns, finish() stops the helper programs Buddy started
(the local model's llama-server), and ends the process outright - the way
pages/color_palette/mac_dropper_worker.py already does. And begin(), as
quitting starts, arms a watchdog: if Buddy is still running WATCHDOG_S
later, every thread's stack goes into the crash log (Windows) and the
process ends anyway.

Ended outright only where the process is Buddy's own - Python, or the
script host Resolve starts for a menu script (fuscript). Run inside
anything else (Resolve's own console), Buddy leaves the ending to it.
"""

import os
import sys
import threading

WATCHDOG_S = 30

_watchdog = None
_enabled = False


def enable():
    """main.py, as Buddy starts: this process is Buddy's to end. Until then
    begin() arms nothing - a test that closes a window goes on running."""
    global _enabled
    _enabled = own_process()


def own_process(executable=None):
    """Whether this process is Buddy's to end: Python itself, or fuscript."""
    name = os.path.basename(executable if executable is not None else sys.executable or "").lower()
    return name.startswith(("python", "fuscript"))


def _trail(text):
    if sys.platform == "win32":
        from core import crash_log
        crash_log.trail("quit", text)


def _stop_helpers():
    """Helper programs that atexit would have stopped - os._exit skips it."""
    llama = sys.modules.get("pages.manual_chat.local_llama")
    if llama is not None:
        try:
            llama.stop_all()
        except Exception:  # noqa: BLE001 - quitting goes on regardless
            pass


def _flush():
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None:
                stream.flush()
        except (OSError, ValueError, AttributeError):
            pass


def _stuck():
    """The watchdog: Buddy didn't manage to end in WATCHDOG_S."""
    _trail(f"still running {WATCHDOG_S}s after quitting began - ending it")
    if sys.platform == "win32":
        from core import crash_log
        crash_log.dump_stacks(f"Buddy was still running {WATCHDOG_S}s after it began quitting:")
    _stop_helpers()
    _flush()
    os._exit(1)


def begin(why="quitting"):
    """Quitting has started for real. Arms the watchdog - once, however many
    ways in (the window's [X], the tray's Quit, aboutToQuit)."""
    global _watchdog
    if _watchdog is not None:
        return
    _trail(why)
    if not _enabled:
        _watchdog = False
        return
    _watchdog = threading.Timer(WATCHDOG_S, _stuck)
    _watchdog.daemon = True
    _watchdog.name = "buddy-quit-watchdog"
    _watchdog.start()


def finish(code=0):
    """The event loop has returned: ends the process (when it's Buddy's own),
    or returns `code` for the caller to exit with."""
    _trail(f"event loop ended ({code})")
    _stop_helpers()
    _flush()
    if _enabled:
        os._exit(code if isinstance(code, int) else 0)
    return code
