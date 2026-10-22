#!/usr/bin/env python3
"""
Buddy - single-window shell hosting a set of DaVinci Resolve tools. See
registry.py for which tools are real pages vs. still placeholders, and
pages/base.py for the ToolPage contract new pages implement.

Run directly (`python main.py`) for development. Deployment to Resolve's
Workspace > Scripts menu is a single launcher stub + zip (see Buddy.py,
build_buddy_zip.py).

Single-instance guard, the system tray, and "Start Buddy automatically when
Resolve starts" (see core/single_instance.py, core/shell_window.py's own
tray handling, and core/startup_manager.py) exist because Time Tracker
makes Buddy a genuine background app, not just a one-shot action tool -
see pages/time_tracker/page.py's module docstring.
"""

import os
import sys

# Resolve's script host (fuscript) runs scripts with bytecode caching off, so
# every start compiled all of Buddy's ~260 modules again - half a second of
# Python before the window. Buddy's own folder (the launcher's extracted copy)
# is its alone and writable: the first start of a version writes the
# __pycache__, the rest just load it.
sys.dont_write_bytecode = False

# NumPy's maths library (OpenBLAS) sets aside a working buffer for every CPU
# thread the moment it loads - ~480 MB of memory on a 16-thread CPU - for
# matrix maths Buddy never does at that size. One thread in Buddy's own
# process, so before anything imports NumPy; the variable is put back after,
# so helper processes Buddy starts (transcription) keep their threads.
_blas_threads = os.environ.get("OPENBLAS_NUM_THREADS")
os.environ["OPENBLAS_NUM_THREADS"] = "1"
try:
    import numpy  # noqa: F401
except ImportError:
    pass
finally:
    if _blas_threads is None:
        os.environ.pop("OPENBLAS_NUM_THREADS", None)
    else:
        os.environ["OPENBLAS_NUM_THREADS"] = _blas_threads

from PySide6.QtCore import QCoreApplication, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

if sys.platform == "darwin":
    from core import backup, ffmpeg_log, gui_gc, resolve_bridge, startup_manager  # noqa: E402
else:
    # The crash log, the GPU pick and the web engine's switches are Windows'.
    from core import backup, crash_log, ffmpeg_log, gpu_adapter, gui_gc, startup_manager, web_flags  # noqa: E402
from core.app_version import buddy_version  # noqa: E402
from core.i18n import tr  # noqa: E402
from core.settings_store import BUDDY_DIR, SharedSettings  # noqa: E402
from core import tool_dirs  # noqa: E402

# Each tool's folder into ~/.buddy/tools, from the home directory where
# earlier Buddys kept them (core/tool_dirs.py) - before the pages are
# imported, since some settle their paths as they load.
_TOOLS_MOVED = tool_dirs.migrate_all()
from core.shell_window import ShellWindow  # noqa: E402
from core.single_instance import notify_existing_instance, SingleInstanceServer  # noqa: E402
from registry import REGISTRY  # noqa: E402


