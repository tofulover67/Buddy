#!/usr/bin/env python3
"""
WebToolPage - a ToolPage whose UI is HTML/CSS/JS in a QWebEngineView -
and WebWindow, the same for a small window of its own (Color Palette's
mini palette, which floats over Resolve), and WebDialog for a modal one
(the shell's Settings, core/message_dialog.py's alert() and confirm()).
All are a WebSurface: the
view, the channel to its JS and the theme.

A web tool is still a ToolPage living in the same shell - nav rail,
header, dual view, busy overlay and the Settings dialog work the same - so
web and Qt pages sit side by side.

The split:
    Python (the page subclass) owns ALL state and every Resolve / file /
    network call. JS is a view: it draws what Python
    sends and reports what the user did.

    Python -> JS   self.emit("event_name", payload)   (payload: JSON-able)
    JS -> Python   Buddy.send("action_name", payload) calls
                   self.on_action_name(payload) on the page

Actions are dispatched on the next event-loop turn, never inside the
channel's own message handler, so a handler may open a modal dialog
(QFileDialog, a consent prompt) without re-entering QWebChannel. Events
emitted before the page's JS has connected are queued and flushed once it
does. After a reload (or a crashed renderer being restarted) the queue
starts empty and web_ready() is called again: a page should push its whole
state from there, not assume the view remembers anything.

The HTML lives next to the page, at pages/<tool>/web/index.html, and pulls
in the shared app/web/buddy.css and buddy.js (see those files). The theme
arrives as CSS variables from core/web_theme.py, on load and on every
theme change; the language's strings ("i18n", core/i18n.py) on load and
whenever Settings changes it, and buddy.js translates the page - so a page
writes English and needs nothing more.

Set BUDDY_WEB_DEBUG=1 to get Chromium DevTools at http://localhost:9223.
"""

import atexit
import json
import os
import sys
import time
import weakref

from PySide6.QtCore import QEvent, QEventLoop, QObject, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import QApplication, QDialog, QVBoxLayout, QWidget

if os.environ.get("BUDDY_WEB_DEBUG"):
    os.environ.setdefault("QTWEBENGINE_REMOTE_DEBUGGING", "9223")

from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView

if sys.platform == "win32":
    from core import crash_log      # the crash trail: Windows only
from core.i18n import LANGUAGE_CODES, get_i18n, tr, web_strings
from core.web_theme import web_theme
from pages.base import ToolPage

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_COMMON_DIR = os.path.join(APP_DIR, "web")

# A window of its own waits this long at most for its page before showing
# anyway (see _ReadyToShow), and this long after the page connects - for
# its script to apply the theme and Chromium to draw.
READY_TIMEOUT_MS = 1500
READY_SETTLE_MS = 40

# Events for a page that isn't there to take them (loading, or let go - see
# let_go()) wait in a queue; past this many the oldest go - web_ready()
# redraws everything when the page is back anyway.
MAX_QUEUED = 500

# What a page's script is asked before it's let go: whether it's holding on to
# something only it has (buddy.js keepAlive). No answer means keep it.
KEEP_ALIVE_JS = "typeof Buddy === 'object' && typeof Buddy.keepAlive === 'function' ? Buddy.keepAlive() : true"

# Links a page may open in the user's browser. Anything else a page tries
# to navigate to is refused: the view only ever shows its own file.
EXTERNAL_SCHEMES = ("http", "https", "mailto")

_profile = None
# Every web view built, until it's deleted - see _delete_web_views(). Kept on
# a Mac only.
_views = weakref.WeakSet()


def _delete_web_views():
    """Deletes every web view (and with it its page and channel) while the
    QApplication still exists. Left to PySide's own exit cleanup, the
    application is destroyed with the pages still alive: Qt then warns
    that the profile outlived them, and QtWebEngine can deliver a page's
    last channel message into a channel already torn down - a segfault on
    exit, seen on macOS after every test had passed."""
    for view in list(_views):
        try:
            view.deleteLater()
        except RuntimeError:   # already deleted along with its window
            pass
    _views.clear()
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def _shared_profile():
    """One off-the-record profile for every web page: nothing (cache,
    cookies, localStorage) is written to disk, and the pages share one set
    of Chromium processes. Parented to the application so it outlives
    every page using it - Qt warns and leaks if a profile dies first."""
    global _profile
    if _profile is None:
        if sys.platform == "darwin":
            # Registered after PySide's own exit cleanup, so it runs before it
            # (atexit runs the newest first).
            atexit.register(_delete_web_views)
        _profile = QWebEngineProfile(QApplication.instance())
        _profile.setHttpCacheType(QWebEngineProfile.NoCache)
        _profile.setPersistentCookiesPolicy(QWebEngineProfile.NoPersistentCookies)
        s = _profile.settings()
        s.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, False)
        s.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)
        s.setAttribute(QWebEngineSettings.JavascriptCanOpenWindows, False)
        s.setAttribute(QWebEngineSettings.JavascriptCanAccessClipboard, False)
        s.setAttribute(QWebEngineSettings.PluginsEnabled, False)
        s.setAttribute(QWebEngineSettings.ScrollAnimatorEnabled, True)
    return _profile


