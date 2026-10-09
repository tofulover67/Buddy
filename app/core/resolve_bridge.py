#!/usr/bin/env python3
"""
Shared DaVinci Resolve connection for the Buddy shell.

connect() below is the ONE place any tool page goes through to get a live
ResolveController - it always probes reachability in a disposable
subprocess first (see resolve_probe_worker.py for why that has to happen
out-of-process), and only imports DaVinciResolveScript in-process,
here, once that probe has confirmed Resolve is actually up. From that
point on, per-action calls (rename a clip, import media, read the current
timeline) go straight through this in-process controller - the fast
path. A crash is a risk only for the FIRST connection attempt, made before
Resolve is confirmed running.

Launched from Resolve's Scripts menu, Resolve hands the script a ready
connection - the `resolve` global, which on a Mac Buddy.py passes to
set_host_resolve(). That's the only connection the free Resolve allows
there (scriptapp() from a menu script returns None, and so does anything
run out-of-process), so when it's there it's what connect() uses, and the
probe subprocess is skipped: Resolve launched this very process, so it's
running. On Windows Buddy.py passes none: connect() always probes, then
asks scriptapp().

Every tool-specific need (get_current_clips, import_to_media_pool, etc.)
belongs on a subclass or a helper module next to that tool's page, not
here - this module only owns the connect/bootstrap plumbing every tool
shares.
"""

import os
import subprocess
import sys

from core.python_exe import standalone_python

_PROBE_SCRIPT = os.path.join(os.path.dirname(__file__), "resolve_probe_worker.py")
_PROBE_TIMEOUT_SECONDS = 8


class ResolveConnectionError(RuntimeError):
    pass


# The `resolve` object Resolve gave Buddy.py, or None when Buddy wasn't
# started from Resolve's Scripts menu (the auto-start watcher, a dev run) -
# and always on Windows, where Buddy.py hands none over.
_host_resolve = None
_launched_by = None


def set_host_resolve(resolve):
    """Called once by Buddy.py on a Mac, before the app starts, with the
    `resolve` global Resolve injected into it."""
    global _host_resolve, _launched_by
    _host_resolve = resolve
    _launched_by = os.getppid()


def _host_resolve_alive():
    """Whether the Resolve that launched this process still answers. Checks
    first that it's still this process's parent - once Resolve quits, the
    script host is left orphaned and its connection is dead for good, and
    calling into it then isn't worth the risk."""
    if os.getppid() != _launched_by:
        return False
    try:
        return bool(_host_resolve.GetVersionString())
    except Exception:
        return False


def live_host_resolve():
    """The `resolve` object Resolve handed Buddy.py while the Resolve that
    launched this process is still running and answering, else None. For
    checks that would otherwise need a process of their own, which the
    free Resolve won't let connect (Time Tracker's project poll)."""
    if _host_resolve is not None and _host_resolve_alive():
        return _host_resolve
    return None


def probe_resolve_reachable():
    """With a connection from Resolve (see set_host_resolve), whether it's
    still alive. Otherwise runs the reachability check in a child process.
    Returns False (never raises) on any failure, including the probe
    itself timing out or the child process crashing outright - all of
    those just mean "not reachable right now", not an error the caller
    needs to handle specially."""
    if _host_resolve is not None:
        return _host_resolve_alive()
    try:
        result = subprocess.run(
            # On a Mac sys.executable is Resolve's script host, which can't
            # report the probe's result (core/python_exe.py).
            [standalone_python() if sys.platform == "darwin" else sys.executable, _PROBE_SCRIPT],
            timeout=_PROBE_TIMEOUT_SECONDS,
            capture_output=True,
        )
        return result.returncode == 0
    except Exception:
        return False


def _bootstrap_resolve_env():
    if sys.platform == "darwin":
        if "RESOLVE_SCRIPT_API" not in os.environ:
            os.environ["RESOLVE_SCRIPT_API"] = (
                "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
            )
        if "RESOLVE_SCRIPT_LIB" not in os.environ:
            os.environ["RESOLVE_SCRIPT_LIB"] = (
                "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"
            )
    else:
        if "RESOLVE_SCRIPT_API" not in os.environ:
            os.environ["RESOLVE_SCRIPT_API"] = os.path.expandvars(
                r"%PROGRAMDATA%\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting"
            )
        if "RESOLVE_SCRIPT_LIB" not in os.environ:
            os.environ["RESOLVE_SCRIPT_LIB"] = (
                r"C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll"
            )

    modules_path = os.path.join(os.environ["RESOLVE_SCRIPT_API"], "Modules")
    if modules_path not in sys.path:
        sys.path.append(modules_path)

    import DaVinciResolveScript as dvr_script  # noqa: E402
    return dvr_script


class ResolveController:
    """In-process connection - only ever constructed after
    probe_resolve_reachable() has returned True (see connect() below)."""

    def __init__(self):
        if _host_resolve is not None:
            self.resolve = _host_resolve
        else:
            self.resolve = _bootstrap_resolve_env().scriptapp("Resolve")
        if self.resolve is None:
            raise ResolveConnectionError(
                "Could not connect to DaVinci Resolve.\n"
                "Make sure Resolve is running with a project open."
            )
        self.project_manager = self.resolve.GetProjectManager()
        # Which Resolve this is, read once here (off the UI thread) for bug
        # reports (core/bug_report.py): "DaVinci Resolve Studio 20.1.0.21".
        self.about = _about(self.resolve)

    def current_project(self):
        return self.project_manager.GetCurrentProject()


def _about(resolve) -> str:
    try:
        product = " ".join(str(resolve.GetProductName() or "").split())
        version = " ".join(str(resolve.GetVersionString() or "").split())
    except Exception:   # an older Resolve without one of them
        return ""
    return f"{product} {version}".strip()[:120]


def connect():
    """Returns a live ResolveController, or raises ResolveConnectionError.

    Always safe to call even when Resolve isn't running or hasn't started
    yet - the probe subprocess absorbs that crash risk so this function
    itself never takes the caller's process down with it."""
    if not probe_resolve_reachable():
        raise ResolveConnectionError(
            "Could not reach DaVinci Resolve.\n"
            "Make sure Resolve is running with a project open."
        )
    return ResolveController()
