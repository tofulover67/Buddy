#!/usr/bin/env python3
"""
Web: a light browser in Buddy (pages/web/browser.py has the rules,
pages/web/engine.py the engine).

The page is a bar drawn by Buddy (web/: tabs, back and forward, the
address, downloads, a menu) over the open tab's page. Pages are real
Chromium pages in the browser's own profile, so sites remember you; a tab
only gets a page once it's first shown, and a background tab sleeps after
a while (Settings > Tools > Web) unless it's playing sound, kept awake, or
on a site that never sleeps - so music plays on with the Web tab out of
sight, and a dozen idle tabs cost next to nothing. Buddy starts with every
tab asleep, the one it shows too (SleepingPanel stands in for its page):
a site that plays on its own - music, a video - waits until it's opened. Downloads go to a
folder of the user's choosing and can be dragged from the bar or the
Downloads window straight into Resolve's Media Pool.

Protocol (bar, web/):
    to the view    browser, focus_address, toast
    from the view  new_tab, close, select, move, go, back, forward, reload,
                   stop, mute, media, menu, more, downloads, drag_download, size

The Downloads button opens a window of its own (downloads_window.py): the
history of what's been downloaded, searchable and filterable, with each
file draggable into Resolve - where it lands in a Downloads bin of the
Media Pool (resolve_ext.py).

A private tab (Ctrl+Shift+N) is in a profile kept in memory only
(engine.private_profile): nothing of it is saved, remembered for the
address bar or kept once the last private tab closes.

Profiles (the menu's Profile, or the button with the profile's name once
there's more than one): one in use at a time, each signed in to its own
accounts (engine.py) with its own tabs, history and sites the address bar
remembers. Switching puts this one's tabs away and brings the other's
back. Settings, the Downloads list and its folder are shared.

History (the menu, Ctrl+H) opens a window of its own (history_window.py):
the pages visited in the profile in use (history_log.py), never a private
tab's.
"""

import base64
import copy
import json
import os
import shutil
import subprocess
import sys
import time

from PySide6.QtCore import QBuffer, QByteArray, QDataStream, QIODevice, QModelIndex, QPoint, QRect, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QCursor, QDesktopServices, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWebEngineCore import (QWebEngineContextMenuRequest, QWebEngineDownloadRequest, QWebEnginePage,
                                     QWebEngineScript)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (QApplication, QFileDialog, QLabel, QMenu, QPushButton, QStackedWidget, QVBoxLayout,
                               QWidget)

from core import recycle
from core import settings_form as sf
from core import link_bar
from core.i18n import LANGUAGE_CODES, get_i18n, tr
from core.link_bar_web import LinkDialog
from core.link_peek_web import start_file_drag
from core.message_dialog import alert, confirm, prompt
from core.settings_dialog import refresh_open
from core.web_theme import web_theme
from pages.base import ToolPage
from core import audio_sessions
from pages.web import browser, download_guard, downloads_log, ducking, engine, filter_lists, resolve_ext
from pages.web.bar import BrowserBar
from pages.web.downloads_window import DownloadsWindow
from pages.web.history_window import HistoryWindow
from pages.web import history_log, spelling
from pages.web.fullscreen_notice import FullScreenNotice
from pages.web.video_window import Videos

LEAVE_SETTLE_MS = 120   # leaving full screen: the tab's view gets this long to resize before its picture counts
LEAVE_WAIT_MS = 700     # ... and the black window goes after this at the most
LEAVE_POLL_MS = 30
COVER_MIN_MS = 40       # a tab's new page: Buddy's colour over it at least this long...
COVER_MAX_MS = 600      # ... until Chromium has drawn it, or this at the most


def _fill(widget, colour):
    """A widget that fills with `colour` before anything is drawn on it -
    a new view showed white for a frame (closing a tab onto one never
    opened lit Buddy's whole window up)."""
    widget.setStyleSheet(f"background: {colour.name()};")
    widget.setAttribute(Qt.WA_StyledBackground, True)
    widget.setAutoFillBackground(True)
    palette = widget.palette()
    palette.setColor(widget.backgroundRole(), colour)
    widget.setPalette(palette)


def _black(widget):
    _fill(widget, QColor(Qt.black))


def _drawn(view):
    """Whether Chromium has drawn the view at its size: a picture that
    isn't one flat colour (core/shell_window.py's test)."""
    try:
        from core.shell_window import _has_picture
        return _has_picture(view)
    except Exception:
        return True

SETTINGS_ID = "buddy_web"
CHECK_SLEEP_MS = 20_000
# The ad blocker's lists are read (a second or more of Python) once the tool is first shown, or this
# long after Buddy starts - not while it starts, slowing it; the built-in tracker list blocks till then.
LISTS_AFTER_MS = 15_000
MEMORY_MS = 4_000
SAVE_DELAY_MS = 1_500
VISITS_SAVE_MS = 5_000        # the history file, written this long after a change
DROP_PROFILE_MS = 1_000       # a deleted profile goes once its pages have
# History and Downloads as browsers have them; on a Mac Qt's Ctrl is Cmd,
# and Cmd+H hides the app there - Safari and Chrome use Cmd+Y.
HISTORY_KEY = "Ctrl+Y" if sys.platform == "darwin" else "Ctrl+H"
DOWNLOADS_KEY = "Ctrl+Alt+L" if sys.platform == "darwin" else "Ctrl+J"
CLEAR_KEY = "Ctrl+Shift+Backspace" if sys.platform == "darwin" else "Ctrl+Shift+Del"
POP_OUT_SIZE = (480, 270)     # a video popped out into its own window starts this big...
POP_OUT_MARGIN = 24           # ... this far in from the screen's bottom-right corner
POP_OUT_SETTLE_MS = 600       # its page gets this long in the window before its video fills it
ZOOMS = (0.5, 0.67, 0.75, 0.8, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0)
TABS_KEYWORD = "@tabs"         # typed first in the address bar: only the open tabs are suggested
SUGGESTIONS = 7                # rows under the address bar at the most
FILE_TRIES = 14                # a file dragged onto Resolve is looked for in the Media Pool this many times...
FILE_EVERY_MS = 1_500          # ...this far apart
# Chromium says a tab is audible a moment late and stays so a moment
# after it stops: sound reported this soon after Buddy paused it is the
# old sound, not the page playing again.
PAUSE_SETTLE_S = 2.0
DROP_PRIVATE_MS = 1_000       # after the last private tab's page is gone
CLOSE_WAIT_MS = 2_000         # a page asked to close that hasn't answered by now (hung) is closed anyway

# The tab's pause and play: every playing video and audio in the page (and
# its frames), and back on with the ones it paused - or, after a reload,
# the one that was part-way through. Run in Buddy's own script world, so
# the page can't see or change it.
PAUSE_JS = """(() => {
    const playing = [...document.querySelectorAll('video, audio')].filter(m => !m.paused && !m.ended);
    playing.forEach(m => m.pause());
    window.__buddyPaused = playing;
    return playing.length;
})()"""
PLAY_JS = """(() => {
    let media = (window.__buddyPaused || []).filter(m => m.isConnected);
    if (!media.length) {
        media = [...document.querySelectorAll('video, audio')]
            .filter(m => m.paused && m.currentTime > 0 && !m.ended).slice(0, 1);
    }
    media.forEach(m => m.play().catch(() => {}));
    window.__buddyPaused = [];
    return media.length;
})()"""


def _refresh(ui):
    try:
        ui.refresh()
    except RuntimeError:                       # its window has gone
        pass


def history_to_text(page):
    """The page's back and forward list as text to save ("" if too big)."""
    try:
        data = QByteArray()
        stream = QDataStream(data, QIODevice.WriteOnly)
        stream << page.history()
        text = base64.b64encode(bytes(data)).decode("ascii")
    except Exception:  # noqa: BLE001 - a Qt without history streaming
        return ""
    return text if len(text) <= browser.MAX_TAB_HISTORY else ""


def history_from_text(page, text):
    """Puts a saved back and forward list into the page, which then loads
    the page it was on. True if it took."""
    try:
        data = QByteArray(base64.b64decode(text))
        stream = QDataStream(data, QIODevice.ReadOnly)
        stream >> page.history()
        return page.history().count() > 0
    except Exception:  # noqa: BLE001 - damaged, or from another Qt
        return False


# Text typed on a page and not sent (a form, a reply box being written in):
# such a tab isn't put to sleep, which would lose it. Site searches don't count.
UNSENT_JS = """(() => {
    const SKIP = new Set(["hidden", "checkbox", "radio", "submit", "button", "reset", "image", "file", "range",
                          "color", "search"]);
    for (const f of document.querySelectorAll("input, textarea")) {
        if (f.tagName === "INPUT" && SKIP.has((f.type || "").toLowerCase())) continue;
        if (f.disabled || f.readOnly || f.getAttribute("role") === "combobox") continue;
        if (f.closest('[role="search"]') || /search|query|^q$/i.test(f.name || f.id || "")) continue;
        if (f.value.trim() && f.value !== f.defaultValue) return true;
    }
    const a = document.activeElement;
    return !!(a && a.isContentEditable && (a.innerText || "").trim());
})()"""

# Capture permissions that put a mark on the tab while it has them.
CAPTURE_KINDS = ("mic", "camera", "camera_mic", "screen", "screen_audio")


class Tab:
    """One tab: what it shows, and its page once it has one."""

    def __init__(self, browser_page, tab_id, url="", title="", awake=False, private=False, pinned=False, history=""):
        self.browser = browser_page
        self.private = private
        self.pinned = pinned
        self.saved_history = history          # its back and forward list, until its page is made
        self.paused = False                   # its sound paused from its tab (Buddy's button)
        self.paused_at = 0.0
        self.id = tab_id
        self.url = url                       # where its page is - what's saved for next time
        # An address typed in, shown in the bar until the page goes there -
        # or stays, if it asks before it's left (TabPage.javaScriptConfirm).
        self.typed = ""
        self.close_asked = 0.0               # time.monotonic() its page was asked to close
        self.title = title
        self.icon = ""
        self.awake = awake
        self.seen = time.time()
        self.loading = False
        self.progress = 0
        self.crashed = False                 # its page's process died: a note and Reload stand in
        self.unsent = False                  # text typed in it that hasn't been sent (it won't sleep)
        self.capturing = set()               # camera, microphone, screen allowed to it (CAPTURE_KINDS)
        self._zoom_site = None               # the site whose zoom its view is at
        self.web = None                     # made the first time it's shown
        self.page = None

    @property
    def asleep(self):
        return self.web is None or self.page.lifecycleState() == QWebEnginePage.LifecycleState.Discarded

    def make_view(self, load=True):
        self.web = QWebEngineView()
        _fill(self.web, self.browser.ground_color())      # Buddy's own colour until the page has drawn
        self.page = engine.TabPage(self, self.web)
        self.web.setPage(self.page)
        self.web.setContextMenuPolicy(Qt.CustomContextMenu)
        b = self.browser
        self.web.customContextMenuRequested.connect(lambda pos: b.page_menu(self, pos))
        self.page.titleChanged.connect(lambda t: (self._set(title=t), b.titled(self)))
        self.page.urlChanged.connect(self._went)
        self.page.iconChanged.connect(self._icon)
        self.page.loadStarted.connect(self._started)
        self.page.loadProgress.connect(lambda p: self._set(progress=p))
        self.page.loadFinished.connect(lambda ok: (self._set(loading=False, progress=100, typed=""), self._ground(),
                                                   ok and b.visited(self)))
        # Its page agreed to close (asked by request_close), or closed itself
        # (window.close() in a window a script opened).
        self.page.windowCloseRequested.connect(lambda: b.close_tab(self))
        self.page.recentlyAudibleChanged.connect(self._audible)
        self.page.audioMutedChanged.connect(lambda _m: b.changed())
        self.page.lifecycleStateChanged.connect(lambda _s: b.changed())
        self.page.fullScreenRequested.connect(lambda request: b.full_screen(self, request))
        self.page.renderProcessPidChanged.connect(lambda _p: b.changed())
        self.page.renderProcessTerminated.connect(self._died)
        self.page.findTextFinished.connect(lambda result: b.found(self, result))
        self.page.pdfPrintingFinished.connect(lambda path, ok: b.pdf_saved(self, path, ok))
        self._ground(loading=True)            # a page is on its way: no white before it
        b.stack.addWidget(self.web)
        if load:
            history, self.saved_history = self.saved_history, ""
            if not (history and history_from_text(self.page, history)):
                self.load()

    def _went(self, url):
        # Put to sleep, Chromium says the page's address is empty: the tab
        # still belongs where it was (it wakes there, and is saved there).
        if not self._shown_url(url) and self.page is not None \
                and self.page.lifecycleState() == QWebEnginePage.LifecycleState.Discarded:
            return
        old = browser.site_of(self.url)
        self._set(url=self._shown_url(url), typed="")
        site = browser.site_of(self.url)
        if site != old:
            self.capturing = set()            # what a site was allowed is its own
        if site != self._zoom_site:           # its first page too: the site's own zoom
            self._zoom_site = site
            self.browser.site_zoom(self)
        self._ground()
        self.browser.went(self)

    def _started(self):
        if self.crashed:                      # going again after a crash (Back, a link): the page is back
            self.crashed = False
            self.browser.reload_crashed_shown(self)
        self._set(loading=True, progress=0, paused=False)
        self._ground()

    def _died(self, status, _code):
        """Its page's process ended. Asleep, that's Buddy's doing; otherwise
        it crashed (or was killed): its tab says so, with Reload."""
        if status == QWebEnginePage.RenderProcessTerminationStatus.NormalTerminationStatus or self.asleep:
            return
        self.crashed = True
        self.loading = False
        self.capturing = set()
        self.browser.tab_crashed(self)

    def _ground(self, loading=None):
        """What shows before a page has painted - and behind one that sets no
        background. While a page loads (opened, reloaded, woken from sleep)
        it's the theme's own colour, so a dark theme isn't lit up white
        between pages; so is the new-tab page, drawn in the theme. Once a
        site has loaded it gets the white every browser gives it, or a page
        with no background of its own would be dark text on dark - near
        black when it's darkened (Settings > Web > Dark pages)."""
        if self.page is not None:
            loading = self.loading if loading is None else loading
            themed = not self.url or loading
            if self.browser._is_full(self):
                colour = QColor(Qt.black)     # full screen: black while it grows and shrinks, as browsers do
            elif themed:
                colour = self.browser.ground_color()
            else:
                colour = QColor("#121212") if self.browser.dark_for(self.url) else QColor(Qt.white)
            self.page.setBackgroundColor(colour)

    def load(self):
        if self.url:
            self.page.load(QUrl(self.url))
        else:
            self.page.setHtml(self.browser.start_html(self.private), QUrl("about:blank"))

    def saved(self):
        """What's kept of it between runs (browser.clean_tabs)."""
        history = self.saved_history
        if self.page is not None and self.url:
            history = history_to_text(self.page)
        return {"url": self.url, "title": self.title, "awake": self.awake, "pinned": self.pinned, "history": history}

    @staticmethod
    def _shown_url(url):
        text = url.toString()
        return "" if text in ("about:blank", "") else text

    def _audible(self, audible):
        if audible and time.time() - self.paused_at > PAUSE_SETTLE_S:
            self.paused = False               # played again, from the page itself
        self.browser.changed()

    def _set(self, **values):
        for key, value in values.items():
            setattr(self, key, value)
        self.browser.changed(save="url" in values or "title" in values)

    def _icon(self, icon):
        pixmap = icon.pixmap(32, 32)
        if pixmap.isNull():
            self.icon = ""
        else:
            data = QByteArray()
            buffer = QBuffer(data)
            buffer.open(QIODevice.WriteOnly)
            pixmap.save(buffer, "PNG")
            self.icon = "data:image/png;base64," + base64.b64encode(bytes(data)).decode("ascii")
        self.browser.changed()

    def state(self, minutes, never):
        page = self.page
        audible = bool(page and page.recentlyAudible())
        info = {"url": self.url, "visible": bool(self.web and self.web.isVisible()), "audible": audible,
                "awake": self.awake, "asleep": self.asleep, "seen": self.seen, "unsent": self.unsent}
        capture = ("screen" if self.capturing & {"screen", "screen_audio"}
                   else "camera" if self.capturing & {"camera", "camera_mic"}
                   else "mic" if self.capturing else "")
        return {
            "id": self.id, "url": self.url, "private": self.private, "pinned": self.pinned,
            "title": self.title or browser.site_of(self.url) or tr("Private tab" if self.private else "New tab"),
            "paused": self.paused and not self.asleep,
            "icon": self.icon, "loading": self.loading, "progress": self.progress, "audible": audible,
            "muted": bool(page and page.isAudioMuted()), "asleep": self.asleep and bool(self.url),
            "awake": self.awake, "never": browser.on_site(self.url, never), "crashed": self.crashed,
            "capture": capture, "why": browser.why_awake(info, minutes, never),
        }


