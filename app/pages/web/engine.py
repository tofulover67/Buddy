#!/usr/bin/env python3
"""
The Web tab's engine: its own browser profile, the ad and tracker filter,
a tab's page, and how much memory the pages use. Qt WebEngine - the
Chromium Buddy's own windows are drawn with, so the browser adds nothing
to install.

The profile is the browser's alone, kept apart from Buddy's own pages
(core/web_page.py): it remembers logins and cookies between runs, under
~/.buddy/web, and Buddy's pages and the web's never share a process. Its
cookies - the user's sign-ins - are kept encrypted (cookie_vault.py), not
in the plain database Qt WebEngine keeps by itself.

There can be more than one (the Web tab's profiles, one used at a time):
each signed in to its own accounts, with its own cookies, cache and what
sites stored - the first in ~/.buddy/web as ever, the rest in
~/.buddy/web-profiles/<id>. use_profile() picks the one new pages go in.
"""

import ctypes
import json
import os
import re
import secrets
import shutil
import sys
import time

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWebEngineCore import (QWebEnginePage, QWebEngineProfile, QWebEngineScript, QWebEngineSettings,
                                     QWebEngineUrlRequestInfo, QWebEngineUrlRequestInterceptor)
from PySide6.QtWidgets import QApplication

from core.message_dialog import alert, confirm, prompt
from core.settings_store import BUDDY_DIR
from pages.web import browser, cookie_vault

PROFILE_DIR = os.path.join(BUDDY_DIR, "web")              # the first profile's ("default")
PROFILES_DIR = os.path.join(BUDDY_DIR, "web-profiles")    # every other profile's: <id>/
DEFAULT_PROFILE = "default"
# Left by delete_profile: the folder goes as Buddy next starts.
DELETE_MARK = "delete-on-next-start"
# Left by clear_site_data: the whole profile folder goes as Buddy next
# starts, before the browser opens it (while it runs, it holds its files).
WIPE_MARK = "clear-on-next-start"
CACHE_BYTES = 256 * 1024 * 1024

# Schemes a page may open itself; the rest (mailto:, zoommtg:, ...) go to
# whatever on this PC handles them.
WEB_SCHEMES = ("http", "https", "about", "data", "blob", "file", "qrc")

# What Qt WebEngine asks when a page wants to be sure before it's left
# (onbeforeunload - YouTube Music while it plays, a half-filled form).
LEAVE_PAGE = "Are you sure you want to leave this page? Changes that you made may not be saved."


# Chrome's "which browser are you" headers, which Firefox never sends:
# emptied on Google's sign-in page (they can't be taken off altogether).
CLIENT_HINTS = (b"Sec-CH-UA", b"Sec-CH-UA-Mobile", b"Sec-CH-UA-Platform", b"Sec-CH-UA-Platform-Version",
                b"Sec-CH-UA-Full-Version", b"Sec-CH-UA-Full-Version-List", b"Sec-CH-UA-Arch",
                b"Sec-CH-UA-Bitness", b"Sec-CH-UA-Model", b"Sec-CH-UA-WoW64", b"Sec-CH-UA-Form-Factors")


# What kind of request it is, in the filter lists' words.
_RT = QWebEngineUrlRequestInfo.ResourceType
_KINDS = {getattr(_RT, f"ResourceType{name}"): kind for name, kind in (
    ("MainFrame", "document"), ("NavigationPreloadMainFrame", "document"), ("SubFrame", "subdocument"),
    ("NavigationPreloadSubFrame", "subdocument"), ("Stylesheet", "stylesheet"), ("Script", "script"),
    ("Worker", "script"), ("SharedWorker", "script"), ("ServiceWorker", "script"), ("Image", "image"),
    ("Favicon", "image"), ("FontResource", "font"), ("Object", "object"), ("PluginResource", "object"),
    ("Media", "media"), ("Xhr", "xmlhttprequest"), ("Json", "xmlhttprequest"), ("Ping", "ping"),
    ("WebSocket", "websocket")) if hasattr(_RT, f"ResourceType{name}")}


