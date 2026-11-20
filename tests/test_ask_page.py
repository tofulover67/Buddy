"""Ask Buddy's page (app/pages/manual_chat/page.py) around one answer: it
streams, can be stopped, regenerated or asked again in other words; it
comes with its sources, a note on pages cited but never read, and
questions to ask next. Plus pinned chats, a tool opened with what the
model handed over, and exporting. No model and no thread: the worker is a
stand-in whose signals the test fires itself."""

import os
import tempfile
import unittest
from unittest import mock

import _paths  # noqa: F401
from pages.manual_chat.agent import AgentResult
from pages.manual_chat.conversation import SUMMARY_BATCH, YOU
from pages.manual_chat.retrieval import Passage

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication, Qt
    from PySide6.QtWidgets import QApplication, QFileDialog
    from shiboken6 import delete
    from pages.manual_chat import page as chat_page
    from pages.manual_chat.page import ManualChatPage
    HAVE_QT = True
except ImportError:
    HAVE_QT = False


class Signal:
    def connect(self, slot):
        self.slot = slot


class Worker:
    """What the page starts for a question - never run."""

    made = []

    def __init__(self, agent, question, history, images=None, stream=False, summary="", fold=0):
        self.question, self.history, self.images = question, list(history), images
        self.stream, self.summary, self.fold = stream, summary, fold
        self.done, self.progress, self.delta, self.finished = Signal(), Signal(), Signal(), Signal()
        self.stopped = False
        Worker.made.append(self)

    def start(self):
        pass

    def stop(self):
        self.stopped = True

    def wait(self, _ms):
        pass

    def deleteLater(self):
        pass


class Settings(dict):
    def save(self):
        pass


class Tool:
    is_placeholder = False

    def __init__(self):
        self.got = []

    def prefill(self, text):
        self.got.append(text)
        return True