def view_label(home_url):
    """A short name for a view in the crash trail, from its page's folder:
    pages/buddy_network/web/index.html -> "buddy_network",
    web/shell/header/index.html -> "shell/header"."""
    parts = home_url.toLocalFile().replace("\\", "/").split("/")
    if "web" not in parts:
        return parts[-1]
    i = len(parts) - 1 - parts[::-1].index("web")
    after = parts[i + 1:-1]
    return "/".join(after if parts[i - 1] == "app" else [parts[i - 1], *after]) or parts[-1]


class _Page(QWebEnginePage):
    """Keeps the view on its own file: links to the web open in the user's
    browser, everything else is refused."""

    def __init__(self, home_url, parent):
        super().__init__(_shared_profile(), parent)
        self._home = home_url
        if sys.platform == "win32":
            self.label = view_label(home_url)

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        if url.scheme() in EXTERNAL_SCHEMES:
            QDesktopServices.openUrl(url)
            return False
        if not is_main_frame:
            return False
        # The page's own file (a load or reload) and nothing else.
        return url.isLocalFile() and url.toLocalFile() == self._home.toLocalFile()

    def createWindow(self, _type):
        return None

    def javaScriptConsoleMessage(self, level, message, line, source):
        if level != QWebEnginePage.InfoMessageLevel or os.environ.get("BUDDY_WEB_DEBUG"):
            where = os.path.basename(QUrl(source).path()) or source
            if sys.platform == "win32":
                crash_log.trail("console", f"{self.label} {where}:{line} {message[:160]}")
            print(f"[web {where}:{line}] {message}", file=sys.stderr)


class _Bridge(QObject):
    """The one object the page's JS sees (as `backend`). Deliberately
    generic - two members - so a tool's protocol lives in its own page
    class, not in a growing list of typed slots here."""

    event = Signal(str, str)

    def __init__(self, page):
        # A Qt parent, so Qt deletes it with its window, and only a weak
        # reference back: a strong one made a cycle (surface -> bridge ->
        # surface) that left every closed window's web view for Python's
        # cycle collector, which runs on whichever thread triggers it.
        # Chromium aborts when a view is destroyed off the GUI thread - a
        # crash every hour or two. See core/gui_gc.py.
        super().__init__(page)
        self._page = weakref.ref(page)

    @Slot()
    def ready(self):
        page = self._page()
        if page is not None:
            page._js_ready()

    @Slot(str, str)
    def send(self, name, payload):
        try:
            data = json.loads(payload) if payload else None
        except ValueError:
            data = None
        # Next turn of the event loop, never inside QWebChannel's handler.
        page_ref = self._page
        QTimer.singleShot(0, lambda: (page := page_ref()) is not None and page._dispatch(name, data))


# What a clip dragged off Resolve's timeline carries: one format of Resolve's
# own ("Timeline/Item", Qt's name for it on Windows) holding only a pointer
# inside Resolve - the same for every clip. The Media Pool's clips can't be
# dragged out of Resolve at all (both seen with Resolve 21.1 on Windows,
# 2026-10-07; not yet checked on a Mac).
RESOLVE_CLIP = "Timeline/Item"


def _is_resolve_clip(mime):
    return mime is not None and any(f == RESOLVE_CLIP or f.endswith(f'"{RESOLVE_CLIP}"') for f in mime.formats())