class TrackerFilter(QWebEngineUrlRequestInterceptor):
    """Refuses requests to ad and tracking domains from other sites' pages
    (browser.is_tracker) and, blocking "strong", what the filter lists
    block (filters.Filters) - never on a site the user allows ads on - and
    on Google's sign-in page says the browser is Firefox
    (browser.signs_in_as_firefox). Runs on Chromium's network thread, so
    it only reads its attributes (swapped whole from the GUI thread), and
    counts."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.mode = browser.DEFAULT_BLOCKING   # browser.BLOCKING
        self.filters = None                    # filters.Filters, once the lists are read
        self.allowed = frozenset()             # sites ads are allowed on
        self.blocked = 0
        self.agent = browser.firefox_user_agent().encode()

    @property
    def enabled(self):
        return self.mode != "off"

    @enabled.setter
    def enabled(self, on):
        self.mode = browser.DEFAULT_BLOCKING if on else "off"

    def blocks(self, url, host, site, kind):
        """True if this request is refused."""
        if self.mode == "off" or browser.allowed_site(site, self.allowed):
            return False
        if kind != "document" and browser.is_tracker(host, site):
            return True
        filters = self.filters
        return self.mode == "strong" and filters is not None and filters.matches(url, host, site, kind) is not None

    def interceptRequest(self, info):
        url = info.requestUrl()
        host = url.host()
        if self.blocks(url.toString(), host, info.firstPartyUrl().host(), _KINDS.get(info.resourceType(), "other")):
            info.block(True)
            self.blocked += 1
            return
        if browser.signs_in_as_firefox(host):
            info.setHttpHeader(b"User-Agent", self.agent)
            for name in CLIENT_HINTS:
                info.setHttpHeader(name, b"")


_profiles = {}         # profile id -> QWebEngineProfile, made when first used
_vaults = {}           # profile id -> its CookieVault
_current = DEFAULT_PROFILE
_made_hooks = []       # called with each profile as it's made (the browser's downloads)
_retired = []          # deleted profiles, kept unused until Buddy closes (delete_profile)
_swept = False
_quality = browser.DEFAULT_VIDEO_QUALITY
QUALITY_SCRIPT = "buddy-video-quality"
_youtube_ads = False
YOUTUBE_ADS_SCRIPT = "buddy-youtube-ads"
_private = None
_filter = None
_default_language = ""
_accept = None


def _set_up(made):
    """What both profiles share: plain Chrome to the sites (some refuse to
    sign in to anything that says it's an embedded browser) - Firefox to
    Google's sign-in page - the same page settings and the same filter."""
    made.setHttpUserAgent(re.sub(r"\s*QtWebEngine/\S+", "", made.httpUserAgent()))
    s = made.settings()
    for attribute, on in ((QWebEngineSettings.FullScreenSupportEnabled, True),
                          (QWebEngineSettings.JavascriptCanOpenWindows, False),
                          (QWebEngineSettings.JavascriptCanAccessClipboard, False),
                          (QWebEngineSettings.PluginsEnabled, False),
                          (QWebEngineSettings.PdfViewerEnabled, True),
                          (QWebEngineSettings.PlaybackRequiresUserGesture, False),
                          (QWebEngineSettings.DnsPrefetchEnabled, False),
                          (QWebEngineSettings.ScrollAnimatorEnabled, True),
                          (QWebEngineSettings.LocalContentCanAccessRemoteUrls, False)):
        s.setAttribute(attribute, on)
    made.setUrlRequestInterceptor(_filter)
    # Buddy asks and remembers (page.py permission_requested): Qt's own
    # memory of an answer would never let the user change their mind.
    if hasattr(made, "setPersistentPermissionsPolicy"):
        made.setPersistentPermissionsPolicy(QWebEngineProfile.PersistentPermissionsPolicy.AskEveryTime)
    if hasattr(made, "setNotificationPresenter"):
        made.setNotificationPresenter(_present_notification)
    s.setAttribute(QWebEngineSettings.ForceDarkMode, _dark)
    # The page's own script hears Firefox too, on the sign-in page.
    script = QWebEngineScript()
    script.setName("buddy-sign-in")
    script.setInjectionPoint(QWebEngineScript.DocumentCreation)
    script.setWorldId(QWebEngineScript.MainWorld)
    script.setRunsOnSubFrames(True)
    script.setSourceCode(browser.firefox_script(_filter.agent.decode()))
    made.scripts().insert(script)
    _put_quality_script(made)
    _put_youtube_ads_script(made)
    _put_video_script(made)
    _put_media_script(made)
    _put_spelling(made)


# A page's notification (one it was allowed to show): handed to the
# browser, which shows it on its bar - set by page.py.
_notify = None


def on_notification(hook):
    global _notify
    _notify = hook


def _present_notification(notification):
    if _notify is not None:
        _notify(notification)


# A page's own media controls (navigator.mediaSession - YouTube Music's,
# Spotify's next and previous track), kept where Buddy can reach them: its
# hot keys fire them with an event named for the action (media_action).
MEDIA_SCRIPT = "buddy-media-session"
_MEDIA_JS = """(() => {
  if (!window.MediaSession || window.__buddyMedia) return;
  window.__buddyMedia = true;
  const handlers = {};
  const set = MediaSession.prototype.setActionHandler;
  MediaSession.prototype.setActionHandler = function (action, handler) {
    handlers[action] = handler;
    return set.call(this, action, handler);
  };
  for (const action of ["play", "pause", "nexttrack", "previoustrack"]) {
    window.addEventListener("buddy-media-" + action, () => {
      try { if (handlers[action]) handlers[action]({action}); } catch (e) {}
    });
  }
})();"""


def _put_media_script(made):
    for old in made.scripts().find(MEDIA_SCRIPT):
        made.scripts().remove(old)
    script = QWebEngineScript()
    script.setName(MEDIA_SCRIPT)
    script.setInjectionPoint(QWebEngineScript.DocumentCreation)
    script.setWorldId(QWebEngineScript.MainWorld)         # the page's own handlers
    script.setRunsOnSubFrames(False)
    script.setSourceCode(_MEDIA_JS)
    made.scripts().insert(script)


# Pages darkened (browser.DARK_PAGES): the default for every page, which
# page.py turns off for a site the user keeps light (TabPage.set_dark).
_dark = False


def set_dark(on):
    global _dark
    _dark = bool(on)
    for made in _every_profile():
        made.settings().setAttribute(QWebEngineSettings.ForceDarkMode, _dark)


# Spelling: Chromium's own spell checker, with Chrome's dictionaries
# (pages/web/spelling.py fetches them into the folder Qt WebEngine reads -
# core/web_flags.py on Windows, main.py on macOS). None there: no spell check.
_spelling = []


def spelling_languages():
    """The dictionaries there are: ["en-US", ...]."""
    from pages.web import spelling
    try:
        return sorted(name[:-5] for name in os.listdir(spelling.folder()) if name.endswith(".bdic"))
    except OSError:
        return []


def set_spelling(languages):
    """Spell check in what's typed on pages, in these languages - [] off."""
    global _spelling
    have = spelling_languages()
    _spelling = [lang for lang in languages if lang in have]
    for made in _every_profile():
        _put_spelling(made)


def _put_spelling(made):
    if _spelling:
        made.setSpellCheckLanguages(_spelling)
    made.setSpellCheckEnabled(bool(_spelling))


# Chromium here can't decode H.264 or AAC; video_fallback.js notices a video
# that failed for it and says so on the console, behind this secret (a page
# can't guess it, so it can't make Buddy open a video). TabPage hears it.
VIDEO_TOKEN = secrets.token_hex(12)
VIDEO_SCRIPT = "buddy-video-fallback"
_VIDEO_JS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "video_fallback.js")


