"""The shared colour picker (Buddy.pickColor in app/web/buddy.js) opens where
it can be seen.

Settings' Custom colours (accent, background, panels) and a few Color Palette
buttons pass the clicked button itself as `at`; the picker has to anchor to
where that button is drawn, or it lands below the bottom of the window, out
of sight, and pressing them seems to do nothing. Runs the real Settings page
offscreen with a Custom theme in in-memory settings - never the real ones:
against a stand-in shell, and on Windows inside the real shell window too."""

import json
import os
import sys
import unittest
from unittest import mock

import _paths  # noqa: F401

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, Qt, QTimer
    QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget
    import PySide6.QtWebEngineWidgets  # noqa: F401
    import core.shell_window as shell_window
    from core.settings_dialog import SettingsDialog
    from core.theme import get_theme_tokens
    from pages.base import ToolPage
    HAVE_WEB = True
except ImportError:  # pragma: no cover
    HAVE_WEB = False


class Settings(dict):
    def save(self):
        pass


class Shell(QWidget if HAVE_WEB else object):
    def __init__(self):
        super().__init__()
        self.shared_settings = Settings(theme="Resolve", subtheme="Custom")

    def theme_tokens(self):
        return get_theme_tokens("Resolve", "Custom")


class _Page(ToolPage if HAVE_WEB else object):
    tool_id, display_name, category = "alpha", "Alpha", "Tools"

    def build_ui(self):
        QVBoxLayout(self).addWidget(QWidget())