class _FileDropFilter(QObject):
    """Catches files dragged onto the view from Explorer or the Finder. The page's JS
    can't get at a dropped file's path (browsers hide it), so drops are
    taken on the Qt side and handed to the page as paths. With the page's
    resolve_clip_drops, a clip dragged off Resolve's timeline too."""

    def __init__(self, page):
        super().__init__(page)
        self._page = weakref.ref(page)   # no cycle - see _Bridge

    @staticmethod
    def _paths(event):
        mime = event.mimeData()
        if mime is None or not mime.hasUrls():
            return []
        return [u.toLocalFile() for u in mime.urls() if u.isLocalFile() and u.toLocalFile()]

    @staticmethod
    def _resolve_clip(page, event):
        return getattr(page, "resolve_clip_drops", False) and _is_resolve_clip(event.mimeData())

    @staticmethod
    def _accept(event):
        # A copy where the source allows it: nothing is to leave Resolve's timeline.
        if event.possibleActions() & Qt.CopyAction:
            event.setDropAction(Qt.CopyAction)
            event.accept()
        else:
            event.acceptProposedAction()

    def eventFilter(self, obj, event):
        page = self._page()
        if page is None:
            return False
        kind = event.type()
        if kind in (QEvent.DragEnter, QEvent.DragMove):
            if self._paths(event) or self._resolve_clip(page, event):
                accepts = getattr(page, "accepts_drops", None)
                if accepts is not None and not accepts():
                    # Turned away: the "not here" cursor - and the page told once, to say why.
                    event.ignore()
                    if kind == QEvent.DragEnter:
                        page.emit("drop_hover", "refused")
                    return True
                self._accept(event)
                if kind == QEvent.DragEnter:
                    page.emit("drop_hover", True)
                return True
        elif kind == QEvent.DragLeave:
            page.emit("drop_hover", False)
        elif kind == QEvent.Drop:
            page_ref = self._page
            paths = self._paths(event)
            if paths:
                self._accept(event)
                page.emit("drop_hover", False)
                QTimer.singleShot(0, lambda: (page := page_ref()) is not None and page.on_files_dropped(paths))
                return True
            if self._resolve_clip(page, event):
                self._accept(event)
                page.emit("drop_hover", False)
                QTimer.singleShot(0, lambda: (page := page_ref()) is not None and page.on_resolve_clip_dropped())
                return True
        return False