def _put_video_script(made):
    for old in made.scripts().find(VIDEO_SCRIPT):
        made.scripts().remove(old)
    try:
        with open(_VIDEO_JS, encoding="utf-8") as fh:
            source = fh.read().replace("__TOKEN__", VIDEO_TOKEN)
    except OSError:
        return
    script = QWebEngineScript()
    script.setName(VIDEO_SCRIPT)
    script.setInjectionPoint(QWebEngineScript.DocumentReady)
    script.setWorldId(QWebEngineScript.ApplicationWorld)       # out of the page's reach
    script.setRunsOnSubFrames(True)                            # players embedded in other sites too
    script.setSourceCode(source)
    made.scripts().insert(script)


def _put_quality_script(made):
    for old in made.scripts().find(QUALITY_SCRIPT):
        made.scripts().remove(old)
    script = QWebEngineScript()
    script.setName(QUALITY_SCRIPT)
    script.setInjectionPoint(QWebEngineScript.DocumentReady)
    script.setWorldId(QWebEngineScript.MainWorld)         # YouTube's player is the page's own
    script.setRunsOnSubFrames(True)                       # its players embedded in other sites too
    script.setSourceCode(browser.youtube_quality_script(_quality))
    made.scripts().insert(script)


def _put_youtube_ads_script(made):
    for old in made.scripts().find(YOUTUBE_ADS_SCRIPT):
        made.scripts().remove(old)
    if not _youtube_ads:
        return
    script = QWebEngineScript()
    script.setName(YOUTUBE_ADS_SCRIPT)
    # Before any of YouTube's own script runs, and in its world: it's
    # YouTube's own JSON.parse and fetch that hand the player its data.
    script.setInjectionPoint(QWebEngineScript.DocumentCreation)
    script.setWorldId(QWebEngineScript.MainWorld)
    script.setRunsOnSubFrames(True)                       # YouTube's players embedded in other sites
    script.setSourceCode(browser.youtube_ads_script())
    made.scripts().insert(script)


