"""Ask Buddy's handoffs (pages/manual_chat/handoffs.py): an offered tool
opened with more than words - a bin list for Project Setup, Media Relink on
its offline clips - through the shell's switch_tool(tool_id, payload=...)."""

import os
import tempfile
import types
import unittest
from unittest import mock

import _paths  # noqa: F401
from core import tools_kb
from pages.manual_chat import handoffs
from pages.manual_chat.agent import AgentResult, ManualAgent, tool_specs
from pages.manual_chat.conversation import ChatSessions

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication, Qt
    from PySide6.QtWidgets import QApplication
    from shiboken6 import delete
    from core.shell_window import ShellWindow
    from pages.base import ToolPage
    from pages.manual_chat.page import ManualChatPage
    HAVE_QT = True
except ImportError:
    HAVE_QT = False


class CleanTests(unittest.TestCase):
    def test_a_bin_list_is_kept_in_order_with_its_nesting(self):
        handoff, shown = handoffs.clean("project_setup", {"bins": ["  Footage ", ">A-cam", "", 7, ">> Day  1"]})
        self.assertEqual(handoff, {"bins": ["Footage", ">A-cam", ">>Day 1"]})
        self.assertEqual(shown, "Footage\n>A-cam\n>>Day 1")

    def test_a_bin_list_as_text_and_its_limits(self):
        handoff, _ = handoffs.clean("project_setup", {"bins": "A\n>B"})
        self.assertEqual(handoff["bins"], ["A", ">B"])
        many, _ = handoffs.clean("project_setup", {"bins": [f"Bin {n}" for n in range(500)]})
        self.assertEqual(len(many["bins"]), handoffs.MAX_BINS)
        deep, _ = handoffs.clean("project_setup", {"bins": [">" * 40 + "x" * 500]})
        self.assertEqual(deep["bins"], [">" * handoffs.MAX_DEPTH + "x" * handoffs.MAX_BIN_NAME])

    def test_media_relink_opens_on_offline_clips_unless_told_otherwise(self):
        self.assertEqual(handoffs.clean("media_relink", {}), ({"mode": "fix"}, "Offline clips"))
        self.assertEqual(handoffs.clean("media_relink", {"mode": "nonsense", "scope": "timeline"}),
                         ({"mode": "fix", "scope": "timeline"}, "Offline clips – the open timeline"))
        self.assertEqual(handoffs.clean("media_relink", {"mode": "relocate", "scope": "everything"})[0],
                         {"mode": "relocate"})

    def test_nothing_usable_is_nothing(self):
        for tool_id, raw in (("project_setup", {"bins": []}), ("project_setup", "Footage"),
                             ("footage_search", {"bins": ["A"]}), ("media_relink", None)):
            self.assertEqual(handoffs.clean(tool_id, raw), ({}, ""))


class AgentTests(unittest.TestCase):
    def offer(self, args):
        agent = ManualAgent(mock.Mock(), mock.Mock(), None)
        result = AgentResult()
        with mock.patch.object(tools_kb.ToolInfo, "is_available", True):
            said = agent._do_offer_tool(args, result)
        return result, said

    def test_the_model_is_told_which_tools_take_one(self):
        spec = next(s for s in tool_specs([("project_setup", "Project Setup")]) if s["name"] == "offer_tool")
        self.assertIn("handoff", spec["parameters"]["properties"])
        for tool_id in handoffs.HANDOFF_TOOLS:
            self.assertIn(tool_id, spec["description"])

    def test_a_drafted_bin_list_rides_with_the_offer(self):
        result, said = self.offer({"tool_id": "project_setup", "reason": "A bin structure for a documentary",
                                   "handoff": {"bins": ["Footage", ">Interviews"]}})
        self.assertEqual(result.offered_tool[0], "project_setup")
        self.assertEqual(result.offered_handoff, {"bins": ["Footage", ">Interviews"]})
        self.assertEqual(result.offered_prefill, "Footage\n>Interviews")
        self.assertIn("Nothing is changed", said)

    def test_an_empty_handoff_opens_the_tool_as_it_is(self):
        result, said = self.offer({"tool_id": "project_setup", "reason": "r", "handoff": {"bins": []}})
        self.assertEqual((result.offered_handoff, result.offered_prefill), ({}, ""))
        self.assertIn("nothing usable", said)

    def test_a_tool_that_takes_none_gets_none(self):
        result, _ = self.offer({"tool_id": "transcribe", "reason": "r", "handoff": {"bins": ["A"]}})
        self.assertEqual(result.offered_handoff, {})


