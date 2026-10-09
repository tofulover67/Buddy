"""The Web tab's Downloads window (app/pages/web/downloads/), in a real web
view: Shift+click picks a range from the download clicked before - not
from a row number, which filtering moves under it."""

import json
import os
import time
import unittest

import _paths  # noqa: F401

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication, QEventLoop, Qt, QTimer
    from PySide6.QtWidgets import QApplication, QWidget
    HAVE_QT = True
except ImportError:
    HAVE_QT = False


def _wait(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def _item(n, name):
    from pages.web import downloads_log as log
    record = dict(log.new_record(n, name, os.path.join("C:/dl", name), "site.test", now=1000.0 + n),
                  state="done", size=1000)
    return log.shown(record, True)


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class ShiftClickTests(unittest.TestCase):
    def setUp(self):
        QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
        self.app = QApplication.instance() or QApplication([])
        from core.theme import get_theme_tokens
        from pages.web.downloads_window import DownloadsWindow
        names = ["a.mp4", "b.png", "c.mp4", "d.png", "e.mp4"]
        items = [_item(n, name) for n, name in enumerate(names)][::-1]           # newest first, as sent
        self.ids = {d["name"]: d["id"] for d in items}

        class Shell(QWidget):
            shared_settings = {"theme": "Resolve"}

            def theme_tokens(self):
                return get_theme_tokens("Resolve")

        self.shell = Shell()
        shell = self.shell

        class Owner:
            host = shell

            def window(self):
                return shell

            def downloads_view(self):
                return {"items": items, "folder": "C:/dl"}

        self.window = DownloadsWindow(Owner())
        self.addCleanup(self.window.deleteLater)
        self.window.show()
        self.until("document.querySelectorAll('.dl-item').length === 5")

    def js(self, script):
        got = []
        self.window.view.page().runJavaScript(script, 0, got.append)
        for _ in range(60):
            if got:
                return got[0]
            _wait(50)
        return None

    def until(self, script, ms=8000):
        end = time.monotonic() + ms / 1000
        while time.monotonic() < end:
            if self.js(script):
                return
            _wait(100)
        self.fail(f"never: {script}")

    def press(self, name, shift=False):
        self.js(f"""(() => {{
            const row = document.querySelector('.dl-item[data-id="{self.ids[name]}"]');
            row.dispatchEvent(new PointerEvent("pointerdown", {{bubbles: true, button: 0, shiftKey: {str(shift).lower()}}}));
            row.dispatchEvent(new PointerEvent("pointerup", {{bubbles: true, button: 0}}));
            return true;
        }})()""")

    def chosen(self):
        names = {v: k for k, v in self.ids.items()}
        return sorted(names[i] for i in json.loads(self.js("JSON.stringify([...selected])")))

    def test_a_range_from_the_download_clicked_not_its_old_row(self):
        self.press("c.mp4")                       # row 3 of all five
        self.js("filters.kind = 'video'; draw(true); true")
        self.until("document.querySelectorAll('.dl-item').length === 3")
        self.press("a.mp4", shift=True)           # c is row 2 of these three now
        self.assertEqual(self.chosen(), ["a.mp4", "c.mp4"])

    def test_a_range_from_a_download_filtered_away_starts_afresh(self):
        self.press("b.png")
        self.js("filters.kind = 'video'; draw(true); true")
        self.until("document.querySelectorAll('.dl-item').length === 3")
        self.press("a.mp4", shift=True)
        self.assertEqual(self.chosen(), ["a.mp4"])
        self.press("e.mp4", shift=True)           # and that one's the start now
        self.assertEqual(self.chosen(), ["a.mp4", "c.mp4", "e.mp4"])


if __name__ == "__main__":
    unittest.main()