def _every_profile():
    return [p for p in list(_profiles.values()) + [_private] if p is not None]


def set_youtube_ads(on):
    """YouTube's video ads taken out (browser.youtube_ads_script) from the
    next page YouTube loads, in every profile."""
    global _youtube_ads
    _youtube_ads = bool(on)
    for made in _every_profile():
        _put_youtube_ads_script(made)


def set_video_quality(choice):
    """YouTube's quality from now on, in every profile (pages already open:
    page.py runs the script in them)."""
    global _quality
    _quality = browser.video_quality(choice)
    for made in _every_profile():
        _put_quality_script(made)


def profile_dir(pid):
    return PROFILE_DIR if pid == DEFAULT_PROFILE else os.path.join(PROFILES_DIR, pid)


def use_profile(pid):
    """The profile new pages go in from now on (made if it hasn't been)."""
    global _current
    _current = pid
    return profile()


def current_profile():
    return _current


def on_profile_made(hook):
    """hook(profile) for every profile, those made already and those to come."""
    _made_hooks.append(hook)
    for made in _profiles.values():
        hook(made)


def profile(pid=None):
    """A profile of the browser's - the one in use unless asked for
    another - made once and parented to the application, which outlives
    every page using it."""
    global _filter, _default_language
    pid = pid or _current
    made = _profiles.get(pid)
    if made is None:
        _sweep_deleted()
        folder = profile_dir(pid)
        wipe_if_asked(folder)
        os.makedirs(folder, exist_ok=True)
        made = QWebEngineProfile("buddy-web" if pid == DEFAULT_PROFILE else f"buddy-web-{pid}",
                                 QApplication.instance())
        made.setPersistentStoragePath(folder)
        made.setCachePath(os.path.join(folder, "cache"))
        made.setHttpCacheType(QWebEngineProfile.DiskHttpCache)
        made.setHttpCacheMaximumSize(CACHE_BYTES)
        made.setPersistentCookiesPolicy(QWebEngineProfile.NoPersistentCookies if cookie_vault.available
                                        else QWebEngineProfile.AllowPersistentCookies)
        if _filter is None:
            _default_language = made.httpAcceptLanguage()
            _filter = TrackerFilter(QApplication.instance())
        _set_up(made)
        made.setHttpAcceptLanguage(_accept or _default_language)
        _profiles[pid] = made
        if cookie_vault.available:
            vault = cookie_vault.CookieVault(made, folder)
            vault.load()
            _vaults[pid] = vault
        for hook in _made_hooks:
            hook(made)
    return made