class WebSurface:
    """The web view and its channel, for a QWidget subclass. Set `web_dir`
    (a folder holding `web_file`) and implement web_ready() plus an
    on_<action>(payload) method per JS action. `self.host` gives the theme
    (theme_tokens(), shared_settings).

    Set `file_drops = True` to take files dragged in from Explorer:
    on_files_dropped(paths) is called, and the view gets "drop_hover"
    (true/false) while a drag is over it. With `resolve_clip_drops = True`
    as well, a clip dragged off Resolve's timeline calls
    on_resolve_clip_dropped() - which clip it was is for the page to ask
    Resolve (it's the one selected)."""

    web_dir = None
    web_file = "index.html"
    file_drops = False
    resolve_clip_drops = False
    # Transparent, so a tool sits on whatever the shell draws behind it
    # (Modern's gradient, Resolve's window grey) like a Qt page does. A
    # window of its own has nothing behind it: its page paints --page-bg.
    transparent = True

    def _build_web(self):
        """Builds the view into this widget, runs build_state() and loads
        the page."""
        self._ready = False
        self._queue = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.view = QWebEngineView(self)
        if sys.platform == "darwin":
            _views.add(self.view)
        home = QUrl.fromLocalFile(os.path.join(self.web_dir, self.web_file))
        page = _Page(home, self.view)
        if self.transparent:
            page.setBackgroundColor(QColor(Qt.transparent))
        else:
            # The theme's colour until the page paints, not a white flash.
            try:
                page.setBackgroundColor(QColor(self.host.theme_tokens()["surface"]))
            except Exception:  # noqa: BLE001 - a host without a theme
                pass
        self.view.setPage(page)
        self._channel = QWebChannel(page)
        self._bridge = _Bridge(self)
        self._channel.registerObject("backend", self._bridge)
        page.setWebChannel(self._channel)
        page.loadStarted.connect(self._on_load_started)
        page.renderProcessTerminated.connect(self._on_renderer_died)
        self.view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._edit_menu)
        layout.addWidget(self.view)
        if self.file_drops:
            # Chromium's own widget (the focus proxy) is what receives the
            # drag, and it's made when the page first loads.
            self._drop_filter = self._make_drop_filter()
            self.view.installEventFilter(self._drop_filter)
            page.loadFinished.connect(self._watch_drops)

        # A new language: this view gets its strings and redraws in it.
        # A bound method of a QObject, so Qt drops the connection with it.
        get_i18n().language_changed.connect(self._on_language_changed)

        self.build_state()
        page.load(home)

    def _make_drop_filter(self):
        """What catches the drops (see file_drops) - a view taking more than
        files gives its own."""
        return _FileDropFilter(self)

    def _watch_drops(self, _ok=True):
        proxy = self.view.focusProxy()
        if proxy is not None:
            proxy.installEventFilter(self._drop_filter)

    def accepts_drops(self):
        """Whether a drag over the view may be dropped now (see file_drops) -
        False turns it away, and the view gets "drop_hover" "refused"."""
        return True

    def on_files_dropped(self, paths):
        """Files dragged in from Explorer (see file_drops)."""

    def on_resolve_clip_dropped(self):
        """A clip dragged off Resolve's timeline (see resolve_clip_drops)."""

    # ---------------------------------------------------------- overrides --

    def build_state(self):
        """Set up the page's Python-side state. Runs before the HTML loads;
        don't emit() from here - web_ready() is where the view gets it."""

    def web_ready(self):
        """The page's JS has connected. Push everything it should show."""

    def on_language_changed(self):
        """The language changed and the page has its new strings. For a
        page that caches text in Python it doesn't send as English."""

    def theme_vars(self, tokens):
        """Extra CSS variables (names without "--") for this page only."""
        return {}

    def on_theme_changed(self):
        self._push_theme()

    # --------------------------------------------------------------- API --

    def emit(self, name, payload=None):
        message = (name, json.dumps(payload))
        if sys.platform == "win32":
            crash_log.trail("emit",
                            f"{self._trail_label()} {name} {len(message[1])}B{'' if self._ready else ' (queued)'}")
        if name not in ("theme", "i18n"):
            self._last_emit = time.monotonic()      # busy lately: not let go (ask_to_let_go)
        if self._ready:
            self._bridge.event.emit(*message)
        else:
            if len(self._queue) >= MAX_QUEUED:
                del self._queue[0]
            self._queue.append(message)

    # ------------------------------------------------------ letting go --

    def ask_to_let_go(self, done, quiet_for, now=None):
        """Calls done(True) if this page may be let go (let_go()): out of
        sight, nothing sent to its view for `quiet_for` seconds, and its
        script holding nothing only it has (buddy.js keepAlive - text typed
        and still in a field, a game in progress, an unsaved edit). The
        script is asked, so done() may come a moment later."""
        now = time.monotonic() if now is None else now
        view = getattr(self, "view", None)
        if (view is None or view.isVisible() or not self._ready
                or now - getattr(self, "_last_emit", float("-inf")) < quiet_for):
            done(False)
            return
        view.page().runJavaScript(KEEP_ALIVE_JS, 0, lambda keep: done(keep is False))

    def let_go(self):
        """Lets Chromium discard this page while it's out of sight - its
        pictures and its script - to give their memory back
        (core/tool_memory.py). Shown again, it reloads and web_ready()
        redraws it from the state Python keeps, as on a first visit."""
        view = getattr(self, "view", None)
        if view is None or view.isVisible():
            return False
        page = view.page()
        if page.lifecycleState() != QWebEnginePage.LifecycleState.Active:
            return False
        if sys.platform == "win32":
            crash_log.trail("let go", self._trail_label())
        self._ready = False                      # events wait for it to be back
        self._queue = []
        page.setLifecycleState(QWebEnginePage.LifecycleState.Discarded)
        return True

    # ---------------------------------------------------------- plumbing --

    def _push_theme(self):
        host = self.host
        settings = getattr(host, "shared_settings", None) or {}
        tokens = host.theme_tokens()
        theme = web_theme(settings.get("theme", "Default"), settings.get("subtheme"), tokens)
        theme["vars"].update(self.theme_vars(tokens))
        theme["common"] = QUrl.fromLocalFile(WEB_COMMON_DIR + os.sep).toString()
        self.emit("theme", theme)

    def _push_i18n(self):
        language = get_i18n().language
        self.emit("i18n", {"language": language, "lang": LANGUAGE_CODES.get(language, "en"),
                           "strings": web_strings(language)})

    def _on_language_changed(self, _language=None):
        title = getattr(self, "_english_title", None)
        if title:
            self.setWindowTitle(tr(title))
        self._push_i18n()
        self.on_language_changed()

    def _trail_label(self):
        view = getattr(self, "view", None)
        return getattr(view.page(), "label", type(self).__name__) if view is not None else type(self).__name__

    def _on_load_started(self):
        if sys.platform == "win32":
            crash_log.trail("load", self._trail_label())
        self._ready = False
        self._queue = []

    def _js_ready(self):
        # Strings before anything is drawn, so nothing shows in English first.
        self._push_i18n()
        self._push_theme()
        self.web_ready()
        self._ready = True
        queued, self._queue = self._queue, []
        for message in queued:
            self._bridge.event.emit(*message)
        self._page_ready()

    def _page_ready(self):
        """The page has its theme and state (see _ReadyToShow)."""

    def _dispatch(self, name, data):
        if sys.platform == "win32":
            try:
                label = self._trail_label()     # reaches the view: gone, it raises
            except RuntimeError:
                return                          # sent just before its window closed: nothing left to do it in
            crash_log.trail("action", f"{label} {name}")
        else:
            view = getattr(self, "view", None)
            try:
                if view is not None:
                    view.page()                 # gone, it raises
            except RuntimeError:
                return                          # sent just before its window closed: nothing left to do it in
        handler = getattr(self, f"on_{name}", None)
        if handler is None or not name.isidentifier():
            print(f"[web] {type(self).__name__} has no handler for {name!r}", file=sys.stderr)
            return
        handler(data)

    def _on_renderer_died(self, _status, _code):
        if sys.platform == "win32":
            crash_log.trail("renderer", f"{self._trail_label()} died (status {_status}, exit {_code})")
        # The Chromium renderer is a separate process; if it dies the view
        # goes blank. Reloading brings it back, and web_ready() redraws
        # everything from the Python-side state.
        QTimer.singleShot(500, self.view.reload)

    def _edit_menu(self, pos):
        """Cut/copy/paste/select all - the browser's own menu without Back,
        Reload or View Source, which mean nothing inside Buddy."""
        menu = self.view.createStandardContextMenu()
        page = self.view.page()
        keep = {page.action(a) for a in (
            QWebEnginePage.Undo, QWebEnginePage.Redo, QWebEnginePage.Cut, QWebEnginePage.Copy,
            QWebEnginePage.Paste, QWebEnginePage.SelectAll, QWebEnginePage.CopyLinkToClipboard,
        )}
        for action in menu.actions():
            if action not in keep and not action.isSeparator():
                menu.removeAction(action)
        if any(not a.isSeparator() for a in menu.actions()):
            menu.exec(self.view.mapToGlobal(pos))
        menu.deleteLater()


