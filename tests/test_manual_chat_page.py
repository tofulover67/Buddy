"""Ask Buddy's conversation navigation preserves each chat's tool shortcut."""

import os
import tempfile
import threading
import unittest
from unittest import mock

import _paths  # noqa: F401
from pages.manual_chat import chat_store
from pages.manual_chat.conversation import YOU

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication, Qt
    from PySide6.QtWidgets import QApplication
    from shiboken6 import delete
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
class NavigationTests(unittest.TestCase):
    def setUp(self):
        QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
        self.app = QApplication.instance() or QApplication([])
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = folder.name
        patch = mock.patch.object(ManualChatPage, "ask_folder", self.folder)
        patch.start()
        self.addCleanup(patch.stop)
        self.page = ManualChatPage(Host())
        self.addCleanup(delete, self.page)
        self.events = []
        self.page.emit = lambda name, value=None: self.events.append((name, value))

    def test_buddy_opens_on_a_fresh_chat_with_the_saved_ones_in_the_list(self):
        self.page._append(YOU, "Relink my clips")
        self.page.on_new_chat(None)
        self.page._append(YOU, "Make subtitles")
        self.page.on_select_chat({"index": 0})       # left on the first one
        chat_store.save(self.folder, self.page.chats)
        again = ManualChatPage(Host())                # Buddy launched again
        self.addCleanup(delete, again)
        events = []
        again.emit = lambda name, value=None: events.append((name, value))
        again.web_ready()
        self.assertTrue(again.chats.is_empty())
        self.assertEqual(again.chats.index, len(again.chats.chats) - 1)
        self.assertEqual([c.title for c in again.chats.chats], ["Relink my clips", "Make subtitles", "New chat"])

    def test_a_corrupt_chat_file_opens_an_empty_page_with_saving_off(self):
        from core.atomic_io import CorruptFileError
        with mock.patch.object(chat_store, "load", side_effect=CorruptFileError("conversations.json is damaged")):
            page = ManualChatPage(Host())
        self.addCleanup(delete, page)
        self.assertTrue(page.chats.is_empty())
        self.assertIn("Saving is disabled", page._chat_store_error)

    def offer(self):
        return [value for name, value in self.events if name == "offer"][-1]

    def test_new_previous_next_picker_and_reload_restore_the_right_offer(self):
        first = {"tool_id": "media_relink", "label": "Open Media Relink", "reason": "Missing files"}
        second = {"tool_id": "transcribe", "label": "Open Transcribe", "reason": "Subtitles"}
        self.page._append(YOU, "Relink my clips")
        self.page._set_offer(first)
        self.page.on_new_chat(None)
        self.assertIsNone(self.offer())
        self.page._append(YOU, "Make subtitles")
        self.page._set_offer(second)
        self.page._proposal = object()
        self.page.on_prev_chat(None)
        self.assertEqual(self.offer(), first)
        self.assertIsNone(self.page._proposal)
        self.page.on_next_chat(None)
        self.assertEqual(self.offer(), second)
        self.page.on_select_chat({"index": 0})
        self.assertEqual(self.offer(), first)
        self.page.chats, warnings = chat_store.load(self.folder)
        self.assertEqual(warnings, [])
        self.page.web_ready()
        self.assertEqual(self.offer(), first)
        self.page.on_delete_chat({"index": 0})
        self.assertEqual(self.offer(), second)
        self.page.on_delete_all_chats(None)
        self.assertIsNone(self.offer())


if __name__ == "__main__":
    unittest.main()


class BusyHost(Host):
    def __init__(self):
        self.busy = []

    def set_busy(self, on, message=None):
        self.busy.append(on)


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class ManualLoadTests(unittest.TestCase):
    """The manual is read on a thread when the page shows - it's the page
    Buddy opens on - and a question sent before that's done waits for it."""

    def setUp(self):
        QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
        self.app = QApplication.instance() or QApplication([])
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        patch = mock.patch.object(ManualChatPage, "ask_folder", folder.name)
        patch.start()
        self.addCleanup(patch.stop)
        self.host = BusyHost()
        self.page = ManualChatPage(self.host)
        self.addCleanup(delete, self.page)
        self.page.emit = lambda name, value=None: None
        self.made = []                      # (retriever, thread it was made on)
        self.release = threading.Event()
        self.release.set()

        def make(_bundle, _text, _settings):
            self.release.wait(5)
            retriever = mock.Mock(tier="hybrid", bundle_dir=None)
            retriever.describe_tier.return_value = "Semantic search"
            self.made.append((retriever, threading.current_thread()))
            return retriever
        patch = mock.patch.object(ManualChatPage, "_make_retriever", staticmethod(make))
        patch.start()
        self.addCleanup(patch.stop)

    def settle(self):
        thread = self.page._manual_thread
        if thread is not None:
            thread.join(5)
        QApplication.sendPostedEvents(self.page)

    def test_showing_the_page_reads_the_manual_on_a_thread_of_its_own(self):
        self.page.on_shown()
        self.settle()
        retriever, thread = self.made[0]
        self.assertIsNot(thread, threading.main_thread())
        self.assertIs(self.page._retriever, retriever)
        self.assertEqual(self.host.busy, [])          # the window never waited
        self.page.on_shown()                          # shown again: nothing to do
        self.assertEqual(len(self.made), 1)

    def test_a_question_sent_while_it_loads_waits_for_that_load(self):
        self.release.clear()
        self.page.on_shown()
        threading.Timer(0.1, self.release.set).start()
        self.page._load_manual_now()
        self.assertEqual(len(self.made), 1)           # not read a second time
        self.assertIs(self.page._retriever, self.made[0][0])
        self.assertEqual(self.host.busy, [True, False])

    def test_a_reload_drops_the_load_it_replaced(self):
        self.release.clear()
        self.page.on_shown()
        old = self.page._manual_thread
        self.page._reload_manual()                    # a new bundle (page hidden: loads on demand)
        self.release.set()
        old.join(5)
        QApplication.sendPostedEvents(self.page)
        self.assertIsNone(self.page._retriever)       # the old bundle's index isn't used
        self.page._load_manual_now()
        self.assertEqual(len(self.made), 2)
        self.assertIs(self.page._retriever, self.made[1][0])