def delete_profile(pid):
    """A profile gone, with all it holds: signed out and its cache cleared
    now, its folder removed as Buddy next starts (while it runs, Chromium
    holds its files). The profile object itself is kept to the end of the
    run, unused: a QWebEngineProfile deleted while Buddy runs corrupted
    the heap. Its pages must be gone first, and it can't be the one in use."""
    if pid == _current:
        raise ValueError("the profile in use can't be deleted")
    vault = _vaults.pop(pid, None)
    if vault is not None:
        vault.forget()
    made = _profiles.pop(pid, None)
    if made is not None:
        made.cookieStore().deleteAllCookies()
        made.clearHttpCache()
        made.clearAllVisitedLinks()
        _retired.append(made)
    folder = profile_dir(pid)
    try:
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, DELETE_MARK), "w", encoding="utf-8") as fh:
            fh.write("Buddy deletes this profile's folder when it next starts.\n")
    except OSError:
        pass


def _sweep_deleted():
    """Once a run, before any profile opens: the folders of deleted profiles."""
    global _swept
    if _swept:
        return
    _swept = True
    folders = [PROFILE_DIR]
    try:
        folders += [os.path.join(PROFILES_DIR, name) for name in os.listdir(PROFILES_DIR)]
    except OSError:
        pass
    for folder in folders:
        if os.path.exists(os.path.join(folder, DELETE_MARK)):
            shutil.rmtree(folder, ignore_errors=True)


def save_sign_ins():
    """Now, not in a moment: Buddy is closing."""
    for vault in _vaults.values():
        vault.save()


def private_profile(on_download=None):
    """Private tabs' profile: kept in memory only, shared by every private
    tab (signed in in one, signed in in all, as in Chrome), and thrown away
    with all it holds once the last of them closes (drop_private)."""
    global _private
    if _private is None:
        profile()
        _private = QWebEngineProfile(QApplication.instance())       # no name: off the record
        _set_up(_private)
        _private.setHttpAcceptLanguage(_accept or _default_language)
        if on_download is not None:
            _private.downloadRequested.connect(on_download)
    return _private


def has_private():
    return _private is not None


def drop_private():
    """Forgets everything the private tabs did. Their pages must be gone
    first - a page can't outlive its profile."""
    global _private
    if _private is not None:
        _private.deleteLater()
        _private = None


def tracker_filter():
    profile()
    return _filter


def set_languages(accept):
    """What sites are told the user reads (browser.accept_language), or
    None to go back to the system's own."""
    global _accept
    _accept = accept
    profile()
    for made in _every_profile():
        made.setHttpAcceptLanguage(accept or _default_language)


def clear_site_data():
    """Signs out of every site, in the profile in use: cookies and the
    cache now, and everything else sites stored (their storage, databases,
    background workers, what they asked the browser to remember) as Buddy
    next starts - the profile as new as a private tab's."""
    p = profile()
    p.cookieStore().deleteAllCookies()
    if _current in _vaults:
        _vaults[_current].forget()
    p.clearHttpCache()
    p.clearAllVisitedLinks()
    try:
        with open(os.path.join(profile_dir(_current), WIPE_MARK), "w", encoding="utf-8") as fh:
            fh.write("Buddy clears this folder when it next starts.\n")
    except OSError:
        pass


def wipe_if_asked(folder):
    """Empties `folder` if clear_site_data asked for it. What can't be
    removed (a file still open somewhere) is left; True if it was asked."""
    if not os.path.exists(os.path.join(folder, WIPE_MARK)):
        return False
    for name in os.listdir(folder):
        path = os.path.join(folder, name)
        try:
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path, ignore_errors=True)
            else:
                os.remove(path)
        except OSError:
            pass
    return True