class SavedOfferTests(unittest.TestCase):
    def test_a_saved_offer_keeps_its_handoff_checked_again(self):
        chats = ChatSessions()
        chats.current.offer = {"tool_id": "project_setup", "label": "Open Project Setup", "reason": "r",
                               "prefill": "anything", "handoff": {"bins": ["Footage", 3, ">A"], "extra": "x"}}
        again = ChatSessions.from_data(chats.to_data())
        self.assertEqual(again.chats[0].offer["handoff"], {"bins": ["Footage", ">A"]})
        self.assertEqual(again.chats[0].offer["prefill"], "Footage\n>A")
        chats.current.offer = {"tool_id": "transcribe", "label": "Open", "reason": "r", "handoff": {"bins": ["A"]}}
        self.assertNotIn("handoff", ChatSessions.from_data(chats.to_data()).chats[0].offer)


if HAVE_QT:
    class Taker:
        is_placeholder = False
        tool_id = "media_relink"

        def __init__(self, takes=True):
            self.got, self.takes = [], takes

        def handoff(self, payload):
            self.got.append(payload)
            if self.takes == "raises":
                raise RuntimeError("no")
            return self.takes

    class ChatHost:
        connected = False
        shared_settings = {"theme": "Resolve"}

        def __init__(self, took=True):
            self.calls, self.took = [], took

        def tool_settings(self, _tool, defaults):
            return defaults

        def theme_tokens(self):
            from core.theme import get_theme_tokens
            return get_theme_tokens("Resolve")

        def switch_tool(self, tool_id, payload=None):
            self.calls.append((tool_id, payload))
            return self.took


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class ShellTests(unittest.TestCase):
    def shell(self, **pages):
        return types.SimpleNamespace(pages=pages, _layout="desktop", desktop=types.SimpleNamespace(open=lambda t: None))

    def test_a_payload_goes_to_the_tool_once_it_is_open(self):
        tool = Taker()
        shell = self.shell(project_setup=tool)
        self.assertTrue(ShellWindow.switch_tool(shell, "project_setup", payload={"bins": ["A"]}))
        self.assertEqual(tool.got, [{"bins": ["A"]}])
        self.assertFalse(ShellWindow.switch_tool(shell, "project_setup"))       # opened, nothing handed over
        self.assertEqual(len(tool.got), 1)

    def test_a_sub_tab_gets_it_not_its_host(self):
        relink = Taker()
        host = types.SimpleNamespace(tool_id="media_manager", SUB_TOOLS=("media_relink",), by_tab={"relink": relink},
                                     show_tool=lambda tool_id: None, is_placeholder=False)
        shell = self.shell(media_manager=host)
        self.assertTrue(ShellWindow.switch_tool(shell, "media_relink", payload={"mode": "fix"}))
        self.assertEqual(relink.got, [{"mode": "fix"}])

    def test_a_tool_that_cannot_take_it_is_still_opened(self):
        for takes in (False, "raises"):
            shell = self.shell(project_setup=Taker(takes))
            with mock.patch("traceback.print_exc"):
                self.assertFalse(ShellWindow.switch_tool(shell, "project_setup", payload={"bins": ["A"]}))
            self.assertEqual(shell._current_tool_id, "project_setup")
        self.assertFalse(ShellWindow.switch_tool(self.shell(), "no_such_tool", payload={"bins": ["A"]}))

    def test_words_still_go_to_prefill(self):
        got = []
        page = types.SimpleNamespace(prefill=lambda text: got.append(text) or True)
        self.assertTrue(ToolPage.handoff(page, {"text": "a drone shot"}))
        self.assertFalse(ToolPage.handoff(page, {"bins": ["A"]}))
        self.assertEqual(got, ["a drone shot"])


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class ChatPageTests(unittest.TestCase):
    def page(self, host):
        QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
        self.app = QApplication.instance() or QApplication([])
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        patch = mock.patch.object(ManualChatPage, "ask_folder", folder.name)
        patch.start()
        self.addCleanup(patch.stop)
        page = ManualChatPage(host)
        self.addCleanup(delete, page)
        self.events = []
        page.emit = lambda name, value=None: self.events.append((name, value))
        return page

    def test_the_button_hands_over_what_the_offer_kept_not_what_the_view_says(self):
        host = ChatHost()
        page = self.page(host)
        page._set_offer({"tool_id": "project_setup", "label": "Open Project Setup", "reason": "r",
                         "prefill": "Footage", "handoff": {"bins": ["Footage"]}})
        page.on_open_tool({"tool_id": "project_setup", "handoff": {"bins": ["Made up by the page"]}})
        self.assertEqual(host.calls, [("project_setup", {"bins": ["Footage"]})])
        page.on_open_tool({"tool_id": "transcribe"})             # not the offered one: opened as it is
        self.assertEqual(host.calls[-1], ("transcribe", None))

    def test_a_tool_that_would_not_take_it_says_so(self):
        page = self.page(ChatHost(took=False))
        page._set_offer({"tool_id": "media_relink", "label": "Open Media Relink", "reason": "r",
                         "handoff": {"mode": "fix"}})
        page.on_open_tool({"tool_id": "media_relink"})
        self.assertIn("couldn't take", [value for name, value in self.events if name == "toast"][-1]["text"])


if __name__ == "__main__":
    unittest.main()