def wait(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def js(view, expression):
    out, loop = [], QEventLoop()
    view.page().runJavaScript(f"JSON.stringify({expression})", 0, lambda value: (out.append(value), loop.quit()))
    QTimer.singleShot(5000, loop.quit)
    loop.exec()
    return json.loads(out[0]) if out and out[0] else None


PICKER_BOX = """(() => { const p = document.getElementById('picker');
  if (!p || p.hidden) return null; const r = p.getBoundingClientRect();
  return {l: r.left, t: r.top, r: r.right, b: r.bottom, w: innerWidth, h: innerHeight}; })()"""


@unittest.skipUnless(HAVE_WEB, "PySide6 with QtWebEngine not installed")
class SettingsColourTests(unittest.TestCase):
    def setUp(self):
        self.app = QApplication.instance() or QApplication([])
        self.shell = Shell()
        self.shell.show()
        self.dialog = SettingsDialog(self.shell, self.shell.shared_settings, lambda: None)
        self.addCleanup(self._close)
        self.dialog.resize(560, 720)
        self.dialog.show()
        for _ in range(100):
            wait(50)
            if self.dialog._ready:
                break
        wait(200)
        if sys.platform == "win32":
            # Until the rail has drawn and the page has the window's size: a
            # slow Windows machine can still be drawing them here.
            for _ in range(300):
                if js(self.dialog.view, "!!document.querySelector('.set-rail-item[title=Look]')"
                                        " && innerHeight >= 600"):
                    break
                wait(100)
            else:
                self.fail("Settings never drew its rail at full size")
        # The swatches are on the Look group's page.
        js(self.dialog.view, "(() => { document.querySelector('.set-rail-item[title=Look]').click(); return 1; })()")
        wait(100)

    def _close(self):
        self.dialog.hide()
        self.dialog.deleteLater()
        self.shell.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_each_custom_colour_opens_the_picker_on_screen_and_saves(self):
        keys = js(self.dialog.view, "[...document.querySelectorAll('.set-color')].map(b => b.dataset.key)")
        self.assertEqual(keys, ["accent_color", "background_color", "panel_color"])
        for size in ((560, 720), (480, 460)):
            self.dialog.resize(*size)
            wait(150)
            for key in keys:
                js(self.dialog.view, f"(() => {{ const b = document.querySelector('.set-color[data-key={key}]');"
                                     f" b.scrollIntoView({{block: 'center'}}); b.click(); return 1; }})()")
                wait(100)
                box = js(self.dialog.view, PICKER_BOX)
                self.assertIsNotNone(box, f"{key}: no picker opened")
                self.assertTrue(box["l"] >= 0 and box["t"] >= 0 and box["r"] <= box["w"] and box["b"] <= box["h"],
                                f"{key} at {size}: the picker is off screen: {box}")
                js(self.dialog.view, "Buddy.closePicker() || 1")
        # Picking a colour and pressing OK saves it.
        js(self.dialog.view, "(() => { document.querySelector('.set-color[data-key=accent_color]').click();"
                             " const f = document.querySelector('#picker .pk-hex'); f.value = '#12AB34';"
                             " f.dispatchEvent(new Event('input')); return 1; })()")
        wait(50)
        js(self.dialog.view, "(() => { document.querySelector('#picker .pk-ok').click(); return 1; })()")
        wait(300)
        self.assertEqual(self.shell.shared_settings.get("accent_Resolve"), "#12AB34")

    def test_the_picker_takes_an_element_or_nothing(self):
        for at in ("document.querySelector('.set-color')", "undefined"):
            js(self.dialog.view, f"Buddy.pickColor({{hex: '#336699', at: {at}}}) || 1")
            wait(50)
            box = js(self.dialog.view, PICKER_BOX)
            self.assertIsNotNone(box)
            self.assertTrue(box["l"] >= 0 and box["t"] >= 0 and box["r"] <= box["w"] and box["b"] <= box["h"],
                            f"at {at}: the picker is off screen: {box}")
            js(self.dialog.view, "Buddy.closePicker() || 1")


@unittest.skipUnless(sys.platform == "win32", "Windows only")
@unittest.skipUnless(HAVE_WEB, "PySide6 with QtWebEngine not installed")
class ColorPickerTests(unittest.TestCase):
    """Settings opened from the real shell window, in the Desktop theme."""

    def setUp(self):
        self.app = QApplication.instance() or QApplication([])
        settings = Settings(theme="Desktop", subtheme="Custom")
        for p in [mock.patch.object(shell_window, "SharedSettings", lambda: settings),
                  mock.patch.object(shell_window, "resolve_connect", side_effect=RuntimeError("no Resolve")),
                  mock.patch.object(shell_window.AnnouncementChecker, "start", lambda self: None),
                  mock.patch.object(shell_window.QSystemTrayIcon, "isSystemTrayAvailable", return_value=False)]:
            p.start()
            self.addCleanup(p.stop)
        self.win = shell_window.ShellWindow(self.app, [("Tools", _Page)])
        self.addCleanup(self.win.deleteLater)
        self.win.show()
        self.dialog = SettingsDialog(self.win, settings, lambda: None)
        self.addCleanup(self.dialog.deleteLater)
        self.addCleanup(self.dialog.close)
        self.dialog.show()
        wait(300)

    def _js(self, code):
        loop, out = QEventLoop(), {}
        self.dialog.view.page().runJavaScript(code, 0, lambda r: (out.update(r=r), loop.quit()))
        QTimer.singleShot(5000, loop.quit)
        loop.exec()
        return out.get("r")

    def test_a_swatch_opens_the_picker_inside_the_window(self):
        # Until the form has drawn and the window has its real size - on a
        # slow machine the page can still be a sliver when the swatches appear.
        for _ in range(300):
            # The swatches are on the Look group's page.
            self._js("document.querySelector('.set-rail-item[title=Look]')?.click()")
            if self._js("document.querySelectorAll('.set-color:not(:disabled)').length > 0"
                        " && innerHeight >= 600"):
                break
            wait(100)
        else:
            self.fail("Settings never drew its colour swatches at full size")
        self._js("document.querySelector('.set-color').click()")
        box = json.loads(self._js("""(() => {
            const p = document.querySelector('#picker'), r = p.getBoundingClientRect();
            const b = document.querySelector('.set-color').getBoundingClientRect();
            return JSON.stringify({hidden: p.hidden, top: r.top, bottom: r.bottom, left: r.left,
                                   right: r.right, h: innerHeight, w: innerWidth, under: b.bottom});
        })()"""))
        self.assertFalse(box["hidden"])
        self.assertGreaterEqual(box["top"], 0)
        self.assertLessEqual(box["bottom"], box["h"])
        self.assertGreaterEqual(box["left"], 0)
        self.assertLessEqual(box["right"], box["w"])
        self.assertGreater(box["top"], box["under"])   # anchored below the swatch it came from


if __name__ == "__main__":
    unittest.main()