HIDE_SCRIPT = "buddy-hide-ads"
# The page's hiding stylesheet (filters.Filters.hiding_css), added as the
# document is made - before any of it is drawn - and again once it's
# loaded, should the page have replaced the document's sheets meanwhile.
_HIDE_JS = """(() => {
  const css = %s;
  let sheet = null;
  const add = () => {
    try {
      if (sheet && document.adoptedStyleSheets.includes(sheet)) return;
      sheet = sheet || new CSSStyleSheet();
      if (!sheet.cssRules.length) sheet.replaceSync(css);
      document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];
    } catch (e) {
      const style = document.createElement('style');
      style.textContent = css;
      (document.head || document.documentElement).appendChild(style);
    }
  };
  add();
  document.addEventListener('DOMContentLoaded', add, {once: true});
})();"""


def hiding_css(url):
    """The stylesheet hiding a page's ad boxes, "" for none."""
    f = _filter
    if f is None or f.mode != "strong" or f.filters is None:
        return ""
    from urllib.parse import urlsplit
    if browser.allowed_site(urlsplit(url).hostname, f.allowed):
        return ""
    return f.filters.hiding_css(url)


# A page's pop-ups (alert, confirm, prompt) one straight after another -
# within DIALOG_GAP_S of the last closing - this many times: Buddy offers
# to stop them, or a page looping alert() would leave no way out but
# quitting (each is a dialog over Buddy's window).
DIALOG_STREAK = 3
DIALOG_GAP_S = 2.0


