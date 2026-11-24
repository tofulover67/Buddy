"""Ask Buddy while its provider says nothing: after a rate limit the next
request can sit unanswered for as long as its timeout, which looked like
Buddy had hung - the dots, and "Answering" only when quitting. The page now
says who it is waiting for and for how long, and a rate-limit error says
what to do about it. No network, no model, no Resolve."""

import os
import tempfile
import unittest
from unittest import mock

import _paths  # noqa: F401
from pages.manual_chat.agent import AgentResult

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication, Qt
    from PySide6.QtWidgets import QApplication
    from shiboken6 import delete
    from pages.manual_chat import page as page_mod
    from pages.manual_chat.page import ManualChatPage
    HAVE_QT = True
except ImportError:
    HAVE_QT = False


class Host:
    connected = False
    shared_settings = {"theme": "Resolve"}

    def tool_settings(self, _tool, defaults):
        return defaults

    def theme_tokens(self):
        from core.theme import get_theme_tokens
        return get_theme_tokens("Resolve")


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class WaitingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
        cls.app = QApplication.instance() or QApplication([])
        cls.folder = tempfile.TemporaryDirectory()
        cls.patch = mock.patch.object(ManualChatPage, "ask_folder", cls.folder.name)
        cls.patch.start()
        cls.page = ManualChatPage(Host())       # one for them all: each is a web view of its own

    @classmethod
    def tearDownClass(cls):
        cls.page._set_sending(False)
        delete(cls.page)
        cls.patch.stop()
        cls.folder.cleanup()

    def setUp(self):
        self.events = []
        self.page.emit = lambda name, value=None: self.events.append((name, value))
        self.page._draft = ""
        self.page._set_sending(False)
        self.events.clear()

    @staticmethod
    def clock_at(now):
        """The page's clock reading `now` - the page's alone: the real one
        is every thread's in the run."""
        return mock.patch.object(page_mod, "time", mock.Mock(monotonic=lambda: now))

    def thinking(self):
        return [value for name, value in self.events if name == "thinking"]

    def test_a_silent_provider_is_said_to_be_waited_for(self):
        page = self.page
        page._waiting_on = "Gemini"
        page._set_sending(True)
        self.assertTrue(page._wait_timer.isActive())
        self.assertEqual(page.working_on(), "Answering")

        with self.clock_at(page._heard + page_mod.WAIT_NOTE_S - 1.5):
            page._wait_tick()
        self.assertEqual(self.thinking()[-1]["message"], "Thinking…")        # not long enough to say so

        # Half a second past: 42 less a rounding error in the clock is 41.
        with self.clock_at(page._heard + 42.5):
            page._wait_tick()
        self.assertEqual(self.thinking()[-1], {"on": True, "message": "Still waiting for Gemini (42 s)…"})

    def test_hearing_from_it_starts_the_count_again_and_an_answer_being_written_is_left_alone(self):
        page = self.page
        page._waiting_on = "Gemini"
        page._set_sending(True)
        start = page._heard
        with self.clock_at(start + 30):
            page._on_progress("Searching the manual: \"markers\"")
            seen = len(self.thinking())
            page._wait_tick()                                    # heard from a moment ago
        self.assertEqual(len(self.thinking()), seen)
        with self.clock_at(start + 90):
            page._draft = "To add a marker"                      # the answer is on the screen
            page._wait_tick()
        self.assertEqual(len(self.thinking()), seen)

    def test_it_stops_with_the_answer(self):
        page = self.page
        page._set_sending(True)
        page._on_done(AgentResult(error="Gemini: HTTP 429 – You exceeded your current quota."))
        self.assertFalse(page._wait_timer.isActive())
        self.assertIsNone(page.working_on())
        self.assertEqual(self.thinking()[-1]["on"], False)
        error = page.chats.blocks[-1]
        self.assertTrue(error["error"])
        self.assertIn("rate limit", error["body"])
        self.assertIn("Try again", error["body"])

    def test_a_key_opencode_refuses_may_be_the_other_services(self):
        page = self.page
        for name in ("OpenCode", "OpenCode Go"):
            page._set_sending(True)
            page._on_done(AgentResult(error=f"{name}: HTTP 401 – Invalid credential"))
            self.assertIn("two kinds of key", page.chats.blocks[-1]["body"])

    def test_other_errors_are_left_as_they_are(self):
        page = self.page
        page._set_sending(True)
        page._on_done(AgentResult(error="Gemini: HTTP 400 – API key not valid."))
        self.assertEqual(page.chats.blocks[-1]["body"], "Gemini: HTTP 400 – API key not valid.")


if __name__ == "__main__":
    unittest.main()