def main(start_hidden=False):
    if sys.platform != "darwin":
        # First, so a native crash anywhere after this (QtWebEngine inside
        # Resolve's script host leaves no traceback) says what Python was doing.
        crash_log.enable(os.path.join(BUDDY_DIR, "crash.log"),
                         version=f"from {os.path.dirname(os.path.abspath(__file__))}")
        crash_log.enable_trail(os.path.join(BUDDY_DIR, "crash_trail.log"))
        crash_log.trail("home", tool_dirs.home() + (" (BUDDY_HOME)" if os.environ.get("BUDDY_HOME", "").strip() else ""))
        if _TOOLS_MOVED:
            crash_log.trail("tools", "; ".join(f"{b}: {r}" for b, r in _TOOLS_MOVED.items())[:600])

    # Keeps an already-enabled watcher's deployed copy in sync with
    # whatever version of Buddy is currently installed - cheap, and a
    # no-op unless the setting is actually on.
    startup_manager.sync_if_enabled()

    if sys.platform != "darwin":
        # Web pages draw through ANGLE's Direct3D 11 on 12: plain Direct3D 11
        # crashes inside Buddy's process (core/web_flags.py) - and with two
        # GPUs, on the low-power one, away from Resolve's card, unless Settings >
        # Window names another (core/gpu_adapter.py).
        # On AMD's integrated graphics the GPU paints rounded corners wrong, so
        # there pages are painted on the CPU - Settings > Window > Draw web
        # pages on the CPU says where (web_flags.cpu_paint), after the card is known.
        try:
            shared = SharedSettings()
            card, choice = shared.get("web_adapter"), web_flags.cpu_paint_choice(shared.get("web_cpu_paint"))
        except Exception:  # noqa: BLE001 - a settings file the shell will complain about itself
            card, choice = gpu_adapter.AUTO, "auto"
        adapter = gpu_adapter.apply(os.environ, card)
        paint = web_flags.cpu_paint(os.environ, choice, gpu_adapter.amd_integrated())
        crash_log.trail("web", f"{web_flags.apply(os.environ)}; {adapter}; {paint}")
    # Web tool pages (core/web_page.py) share GPU contexts with each other;
    # QtWebEngine needs this set before the QApplication exists.
    QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    if sys.platform == "darwin":
        # The Web tab's spelling dictionaries (pages/web/spelling.py): Qt
        # WebEngine reads where they are once, as it starts. (On Windows
        # web_flags.apply, above, says where.)
        os.environ.setdefault("QTWEBENGINE_DICTIONARIES_PATH",
                              os.path.join(os.path.expanduser("~"), ".buddy", "web-dictionaries"))
    # Before any preview opens a file: Qt's FFmpeg otherwise prints every
    # clip's stream dump and warnings to the console, and on Windows hands
    # GPU-decoded frames to the screen as shared textures, which froze
    # Dailies at a cut (core/ffmpeg_log.py).
    ffmpeg_log.silence()
    if sys.platform != "darwin":
        crash_log.trail("media", f"QT_DISABLE_HW_TEXTURES_CONVERSION={ffmpeg_log.cpu_frames()}")
    # Buddy's own taskbar identity, before any window exists: started by
    # pythonw.exe (the updater's relaunch, the login watcher) the taskbar
    # otherwise groups it under Python and shows Python's icon, not Buddy's.
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Buddy.ResolveTools")
        except (AttributeError, OSError):
            pass
    app = QApplication(sys.argv)
    # Cyclic garbage is freed on this thread only, never inside a worker
    # where a web view's destruction crashes QtWebEngine (core/gui_gc.py).
    gui_gc.install(app)
    # The tray icon is the app's actual "still running" signal once the
    # window is closed/hidden - without this, Qt would quit the whole app
    # the moment the (now only) top-level window goes away, defeating the
    # point of minimizing to tray instead of closing.
    app.setQuitOnLastWindowClosed(False)

    # Relaunching from Resolve's Scripts menu while an instance is already
    # running (e.g. minimized to tray, easy to forget about) must NOT start
    # a second Buddy polling Resolve independently for Time Tracker - that
    # would silently double-count or conflict. If something answers here,
    # it's already been asked to raise itself, so this process is done.
    if sys.platform == "darwin":
        # Launched from Resolve's Scripts menu (a live connection Resolve
        # handed Buddy.py), it asks to take over instead: a Buddy still
        # running from an earlier Resolve session can't talk to this one -
        # see core/single_instance.py.
        take_over = resolve_bridge.live_host_resolve() is not None
        if notify_existing_instance(start_hidden, take_over=take_over):
            return
    else:
        if notify_existing_instance(start_hidden):
            return
        # Listening from here on - before the window is built (seconds of web
        # views), so a second launch meanwhile finds this one, not nothing.
        instance_server = SingleInstanceServer()

    # A backup restored in Settings (core/backup.py): put in place now, the
    # only Buddy running and before anything has read its settings.
    try:
        restored = backup.apply_pending(backup.tool_ids(REGISTRY), buddy_version())
    except Exception as exc:  # noqa: BLE001 - a failed restore mustn't stop Buddy starting
        restored = {"error": str(exc)}
    if sys.platform != "darwin" and restored:
        crash_log.trail("backup", f"restore: {restored}"[:300])

    window = ShellWindow(app, REGISTRY)
    if sys.platform == "darwin":
        window._single_instance_server = SingleInstanceServer(window)
    else:
        window._single_instance_server = instance_server
        instance_server.attach(window)

    # start_hidden (set by core/resolve_watcher.py's "--start-hidden" - see
    # Buddy.py) means this launch was the auto-start, not the user actually
    # asking to see the app right now - it should sit quietly in the tray
    # instead of popping up its full window unprompted. Falls back to a
    # normal visible launch if no tray icon exists at all (e.g. a rare
    # environment where the system tray is unavailable) - otherwise Buddy
    # would be completely unreachable with no window and no tray icon.
    if start_hidden and window.tray_icon is not None:
        window._update_tray_show_action()
        window.tray_icon.showMessage(
            "Buddy",
            tr("Running in the background – Time Tracker will start tracking once you open a "
               "project. Click Buddy's icon in the menu bar any time to view it.") if sys.platform == "darwin" else
            tr("Running in the background – Time Tracker will start tracking once you open a "
               "project. Click the tray icon any time to view it."),
            window.tray_icon.MessageIcon.Information, 4000,
        )
    else:
        window.show()
    if restored:
        window.show_restored(restored)

    sys.exit(app.exec())


if __name__ == "__main__":
    main(start_hidden="--start-hidden" in sys.argv[1:])