class WebToolPage(WebSurface, ToolPage):
    """A tool page in the shell, drawn by pages/<tool>/web/index.html."""

    def build_ui(self):
        self._build_web()


class _ReadyToShow:
    """A web window appears only once its page has loaded and themed
    itself - "ready to show", as Electron calls it. Shown straight away, a
    new window is on screen before Chromium has drawn anything, and Windows
    fills it white for a frame or two: a jarring flash on a dark theme. The
    page loads while the window is hidden; show() and exec() wait for it,
    READY_TIMEOUT_MS at most, so a page that never connects still opens."""

    _show_pending = False
    _ready_loop = None

    def _page_ready(self):
        if self._ready_loop is not None:
            QTimer.singleShot(READY_SETTLE_MS, self._ready_loop.quit)
        if self._show_pending:
            QTimer.singleShot(READY_SETTLE_MS, self._show_now)

    def _wait_until_ready(self):
        """Blocks (running the event loop) until the page is ready."""
        if self._ready:
            return
        self._ready_loop = QEventLoop()
        QTimer.singleShot(READY_TIMEOUT_MS, self._ready_loop.quit)
        self._ready_loop.exec()
        self._ready_loop = None

    def show(self):
        # Only a window of its own waits; a view laid out inside the shell
        # (the header, the rail) shows with its parent as usual.
        if self.isWindow() and not self._ready:
            if not self._show_pending:
                self._show_pending = True
                QTimer.singleShot(READY_TIMEOUT_MS, self._show_now)
            return
        super().show()

    def _show_now(self):
        if self._show_pending:
            self._show_pending = False
            super().show()
            self.raise_()


class WebWindow(_ReadyToShow, WebSurface, QWidget):
    """A web page in a window of its own (no Qt parent, so it isn't
    minimized with Buddy). host: what gives it the theme - the shell."""

    transparent = False

    def __init__(self, host):
        super().__init__()
        self.host = host
        self._build_web()


class WebDialog(_ReadyToShow, WebSurface, QDialog):
    """A modal window drawn by a web page - exec() it, once. host gives the theme
    (the shell); parent is the window it sits over."""

    transparent = False

    def __init__(self, host, parent=None, title="", size=(480, 360)):
        super().__init__(parent)
        self.host = host
        self._english_title = title
        self.setWindowTitle(tr(title))
        self.resize(*size)
        self._build_web()

    def exec(self):
        """Shows it modally, once: the dialog deletes itself afterwards
        (read its results straight away). Otherwise every closed dialog -
        a child of its window - stays alive, hidden, with its web view, until
        that window goes."""
        self._wait_until_ready()
        result = super().exec()
        self.deleteLater()
        return result


def _theme_host(widget):
    """The shell (what gives a window its theme) from any widget in it."""
    node = widget
    while node is not None:
        if hasattr(node, "theme_tokens") and hasattr(node, "shared_settings"):
            return node
        node = getattr(node, "host", None) or node.parentWidget()
    return None