class SleepingPanel(QWidget):
    """Where the shown tab's page goes while it has none to show: asleep
    since Buddy started (its name, why, and a button that loads it), its
    page crashed (Reload), or playing in its own window (Bring it back)."""

    def __init__(self, browser_page):
        super().__init__()
        self.browser = browser_page
        self._run = browser_page.wake_active
        self.setObjectName("webSleeping")
        self.setAttribute(Qt.WA_StyledBackground, True)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(10)
        self.title = QLabel(objectName="webSleepingTitle", alignment=Qt.AlignCenter, wordWrap=True)
        self.site = QLabel(objectName="webSleepingSite", alignment=Qt.AlignCenter, wordWrap=True)
        self.note = QLabel(objectName="webSleepingNote", alignment=Qt.AlignCenter, wordWrap=True)
        self.button = QPushButton(objectName="webSleepingWake", cursor=Qt.PointingHandCursor)
        self.button.clicked.connect(lambda: self._run())
        for widget in (self.title, self.site, self.note):
            widget.setMaximumWidth(520)
            widget.setTextInteractionFlags(Qt.NoTextInteraction)
            layout.addWidget(widget, 0, Qt.AlignHCenter)
        layout.addSpacing(6)
        layout.addWidget(self.button, 0, Qt.AlignHCenter)
        self.on_theme_changed()

    def show_tab(self, tab, note=None, button=None, run=None):
        self.title.setText(tab.title or browser.site_of(tab.url) or tab.url)
        self.site.setText(browser.site_of(tab.url) or tab.url)
        self.site.setVisible(bool(tab.title))
        self.note.setText(note or tr("This tab is asleep. Buddy starts with its tabs asleep, so nothing "
                                     "plays until you open one."))
        self.button.setText(button or tr("Open the page"))
        self._run = run or self.browser.wake_active

    def on_theme_changed(self):
        try:
            t = self.browser.host.theme_tokens()
        except Exception:  # noqa: BLE001 - a host without a theme
            return
        self.setStyleSheet(f"""
            #webSleeping {{ background: {t["surface"]}; }}
            #webSleepingTitle {{ color: {t["on_surface"]}; font-size: 15px; font-weight: 700; }}
            #webSleepingSite, #webSleepingNote {{ color: {t["on_surface"]}; font-size: 12px; }}
            #webSleepingWake {{ background: {t["primary"]}; color: {t["on_primary"]}; border: none;
                                border-radius: 14px; padding: 7px 18px; font-weight: 600; }}
            #webSleepingWake:hover {{ background: {t["primary"]}; }}
        """)


