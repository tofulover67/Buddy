#!/usr/bin/env python3
"""
Single-instance enforcement for Buddy.

Launching from Resolve's Workspace > Scripts menu spawns a brand new
process every time - with nothing preventing it, opening the menu item
twice (e.g. because the first window was minimized to the tray and easy to
forget about) would start a second Buddy independently polling Resolve for
Time Tracker, silently double-counting or conflicting with the first. A
QLocalServer/QLocalSocket handshake (Qt's own standard mechanism for
exactly this) makes the first instance listen on a well-known local name;
every later launch just pings that name - if something answers, this
process asks it to come to the front and exits immediately without ever
building a window.

On a Mac, for the free Resolve: a launch from Resolve's Scripts menu
holds the connection Resolve handed it, the only kind that Resolve allows
(core/resolve_bridge.py) - and a Buddy left running after its Resolve quit
holds a dead one it can never replace. So such a launch asks to take over:
the running instance steps aside (quitting cleanly, as from the tray) when
its own connection is gone, and just comes to the front when it still works.

On Windows the first instance listens before its window is built (attach()
hands the window over once it is), so a launch during those seconds finds
it.
"""

import sys

from PySide6.QtCore import QObject, QThread, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket

SERVER_NAME = "Buddy-SingleInstance"


def notify_existing_instance(start_hidden=False, take_over=False):
    """Returns True if a running instance answered (and has been asked to
    raise itself, unless start_hidden - the auto-start watcher's launch
    mustn't pop up a Buddy that's already running) - the caller should
    exit without creating a window.
    Returns False if nothing answered, meaning this process should become
    the primary instance - or, with take_over (this launch holds a live
    connection from Resolve), if the running instance's connection had died
    and it has now quit to make way for this one."""
    socket = QLocalSocket()
    socket.connectToServer(SERVER_NAME)
    # A real existing instance answers near-instantly; this timeout only
    # matters for how long a normal launch waits before concluding it's
    # the first one, so it's kept short.
    if socket.waitForConnected(250):
        socket.write(b"hidden" if start_hidden else b"takeover" if take_over else b"show")
        socket.waitForBytesWritten(100)
        reply = b""
        if take_over and socket.waitForReadyRead(2000):
            reply = socket.readAll().data()
        socket.disconnectFromServer()
        if reply == b"quitting":
            return not _wait_until_gone()
        return True
    return False


def _wait_until_gone(timeout_ms=10000):
    """True once nothing answers on SERVER_NAME any more."""
    for _ in range(timeout_ms // 250):
        probe = QLocalSocket()
        probe.connectToServer(SERVER_NAME)
        if not probe.waitForConnected(50):
            return True
        probe.disconnectFromServer()
        QThread.msleep(200)
    return False


class SingleInstanceServer(QObject):
    """Owned by the primary instance's ShellWindow for its whole lifetime -
    any later launch's connection brings the window to the front, unless
    that launch says it was --start-hidden (see notify_existing_instance)."""

    def __init__(self, window=None):
        super().__init__(window)
        self.window = window
        self._show_when_attached = False
        # Defensive: if a previous process crashed without a clean
        # shutdown and left a stale registration behind, this clears it so
        # listen() below doesn't fail thinking the name's still taken.
        QLocalServer.removeServer(SERVER_NAME)
        self.server = QLocalServer(self)
        self.server.newConnection.connect(self._handle_connection)
        self.server.listen(SERVER_NAME)

    def attach(self, window):
        """The window, once built: listening started before it existed (so a
        launch during those seconds found this one), and a launch that came
        in meanwhile raises it now."""
        self.window = window
        self.setParent(window)
        if self._show_when_attached:
            self._show_when_attached = False
            window.bring_to_front()

    def _handle_connection(self):
        socket = self.server.nextPendingConnection()
        message = b""
        if socket is not None:
            # The message says whether the second launch was --start-hidden
            # (the auto-start watcher): then Buddy is already running and
            # stays where it is. A launch that sent nothing is a plain one.
            if socket.bytesAvailable() or socket.waitForReadyRead(100):
                message = socket.readAll().data()
            if sys.platform == "darwin":
                if message == b"takeover" and not self._connection_alive():
                    socket.write(b"quitting")
                    socket.waitForBytesWritten(100)
                    socket.disconnectFromServer()
                    # The tray's own Quit: every page flushes first (Time
                    # Tracker's open entry), then the new launch takes over.
                    QTimer.singleShot(0, self.window._quit_app)
                    return
                if message == b"takeover":
                    socket.write(b"stay")
                    socket.waitForBytesWritten(100)
            socket.disconnectFromServer()
        if message != b"hidden":
            if self.window is None:
                self._show_when_attached = True
            else:
                self.window.bring_to_front()

    @staticmethod
    def _connection_alive():
        """Whether this instance still holds a working connection from the
        Resolve that launched it (see the module docstring)."""
        from core import resolve_bridge

        return resolve_bridge.live_host_resolve() is not None