class Host:
    connected = False
    shared_settings = {"theme": "Resolve"}

    def __init__(self):
        self.settings = None
        self.pages, self.opened = {"footage_search": Tool(), "media_relink": Tool()}, []

    def tool_settings(self, _tool, defaults):
        self.settings = Settings(defaults)
        return self.settings

    def theme_tokens(self):
        from core.theme import get_theme_tokens
        return get_theme_tokens("Resolve")

    def switch_tool(self, tool_id):
        self.opened.append(tool_id)


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class AnswerTests(unittest.TestCase):
    def setUp(self):
        QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
        self.app = QApplication.instance() or QApplication([])
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = folder.name
        Worker.made = []
        for target, name, value in ((ManualChatPage, "ask_folder", self.folder),
                                    (chat_page, "_AgentWorker", Worker),
                                    (ManualChatPage, "_build_agent", lambda page, prefetch_focus=False: object()),
                                    (ManualChatPage, "_ask_to_read_project", lambda page: None)):
            patch = mock.patch.object(target, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        self.host = Host()
        self.page = ManualChatPage(self.host)
        self.addCleanup(delete, self.page)
        self.page._retriever = mock.Mock(tier="bundle-bm25", bundle_dir=None)
        self.page._retriever.describe_tier.return_value = "Keyword search."
        self.events = []
        self.page.emit = lambda name, value=None: self.events.append((name, value))

    def last(self, name):
        return [value for event, value in self.events if event == name][-1]

    def ask(self, text="How do I add a window?", **more):
        self.page.on_send({"text": text, **more})
        return Worker.made[-1]

    def answer(self, text="Open the Window palette (Chapter 3, p. 10).", **more):
        result = AgentResult(answer=text, **more)
        self.page._on_done(result)
        return self.page.chats.blocks[-1]

    def test_the_status_line_only_speaks_when_something_is_off(self):
        self.page._push_status()
        self.assertEqual(self.last("status"), {"text": "Keyword search."})      # not at its best: says so
        self.page._retriever.tier = "hybrid"
        self.page._push_status()
        self.assertEqual(self.last("status"), {"text": ""})                     # all well: nothing under the box
        self.page._chat_store_error = "Saving is disabled to protect the file."
        self.page._push_status()
        self.assertEqual(self.last("status"), {"text": "Saving is disabled to protect the file."})

    # ------------------------------------------------------- one answer

    def test_an_answer_arrives_as_its_written_then_whole(self):
        worker = self.ask()
        self.assertTrue(worker.stream)
        self.assertTrue(self.last("controls")["can_stop"])
        self.assertTrue(self.page.chats.blocks[-1]["asked"])
        self.page._on_delta("Let me look")
        self.page._on_delta(None)                                # it went to a tool instead
        self.page._push_stream()
        self.assertEqual(self.last("stream"), {"html": ""})
        for piece in ("Open the **Window**", " palette.\n\nFollow-ups: How do I sof"):
            self.page._on_delta(piece)
        self.page._push_stream()
        self.assertEqual(self.last("stream"), {"html": "<p>Open the <b>Window</b> palette.</p>"})   # no half follow-up
        block = self.answer(passages=[Passage("To add a window…", 10, 3, "Windows")],
                            followups=["How do I soften it?"])
        self.assertEqual(self.last("stream"), {"html": ""})      # the draft gives way to the answer
        self.assertEqual((block["answer"], block["sources"][0]["page"], block["unverified"]), (True, 10, []))
        self.assertEqual(self.last("suggestions"), {"items": ["How do I soften it?"], "raw": True})
        self.assertEqual(self.last("controls")["redo"], {"q": 1, "a": 2})
        self.assertFalse(self.last("controls")["can_stop"])
        self.assertEqual(len(self.page.chats.history), 2)

    def test_a_cited_page_buddy_never_read_is_flagged(self):
        self.ask()
        block = self.answer("Windows (Chapter 3, p. 10). Tracking (Chapter 8, p. 777).",
                            passages=[Passage("…", 10, 3, "Windows")])
        self.assertEqual(block["unverified"], ["777"])
        self.ask("And softness?")                                # a follow-up may cite what an earlier answer read
        block = self.answer("Same palette (Chapter 3, p. 10).")
        self.assertEqual(block["unverified"], [])

    def test_followups_can_be_switched_off(self):
        self.host.settings["suggest_followups"] = False
        self.ask()
        self.answer(followups=["Next?"])
        self.assertEqual(self.last("suggestions")["items"], [])

    # ------------------------------------------------------------- stop

    def test_stop_keeps_what_was_written_and_lets_go_of_the_rest(self):
        worker = self.ask()
        self.page._on_delta("Open the Window pal")
        self.page.on_stop()
        self.assertTrue(worker.stopped)
        block = self.page.chats.blocks[-1]
        self.assertEqual((block["body"], block["stopped"], block["answer"]), ("Open the Window pal", True, True))
        self.assertEqual(self.page.chats.history, [])            # half an answer is never re-sent as a whole one
        self.assertFalse(self.last("controls")["sending"])
        self.assertEqual(self.last("stream"), {"html": ""})
        self.page.sender = lambda: worker                        # ...and its answer, when it comes, is nobody's
        self.answer("The whole answer, too late.")
        self.assertEqual(self.page.chats.blocks[-1]["body"], "Open the Window pal")
        self.page._clear_worker()
        self.assertEqual(self.page._stopped, [])

    def test_stop_before_a_word_and_then_ask_again(self):
        self.ask()
        self.page.on_stop()
        self.assertEqual(self.page.chats.blocks[-1]["body"], "Stopped before there was an answer.")
        self.assertEqual(self.last("controls")["redo"], {"q": 1, "a": 2})
        self.page.on_stop()                                      # nothing to stop: nothing happens
        self.page.on_regenerate()
        self.assertEqual([b["body"] for b in self.page.chats.blocks][1:], ["How do I add a window?"])
        self.assertEqual(len(Worker.made), 2)

    # ------------------------------------------------ regenerate, edit

    def test_regenerate_asks_again_without_the_old_answer(self):
        self.ask("First?")
        self.answer("One.")
        self.ask("Second?")
        self.answer("Two.")
        self.page._proposal = object()
        self.page.chats.current.offer = {"tool_id": "media_relink", "label": "Open Media Relink", "reason": ""}
        self.page.on_regenerate()
        worker = Worker.made[-1]
        self.assertEqual(worker.question, "Second?")
        self.assertEqual([m["content"] for m in worker.history], ["First?", "One."])
        self.assertEqual([b["body"] for b in self.page.chats.blocks][1:], ["First?", "One.", "Second?"])
        self.assertIsNone(self.page._proposal)                   # what hung on the old answer goes with it
        self.assertIsNone(self.page.chats.current.offer)
        self.page.on_regenerate()                                # one on its way already
        self.assertEqual(len(Worker.made), 3)

    def test_edit_puts_the_question_back_and_sending_replaces_the_turn(self):
        self.ask("How do I add a widnow?")
        self.answer("A what?")
        self.page.on_edit_last()
        self.assertEqual(self.last("compose"), {"text": "How do I add a widnow?", "replace": True})
        self.assertEqual(len(self.page.chats.blocks), 3)         # nothing removed until it's sent
        worker = self.ask("How do I add a window?", replace=True)
        self.assertEqual((worker.question, worker.history), ("How do I add a window?", []))
        self.assertEqual([b["body"] for b in self.page.chats.blocks][1:], ["How do I add a window?"])

    def test_a_failed_question_can_be_tried_again(self):
        self.ask()
        self.page._on_done(AgentResult(error="Gemini: HTTP 503"))
        self.assertEqual(self.last("controls")["redo"], {"q": 1, "a": None})
        self.page.on_regenerate()
        self.assertEqual([b["who"] for b in self.page.chats.blocks][1:], [YOU])

    def test_help_and_a_checkup_are_not_turns_to_redo(self):
        self.page.on_send({"text": "help"})
        self.assertIsNone(self.last("controls")["redo"])
        self.page.on_regenerate()
        self.assertEqual(Worker.made, [])

    # ---------------------------------------------------------- summary

    def test_turns_past_the_limit_are_sent_to_be_folded_and_the_summary_kept(self):
        self.host.settings["history_turns"] = 1
        for n in range(SUMMARY_BATCH + 1):
            worker = self.ask(f"q{n}")
            self.assertEqual(worker.fold, 0)
            self.answer(f"a{n}")
        worker = self.ask("one more")
        self.assertEqual(worker.fold, SUMMARY_BATCH * 2)
        self.assertEqual(len(worker.history), SUMMARY_BATCH * 2 + 2)
        self.page._on_done(AgentResult(answer="ok", summary="They asked q0 to q2."))
        chat = self.page.chats.current
        self.assertEqual((chat.summary, chat.overflow), ("They asked q0 to q2.", [{"role": "user", "content": f"q{SUMMARY_BATCH}"},
                                                                                {"role": "assistant", "content": f"a{SUMMARY_BATCH}"}]))
        self.assertEqual(self.ask("next").summary, "They asked q0 to q2.")

    # ------------------------------------------------------------ extras

    def test_an_offered_tool_starts_with_what_was_handed_over(self):
        self.ask("Find me a drone shot")
        result = AgentResult(answer="Footage Search can.", offered_tool=("footage_search", "It searches by description"),
                             offered_prefill="a drone shot over water")
        self.page._on_done(result)
        self.assertEqual(self.last("offer")["prefill"], "a drone shot over water")
        self.page.on_open_tool({"tool_id": "footage_search", "prefill": "something the page made up"})
        self.assertEqual(self.host.opened, ["footage_search"])
        self.assertEqual(self.host.pages["footage_search"].got, ["a drone shot over water"])
        self.page.on_open_tool({"tool_id": "media_relink"})      # not the offered one: opened as it is
        self.assertEqual(self.host.pages["media_relink"].got, [])

    def test_pinned_chats_lead_the_list(self):
        self.page._append(YOU, "First chat")
        self.page.on_new_chat(None)
        self.page._append(YOU, "Second chat")
        self.page.on_pin_chat({"index": 0, "on": True})
        self.assertEqual([(i["title"], i["pinned"]) for i in self.last("chat_list")["items"]],
                         [("First chat", True), ("Second chat", False)])
        self.page.on_pin_chat({"index": "x"})                    # nonsense from the view: ignored

    def test_a_checkup_offers_what_to_ask_about_what_it_found(self):
        issues = [{"kind": "format", "details": ["frame rate 29.97 vs timeline 24"]},
                  {"kind": "missing_path", "details": ["source file path is not found on this computer"]}]
        asks = self.page._checkup_followups(issues)
        self.assertEqual(asks, [chat_page.CHECKUP_FOLLOWUPS["frame_rate"], chat_page.CHECKUP_FOLLOWUPS["missing_path"]])
        self.host.settings["allow_project_writes"] = True
        self.assertIn(chat_page.CHECKUP_FOLLOWUPS["flag"], self.page._checkup_followups(issues))
        self.assertEqual(self.page._checkup_followups([]), [chat_page.CHECKUP_FOLLOWUPS["clean"]])

    def test_export_is_markdown_unless_the_file_says_text(self):
        self.ask()
        self.answer("Open the **Window** palette.")
        for ending, expected in ((".md", "**Window**"), (".txt", "Open the Window palette.")):
            path = os.path.join(self.folder, "chat" + ending)
            with mock.patch.object(QFileDialog, "getSaveFileName", return_value=(path, "")):
                self.page.on_export(None)
            with open(path, encoding="utf-8") as f:
                self.assertIn(expected, f.read())


class SlowModel:
    """A model that writes one word at a time, until told to stop."""

    local, destination, label, provider = True, "127.0.0.1", "Fake", "fake"

    def __init__(self, words, pause=0.02):
        self.words, self.pause = words, pause
        self.written = 0

    def validate(self):
        return ""

    def chat(self, system, messages, tools, on_text=None, cancelled=None):
        import time
        from pages.manual_chat.llm import LLMCancelled, Reply
        for word in self.words:
            if cancelled is not None and cancelled():
                raise LLMCancelled("Stopped.")
            time.sleep(self.pause)
            self.written += 1
            if on_text is not None:
                on_text(word + " ")
        return Reply(" ".join(self.words))


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class ThreadTests(unittest.TestCase):
    """The same, with the real worker on its own thread: the pieces and the
    answer cross to the page by signal, and Stop lets go of a thread that
    is still running."""

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
        self.addCleanup(self.page.on_app_quitting)
        self.page._retriever = mock.Mock(tier="bundle-bm25", bundle_dir=None)
        self.page._retriever.describe_tier.return_value = "Keyword search."
        self.events = []
        self.page.emit = lambda name, value=None: self.events.append((name, value))

    def model(self, words, pause=0.02):
        model = SlowModel(words, pause)
        patch = mock.patch.object(chat_page, "llm_client_from_settings", lambda settings, **kw: model)
        patch.start()
        self.addCleanup(patch.stop)
        return model

    def until(self, done, seconds=10):
        import time
        end = time.monotonic() + seconds
        while not done() and time.monotonic() < end:
            self.app.processEvents()
            time.sleep(0.005)
        self.assertTrue(done(), "timed out")

    def test_the_answer_streams_in_and_then_stands_whole(self):
        self.model("Open the Window palette and pick a shape".split())
        self.page.on_send({"text": "How do I add a window?"})
        self.until(lambda: not self.page._sending)
        drafts = [value["html"] for name, value in self.events if name == "stream" and value["html"]]
        self.assertTrue(drafts)                                  # it was drawn on the way
        self.assertTrue(drafts[0].startswith("<p>Open"))
        self.assertEqual(self.page.chats.blocks[-1]["body"], "Open the Window palette and pick a shape")
        self.assertEqual([value for name, value in self.events if name == "stream"][-1], {"html": ""})
        self.until(lambda: self.page._worker is None)            # and its thread is tidied away

    def test_stop_ends_a_real_answer_part_way(self):
        model = self.model(["word"] * 400)
        self.page.on_send({"text": "Tell me everything"})
        self.until(lambda: len(self.page._draft) > 20)
        self.page.on_stop()
        block = self.page.chats.blocks[-1]
        self.assertTrue(block["stopped"])
        self.assertTrue(block["body"].startswith("word word"))
        self.until(lambda: not self.page._stopped)               # the thread saw the flag and ended
        self.assertLess(model.written, 400)
        self.app.processEvents()
        self.assertIs(self.page.chats.blocks[-1], block)         # nothing arrived after it
        self.assertEqual(self.page.chats.history, [])


if __name__ == "__main__":
    unittest.main()