class SuggestList(QWidget):
    """The address bar's suggestions, under it and over the page: open tabs
    to switch to and pages from History (page.py on_suggest). Drawn by
    Buddy, in the theme; it never takes the focus - typing stays in the
    address, whose arrow keys move through it (on_suggest_move)."""

    ROW = 46
    PAD = 6

    def __init__(self, browser_page):
        super().__init__(browser_page)
        self.browser = browser_page
        self.items = []                       # {"kind": "tab" / "page", "title", "url", "id"}
        self.index = -1                       # the one picked with the arrow keys
        self.hover = -1
        self.setFocusPolicy(Qt.NoFocus)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_NoMousePropagation, True)
        self.hide()

    def show_items(self, items, anchor):
        """`anchor`: the address field's rect, in the browser page's coordinates."""
        self.items, self.index, self.hover = items, -1, -1
        if not items:
            self.hide()
            return
        width = max(anchor.width(), 360)
        self.setGeometry(anchor.left(), anchor.bottom() + 4, width, self.PAD * 2 + self.ROW * len(items))
        self.raise_()
        self.show()
        self.update()

    def move_pick(self, step):
        if not self.items or not self.isVisible():
            return None
        # -1 is what was typed: down from it is the first, up from it the last.
        self.index = (self.index + 1 + step) % (len(self.items) + 1) - 1
        self.update()
        return self.items[self.index] if self.index >= 0 else None

    def picked(self):
        return self.items[self.index] if self.isVisible() and 0 <= self.index < len(self.items) else None

    def _row_at(self, y):
        row = int((y - self.PAD) // self.ROW)
        return row if 0 <= row < len(self.items) else -1

    def mouseMoveEvent(self, event):
        row = self._row_at(event.position().y())
        if row != self.hover:
            self.hover = row
            self.update()

    def leaveEvent(self, _event):
        self.hover = -1
        self.update()

    def mousePressEvent(self, event):
        row = self._row_at(event.position().y())
        if row >= 0 and event.button() in (Qt.LeftButton, Qt.MiddleButton):
            self.browser.use_suggestion(self.items[row], background=event.button() == Qt.MiddleButton)

    def paintEvent(self, _event):
        try:
            t = self.browser.host.theme_tokens()
        except Exception:  # noqa: BLE001 - a host without a theme
            t = {}
        bg = QColor(t.get("surface_container", "#2a2a2e"))
        text = QColor(t.get("on_surface", "#e8e8e8"))
        dim = QColor(t.get("outline", "#9a9a9a"))
        accent = QColor(t.get("primary", "#4c8bf5"))
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(t.get("outline_variant", "#444")), 1))
        p.setBrush(bg)
        p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 10, 10)
        font = p.font()
        for i, item in enumerate(self.items):
            row = QRect(self.PAD, self.PAD + i * self.ROW, self.width() - self.PAD * 2, self.ROW)
            if i in (self.index, self.hover):
                shade = QColor(accent)
                shade.setAlpha(70 if i == self.index else 36)
                p.setPen(Qt.NoPen)
                p.setBrush(shade)
                p.drawRoundedRect(row, 7, 7)
            tag = tr("Switch to tab") if item["kind"] == "tab" else ""
            tag_width = 0
            if tag:
                small = p.font()
                small.setPointSizeF(font.pointSizeF() * 0.85)
                p.setFont(small)
                tag_width = p.fontMetrics().horizontalAdvance(tag) + 16
                p.setPen(accent)
                p.drawText(row.adjusted(0, 0, -10, 0), Qt.AlignRight | Qt.AlignVCenter, tag)
            inner = row.adjusted(12, 5, -12 - tag_width, -5)
            bold = p.font()
            bold.setPointSizeF(font.pointSizeF())
            bold.setBold(True)
            p.setFont(bold)
            p.setPen(text)
            metrics = p.fontMetrics()
            title = item["title"] or browser.site_of(item["url"]) or item["url"]
            p.drawText(inner.adjusted(0, 0, 0, -inner.height() // 2), Qt.AlignLeft | Qt.AlignVCenter,
                       metrics.elidedText(title, Qt.ElideRight, inner.width()))
            p.setFont(font)
            p.setPen(dim)
            p.drawText(inner.adjusted(0, inner.height() // 2, 0, 0), Qt.AlignLeft | Qt.AlignVCenter,
                       p.fontMetrics().elidedText(item["url"], Qt.ElideMiddle, inner.width()))
        p.end()


class PopOutWindow(QWidget):
    """A tab's video in a small window of its own, on top of every other
    (page.py pop_out) - closing it brings the page back to its tab."""

    def __init__(self, browser_page, tab):
        super().__init__(None, Qt.Window | Qt.WindowStaysOnTopHint)
        self.browser = browser_page
        self.done = False                     # bring_back is closing it: nothing more to do
        _black(self)
        self.setWindowTitle(tr("{title} - Buddy").replace("{title}", tab.title or browser.site_of(tab.url)))
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        self.setMinimumSize(200, 120)
        esc = QShortcut(QKeySequence("Esc"), self)
        esc.activated.connect(lambda: tab.page is not None and tab.page.triggerAction(QWebEnginePage.ExitFullScreen))

    def closeEvent(self, event):
        if not self.done:
            QTimer.singleShot(0, self.browser.bring_back)
        super().closeEvent(event)


class WebBrowserPage(ToolPage):
    tool_id = "web"
    display_name = "Web"
    category = "Web"

    # ------------------------------------------------------------ build --
    def build_ui(self):
        self.settings = self.host.tool_settings(SETTINGS_ID, copy.deepcopy(browser.DEFAULTS))
        self.tabs = []
        self.active = None
        self._next_id = 1
        # Every download, oldest first (downloads_log), and the requests of those still going.
        self.history = downloads_log.clean_history(self.settings.get("download_history"))
        self._active = {}                     # record id -> QWebEngineDownloadRequest
        self._serial = 0
        self._panel = None                    # the Downloads window, while it's open
        self.videos = Videos(self.host)       # Buddy's player, for videos the page can't decode
        self._filing = {}                     # path -> record: dragged onto Resolve, to move into Downloads
        self._burst = download_guard.BurstGuard()
        self._filing_tries = 0
        self._full = None                     # (tab, window, view) while a page is full screen
        self.memory = None
        self.profiles = browser.clean_profiles(self.settings.get("profiles"))
        ids = [prof["id"] for prof in self.profiles]
        current = self.settings.get("profile")
        self.profile_id = current if current in ids else ids[0]
        self.settings["profile_state"] = browser.clean_profile_state(self.settings.get("profile_state"), ids)
        engine.use_profile(self.profile_id)
        self.visits = history_log.History(history_log.path_for(self.profile_id))
        self._history_panel = None            # the History window, while it's open
        self._clear_panel = None              # the Clear browsing data window, while it's open
        self._popped = None                   # (tab, window, view) while a tab plays in its own window
        self._printing = None                 # the printer while a page prints
        self._to_resolve = set()              # addresses being downloaded to go into Resolve
        self._find = {"text": "", "case": False}
        self._keys = self.key_names()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.bar = BrowserBar(self)
        self.bar.setFixedHeight(76)
        self.stack = QStackedWidget(self)
        _fill(self.stack, self.ground_color())
        self.blank = QWidget()                # behind a tab that has no page yet
        _fill(self.blank, self.ground_color())
        # Over a tab's page until Chromium has drawn it (_cover_until_drawn).
        self.cover = QWidget(self)
        self.cover.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        _fill(self.cover, self.ground_color())
        self.cover.hide()
        self.stack.addWidget(self.blank)
        self.sleeping = SleepingPanel(self)   # the shown tab, until it's opened after a start
        self.stack.addWidget(self.sleeping)
        layout.addWidget(self.bar)
        layout.addWidget(self.stack, 1)
        self.suggest = SuggestList(self)      # the address bar's suggestions, over the page

        engine.on_profile_made(lambda made: made.downloadRequested.connect(self.download_requested))
        engine.on_notification(self.notified)
        self._apply_dark()
        engine.set_spelling(self.spelling())
        self.lists = filter_lists.FilterLists(self)
        self.lists.ready.connect(self._lists_ready)
        self.lists.changed.connect(self._lists_changed)
        self._lists_wanted = False
        self._apply_blocking()
        QTimer.singleShot(LISTS_AFTER_MS, self, self._start_lists)
        engine.set_video_quality(self.settings.get("video_quality"))
        engine.set_youtube_ads(self.settings.get("youtube_ads", False))
        self._apply_region()
        get_i18n().language_changed.connect(self._apply_region)
        self._badge = None
        self._ducked = False
        self.ducker = ducking.Ducker(self, restore=self.settings.get("duck_restore"))
        self.ducker.ducked.connect(self._on_ducked)
        self.ducker.saved.connect(self._duck_saved)
        self._apply_ducking()
        self.ducker.start()

        self._save_timer = QTimer(self, singleShot=True, interval=SAVE_DELAY_MS, timeout=self._save)
        # The history in use when it fires: switching profiles swaps self.visits.
        self._visits_timer = QTimer(self, singleShot=True, interval=VISITS_SAVE_MS)
        self._visits_timer.timeout.connect(lambda: self.visits.save())
        self._push_timer = QTimer(self, singleShot=True, interval=60, timeout=self.push)
        self._sleep_timer = QTimer(self, interval=CHECK_SLEEP_MS, timeout=self.check_sleep)
        self._sleep_timer.start()
        self._memory_timer = QTimer(self, interval=MEMORY_MS, timeout=self._measure)
        self._memory_timer.start()
        self._shortcuts()

        for saved in browser.clean_tabs(self.settings.get("tabs")):
            self._add(saved["url"], saved["title"], saved["awake"], pinned=saved["pinned"], history=saved["history"])
        if not self.tabs:
            self._add("")
        active = self.settings.get("active", 0)
        self._start_on(self.tabs[active if isinstance(active, int) and 0 <= active < len(self.tabs) else 0])

    def _shortcuts(self):
        keys = {
            "Ctrl+T": lambda: self.on_new_tab(), "Ctrl+N": lambda: self.on_new_tab(),
            "Ctrl+Shift+N": lambda: self.new_private_tab(),
            "Ctrl+W": lambda: self.active is not None and self.on_close({"id": self.active.id}),
            "Ctrl+L": self.focus_address, "F6": self.focus_address,
            "F5": self.on_reload, "Ctrl+R": self.on_reload,
            "Ctrl+=": lambda: self.zoom(1), "Ctrl++": lambda: self.zoom(1), "Ctrl+-": lambda: self.zoom(-1),
            "Ctrl+0": lambda: self.zoom(0), "Ctrl+Shift+T": self.reopen_closed,
            HISTORY_KEY: self.show_history, DOWNLOADS_KEY: self.show_downloads,
            "Ctrl+F": self.open_find, "Ctrl+P": lambda: self.active is not None and self.print_page(self.active),
            "Ctrl+Shift+A": self.search_tabs, CLEAR_KEY: self.show_clear_data,
            "Ctrl+9": lambda: self.tabs and self.select(self.tabs[-1]),
        }
        for n in range(1, 9):
            keys[f"Ctrl+{n}"] = lambda n=n: len(self.tabs) >= n and self.select(self.tabs[n - 1])
        # Qt's Ctrl is Cmd on a Mac, and Cmd+Tab is the Mac's own app switcher:
        # tabs go by Control+Tab and Cmd+Option+arrows there, back and
        # forward by Cmd+[ and Cmd+] (Option+arrows move by words in text).
        if sys.platform == "darwin":
            keys.update({"Meta+Tab": lambda: self._cycle(1), "Meta+Shift+Tab": lambda: self._cycle(-1),
                         "Ctrl+Alt+Right": lambda: self._cycle(1), "Ctrl+Alt+Left": lambda: self._cycle(-1),
                         "Ctrl+[": self.on_back, "Ctrl+]": self.on_forward,
                         "Ctrl+G": lambda: self.find_step(False), "Ctrl+Shift+G": lambda: self.find_step(True)})
        else:
            keys.update({"Ctrl+Tab": lambda: self._cycle(1), "Ctrl+Shift+Tab": lambda: self._cycle(-1),
                         "Ctrl+PgDown": lambda: self._cycle(1), "Ctrl+PgUp": lambda: self._cycle(-1),
                         "Alt+Left": self.on_back, "Alt+Right": self.on_forward, "Alt+D": self.focus_address,
                         "F3": lambda: self.find_step(False), "Shift+F3": lambda: self.find_step(True)})
        for key, run in keys.items():
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            shortcut.activated.connect(run)
        self._closed = []                     # (url, title) of closed tabs, newest last

    @staticmethod
    def key_names():
        """The bar's tooltips' keys, as this platform writes them (Cmd+T on a Mac)."""
        native = lambda key: QKeySequence(key).toString(QKeySequence.NativeText)   # noqa: E731
        mac = sys.platform == "darwin"
        return {"new": native("Ctrl+T"), "private": native("Ctrl+Shift+N"), "close": native("Ctrl+W"),
                "back": native("Ctrl+[" if mac else "Alt+Left"), "forward": native("Ctrl+]" if mac else "Alt+Right"),
                "reload": native("Ctrl+R" if mac else "F5"), "zoom": native("Ctrl+0"), "find": native("Ctrl+F"),
                "find_next": native("Ctrl+G" if mac else "F3"), "downloads": native(DOWNLOADS_KEY)}

    # ------------------------------------------------------------- tabs --
    def _add(self, url="", title="", awake=False, private=False, pinned=False, history=""):
        """A new tab always joins the row at its right-hand end - never
        beside the active one, which could put it among the others. A
        pinned one joins the pinned ones, at the left."""
        tab = Tab(self, self._next_id, url, title, awake, private, pinned, history)
        self._next_id += 1
        if pinned:
            self.tabs.insert(sum(1 for t in self.tabs if t.pinned), tab)
        else:
            self.tabs.append(tab)
        return tab

    def _cover_until_drawn(self, tab):
        """Buddy's own colour over the page area until the tab's page has
        drawn: Chromium's view shows white for a frame before its first
        picture - closing a tab onto one not opened yet lit the window up."""
        self.cover.setGeometry(self.stack.geometry())
        self.cover.raise_()
        self.cover.show()
        started = time.monotonic()

        def check():
            waited = (time.monotonic() - started) * 1000
            try:
                if tab is not self.active or tab.web is None or waited >= COVER_MAX_MS \
                        or (waited >= COVER_MIN_MS and _drawn(tab.web)):
                    self.cover.hide()
                else:
                    QTimer.singleShot(LEAVE_POLL_MS, check)
            except RuntimeError:              # the browser or the tab's view has gone meanwhile
                pass
        QTimer.singleShot(LEAVE_POLL_MS, check)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.cover.isVisible():
            self.cover.setGeometry(self.stack.geometry())

    def open_tab(self, url="", show=True, private=False):
        tab = self._add(url, private=private)
        if show:
            self.select(tab)
        else:
            tab.make_view()
        self.changed(save=True)
        return tab

    def open_tab_for_page(self, opener, background=False):
        """A page opened a new window (a link to a new tab, a sign-in
        pop-up): it becomes a tab at the end of the row, with a page the
        site fills in itself - shown, unless it was a middle-click or
        Ctrl+click on a link, which opens it behind (as browsers do)."""
        tab = self._add("", private=opener.private)     # a private page's stay private
        tab.make_view(load=False)
        if not background:
            self.select(tab)
        self.changed(save=True)
        return tab

    def select(self, tab):
        if tab not in self.tabs:
            return
        if self.active is not None and self.active is not tab:
            self.active.seen = time.time()
            self._stop_find(self.active)
        self.active = tab
        self.suggest.hide()
        if self._is_popped(tab):
            self.sleeping.show_tab(tab, tr("This tab is playing in its own window."), tr("Bring it back"),
                                   self.bring_back)
            self.stack.setCurrentWidget(self.sleeping)
            self.changed(save=True)
            return
        fresh = tab.asleep                    # a page made now, or woken: nothing drawn yet
        if tab.web is None:
            tab.make_view()
        elif tab.page.lifecycleState() != QWebEnginePage.LifecycleState.Active:
            tab.page.setLifecycleState(QWebEnginePage.LifecycleState.Active)
        if tab.crashed:
            self._show_crashed(tab)
        else:
            if fresh and tab.url:
                self._cover_until_drawn(tab)
            self.stack.setCurrentWidget(tab.web)
        tab.seen = time.time()
        if not tab.url:
            self.focus_address()
        self.changed(save=True)

    # ----------------------------------------------------------- crashed --
    def tab_crashed(self, tab):
        if tab is self.active and not self._is_popped(tab):
            self._show_crashed(tab)
        self.changed()

    def _show_crashed(self, tab):
        self.sleeping.show_tab(tab, tr("This page stopped working - it may have run out of memory, or crashed. "
                                       "Other tabs on the same site stop with it."),
                               tr("Reload"), lambda: self.reload_crashed(tab))
        self.stack.setCurrentWidget(self.sleeping)

    def reload_crashed(self, tab):
        if tab not in self.tabs or tab.page is None:
            return
        tab.crashed = False
        self.reload_crashed_shown(tab)
        tab.page.triggerAction(QWebEnginePage.WebAction.Reload)

    def reload_crashed_shown(self, tab):
        """A crashed tab loading again: its page in place of the note."""
        if tab is self.active and not self._is_popped(tab) and self.stack.currentWidget() is self.sleeping:
            self._cover_until_drawn(tab)
            self.stack.setCurrentWidget(tab.web)
        self.changed()

    def _start_on(self, tab):
        """Buddy starting: the tab it was on is shown but left asleep - its
        site isn't loaded, so nothing it plays starts with Buddy. A new-tab
        page (no site) is drawn as usual."""
        if not tab.url:
            self.select(tab)
            return
        self.active = tab
        self.sleeping.show_tab(tab)
        self.stack.setCurrentWidget(self.sleeping)
        self.changed()

    def wake_active(self):
        """The asleep tab's Open button: its page loads."""
        if self.active is not None:
            self.select(self.active)

    def request_close(self, tab):
        """Closing a tab as the user does: its page is asked first, so a site
        with something unsaved can say so (Leave / Stay) - the tab goes when
        it agrees (windowCloseRequested, close_tab). A page that never
        answers - hung - is closed anyway after CLOSE_WAIT_MS."""
        if tab not in self.tabs:
            return
        if tab.page is None or tab.asleep:
            return self.close_tab(tab)
        if time.monotonic() - tab.close_asked < CLOSE_WAIT_MS / 1000:
            return                            # already asked: not a second question
        tab.close_asked = time.monotonic()
        tab.page.triggerAction(QWebEnginePage.WebAction.RequestClose)
        QTimer.singleShot(CLOSE_WAIT_MS, lambda: self._close_unanswered(tab))

    def _close_unanswered(self, tab):
        if tab not in self.tabs or not tab.close_asked:
            return                            # closed, or the user chose to stay
        if tab.page is not None and tab.page.asking:
            QTimer.singleShot(CLOSE_WAIT_MS, lambda: self._close_unanswered(tab))     # the question's still up
            return
        self.close_tab(tab)

    def stayed(self, tab):
        """The user chose Stay when a page asked before it was left: the bar
        goes back to where the page still is, and a close waits for another
        go."""
        tab.close_asked = 0.0
        if tab.typed:
            tab._set(typed="")

    def close_tab(self, tab):
        """Takes the tab away now (request_close asks its page first)."""
        if tab not in self.tabs:
            return
        if tab.url and not tab.private:
            self._closed = (self._closed + [(tab.url, tab.title)])[-20:]
        if self._full is not None and self._full[0] is tab:
            self._leave_full(at_once=True)    # its page back home before it goes
        if self._is_popped(tab):
            self.bring_back(at_once=True)
        self._stop_find(tab)
        index = self.tabs.index(tab)
        self.tabs.remove(tab)
        if tab.web is not None:
            self.stack.removeWidget(tab.web)
            tab.web.deleteLater()
        if not self.tabs:
            self._add("")
        if tab is self.active:
            self.active = None
            self.select(self.tabs[min(index, len(self.tabs) - 1)])
        if tab.private and not any(t.private for t in self.tabs):
            QTimer.singleShot(DROP_PRIVATE_MS, self._drop_private)
        self.changed(save=True)

    def new_private_tab(self, url=""):
        self.open_tab(url, private=True)

    def _drop_private(self):
        """The last private tab closed: all it knew forgotten."""
        if not any(t.private for t in self.tabs):
            engine.drop_private()

    def reopen_closed(self):
        if self._closed:
            url, _title = self._closed.pop()
            self.open_tab(url)

    def _cycle(self, step):
        if self.tabs:
            at = self.tabs.index(self.active) if self.active in self.tabs else 0
            self.select(self.tabs[(at + step) % len(self.tabs)])

    def tab(self, payload):
        wanted = (payload or {}).get("id")
        return next((t for t in self.tabs if t.id == wanted), None)

    # ------------------------------------------------------------ sleep --
    def never_sleep(self):
        return browser.clean_sites(self.settings.get("never_sleep"))

    def sleep_minutes(self):
        value = self.settings.get("sleep_after", browser.DEFAULT_SLEEP)
        return value if value in browser.SLEEP_CHOICES else browser.DEFAULT_SLEEP

    def check_sleep(self):
        """Background tabs past their time go to sleep (their page discarded,
        reloaded when they're next shown) - never the tab the Web tab shows,
        even while Buddy shows another tool, and never one with text typed
        in it that hasn't been sent."""
        now, minutes, never = time.time(), self.sleep_minutes(), self.never_sleep()
        for tab in self.tabs:
            if tab.web is None:
                continue
            if tab is self.active or tab.web.isVisible() or self._is_full(tab) or self._is_popped(tab):
                tab.seen = now
                continue
            info = {"url": tab.url, "visible": False, "audible": tab.page.recentlyAudible(),
                    "awake": tab.awake, "asleep": tab.asleep, "seen": tab.seen}
            if browser.should_sleep(info, now, minutes, never):
                self._sleep_unless_typed(tab)

    def _sleep_unless_typed(self, tab):
        """Sleeps the tab once its page (every frame of it) says nothing's
        typed there that would be lost; otherwise it's marked as having
        unsent text, and asked again next time."""
        if tab.page is None or tab.asleep:
            return
        try:
            frames = self._frames(tab.page.mainFrame())
        except RuntimeError:
            return
        answers = []

        def heard(result):
            answers.append(result is True)
            if len(answers) != len(frames) or tab not in self.tabs:
                return
            unsent = any(answers)
            if unsent != tab.unsent:
                tab.unsent = unsent
                self.changed()
            if not unsent:
                self.sleep(tab)

        for frame in frames:
            frame.runJavaScript(UNSENT_JS, QWebEngineScript.ScriptWorldId.ApplicationWorld, heard)

    def sleep(self, tab):
        if tab.web is not None and tab is not self.active and not tab.web.isVisible() and not self._is_full(tab) \
                and not self._is_popped(tab) and not tab.asleep and tab.url:
            tab.paused = False                # it starts over when it wakes
            tab.unsent = False
            tab.page.setLifecycleState(QWebEnginePage.LifecycleState.Discarded)
            self.changed()

    def sleep_others(self):
        """The menu's Sleep other tabs now: every tab but the one shown -
        except one playing sound, kept awake, on a site that never sleeps,
        or with unsent text, as when they sleep by themselves."""
        never = self.never_sleep()
        for tab in list(self.tabs):
            if tab is not self.active and tab.web is not None and not tab.asleep and not self._playing(tab) \
                    and not tab.awake and not browser.on_site(tab.url, never):
                self._sleep_unless_typed(tab)

    def keep_awake(self, tab, on):
        tab.awake = bool(on)
        self.changed(save=True)

    def set_never_sleep(self, site, on):
        sites = self.never_sleep()
        if on and site not in sites:
            sites.append(site)
        elif not on:
            sites = [s for s in sites if s != site and not site.endswith("." + s)]
        self.settings["never_sleep"] = sites
        self.settings.save()
        self.changed()

    # ------------------------------------------------------------- state --
    def emit(self, name, payload=None):
        """To the bar's view (web/browser.js)."""
        self.bar.emit(name, payload)

    def on_size(self, payload):
        height = (payload or {}).get("height")
        if isinstance(height, (int, float)) and 30 <= height <= 200:
            self.bar.setFixedHeight(int(round(height)))

    def changed(self, save=False):
        if save:
            self._save_timer.start()
        self._push_timer.start()

    def push(self):
        if not self.tabs:
            return
        audible = any(self._playing(t) for t in self.tabs)
        self.ducker.active = audible
        # Just paused, a tab still counts as audible for a moment: its mark is "paused" at once.
        sounding = any(self._playing(t) and not t.paused for t in self.tabs)
        badge = "sound" if sounding else "paused" if any(self._paused(t) for t in self.tabs) else None
        if badge != self._badge:
            self._badge = badge
            if hasattr(self.host, "refresh_badges"):
                self.host.refresh_badges()
        minutes, never = self.sleep_minutes(), self.never_sleep()
        page = self.active.page if self.active else None
        history = page.history() if page else None
        busy = [d for d in self._active.values() if not d.isFinished()]
        total = sum(max(d.totalBytes(), 0) for d in busy)
        got = sum(d.receivedBytes() for d in busy)
        self.emit("browser", {
            "tabs": [t.state(minutes, never) for t in self.tabs],
            "active": self.active.id if self.active else None,
            "url": (self.active.typed or self.active.url) if self.active else "",
            "back": bool(history and history.canGoBack()), "forward": bool(history and history.canGoForward()),
            "zoom": round((self.active.web.zoomFactor() if self.active and self.active.web else 1) * 100),
            "memory": self.memory,
            "ducked": self._ducked,
            "downloads": {"busy": len(busy), "progress": (got / total) if total else None},
            "profile": dict(self.current_profile(), many=len(self.profiles) > 1),
            "keys": self._keys,
        })
        if self._panel is not None:
            try:
                self._panel.refresh()
            except RuntimeError:              # the window has gone
                self._panel = None

    # --------------------------------------------------------- autofill --
    def visited(self, tab):
        if tab.url and not tab.private:
            self._remember(tab.url)

    def _remember(self, url, typed=False):
        if self.settings.get("suggest", True):
            sites = self.settings.get("sites")
            self.settings["sites"] = browser.record_site(sites if isinstance(sites, dict) else {}, url, time.time(), typed)
            self._save_timer.start()

    def on_sites(self, _payload=None):
        """The address bar has the focus: the sites it may finish typing with."""
        if not self.settings.get("suggest", True):
            self.emit("sites", {"sites": []})
            return
        links = [link["url"] for link in getattr(self.host, "links", []) or []]
        ranked = browser.ranked_sites(self.settings.get("sites"), links,
                                      [t.url for t in self.tabs if not t.private], time.time())
        self.emit("sites", {"sites": ranked[:500]})

    def forget_sites(self):
        self.settings["sites"] = {}
        self.settings.save()

    # ---------------------------------------------------------- ducking --
    def rail_badge(self):
        return self._badge

    @staticmethod
    def _playing(tab):
        return tab.page is not None and tab.page.recentlyAudible() and not tab.page.isAudioMuted()

    @staticmethod
    def _paused(tab):
        return tab.paused and not tab.asleep

    def rail_media(self):
        """The button beside "Web" in the sidebar: pauses every tab playing
        sound, or plays again the ones it paused."""
        playing = [t for t in self.tabs if self._playing(t) and not t.paused]
        for tab in playing or [t for t in self.tabs if self._paused(t)]:
            self.on_media({"id": tab.id})

    def duck_mode(self):
        mode = self.settings.get("duck", ducking.DEFAULT_MODE)
        return mode if mode in ducking.MODES else ducking.DEFAULT_MODE

    def duck_level(self):
        level = ducking.clean_level(self.settings.get("duck_level", ducking.DEFAULT_LEVEL))
        return ducking.DEFAULT_LEVEL if level is None else level

    def _apply_ducking(self):
        self.ducker.mode = self.duck_mode() if audio_sessions.available else "off"
        self.ducker.level = self.duck_level() / 100

    def _on_ducked(self, on):
        self._ducked = bool(on)
        self.changed()

    def _duck_saved(self, volume):
        """While the Web tab is down, the volume it came from, kept so it's
        put back next time should Buddy close before it comes back up."""
        if self.settings.get("duck_restore") != volume:
            self.settings["duck_restore"] = volume
            self.settings.save()

    def _measure(self):
        if not self.isVisible():
            return
        used = engine.memory_bytes(t.page.renderProcessPid() for t in self.tabs if t.page is not None)
        memory = None if used is None else round(used / (1024 * 1024))
        if memory != self.memory:
            self.memory = memory
            self.changed()

    def _save(self):
        kept = [t for t in self.tabs if not t.private]           # private tabs are never saved
        saved = [t.saved() for t in kept]
        self.settings["tabs"] = browser.clean_tabs(saved)
        self.settings["active"] = kept.index(self.active) if self.active in kept else 0
        self.settings["download_history"] = downloads_log.save(self.history)
        self.settings.save()

    def ground_color(self):
        """The new-tab page's background: the theme's own surface."""
        try:
            return QColor(self.host.theme_tokens()["surface"])
        except Exception:  # noqa: BLE001 - a host without a theme
            return QColor(Qt.white)

    def start_html(self, private=False):
        """The new-tab page, in the theme's own web look - the colours, font,
        corners and borders every Buddy page is drawn with (core/web_theme.py),
        not the raw palette: the palette's outline colour is a border's, and
        as text it all but vanished on Desktop (1.2:1) and Resolve (1.9:1)."""
        t = self.host.theme_tokens()
        links = getattr(self.host, "links", []) or []
        settings = getattr(self.host, "shared_settings", None) or {}
        try:
            v = web_theme(settings.get("theme", "Default"), settings.get("subtheme"), t)["vars"]
            colors = {"bg": v["surface"], "card": v["card-bg"], "text": v["text"], "dim": v["text-dim"],
                      "accent": v["primary"], "on_accent": v["on-primary"], "border": v["card-border"],
                      "field": v["field-bg"], "field_border": v["field-border"], "font": v["font"],
                      "radius": v["r-md"], "radius_card": v["r-lg"], "round": v["r-full"], "bw": v["bw"]}
        except Exception:  # noqa: BLE001 - a host without a theme: the palette, as before
            colors = {"bg": t["surface"], "card": t["surface_container"], "text": t["on_surface"],
                      "dim": t["outline"], "accent": t["primary"], "border": t["outline_variant"],
                      "font": "'Segoe UI', 'Open Sans', sans-serif"}
        return browser.start_page(colors, links, self.settings.get("search", browser.DEFAULT_ENGINE), tr, self.region(),
                                  self.shortcuts(), private)

    # ------------------------------------------------- new-tab shortcuts --
    def shortcuts(self):
        return browser.clean_shortcuts(self.settings.get("shortcuts"))

    def start_page_action(self, tab, action):
        """The new-tab page's + (add a shortcut) or a shortcut's x."""
        what = browser.start_page_action(action)
        if what is None:
            return
        kind, index = what
        if kind == "add":
            self.add_shortcut()
            return
        shortcuts = self.shortcuts()
        if 0 <= index < len(shortcuts):
            del shortcuts[index]
            self._set_shortcuts(shortcuts)

    def add_shortcut(self):
        shortcuts = self.shortcuts()
        if len(shortcuts) >= browser.MAX_SHORTCUTS:
            alert(self.window(), "New tab", f"The new-tab page holds up to {browser.MAX_SHORTCUTS} shortcuts.")
            return
        dialog = LinkDialog(self.window())
        if dialog.exec() and dialog.link is not None:
            if link_bar.is_path(dialog.link["url"]):
                alert(self.window(), "New tab", "Shortcuts on the new-tab page are web addresses. "
                                                "Folders go on the link bar.")
            else:
                self._set_shortcuts(shortcuts + [{"name": dialog.link["name"], "url": dialog.link["url"]}])
        dialog.deleteLater()

    def _set_shortcuts(self, shortcuts):
        self.settings["shortcuts"] = browser.clean_shortcuts(shortcuts)
        self.settings.save()
        self._redraw_start_pages()

    def region(self):
        region = self.settings.get("region", browser.AUTO_REGION)
        return region if region in browser.REGIONS else browser.AUTO_REGION

    def _apply_region(self, _language=None):
        """Sites hear the user's language as spoken in their region."""
        language = LANGUAGE_CODES.get(get_i18n().language, "en")
        engine.set_languages(browser.accept_language(language, self.region()))

    def focus_address(self):
        self.bar.view.setFocus()
        self.emit("focus_address")

    def zoom(self, step):
        view = self.active.web if self.active else None
        if view is None:
            return
        now = view.zoomFactor()
        if step == 0:
            factor = 1.0
        else:
            bigger = [z for z in ZOOMS if z > now + 0.001]
            smaller = [z for z in ZOOMS if z < now - 0.001]
            factor = (bigger[0] if bigger else ZOOMS[-1]) if step > 0 else (smaller[-1] if smaller else ZOOMS[0])
        view.setZoomFactor(factor)
        # Remembered for the site (not a private tab's): its pages open at it, in every tab.
        site = browser.site_of(self.active.url)
        if site and not self.active.private:
            zooms = browser.clean_zoom(self.settings.get("zoom"))
            zooms.pop(site, None)
            if abs(factor - 1.0) > 0.001:
                zooms[site] = factor
            self.settings["zoom"] = zooms
            self._save_timer.start()
            for tab in self.tabs:
                if tab is not self.active and tab.web is not None and browser.site_of(tab.url) == site:
                    tab.web.setZoomFactor(factor)
        self.changed()

    def site_zoom(self, tab):
        """A tab gone to another site: at that site's zoom."""
        if tab.web is not None:
            factor = browser.clean_zoom(self.settings.get("zoom")).get(browser.site_of(tab.url), 1.0)
            if abs(tab.web.zoomFactor() - factor) > 0.001:
                tab.web.setZoomFactor(factor)
                self.changed()

    # ------------------------------------------------------ full screen --
    def full_screen(self, tab, request):
        """A video's full-screen button: the page fills the screen Buddy is
        on, in a window of its own, and comes back when it's done (Esc).
        The page goes into a view made in that window, as Qt's own browser
        does it - the tab's view moved into the window instead stayed black
        there under Buddy's GPU drawing. Black, not white, shows while the
        page is redrawn at the new size, both ways - the window, the view
        and the page's own background were white for up to a second."""
        request.accept()
        if self._is_popped(tab):
            return                            # popped out: full screen fills its small window (pop_out)
        if request.toggleOn() and self._full is None:
            window = QWidget(None, Qt.Window | Qt.FramelessWindowHint)
            _black(window)
            box = QVBoxLayout(window)
            box.setContentsMargins(0, 0, 0, 0)
            view = QWebEngineView(window)
            _black(view)
            box.addWidget(view)
            self._full = (tab, window, view)
            tab._ground()
            view.setPage(tab.page)            # the tab's own view is left empty meanwhile
            esc = QShortcut(QKeySequence("Esc"), window)
            esc.activated.connect(lambda: tab.page.triggerAction(QWebEnginePage.ExitFullScreen))
            screen = self.window().screen()
            if screen is not None:
                window.setGeometry(screen.geometry())
            window.showFullScreen()
            view.setFocus()
            # Buddy's bar is covered: say whose page this is, over the top of it.
            site = request.origin().host() or browser.site_of(tab.url) or tr("A page")
            FullScreenNotice(window, view,
                             tr("{site} is full screen - press Esc to exit").replace("{site}", site))
        elif not request.toggleOn() and self._full is not None:
            self._leave_full()

    def _is_full(self, tab):
        return self._full is not None and self._full[0] is tab

    def _leave_full(self, at_once=False):
        """The full-screen window gone, its page back in the tab's view.
        The window stays up, black, until the tab's view has drawn the page
        at its size (or LEAVE_WAIT_MS) - gone at once, the tab showed white
        while Chromium caught up. The page's own background goes back after.
        A tab closed while full screen takes the window with it at once."""
        tab, window, view = self._full
        self._full = None
        view.hide()                           # its page is leaving: black behind, not an empty view
        home = tab.web is not None and tab.page is not None
        if home:
            tab.web.setPage(tab.page)
            if tab is self.active:
                tab.web.setFocus()
        started = time.monotonic()

        def done():
            window.hide()
            window.deleteLater()
            try:
                if tab.page is not None and not self._is_full(tab):
                    tab._ground()
            except RuntimeError:              # the tab closed meanwhile
                pass

        def check():
            waited = (time.monotonic() - started) * 1000
            gone = at_once or not home or tab is not self.active or tab not in self.tabs
            if gone or waited >= LEAVE_WAIT_MS or (waited >= LEAVE_SETTLE_MS and _drawn(tab.web)):
                done()
            else:
                QTimer.singleShot(LEAVE_POLL_MS, check)
        check()

    # ------------------------------------------------------------ videos --
    def play_video(self, tab, raw):
        """A page's video that Chromium here can't decode (video_fallback.js
        told us): played in Buddy's own window."""
        try:
            info = json.loads(raw)
            url = info["url"]
        except (ValueError, KeyError, TypeError):
            return
        if not isinstance(url, str) or QUrl(url).scheme().lower() not in ("http", "https"):
            return
        title = info.get("title") if isinstance(info.get("title"), str) else ""
        start = info.get("time") if isinstance(info.get("time"), (int, float)) else 0.0
        self.videos.play(url, title or tab.title or browser.site_of(tab.url), start,
                         save=lambda address, t=tab: self._save_video(t, address),
                         page_host=browser.site_of(tab.url),
                         refused=lambda why: self.emit("toast", {"text": tr(why), "id": None}))

    def _save_video(self, tab, url):
        """Save in the video window: a download through the tab's page, so
        it has the page's cookies and lands in the Downloads window."""
        if tab.page is not None and tab in self.tabs:
            tab.page.download(QUrl(url))

    # -------------------------------------------------------- downloads --
    def downloads_folder(self):
        folder = self.settings.get("downloads") or browser.default_downloads()
        return folder if os.path.isdir(folder) else browser.default_downloads()

    def download_requested(self, item):
        folder = self.downloads_folder()
        os.makedirs(folder, exist_ok=True)
        item.setDownloadDirectory(folder)
        going = [os.path.basename(r["path"]) for r in self.history
                 if r["state"] == "active" and os.path.normcase(os.path.dirname(r["path"])) == os.path.normcase(folder)]
        item.setDownloadFileName(browser.unique_name(folder, item.downloadFileName(), going))
        tab = getattr(item.page(), "tab", None)
        source = tab.url if tab is not None and tab.url else item.url().toString()
        if not self._download_allowed(item, downloads_log.site_of(source)):
            item.cancel()
            return
        self._serial += 1
        record = downloads_log.new_record(self._serial, item.downloadFileName(),
                                          os.path.join(folder, item.downloadFileName()), downloads_log.site_of(source))
        record["private"] = bool(tab is not None and tab.private)
        record["source"] = source if source.lower().startswith(("http://", "https://")) else ""
        address = item.url().toString()
        record["url"] = address if address.lower().startswith(("http://", "https://")) else ""
        if address in self._to_resolve:       # "Save image to Resolve" and the like
            self._to_resolve.discard(address)
            record["to_resolve"] = True
        self.history.append(record)
        self._active[record["id"]] = item
        item.receivedBytesChanged.connect(self.changed)
        item.isFinishedChanged.connect(lambda: self._download_done(item, record))
        item.accept()
        self.changed()

    def _download_allowed(self, item, site):
        """A download is only started if it fits: not too many together or
        for the disk, and a program, or a burst of files from one site, only
        if the user says so. A refusal says why."""
        name, parent = item.downloadFileName(), self._panel or self.window()
        who = site or tr("A page")
        if len([d for d in self._active.values() if not d.isFinished()]) >= download_guard.MAX_AT_ONCE:
            self.emit("toast", {"text": tr("Too many downloads at once - wait for some to finish"), "id": None})
            return False
        try:
            free = shutil.disk_usage(item.downloadDirectory()).free
        except OSError:
            free = None
        if free is not None and not download_guard.room_for(free, item.totalBytes()):
            self.emit("toast", {"text": tr("Not enough room on that drive to download {name}").replace("{name}", name),
                                "id": None})
            return False
        if self._burst.burst(site or "?", time.time()):
            if not confirm(parent, "Many downloads",
                           tr("{site} is downloading a lot of files in a row. Allow it to keep going?")
                           .replace("{site}", who), ok="Allow", cancel="Stop", danger=True):
                return False
            self._burst.allow(site or "?", time.time())     # asked once, not at every file after

        if download_guard.is_program_file(name) and not confirm(
                parent, "Download a program?",
                tr("{site} wants to download {name}. This kind of file runs code when it is opened, so only "
                   "keep it if you trust the site.").replace("{site}", who).replace("{name}", name),
                ok="Download", cancel="Cancel", danger=True):
            return False
        return True

    def _download_done(self, item, record):
        completed = item.state() == QWebEngineDownloadRequest.DownloadCompleted
        record["name"] = item.downloadFileName()
        record["path"] = os.path.join(item.downloadDirectory(), record["name"])
        record["state"] = "done" if completed else \
            "cancelled" if item.state() == QWebEngineDownloadRequest.DownloadCancelled else "failed"
        try:
            record["size"] = os.path.getsize(record["path"]) if completed else 0
        except OSError:
            record["size"] = max(item.totalBytes(), 0)
        self._active.pop(record["id"], None)
        if completed:                         # Windows then warns before it is run
            download_guard.mark_from_internet(record["path"], item.url().toString(), record.get("source", ""))
        self.history = downloads_log.trim(self.history)
        self._save_timer.start()
        if completed:
            self.achieve("downloads", 1)
            self.emit("toast", {"text": tr("Saved {name}").replace("{name}", record["name"]), "id": record["id"]})
            wanted = record.pop("to_resolve", False)
            media = downloads_log.kind_of(record["name"]) in ("video", "audio", "image")
            if wanted or (media and self.settings.get("auto_resolve") and not record.get("private")):
                QTimer.singleShot(0, lambda: self.send_downloads([record["id"]], quiet=True))
        self.changed()

    def _records(self, ids):
        """The history's records for these ids, in the order asked."""
        by_id = {r["id"]: r for r in self.history}
        return [by_id[i] for i in ids if i in by_id]

    @staticmethod
    def _has_file(record):
        return record["state"] == "done" and os.path.isfile(record["path"])

    def downloads_view(self):
        """The Downloads window's list: newest first."""
        items = []
        for record in reversed(self.history):
            item = self._active.get(record["id"])
            if item is None:
                items.append(downloads_log.shown(record, self._has_file(record)))
                continue
            total, got = item.totalBytes(), item.receivedBytes()
            items.append(dict(downloads_log.shown(dict(record, size=max(total, got, 0)), False,
                                                  (got / total) if total > 0 else None), paused=item.isPaused()))
        return {"items": items, "folder": self.downloads_folder()}

    def show_downloads(self):
        if self._panel is not None:
            try:
                self._panel.show()
                self._panel.raise_()
                self._panel.activateWindow()
                return
            except RuntimeError:
                self._panel = None
        panel = DownloadsWindow(self)
        panel.destroyed.connect(lambda: setattr(self, "_panel", None))
        self._panel = panel
        panel.show()

    def drag_downloads(self, ids, view=None):
        """Carries downloads out as files (the button is down). Let go over
        Resolve, they're moved into its Downloads bin once it has them."""
        records = [r for r in self._records(ids) if self._has_file(r)]
        if not records:
            return
        view = view or self.bar.view
        start_file_drag(view, view, [r["path"] for r in records])
        if resolve_ext.cursor_over_resolve():
            self._file_in_resolve(records)

    def _file_in_resolve(self, records):
        """Resolve takes a dragged file into whichever bin is open: look
        for them in the Media Pool and move them into Downloads."""
        started = not self._filing
        self._filing.update({r["path"]: r for r in records})
        self._filing_tries = 0
        if started:
            QTimer.singleShot(FILE_EVERY_MS, self._file_step)

    def _file_step(self):
        if not self._filing:
            return
        self._filing_tries += 1
        filed, failed = [], None
        try:
            controller = self.host.ensure_connected()
            for path, record in list(self._filing.items()):
                if resolve_ext.file_into_bin(controller, path):
                    record["resolve"] = True
                    filed.append(path)
                    del self._filing[path]
        except Exception as exc:              # noqa: BLE001 - Resolve gone, or no project open
            failed = exc
        if filed:
            self._save_timer.start()
            self.changed()
            self.emit("toast", {"text": tr("Moved to the Downloads bin in Resolve"), "id": None})
        if failed is not None or (self._filing and self._filing_tries >= FILE_TRIES):
            if self._filing:
                self.emit("toast", {"text": tr("Couldn't find it in the Media Pool - use Send to Resolve"), "id": None})
            self._filing.clear()
        elif self._filing:
            QTimer.singleShot(FILE_EVERY_MS, self._file_step)

    def send_downloads(self, ids, quiet=False):
        """Imports downloads into the Media Pool's Downloads bin. `quiet`
        (a download going in by itself): what goes wrong is said on the
        bar, not in a dialog."""
        records = [r for r in self._records(ids) if self._has_file(r)]
        parent = self._panel or self.window()
        if not records:
            if not quiet:
                alert(parent, "Send to Resolve", "Those files aren't on disk any more.")
            return
        busy = getattr(self.host, "set_busy", None)
        if busy:
            busy(True, tr("Sending to Resolve…"))
        try:
            controller = self.host.ensure_connected()
            imported = resolve_ext.send_to_bin(controller, [r["path"] for r in records])
        except Exception as exc:              # noqa: BLE001 - Resolve refused, gone, or no project open
            if quiet:
                self.emit("toast", {"text": tr("Couldn't put {name} in Resolve: {why}")
                                    .replace("{name}", records[0]["name"]).replace("{why}", str(exc)), "id": None})
            else:
                alert(parent, "Send to Resolve", str(exc))
            return
        finally:
            if busy:
                busy(False)
        # Only what Resolve took is "In Resolve": it can leave some of a batch out.
        taken = {os.path.normcase(os.path.normpath(p)) for p in imported}
        left = []
        for record in records:
            if os.path.normcase(os.path.normpath(record["path"])) in taken:
                record["resolve"] = True
            else:
                left.append(record["name"])
        self._save_timer.start()
        self.changed()
        if left and quiet:
            self.emit("toast", {"text": tr("Resolve didn't take {name}").replace("{name}", left[0]), "id": None})
        elif left:
            alert(parent, "Send to Resolve",
                  tr("Resolve imported {done} of {count}. It left out: {names}")
                  .replace("{done}", str(len(records) - len(left))).replace("{count}", str(len(records)))
                  .replace("{names}", ", ".join(left[:10]) + (" …" if len(left) > 10 else "")))
        else:
            self.emit("toast", {"text": tr("Sent to the Downloads bin in Resolve"), "id": None})

    def open_downloads(self, ids):
        records = [r for r in self._records(ids) if self._has_file(r)][:10]
        programs = [r["name"] for r in records if download_guard.is_program_file(r["name"])]
        if programs and not confirm(
                self._panel or self.window(), "Open a program?",
                tr("{name} is a program or script. Opening it runs it on this PC - only do that if you "
                   "trust where it came from.").replace("{name}", programs[0]) if len(programs) == 1 else
                tr("{count} of these are programs or scripts. Opening them runs them on this PC - only do "
                   "that if you trust where they came from.").replace("{count}", str(len(programs))),
                ok="Open", cancel="Cancel", danger=True):
            records = [r for r in records if r["name"] not in programs]      # the rest open as asked
        for record in records:
            QDesktopServices.openUrl(QUrl.fromLocalFile(record["path"]))

    def reveal_download(self, ident):
        record = next(iter(self._records([ident])), None)
        if record is None:
            return
        if self._has_file(record) and sys.platform == "win32":
            subprocess.Popen(["explorer", "/select," + os.path.normpath(record["path"])])
        else:
            folder = os.path.dirname(record["path"])
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder if os.path.isdir(folder) else self.downloads_folder()))

    def open_downloads_folder(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.downloads_folder()))

    def cancel_downloads(self, ids):
        for record in self._records(ids):
            item = self._active.get(record["id"])
            if item is not None:
                item.cancel()

    def pause_downloads(self, ids, pause):
        """Pause (or carry on with) downloads still going."""
        for record in self._records(ids):
            item = self._active.get(record["id"])
            if item is not None and not item.isFinished():
                item.pause() if pause else item.resume()
        self.changed()

    def retry_downloads(self, ids):
        """A failed or cancelled download, from the start: through the tab
        it came from if it's still open (its sign-in), else the one shown.
        The old line makes way for the new one."""
        page = self.active.page if self.active is not None and self.active.page is not None else None
        retried = []
        for record in self._records(ids):
            if record["state"] not in ("failed", "cancelled") or not record.get("url"):
                continue
            source = next((t for t in self.tabs if t.page is not None and not t.asleep and t.url
                           and t.url == record.get("source")), None)
            via = source.page if source is not None else page
            if via is None:
                continue
            via.download(QUrl(record["url"]))
            retried.append(record["id"])
        if retried:
            self.history = [r for r in self.history if r["id"] not in set(retried)]
            self._save_timer.start()
            self.changed()

    def remove_downloads(self, ids):
        """Off the list (a download still going is cancelled); the files stay."""
        gone = {r["id"] for r in self._records(ids)}
        for ident in gone:
            item = self._active.pop(ident, None)
            if item is not None:
                item.cancel()
        self.history = [r for r in self.history if r["id"] not in gone]
        self._save_timer.start()
        self.changed()

    def delete_downloads(self, ids):
        """The files to the Recycle Bin, and off the list."""
        records = [r for r in self._records(ids) if r["state"] != "active"]
        files = [r["path"] for r in records if os.path.isfile(r["path"])]
        parent = self._panel or self.window()
        if files:
            text = (tr("Move {name} to the Recycle Bin?").replace("{name}", os.path.basename(files[0]))
                    if len(files) == 1 else
                    tr("Move {count} files to the Recycle Bin?").replace("{count}", str(len(files))))
            if not confirm(parent, "Delete downloads", text, ok="Move to Recycle Bin", danger=True):
                return
            try:
                recycle.to_recycle_bin(files)
            except OSError as exc:
                alert(parent, "Delete downloads", str(exc))
                return
        self.remove_downloads([r["id"] for r in records])

    # ---------------------------------------------------------- the bar --
    def on_new_tab(self, payload=None):
        self.open_tab("", private=bool((payload or {}).get("private")))

    def on_close(self, payload):
        tab = self.tab(payload)
        if tab is not None:
            self.request_close(tab)

    def on_move(self, payload):
        """A tab dragged to another place in the row."""
        tab, index = self.tab(payload), (payload or {}).get("index")
        if tab is None or not isinstance(index, int):
            return
        self.tabs.remove(tab)
        # Pinned tabs stay together at the left; the rest after them.
        pinned = sum(1 for t in self.tabs if t.pinned)
        low, high = (0, pinned) if tab.pinned else (pinned, len(self.tabs))
        self.tabs.insert(max(low, min(index, high)), tab)
        self.changed(save=True)

    def pin(self, tab, on):
        """A pinned tab: small, at the left, kept by Close other tabs."""
        if tab not in self.tabs or not tab.url:
            return
        tab.pinned = bool(on)
        self.tabs.remove(tab)
        pinned = sum(1 for t in self.tabs if t.pinned)
        self.tabs.insert(pinned, tab)          # the end of the pinned ones, or the first of the rest
        self.changed(save=True)

    def _apply_video_quality(self):
        engine.set_video_quality(self.settings.get("video_quality"))
        script = browser.youtube_quality_script(browser.video_quality(self.settings.get("video_quality")))
        for tab in self.tabs:                  # open YouTube pages switch now, not on their next video
            if tab.page is not None and not tab.asleep and browser.on_site(tab.url, list(browser.YOUTUBE_HOSTS)):
                for frame in self._frames(tab.page.mainFrame()):
                    frame.runJavaScript(script, QWebEngineScript.ScriptWorldId.MainWorld, lambda _r: None)

    # --------------------------------------------------------- blocking --
    def allowed_sites(self):
        return browser.clean_sites(self.settings.get("allow_ads"))

    def _apply_blocking(self):
        f = engine.tracker_filter()
        f.mode = browser.blocking(self.settings)
        f.allowed = frozenset(self.allowed_sites())
        if f.mode == "strong" and self.lists.filters is None and self._lists_wanted:
            self.lists.start()

    def _start_lists(self):
        if not self._lists_wanted:
            self._lists_wanted = True
            self._apply_blocking()

    def _lists_ready(self, built):
        engine.tracker_filter().filters = built

    def _lists_changed(self):
        refresh_open(self.tool_id)            # an open Settings: Updating..., then how many rules

    def allow_ads(self, site, on):
        sites = self.allowed_sites()
        if on and site not in sites:
            sites.append(site)
        elif not on:
            sites = [s for s in sites if s != site and not site.endswith("." + s)]
        self.settings["allow_ads"] = sites
        self.settings.save()
        self._apply_blocking()
        for tab in self.tabs:                   # see the site as it now is
            if tab.page is not None and not tab.asleep and browser.on_site(tab.url, [site]):
                tab.page.triggerAction(QWebEnginePage.Reload)

    def on_select(self, payload):
        tab = self.tab(payload)
        if tab is not None:
            self.select(tab)

    def on_go(self, payload):
        text = (payload or {}).get("text")
        picked = self.suggest.picked()
        self.suggest.hide()
        if picked is not None and isinstance(text, str) and text.strip() == picked["url"]:
            self.use_suggestion(picked)          # chosen with the arrow keys
            return
        if isinstance(text, str) and text.strip().lower().startswith(TABS_KEYWORD):
            return                                # "@tabs" with no tab picked: nothing to go to
        engine = self.settings.get("search", browser.DEFAULT_ENGINE)
        url = browser.address_to_url(text, engine, self.region())
        private = self.active is not None and self.active.private
        if url and not private and url != browser.search_url((text or "").strip(), engine, self.region()):
            self._remember(url, typed=True)               # typed sites come first next time
        if url and self.active is not None:
            if self.active.web is None:
                self.active.url = url
                self.active.saved_history = ""   # the typed address, not where it was
                self.select(self.active)      # its page made, loading the address, and shown
                self.changed(save=True)
            else:
                # Shown in the bar, but not the tab's address until its page goes
                # there (urlChanged): one that asks before it's left may be told Stay.
                self.active.typed = url
                self.active.page.load(QUrl(url))
                self.changed()
            self.active.web.setFocus()

    def on_back(self, _payload=None):
        if self.active and self.active.page:
            self.active.page.triggerAction(QWebEnginePage.Back)

    def on_forward(self, _payload=None):
        if self.active and self.active.page:
            self.active.page.triggerAction(QWebEnginePage.Forward)

    def on_reload(self, _payload=None):
        if self.active is not None and self.active.crashed:
            self.reload_crashed(self.active)
        elif self.active is not None and self._is_popped(self.active):
            self.active.page.triggerAction(QWebEnginePage.Reload)
        elif self.active is not None and self.active.web is None:
            self.select(self.active)          # asleep since Buddy started: loads it
        elif self.active and self.active.page:
            if self.active.url:
                self.active.page.triggerAction(QWebEnginePage.Reload)
            else:
                self.active.load()

    def on_stop(self, _payload=None):
        if self.active and self.active.page:
            self.active.page.triggerAction(QWebEnginePage.Stop)

    def on_mute(self, payload):
        tab = self.tab(payload)
        if tab is not None and tab.page is not None:
            tab.page.setAudioMuted(not tab.page.isAudioMuted())

    def on_media(self, payload):
        """The tab's pause / play button."""
        tab = self.tab(payload)
        if tab is None or tab.page is None or tab.asleep:
            return
        pausing = not tab.paused
        script = PAUSE_JS if pausing else PLAY_JS
        tab.paused = pausing
        tab.paused_at = time.time() if pausing else 0.0
        frames = self._frames(tab.page.mainFrame())
        found = []

        def done(count):
            found.append(count if isinstance(count, (int, float)) else 0)
            if len(found) == len(frames) and pausing and not any(found):
                tab.paused = False            # nothing in the page was playing after all
                self.changed()

        for frame in frames:
            frame.runJavaScript(script, QWebEngineScript.ScriptWorldId.ApplicationWorld, done)
        self.changed()

    def _frames(self, frame):
        out = [frame]
        for child in frame.children():
            out += self._frames(child)
        return out

    def on_zoom_reset(self, _payload=None):
        self.zoom(0)

    def on_downloads(self, _payload=None):
        self.show_downloads()

    def on_drag_download(self, payload):
        ident = (payload or {}).get("id")
        self.drag_downloads([ident] if isinstance(ident, str) else [])
        self.emit("drag_done")

    def on_menu(self, payload):
        tab = self.tab(payload)
        if tab is None:
            return
        menu = QMenu(self)
        site = browser.site_of(tab.url)
        add = lambda text, run, enabled=True: menu.addAction(tr(text), run).setEnabled(enabled)  # noqa: E731
        add("Reload", lambda: (self.select(tab), self.on_reload()), bool(tab.url))
        add("Duplicate", lambda: self.open_tab(tab.url, private=tab.private), bool(tab.url))
        add("Unpin tab" if tab.pinned else "Pin tab", lambda: self.pin(tab, not tab.pinned), bool(tab.url))
        if not tab.private:
            add("Open in a private tab", lambda: self.open_tab(tab.url, private=True), bool(tab.url))
        muted = bool(tab.page and tab.page.isAudioMuted())
        add("Unmute tab" if muted else "Mute tab", lambda: self.on_mute({"id": tab.id}), tab.page is not None)
        add("Pop out video", lambda: self.pop_out(tab), tab.page is not None and not tab.asleep and bool(tab.url))
        menu.addSeparator()
        awake = menu.addAction(tr("Keep this tab awake"), lambda: self.keep_awake(tab, not tab.awake))
        awake.setCheckable(True)
        awake.setChecked(tab.awake)
        if site:
            never = menu.addAction(tr("Never sleep on {site}").replace("{site}", site),
                                   lambda: self.set_never_sleep(site, not browser.on_site(tab.url, self.never_sleep())))
            never.setCheckable(True)
            never.setChecked(browser.on_site(tab.url, self.never_sleep()))
            allowed = browser.allowed_site(browser.site_of(tab.url), self.allowed_sites())
            ads = menu.addAction(tr("Allow ads on {site}").replace("{site}", site),
                                 lambda: self.allow_ads(site, not allowed))
            ads.setCheckable(True)
            ads.setChecked(allowed)
            ads.setEnabled(browser.blocking(self.settings) != "off")
            if self.dark_mode() != "off":
                dark = menu.addAction(tr("Darken {site}").replace("{site}", site),
                                      lambda: self.keep_light(site, not browser.on_site(tab.url, self.light_sites())))
                dark.setCheckable(True)
                dark.setChecked(not browser.on_site(tab.url, self.light_sites()))
            if self.site_permissions(site):
                add("Forget what {site} may use".replace("{site}", site), lambda: self.forget_permissions(site))
        add("Sleep now", lambda: self.sleep(tab),
            tab.web is not None and tab is not self.active and not tab.web.isVisible() and not tab.asleep
            and bool(tab.url))
        menu.addSeparator()
        add("Add to link bar…", lambda: self.host.add_link(tab.url), bool(tab.url) and hasattr(self.host, "add_link"))
        add("Open in your browser", lambda: engine.open_outside(tab.url), bool(tab.url))
        menu.addSeparator()
        add("Close tab", lambda: self.request_close(tab))
        add("Close other tabs", lambda: [self.request_close(t) for t in list(self.tabs) if t is not tab and not t.pinned],
            any(t is not tab and not t.pinned for t in self.tabs))
        menu.exec(QCursor.pos())
        menu.deleteLater()

    def on_more(self, _payload=None):
        menu = QMenu(self)
        tab = self.active
        native = lambda key: QKeySequence(key).toString(QKeySequence.NativeText)   # noqa: E731
        keyed = lambda text, key, run: menu.addAction(tr(text) + "\t" + native(key), run)   # noqa: E731
        keyed("New tab", "Ctrl+T", self.on_new_tab)
        keyed("New private tab", "Ctrl+Shift+N", lambda: self.new_private_tab())
        keyed("Reopen closed tab", "Ctrl+Shift+T", self.reopen_closed).setEnabled(bool(self._closed))
        keyed("Search tabs…", "Ctrl+Shift+A", self.search_tabs)
        menu.addSeparator()
        keyed("Find on page…", "Ctrl+F", self.open_find).setEnabled(bool(tab and tab.page))
        menu.addAction(tr("Zoom in"), lambda: self.zoom(1))
        menu.addAction(tr("Zoom out"), lambda: self.zoom(-1))
        menu.addAction(tr("Actual size"), lambda: self.zoom(0))
        keyed("Print…", "Ctrl+P", lambda: self.print_page(tab)).setEnabled(bool(tab and tab.page and tab.url))
        menu.addAction(tr("Save page as PDF…"), lambda: self.save_pdf(tab)).setEnabled(bool(tab and tab.page and tab.url))
        menu.addAction(tr("Pop out video"), lambda: self.pop_out(tab)).setEnabled(
            bool(tab and tab.page and not tab.asleep and tab.url))
        menu.addSeparator()
        menu.addAction(tr("Add to link bar…"), lambda: self.host.add_link(tab.url)).setEnabled(
            bool(tab and tab.url) and hasattr(self.host, "add_link"))
        menu.addAction(tr("Open in your browser"), lambda: engine.open_outside(tab.url)).setEnabled(bool(tab and tab.url))
        menu.addAction(tr("Sleep other tabs now"), self.sleep_others)
        menu.addSeparator()
        menu.addAction(tr("Add a shortcut to the new-tab page…"), self.add_shortcut)
        keyed("History", HISTORY_KEY, self.show_history)
        keyed("Downloads", DOWNLOADS_KEY, self.show_downloads)
        keyed("Clear browsing data…", CLEAR_KEY, self.show_clear_data)
        menu.addSeparator()
        self._profile_menu(menu.addMenu(tr("Profile: {name}").replace("{name}", self.current_profile()["name"])))
        if hasattr(self.host, "open_settings"):
            menu.addAction(tr("Web settings…"), lambda: self.host.open_settings(self.tool_id))
        menu.exec(QCursor.pos())
        menu.deleteLater()

    def page_menu(self, tab, pos):
        """A page's own right-click menu: Chromium's (copy, open a link in a
        new tab...) and Buddy's - an image or video straight into Resolve,
        a video popped out."""
        if tab.web is None:
            return
        menu = tab.web.createStandardContextMenu()
        request = tab.web.lastContextMenuRequest()
        if request is not None:
            kind = request.mediaType()
            media = request.mediaUrl()
            fetchable = media.scheme().lower() in ("http", "https")
            types = QWebEngineContextMenuRequest.MediaType
            extra = []
            if kind == types.MediaTypeImage and fetchable:
                extra.append((tr("Save image to Resolve"), lambda: self.save_to_resolve(tab, media)))
            if kind == types.MediaTypeVideo:
                if fetchable:
                    extra.append((tr("Save video to Resolve"), lambda: self.save_to_resolve(tab, media)))
                extra.append((tr("Pop out video"), lambda: self.pop_out(tab)))
            if kind == types.MediaTypeAudio and fetchable:
                extra.append((tr("Save audio to Resolve"), lambda: self.save_to_resolve(tab, media)))
            if extra:
                first = menu.actions()[0] if menu.actions() else None
                for text, run in extra:
                    action = menu.addAction(text, run)
                    if first is not None:
                        menu.removeAction(action)
                        menu.insertAction(first, action)
                if first is not None:
                    menu.insertSeparator(first)
        menu.setAttribute(Qt.WA_DeleteOnClose, True)
        menu.popup(tab.web.mapToGlobal(pos))

    def save_to_resolve(self, tab, url):
        """Downloaded, then into Resolve's Downloads bin (_download_done)."""
        if tab.page is not None:
            self._to_resolve.add(url.toString())
            tab.page.download(url)

    # -------------------------------------------------------------- find --
    def open_find(self):
        if self.active is None or self.active.page is None or self.active.asleep:
            return
        self.emit("find_open", {"text": self._find["text"]})

    def on_find(self, payload):
        """The find bar's text, Enter (next) and Shift+Enter (back)."""
        payload = payload or {}
        text = payload.get("text")
        if not isinstance(text, str) or self.active is None or self.active.page is None:
            return
        self._find = {"text": text[:500], "case": bool(payload.get("case"))}
        self._find_in(self.active, back=bool(payload.get("back")))

    def find_step(self, back):
        if self._find["text"] and self.active is not None and self.active.page is not None:
            self._find_in(self.active, back=back)
        else:
            self.open_find()

    def _find_in(self, tab, back=False):
        flags = QWebEnginePage.FindFlag(0)
        if back:
            flags |= QWebEnginePage.FindFlag.FindBackward
        if self._find["case"]:
            flags |= QWebEnginePage.FindFlag.FindCaseSensitively
        tab.page.findText(self._find["text"], flags)
        if not self._find["text"]:
            self.emit("find_result", {"active": 0, "total": 0})

    def found(self, tab, result):
        if tab is self.active:
            self.emit("find_result", {"active": result.activeMatch(), "total": result.numberOfMatches()})

    def on_find_close(self, _payload=None):
        if self.active is not None:
            self._stop_find(self.active)
            if self.active.web is not None:
                self.active.web.setFocus()

    def _stop_find(self, tab):
        if tab.page is not None and self._find["text"]:
            tab.page.findText("")
        self.emit("find_close")

    # -------------------------------------------------------- suggestions --
    def on_suggest(self, payload):
        """What's typed in the address bar so far: the open tabs and pages
        from History it may be, listed under it. "@tabs" first lists only
        the open tabs."""
        payload = payload or {}
        text = payload.get("text") if isinstance(payload.get("text"), str) else ""
        rect = payload.get("rect") or {}
        try:
            box = QRect(int(rect["x"]), int(rect["y"]), int(rect["w"]), int(rect["h"]))
        except (KeyError, TypeError, ValueError):
            return self.suggest.hide()
        self.suggest.show_items(self.suggestions(text), QRect(self.bar.view.mapTo(self, box.topLeft()), box.size()))

    def suggestions(self, text):
        text = text.strip()
        only_tabs = text.lower().startswith(TABS_KEYWORD)
        if only_tabs:
            text = text[len(TABS_KEYWORD):].strip()
        words = text.lower().split()
        private = self.active is not None and self.active.private
        if not words and not only_tabs:
            return []
        if browser.site_of(text) and "/" in text and not only_tabs:
            words = [text.lower()]

        def hit(title, url):
            hay = f"{title} {url}".lower()
            return all(w in hay for w in words)

        tabs = [{"kind": "tab", "title": t.title, "url": t.url, "id": t.id} for t in self.tabs
                if t.url and t is not self.active and t.private == private and hit(t.title, t.url)]
        if only_tabs:
            return tabs[:SUGGESTIONS * 2]
        tabs = tabs[:2]
        shown = {t["url"] for t in tabs} | {self.active.url if self.active else ""}
        pages = [] if private else [{"kind": "page", "title": e["title"], "url": e["url"]}
                                    for e in self.visits.suggest(text, SUGGESTIONS) if e["url"] not in shown]
        return (tabs + pages)[:SUGGESTIONS]

    def on_suggest_move(self, payload):
        step = (payload or {}).get("step")
        if step in (1, -1):
            item = self.suggest.move_pick(step)
            self.emit("suggest_pick", {"url": item["url"] if item else None})

    def on_suggest_close(self, _payload=None):
        self.suggest.hide()

    def use_suggestion(self, item, background=False):
        self.suggest.hide()
        if item["kind"] == "tab":
            tab = next((t for t in self.tabs if t.id == item.get("id")), None)
            if tab is not None:
                self.select(tab)
                if tab.web is not None:
                    tab.web.setFocus()
        elif background:
            self.open_tab(item["url"], show=False)
        else:
            self.on_go({"text": item["url"]})

    def search_tabs(self):
        """The address bar, ready to find an open tab by its name or site."""
        self.focus_address()
        self.emit("address_text", {"text": TABS_KEYWORD + " "})

    # ----------------------------------------------------------- pop out --
    def _is_popped(self, tab):
        return self._popped is not None and self._popped[0] is tab

    def pop_out(self, tab):
        """The tab's video in a small window of its own that stays on top -
        to watch a tutorial over Resolve. The tab's page moves into it (as
        full screen does) and its video is made to fill it; close the
        window, or Bring it back, and the page comes home."""
        if tab is None or tab.page is None or tab.asleep or not tab.url:
            return
        if self._popped is not None:
            same = self._popped[0] is tab
            self.bring_back(at_once=True)
            if same:
                return
        if self._full is not None and self._full[0] is tab:
            self._leave_full(at_once=True)
        window = PopOutWindow(self, tab)
        view = QWebEngineView(window)
        _black(view)
        window.layout().addWidget(view)
        self._popped = (tab, window, view)
        view.setPage(tab.page)
        screen = self.window().screen()
        if screen is not None:
            area = screen.availableGeometry()
            width, height = POP_OUT_SIZE
            window.setGeometry(area.right() - width - POP_OUT_MARGIN, area.bottom() - height - POP_OUT_MARGIN,
                               width, height)
        window.show()
        if tab is self.active:
            self.select(tab)                  # the note that it's playing elsewhere, in its place
        QTimer.singleShot(POP_OUT_SETTLE_MS, lambda: self._fill_with_video(tab))
        self.changed()

    def _fill_with_video(self, tab):
        """The popped-out page's video fills its window: Chromium's full
        screen, which needs a key press from the user - Buddy presses a key
        no page uses, into the window, and the page's (Buddy's) script that
        hears it asks for the video's player to go full screen."""
        if not self._is_popped(tab):
            return
        _tab, window, view = self._popped
        script = """(() => {
            const videos = [...document.querySelectorAll("video")].filter(v => v.offsetWidth > 0);
            const video = videos.find(v => !v.paused) || videos.sort((a, b) =>
                b.offsetWidth * b.offsetHeight - a.offsetWidth * a.offsetHeight)[0];
            if (!video) return false;
            // The player around it (its own controls), if that's about the video's size.
            let box = video;
            const r = video.getBoundingClientRect();
            for (let p = video.parentElement; p && p !== document.body; p = p.parentElement) {
                const q = p.getBoundingClientRect();
                if (q.width > r.width * 1.15 || q.height > r.height * 1.3) break;
                box = p;
            }
            const go = e => {
                if (e.key !== "F24") return;
                e.preventDefault();
                e.stopImmediatePropagation();
                box.requestFullscreen().catch(() => {});
            };
            window.addEventListener("keydown", go, {capture: true, once: true});
            return true;
        })()"""

        def ready(has_video):
            if has_video is True and self._is_popped(tab):
                target = view.focusProxy() or view
                from PySide6.QtGui import QKeyEvent
                for kind in (QKeyEvent.KeyPress, QKeyEvent.KeyRelease):
                    QApplication.sendEvent(target, QKeyEvent(kind, Qt.Key_F24, Qt.NoModifier))
            elif self._is_popped(tab):
                self.emit("toast", {"text": tr("No video on this page to pop out - it's shown whole instead"),
                                    "id": None, "info": True})
        view.setFocus()
        tab.page.runJavaScript(script, QWebEngineScript.ScriptWorldId.ApplicationWorld, ready)

    def bring_back(self, at_once=False):
        """The popped-out page back in its tab (the window closed)."""
        if self._popped is None:
            return
        tab, window, view = self._popped
        self._popped = None
        if tab.page is not None:
            tab.page.triggerAction(QWebEnginePage.ExitFullScreen)     # its video back to its place on the page
        view.hide()
        if tab.web is not None and tab.page is not None:
            tab.web.setPage(tab.page)
        window.done = True
        window.close()
        window.deleteLater()
        if tab is self.active and tab in self.tabs and not at_once:
            self.select(tab)
        self.changed()

    # ------------------------------------------------------- print, PDF --
    def print_page(self, tab):
        """The page on paper (or to any printer Windows has), as Chrome's
        Print does - a page's own print button lands here too."""
        if tab is None or tab.page is None or tab.web is None or not tab.url or self._printing is not None:
            return
        from PySide6.QtPrintSupport import QPrintDialog, QPrinter
        printer = QPrinter(QPrinter.HighResolution)
        dialog = QPrintDialog(printer, self.window())
        dialog.setWindowTitle(tr("Print {title}").replace("{title}", tab.title or browser.site_of(tab.url)))
        if dialog.exec() != QPrintDialog.Accepted:
            return
        self._printing = printer              # kept until Chromium has drawn every page into it
        view = tab.web

        def finished(ok):
            self._printing = None
            try:
                view.printFinished.disconnect(finished)
            except (RuntimeError, TypeError):
                pass
            if not ok:
                self.emit("toast", {"text": tr("Printing didn't work"), "id": None, "info": True})
        view.printFinished.connect(finished)
        view.print(printer)

    def save_pdf(self, tab):
        if tab is None or tab.page is None or not tab.url:
            return
        name = browser.unique_name(self.downloads_folder(), (tab.title or browser.site_of(tab.url) or "page")[:80]
                                   + ".pdf")
        path, _ = QFileDialog.getSaveFileName(self.window(), tr("Save page as PDF"),
                                              os.path.join(self.downloads_folder(), name), "PDF (*.pdf)")
        if path:
            tab.page.printToPdf(path)

    def pdf_saved(self, tab, path, ok):
        self.emit("toast", {"text": (tr("Saved {name}") if ok else tr("Couldn't save {name}"))
                            .replace("{name}", os.path.basename(path)), "id": None, "info": True})

    # -------------------------------------------------------- permissions --
    _PERMISSION_KEYS = {
        "MediaAudioCapture": "mic", "MediaVideoCapture": "camera", "MediaAudioVideoCapture": "camera_mic",
        "DesktopVideoCapture": "screen", "DesktopAudioVideoCapture": "screen_audio",
        "Notifications": "notifications", "Geolocation": "location", "ClipboardReadWrite": "clipboard",
        "MouseLock": "mouse_lock",
    }

    def permission_requested(self, tab, permission):
        """A page wants the camera, microphone, screen, notifications... -
        asked once per site and remembered (Settings > Web, or the tab's
        menu, forgets it). A private tab's answer is kept only while it's open."""
        kind_name = str(permission.permissionType()).split(".")[-1]
        kind = self._PERMISSION_KEYS.get(kind_name)
        site = permission.origin().host() or browser.site_of(tab.url)
        if kind is None or not site:
            permission.deny()                 # local fonts and the like: never
            return
        site = site.lower()
        site = site[4:] if site.startswith("www.") else site
        remembered = self._session_permissions if tab.private else browser.clean_permissions(
            self.settings.get("permissions"))
        answer = remembered.get(site, {}).get(kind)
        if answer is None:
            answer = confirm(self.window(), tr("Allow {site}?").replace("{site}", site),
                             tr("{site} wants to {what}.").replace("{site}", site)
                             .replace("{what}", tr(browser.PERMISSION_KINDS[kind])),
                             ok="Allow", cancel="Block")
            remembered.setdefault(site, {})[kind] = bool(answer)
            if not tab.private:
                self.settings["permissions"] = browser.clean_permissions(remembered)
                self.settings.save()
        if answer:
            permission.grant()
            if kind in CAPTURE_KINDS and tab in self.tabs:
                tab.capturing.add(kind)
                self.changed()
        else:
            permission.deny()
            self.emit("toast", {"text": tr("Blocked {site} from using it - right-click its tab to change that")
                                .replace("{site}", site), "id": None, "info": True})

    @property
    def _session_permissions(self):
        if not hasattr(self, "_private_permissions"):
            self._private_permissions = {}
        return self._private_permissions

    def site_permissions(self, site):
        return browser.clean_permissions(self.settings.get("permissions")).get(site, {})

    def forget_permissions(self, site=None):
        """A site's answers (None: every site's) forgotten - it asks again."""
        kept = browser.clean_permissions(self.settings.get("permissions"))
        if site is None:
            kept = {}
        else:
            kept.pop(site, None)
        self.settings["permissions"] = kept
        self.settings.save()

    def choose_screen(self, tab, request):
        """A page allowed to share the screen asks which: a screen, or one window."""
        menu = QMenu(self)
        menu.addSection(tr("Share with {site}").replace("{site}", browser.site_of(tab.url) or tr("the page")))
        chosen = []
        for model, pick, whole in ((request.screensModel(), request.selectScreen, True),
                                   (request.windowsModel(), request.selectWindow, False)):
            for row in range(model.rowCount()):
                index = model.index(row, 0)
                name = str(model.data(index, Qt.DisplayRole) or "")
                action = menu.addAction(tr("Entire screen: {name}").replace("{name}", name) if whole else name)
                action.triggered.connect(lambda _c=False, p=pick, i=QModelIndex(index): chosen.append((p, i)))
            menu.addSeparator()
        menu.exec(QCursor.pos())
        menu.deleteLater()
        if chosen:
            pick, index = chosen[0]
            pick(index)
        else:
            request.cancel()
            tab.capturing.discard("screen")
            tab.capturing.discard("screen_audio")
            self.changed()

    def notified(self, notification):
        """A page's notification (it was allowed to): on the bar, and the
        taskbar flashes if Buddy isn't in front."""
        site = notification.origin().host() or tr("A page")
        text = notification.title() + (f" - {notification.message()}" if notification.message() else "")
        self.emit("toast", {"text": f"{site}: {text}"[:300], "id": None, "info": True})
        notification.show()
        if not self.window().isActiveWindow():
            QApplication.alert(self.window(), 3000)

    # ------------------------------------------------------- dark pages --
    def dark_mode(self):
        mode = self.settings.get("dark_pages", "off")
        return mode if mode in browser.DARK_PAGES else "off"

    def light_sites(self):
        return browser.clean_sites(self.settings.get("light_sites"))

    def _theme_is_dark(self):
        return self.ground_color().lightness() < 128

    def _darkening(self):
        mode = self.dark_mode()
        return mode == "on" or (mode == "theme" and self._theme_is_dark())

    def dark_for(self, url):
        """True if this page is drawn dark."""
        return self._darkening() and not browser.on_site(url, self.light_sites())

    def _apply_dark(self):
        """Every page as dark (or not) as the setting says, from now on -
        open pages redrawn by reloading them."""
        engine.set_dark(self._darkening())
        for tab in self.tabs:
            if tab.page is not None and not tab.asleep and tab.url:
                want = self.dark_for(tab.url)
                attribute = tab.page.settings().testAttribute(tab.page.settings().WebAttribute.ForceDarkMode)
                if attribute != want:
                    tab.page.set_dark(want)
                    tab._ground()
                    tab.page.triggerAction(QWebEnginePage.Reload)

    def keep_light(self, site, light):
        sites = self.light_sites()
        if light and site not in sites:
            sites.append(site)
        elif not light:
            sites = [s for s in sites if s != site and not site.endswith("." + s)]
        self.settings["light_sites"] = sites
        self.settings.save()
        self._apply_dark()

    # ---------------------------------------------------- music hot keys --
    def media_hotkey(self, action):
        """Command Center's Web music keys: "play_pause", "next", "previous" -
        on the tab playing sound (or the one paused last). Returns what to say."""
        playing = [t for t in self.tabs if self._playing(t) and not t.paused]
        paused = [t for t in self.tabs if self._paused(t)]
        if action == "play_pause":
            if not playing and not paused:
                return tr("Nothing's playing in the Web tab.")
            self.rail_media()
            return tr("Paused the Web tab") if playing else tr("Playing the Web tab again")
        tab = (playing or paused or [None])[0]
        if tab is None or tab.page is None:
            return tr("Nothing's playing in the Web tab.")
        event = {"next": "nexttrack", "previous": "previoustrack"}.get(action)
        if event is None:
            return ""
        tab.page.runJavaScript(f'window.dispatchEvent(new Event("buddy-media-{event}")); true',
                               QWebEngineScript.ScriptWorldId.ApplicationWorld, lambda _r: None)
        return (tr("Next track on {site}") if action == "next" else tr("Previous track on {site}")).replace(
            "{site}", browser.site_of(tab.url) or tr("the page"))

    # ------------------------------------------------- clearing the data --
    def show_clear_data(self):
        if self._clear_panel is not None:
            try:
                self._clear_panel.show()
                self._clear_panel.raise_()
                self._clear_panel.activateWindow()
                return
            except RuntimeError:
                self._clear_panel = None
        from pages.web.clear_window import ClearWindow
        panel = ClearWindow(self)
        panel.destroyed.connect(lambda: setattr(self, "_clear_panel", None))
        self._clear_panel = panel
        panel.show()

    def clear_data(self, span, what):
        """Clear browsing data: `span` "hour", "day", "week" or "all";
        `what` some of history, sites (the address bar's), closed (Reopen
        closed tab), downloads (the list - the files stay), cookies (sign-
        ins and what sites stored), cache, permissions. Cookies, the cache
        and permissions go whole, whatever the span. In the profile in use."""
        what = set(what)
        since = None if span == "all" else time.time() - history_log.RANGES[span]
        if "history" in what:
            self.visits.clear(span)
            self.visits.save()
            self._refresh_history()
        if "sites" in what:
            sites = self.settings.get("sites") if isinstance(self.settings.get("sites"), dict) else {}
            self.settings["sites"] = {} if since is None else {
                k: v for k, v in sites.items() if not (isinstance(v, list) and len(v) >= 3
                                                       and isinstance(v[2], (int, float)) and v[2] >= since)}
        if "closed" in what:
            self._closed = []
        if "downloads" in what:
            self.history = [r for r in self.history if r["state"] == "active"
                            or (since is not None and r["time"] < since)]
        if "cookies" in what:
            engine.clear_site_data()
        elif "cache" in what:
            engine.profile().clearHttpCache()
        if "permissions" in what:
            self.forget_permissions()
        self.settings.save()
        self._save_timer.start()
        self.changed()

    # ------------------------------------------------------------ history --
    def went(self, tab):
        """A tab's page moved to an address: a visit, unless it's private."""
        if not tab.private and tab.url and self.visits.add(tab.url, "") is not None:
            self._visits_changed()

    def titled(self, tab):
        if not tab.private and tab.url and tab.title:
            self.visits.retitle(tab.url, tab.title)
            self._visits_changed()

    def _visits_changed(self):
        if self.visits.dirty:
            self._visits_timer.start()
            self._refresh_history()

    def _refresh_history(self):
        if self._history_panel is not None:
            try:
                self._history_panel.refresh()
            except RuntimeError:              # the window has gone
                self._history_panel = None

    def history_view(self, query="", limit=300):
        """The History window's list: newest first, `limit` at a time."""
        entries, more = self.visits.search(query, limit)
        return {"items": [dict(e, site=browser.site_of(e["url"])) for e in entries], "more": more,
                "query": query, "profile": self.current_profile()["name"] if len(self.profiles) > 1 else ""}

    def show_history(self):
        if self._history_panel is not None:
            try:
                self._history_panel.show()
                self._history_panel.raise_()
                self._history_panel.activateWindow()
                return
            except RuntimeError:
                self._history_panel = None
        panel = HistoryWindow(self)
        panel.destroyed.connect(lambda: setattr(self, "_history_panel", None))
        self._history_panel = panel
        panel.show()

    def open_from_history(self, url, here=False):
        if here and self.active is not None and not self.active.private and not self._is_popped(self.active):
            if self.active.web is None:       # asleep since Buddy started: straight to this page
                self.active.url = url
                self.active.saved_history = ""
                self.select(self.active)
            else:
                self.select(self.active)
                self.active.page.load(QUrl(url))
        else:
            self.open_tab(url)
        self.show_me()

    def show_me(self):
        """The Web tab in front - something opened from one of its windows
        while Buddy shows another tool would otherwise seem to do nothing."""
        switch = getattr(self.host, "switch_tool", None)
        if callable(switch) and not self.isVisible():
            try:
                switch(self.tool_id)
            except Exception:  # noqa: BLE001 - a host that can't
                pass
        window = self.window()
        if window is not None:
            window.raise_()
            window.activateWindow()

    def remove_history(self, ids):
        self.visits.remove(ids)
        self._visits_timer.start()
        self._refresh_history()


    # ----------------------------------------------------------- profiles --
    def current_profile(self):
        return next((prof for prof in self.profiles if prof["id"] == self.profile_id), self.profiles[0])

    def on_profiles(self, _payload=None):
        """The profile button on the bar: the same choices as the menu's Profile."""
        menu = QMenu(self)
        self._profile_menu(menu)
        menu.exec(QCursor.pos())
        menu.deleteLater()

    def _profile_menu(self, menu):
        for prof in self.profiles:
            action = menu.addAction(prof["name"], lambda pid=prof["id"]: self.switch_profile(pid))
            action.setCheckable(True)
            action.setChecked(prof["id"] == self.profile_id)
        menu.addSeparator()
        menu.addAction(tr("Add a profile…"), self.add_profile).setEnabled(len(self.profiles) < browser.MAX_PROFILES)
        name = self.current_profile()["name"]
        menu.addAction(tr("Rename \u201c{name}\u201d…").replace("{name}", name), self.rename_profile)
        menu.addAction(tr("Delete \u201c{name}\u201d…").replace("{name}", name),
                       self.delete_profile).setEnabled(len(self.profiles) > 1)

    def _save_profiles(self):
        self.settings["profiles"] = [dict(prof) for prof in self.profiles]
        self.settings["profile"] = self.profile_id
        self.settings.save()

    def add_profile(self):
        name = prompt(self.window(), "Add a profile",
                      "A profile has its own sign-ins, tabs and history - one for work and one for home, say.",
                      placeholder=tr("Work"), ok="Add")
        name = browser.clean_profile_name(name) if name is not None else ""
        if not name or len(self.profiles) >= browser.MAX_PROFILES:
            return
        prof = {"id": browser.new_profile_id(self.profiles), "name": name,
                "color": browser.next_profile_color(self.profiles)}
        self.profiles.append(prof)
        self._save_profiles()
        self.switch_profile(prof["id"])

    def rename_profile(self):
        prof = self.current_profile()
        name = prompt(self.window(), "Rename profile", "What should this profile be called?", value=prof["name"],
                      ok="Rename")
        name = browser.clean_profile_name(name) if name is not None else ""
        if name and name != prof["name"]:
            prof["name"] = name
            self._save_profiles()
            self.changed()
            self._refresh_history()

    def delete_profile(self):
        """The profile in use, with its sign-ins, tabs and history - after
        switching to another."""
        if len(self.profiles) < 2:
            return
        prof = self.current_profile()
        if not confirm(self.window(), "Delete profile",
                       tr("Delete \u201c{name}\u201d? You'll be signed out of every site in it, and its tabs and "
                          "history are removed from this PC. Downloads stay where they are.")
                       .replace("{name}", prof["name"]), ok="Delete", danger=True):
            return
        other = next(p_["id"] for p_ in self.profiles if p_["id"] != prof["id"])
        self.switch_profile(other)
        self.profiles = [p_ for p_ in self.profiles if p_["id"] != prof["id"]]
        self.settings["profile_state"].pop(prof["id"], None)
        history_log.History(history_log.path_for(prof["id"])).delete_file()
        self._save_profiles()
        # Its pages are on their way out: the profile goes once they have.
        QTimer.singleShot(DROP_PROFILE_MS, lambda pid=prof["id"]: engine.delete_profile(pid))
        self.changed()

    def switch_profile(self, pid):
        """This profile's tabs put away (kept for when it's back), the
        other's brought back - its sign-ins, history and remembered sites
        with them. Private tabs belong to no profile and stay."""
        if pid == self.profile_id or pid not in [prof["id"] for prof in self.profiles]:
            return
        self._save_timer.stop()
        self._save()
        state = self.settings["profile_state"]
        state[self.profile_id] = {"tabs": self.settings.get("tabs", []), "active": self.settings.get("active", 0),
                                  "sites": self.settings.get("sites", {})}
        self._visits_timer.stop()
        self.visits.save()
        for tab in [t for t in self.tabs if not t.private]:
            self._put_away(tab)
        self.profile_id = pid
        engine.use_profile(pid)
        mine = state.pop(pid, {"tabs": [], "active": 0, "sites": {}})
        self.settings["tabs"], self.settings["active"] = mine["tabs"], mine["active"]
        self.settings["sites"] = mine["sites"]
        self.visits = history_log.History(history_log.path_for(pid))
        self._closed = []
        added = [self._add(saved["url"], saved["title"], saved["awake"], pinned=saved["pinned"],
                           history=saved["history"]) for saved in browser.clean_tabs(mine["tabs"])]
        if not added:
            added = [self._add("")]
        active = mine["active"]
        self.active = None
        self.select(added[active if 0 <= active < len(added) else 0])
        self._save_profiles()
        self.changed(save=True)
        self._refresh_history()
        self.emit("toast", {"text": tr("Switched to {name}").replace("{name}", self.current_profile()["name"]),
                            "id": None, "info": True})

    def _put_away(self, tab):
        """A tab taken away without being closed: not offered by Reopen closed tab."""
        if self._full is not None and self._full[0] is tab:
            self._leave_full(at_once=True)
        if self._is_popped(tab):
            self.bring_back(at_once=True)
        self.tabs.remove(tab)
        if tab is self.active:
            self.active = None
        if tab.web is not None:
            self.stack.removeWidget(tab.web)
            tab.web.deleteLater()

    # --------------------------------------------------------- the shell --
    def on_shown(self):
        self._start_lists()
        if self.active is not None:
            self.active.seen = time.time()
        self.changed()

    def on_theme_changed(self):
        self.bar.on_theme_changed()
        self.sleeping.on_theme_changed()
        ground = self.ground_color()
        for widget in [self.stack, self.blank, self.cover] + [t.web for t in self.tabs if t.web is not None]:
            _fill(widget, ground)
        self.suggest.update()
        self._redraw_start_pages()
        if self.dark_mode() == "theme":
            self._apply_dark()                # dark pages follow a dark theme

    def _redraw_start_pages(self):
        """New-tab pages showing: drawn again in the theme, search and region now set."""
        for tab in self.tabs:
            if tab.web is not None and not tab.url and not tab.asleep:
                tab.load()

    def on_app_quitting(self):
        self.bring_back(at_once=True)
        self.videos.close_all()
        self.ducker.stop()                    # the Web tab's volume put back first
        engine.save_sign_ins()
        self._save_timer.stop()
        self._save()
        self._visits_timer.stop()
        self.visits.save()

    # --------------------------------------------------------- settings --
    def settings_fields(self):
        s = self.settings
        engines = [(key, name) for key, (name, _url) in browser.SEARCH_ENGINES.items()]
        return [
            sf.heading("Tabs"),
            sf.select("sleep_after", "Put background tabs to sleep", self.sleep_minutes(),
                      list(browser.SLEEP_CHOICES.items()),
                      tooltip="A sleeping tab gives its memory back and reloads when you next open it."),
            sf.hint("A tab playing sound never sleeps, so music keeps going while you work in Resolve. Nor does a "
                    "tab you keep awake - right-click it."),
            sf.textarea("never_sleep", "Sites that never sleep", "\n".join(self.never_sleep()),
                        placeholder="music.youtube.com\nopen.spotify.com", rows=4,
                        hint_text="One site per line. A site covers everything under it."),
            *self._sound_fields(),
            sf.heading("Video"),
            sf.select("video_quality", "YouTube video quality", browser.video_quality(s.get("video_quality")),
                      list(browser.VIDEO_QUALITIES.items())),
            sf.hint("YouTube and YouTube Music play at this quality, or the nearest below it a video has - lower "
                    "uses less data and memory. You can still change one video's quality in YouTube's own menu. "
                    "Other sites choose their own."),
            sf.heading("Search"),
            sf.select("search", "Search with", s.get("search", browser.DEFAULT_ENGINE), engines, raw=True),
            sf.select("region", "Region", self.region(),
                      [(browser.AUTO_REGION, "Automatic")]
                      + sorted(((code, name) for code, (name, _kl, _lang) in browser.REGIONS.items()),
                               key=lambda item: tr(item[1])),
                      tooltip="Which country's results a search gives, and the region sites are told you're in. "
                              "Automatic leaves it to the search engine and the sites."),
            sf.check("suggest", "Finish addresses as you type", s.get("suggest", True),
                     hint_text="Type \"goo\" and the address bar offers google.com, with the pages you've been to "
                               "and the tabs you have open listed under it. Type @tabs first to look for an open tab."),
            sf.heading("Pages"),
            sf.select("dark_pages", "Dark pages", self.dark_mode(), list(browser.DARK_PAGES.items()),
                      tooltip="Darkens sites that only come in white - easier on the eyes in a dark edit suite."),
            sf.textarea("light_sites", "Sites never darkened", "\n".join(self.light_sites()),
                        placeholder="example.com", rows=3,
                        hint_text="One site per line, for a site that looks wrong darkened - or right-click its tab."),
            sf.select("spelling", "Check spelling in", self.spelling_choice(),
                      [(spelling.OFF, "Off")] + [(code, name) for code, (name, _file) in spelling.LANGUAGES.items()],
                      tooltip="Underlines misspelt words in what you type on pages. Right-click one for "
                              "suggestions."),
            sf.hint("The first time you pick a language, Buddy downloads its dictionary from Chrome's dictionary "
                    "server, as Chrome does."),
            sf.heading("Privacy"),
            sf.select("blocking", "Block", browser.blocking(s), list(browser.BLOCKING.items()),
                      tooltip="Pages load faster and use less memory with ads and trackers blocked."),
            *self._list_fields(),
            sf.check("youtube_ads", "Remove YouTube's video ads", bool(s.get("youtube_ads", False)),
                     hint_text="YouTube's ads come from YouTube itself, so the filter lists can't stop them. This "
                               "takes them out of the video data YouTube sends its player. YouTube works against it: "
                               "it may show its \"ad blockers aren't allowed\" message, or ads may come back until "
                               "Buddy is updated. Reload YouTube after turning it on or off."),
            sf.textarea("allow_ads", "Sites where nothing's blocked", "\n".join(self.allowed_sites()),
                        placeholder="example.com", rows=3,
                        hint_text="One site per line, for a site that doesn't work with its ads blocked - or "
                                  "right-click its tab."),
            sf.buttons(("Clear browsing data…", "clear_data",
                        {"tooltip": "History, the address bar's sites, cookies and sign-ins, the cache..."})),
            sf.heading("Site permissions"),
            sf.hint(self._permissions_text()),
            sf.buttons(("Forget every site's answers", "forget_permissions")),
            sf.heading("Downloads"),
            sf.text("downloads", "Save downloads to", self.downloads_folder(), browse="browse_downloads"),
            sf.check("auto_resolve", "Put downloaded video, audio and pictures into Resolve",
                     bool(s.get("auto_resolve", False)),
                     hint_text="Each one goes into the Downloads bin of the project open in Resolve as soon as "
                               "it's downloaded. Right-click a picture or video on a page to send just that one."),
        ]

    def _permissions_text(self):
        kept = browser.clean_permissions(self.settings.get("permissions"))
        if not kept:
            return tr("When a site wants your camera, microphone, screen, location or to show notifications, "
                      "Buddy asks you - and remembers what you said. No site has asked yet.")
        allowed = sorted(site for site, kinds in kept.items() if any(kinds.values()))
        blocked = sorted(site for site, kinds in kept.items() if not all(kinds.values()))
        parts = []
        if allowed:
            parts.append(tr("Allowed: {sites}").replace("{sites}", ", ".join(allowed[:12])))
        if blocked:
            parts.append(tr("Blocked: {sites}").replace("{sites}", ", ".join(blocked[:12])))
        return ". ".join(parts) + ". " + tr("Right-click a site's tab to forget just its answers.")

    def spelling_choice(self):
        choice = self.settings.get("spelling", spelling.OFF)
        return choice if choice in spelling.LANGUAGES else spelling.OFF

    def spelling(self):
        """The languages spell check uses now: the one chosen, once its dictionary is here."""
        choice = self.spelling_choice()
        return [choice] if choice != spelling.OFF and spelling.have(choice) else []

    def _fetch_dictionary(self, language, ui=None):
        if getattr(self, "_fetcher", None) is None:
            self._fetcher = spelling.Fetcher(self)
            self._fetcher.done.connect(self._dictionary_fetched)
        self._fetch_ui = ui
        self._fetcher.fetch(language)

    def _dictionary_fetched(self, language, error):
        ui = getattr(self, "_fetch_ui", None)
        if error:
            text, tone = tr("Couldn't download the dictionary: {why}").replace("{why}", error), "error"
        else:
            engine.set_spelling(self.spelling())
            text, tone = tr("Spell check is on."), "success"
        if ui is not None:
            try:
                ui.status(text, tone)
            except RuntimeError:              # Settings closed meanwhile
                pass

    def _list_fields(self):
        if browser.blocking(self.settings) != "strong":
            return []
        # Not read yet (LISTS_AFTER_MS): they will be shortly - and not now, slowing Settings as it opens.
        idle = self.lists.state == "idle"
        text, tone = (tr("Updating the filter lists…"), "") if idle else self.lists.status()
        return [
            sf.status("Filter lists", text, tone,
                      hint_text="EasyList, EasyPrivacy and uBlock Origin's lists, from their makers, updated "
                                f"every {filter_lists.UPDATE_DAYS} days. They block ads and trackers, and hide "
                                "the boxes ads leave behind."),
            sf.buttons(("Update the lists now", "update_lists")),
        ]

    def _sound_fields(self):
        if not audio_sessions.available:
            return [sf.heading("Sound"), sf.hint("Lowering the Web tab's sound works on Windows only for now.")]
        return [
            sf.heading("Sound"),
            sf.select("duck", "Lower the Web tab's sound", self.duck_mode(), list(ducking.MODES.items()),
                      tooltip="Music in the Web tab drops while Resolve plays your timeline, and comes back "
                              "when it stops."),
            sf.slider("duck_level", "Lower it to", self.duck_level(), 0, 100, ducking.DEFAULT_LEVEL,
                      {v: f"{v}%" for v in range(0, 101, ducking.LEVEL_STEP)}, step=ducking.LEVEL_STEP,
                      hint_text="How loud the Web tab stays while it's lowered: 0% is silent, 100% leaves it "
                                "as it is."),
        ]

    def on_setting(self, key, value, ui):
        if key == "sleep_after":
            try:
                value = int(value)
            except (TypeError, ValueError):
                return
            if value in browser.SLEEP_CHOICES:
                self.settings["sleep_after"] = value
        elif key == "never_sleep":
            self.settings["never_sleep"] = browser.clean_sites(value)
        elif key == "search" and value in browser.SEARCH_ENGINES:
            self.settings["search"] = value
        elif key == "region" and (value in browser.REGIONS or value == browser.AUTO_REGION):
            self.settings["region"] = value
            self._apply_region()
        elif key == "duck" and value in ducking.MODES:
            self.settings["duck"] = value
            self._apply_ducking()
        elif key == "duck_level":
            value = ducking.clean_level(value)
            if value is None:
                return
            self.settings["duck_level"] = value
            self._apply_ducking()
        elif key == "video_quality" and value in browser.VIDEO_QUALITIES:
            self.settings["video_quality"] = value
            self._apply_video_quality()
        elif key == "suggest":
            self.settings["suggest"] = bool(value)
            if not value:
                self.settings["sites"] = {}
        elif key == "blocking" and value in browser.BLOCKING:
            self.settings["blocking"] = value
            self._apply_blocking()
            ui.refresh()
        elif key == "youtube_ads":
            self.settings["youtube_ads"] = bool(value)
            engine.set_youtube_ads(bool(value))
        elif key == "allow_ads":
            self.settings["allow_ads"] = browser.clean_sites(value)
            self._apply_blocking()
        elif key == "dark_pages" and value in browser.DARK_PAGES:
            self.settings["dark_pages"] = value
            self.settings.save()
            self._apply_dark()
        elif key == "light_sites":
            self.settings["light_sites"] = browser.clean_sites(value)
            self.settings.save()
            self._apply_dark()
        elif key == "spelling" and (value == spelling.OFF or value in spelling.LANGUAGES):
            self.settings["spelling"] = value
            engine.set_spelling(self.spelling())
            if value != spelling.OFF and not spelling.have(value):
                self._fetch_dictionary(value, ui)
                ui.status("Downloading the dictionary…", "")
        elif key == "auto_resolve":
            self.settings["auto_resolve"] = bool(value)
        elif key == "downloads" and isinstance(value, str):
            folder = value.strip().strip('"')
            if folder and not os.path.isdir(folder):
                ui.alert("Downloads", "That folder isn't there. Choose one with Browse.")
                return
            self.settings["downloads"] = folder
        else:
            return
        self.settings.save()
        if key in ("search", "region"):
            self._redraw_start_pages()
        self.changed()

    def on_settings_action(self, action, ui):
        if action == "browse_downloads":
            folder = QFileDialog.getExistingDirectory(ui.parent(), tr("Save downloads to"), self.downloads_folder())
            if folder:
                self.settings["downloads"] = os.path.normpath(folder)
                self.settings.save()
                ui.refresh()
        elif action == "update_lists":
            self.lists.start(force=True)
            # Once, when they're done: Settings may have been closed by then.
            self.lists.changed.connect(lambda: _refresh(ui), Qt.SingleShotConnection)
            ui.status("Updating the filter lists…", "")
        elif action == "forget_sites":
            self.forget_sites()
            ui.status("Forgotten.", "success")
        elif action == "clear_data":
            self.show_clear_data()
        elif action == "forget_permissions":
            if ui.confirm("Site permissions", "Forget every site's answers? Each asks again next time.",
                          ok="Forget", danger=True):
                self.forget_permissions()
                ui.refresh()