class TabPage(QWebEnginePage):
    """One tab's page, in the private profile for a private tab. New
    windows a page opens become tabs (a middle-click's in the background);
    schemes that aren't the web's open in the app that handles them; a
    request for the camera, microphone, location or notifications is asked
    of the user (page.py permission_requested)."""

    def __init__(self, tab, parent):
        super().__init__(private_profile(tab.browser.download_requested) if tab.private else profile(), parent)
        self.tab = tab
        self.asking = False                   # a Leave / Stay question is on screen
        self._streak = 0                      # pop-ups one after another (DIALOG_STREAK)
        self._closed_at = 0.0
        self.quiet_site = ""                  # the site whose pop-ups the user stopped
        if hasattr(self, "permissionRequested"):
            self.permissionRequested.connect(lambda permission: tab.browser.permission_requested(tab, permission))
        if hasattr(self, "desktopMediaRequested"):
            self.desktopMediaRequested.connect(lambda request: tab.browser.choose_screen(tab, request))
        self.printRequested.connect(lambda: tab.browser.print_page(tab))

    def createWindow(self, kind):
        background = kind == QWebEnginePage.WebWindowType.WebBrowserBackgroundTab
        made = self.tab.browser.open_tab_for_page(self.tab, background=background)
        return made.page if made is not None else None

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        if url.host().lower() == browser.START_HOST:
            # The new-tab page's own + and x (browser.start_page) - only
            # from that page, never from a site's.
            if is_main_frame and not self.tab.url and self.url().toString() in ("", "about:blank"):
                action = url.toString()
                QTimer.singleShot(0, lambda: self.tab.browser.start_page_action(self.tab, action))
            return False
        if url.scheme().lower() not in WEB_SCHEMES:
            if is_main_frame and nav_type == QWebEnginePage.NavigationTypeLinkClicked:
                QDesktopServices.openUrl(url)
            return False
        if is_main_frame and url.scheme().lower() in ("http", "https"):
            self.prepare_hiding(url.toString())
            self.set_dark(self.tab.browser.dark_for(url.toString()))
        return True

    def set_dark(self, on):
        """This page darkened or not (browser.DARK_PAGES), from its next drawing."""
        self.settings().setAttribute(QWebEngineSettings.ForceDarkMode, bool(on))

    def prepare_hiding(self, url):
        """The document this navigation makes gets its page's hiding
        stylesheet (none for frames: theirs are the ads)."""
        scripts = self.scripts()
        for old in scripts.find(HIDE_SCRIPT):
            scripts.remove(old)
        css = hiding_css(url)
        if css:
            script = QWebEngineScript()
            script.setName(HIDE_SCRIPT)
            script.setInjectionPoint(QWebEngineScript.DocumentCreation)
            script.setWorldId(QWebEngineScript.ApplicationWorld)    # the page can't see or undo it
            script.setRunsOnSubFrames(False)
            script.setSourceCode(_HIDE_JS % json.dumps(css))
            scripts.insert(script)

    # A page's own pop-ups, drawn as Buddy's dialogs rather than bare Qt
    # boxes, and over Buddy's window. What the site says is its own text.
    def _site(self, origin):
        return origin.host() or browser.site_of(self.url().toString()) or "This page"

    def _parent(self):
        return self.tab.web.window() if self.tab.web is not None else None

    def _may_ask(self, origin):
        """False once the user has stopped this page's pop-ups; asks them
        when the page keeps showing one after another."""
        here = browser.site_of(self.url().toString())
        if self.quiet_site and self.quiet_site == here:
            return False
        self.quiet_site = ""
        now = time.monotonic()
        self._streak = self._streak + 1 if now - self._closed_at < DIALOG_GAP_S else 1
        if self._streak < DIALOG_STREAK:
            return True
        self._streak = 0
        site = self._site(origin)
        if confirm(self._parent(), "Stop these pop-ups?",
                   "{site} keeps showing pop-ups, one after another. Stop them for this page?"
                   .replace("{site}", site), ok="Stop them", cancel="Keep showing them"):
            self.quiet_site = here or site
            self._closed_at = time.monotonic()
            return False
        return True

    def _done(self):
        self._closed_at = time.monotonic()

    def javaScriptConfirm(self, origin, message):
        site = self._site(origin)
        if message == LEAVE_PAGE:
            self.asking = True
            try:
                leave = confirm(self._parent(), "Leave {site}?".replace("{site}", site),
                                "The site asks before you leave. Anything you haven't saved on it, like a "
                                "half-filled form, may be lost - and what it's playing stops.",
                                ok="Leave", cancel="Stay")
            finally:
                self.asking = False
            if not leave:
                self.tab.browser.stayed(self.tab)
            return leave
        if not self._may_ask(origin):
            return False
        try:
            return confirm(self._parent(), site, message)
        finally:
            self._done()

    def javaScriptAlert(self, origin, message):
        if not self._may_ask(origin):
            return
        try:
            alert(self._parent(), self._site(origin), message)
        finally:
            self._done()

    def javaScriptPrompt(self, origin, message, default):
        if not self._may_ask(origin):
            return False, ""
        try:
            answer = prompt(self._parent(), self._site(origin), message, value=default or "")
        finally:
            self._done()
        return (False, "") if answer is None else (True, answer)

    def javaScriptConsoleMessage(self, level, message, line, source):
        # The web's own logging isn't Buddy's - except a video that couldn't
        # play here (video_fallback.js), which Buddy's player takes.
        if message.startswith(VIDEO_TOKEN):
            raw = message[len(VIDEO_TOKEN):]
            QTimer.singleShot(0, lambda: self.tab.browser.play_video(self.tab, raw))


# ---------------------------------------------------------------- memory --

def memory_bytes(pids):
    """Memory the processes with these ids use (their private bytes,
    what Task Manager counts), or None where it can't be read."""
    pids = {p for p in pids if p}
    if not pids:
        return 0
    if sys.platform != "win32":
        return None
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
                    ("PrivateUsage", ctypes.c_size_t)]

    kernel32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
    kernel32.OpenProcess.restype = wintypes.HANDLE
    total = 0
    for pid in pids:
        handle = kernel32.OpenProcess(0x1000, False, pid)     # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            continue
        try:
            counters = Counters()
            counters.cb = ctypes.sizeof(Counters)
            if psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                total += counters.PrivateUsage
        finally:
            kernel32.CloseHandle(handle)
    return total


def open_outside(url):
    """In the user's own browser."""
    QDesktopServices.openUrl(QUrl(url))
