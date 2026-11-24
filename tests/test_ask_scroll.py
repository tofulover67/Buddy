"""Ask Buddy's transcript while an answer is being written: it follows the
newest line until the reader scrolls up - by however little - and then
stays where they put it until they come back down. The real page in an
offscreen web view; no model, no Resolve."""

import json
import os
import tempfile
import time
import unittest
from unittest import mock

import _paths  # noqa: F401

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication, QEventLoop, Qt, QTimer
    from PySide6.QtWidgets import QApplication
    from shiboken6 import delete
    from pages.manual_chat.page import ManualChatPage
    HAVE_QT = True
except ImportError:
    HAVE_QT = False

GAP = "(transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight)"


class Host:
    connected = False
    shared_settings = {"theme": "Resolve"}

    def tool_settings(self, _tool, defaults):
        return defaults

    def theme_tokens(self):
        from core.theme import get_theme_tokens
        return get_theme_tokens("Resolve")


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class ScrollTests(unittest.TestCase):
    def setUp(self):
        QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
        self.app = QApplication.instance() or QApplication([])
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        patch = mock.patch.object(ManualChatPage, "ask_folder", folder.name)
        patch.start()
        self.addCleanup(patch.stop)
        self.page = ManualChatPage(Host())
        self.addCleanup(delete, self.page)
        self.page.resize(700, 500)
        self.page.show()
        deadline = time.monotonic() + 15
        while not self.js("typeof scrollToBottom === 'function' && typeof Buddy === 'object'"):
            if time.monotonic() > deadline:
                self.skipTest("the page didn't load")
        self.settle(1000)                                   # what Python sends a page that has just loaded

    def js(self, code):
        loop, result = QEventLoop(), {}
        self.page.view.page().runJavaScript(code, 0, lambda value: (result.update(value=value), loop.quit()))
        QTimer.singleShot(5000, loop.quit)
        loop.exec()
        return result.get("value")

    def settle(self, ms=250):
        """Long enough for the frames in which the page scrolls and hears of it."""
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    def write(self, lines):
        html = "".join(f"<p>Line {n}</p>" for n in range(lines))
        # The page, once loaded, is sent where the conversation stands - no
        # draft - and on a slow runner that can land after this one: again.
        deadline = time.monotonic() + 10
        while True:
            self.js(f"Buddy.receive('stream', {json.dumps({'html': html})})")
            self.settle()
            if not self.js("document.getElementById('draft').hidden") or time.monotonic() > deadline:
                break

    def test_scrolling_up_while_an_answer_is_written_stays_put(self):
        self.write(80)
        self.assertTrue(self.js("following"))
        self.assertLess(self.js(GAP), 3)                    # following: the newest line is in view

        # A nudge up, well inside what used to count as "still at the bottom".
        self.js("transcript.style.scrollBehavior = 'auto'; transcript.scrollTop -= 25")
        self.settle()
        self.assertFalse(self.js("following"))
        top = self.js("transcript.scrollTop")
        self.write(120)                                     # the answer goes on
        self.assertEqual(self.js("transcript.scrollTop"), top)
        self.assertGreater(self.js(GAP), 25)

        # Back down to the newest line: following again.
        self.js("transcript.scrollTop = transcript.scrollHeight")
        self.settle()
        self.assertTrue(self.js("following"))
        self.write(160)
        self.assertLess(self.js(GAP), 3)

    def test_the_wheel_stops_the_following_at_once(self):
        self.write(80)
        self.js("transcript.dispatchEvent(new WheelEvent('wheel', {deltaY: -40}))")
        self.assertFalse(self.js("following"))

    def test_a_draft_taken_back_is_not_the_reader_scrolling_up(self):
        self.write(80)
        self.write(60)                                      # shorter: the transcript moves up with it
        self.assertTrue(self.js("following"))
        self.write(100)
        self.assertLess(self.js(GAP), 3)


if __name__ == "__main__":
    unittest.main()
