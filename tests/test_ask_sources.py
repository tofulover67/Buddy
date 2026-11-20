"""What Ask Buddy reads and what its answers cite: the manual search's
re-ranking, context and whole pages (retrieval.py), Buddy's own docs
(buddy_docs.py), the agent's tools for both, and the conversation's side -
sources, the check on cited pages, follow-ups, tables and nested lists,
pinned chats, the last turn, the summary's overflow, Markdown export."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _paths  # noqa: F401
from pages.manual_chat import buddy_docs, retrieval
from pages.manual_chat.agent import ManualAgent, PREFILL_TOOLS, tool_specs
from pages.manual_chat.conversation import (
    BUDDY,
    SUMMARY_BATCH,
    YOU,
    ChatSessions,
    block_view,
    cited_pages,
    followups_view,
    hide_followups,
    md_to_html,
    sources_from,
    split_followups,
    unverified_pages,
)
from pages.manual_chat.llm import Reply, ToolCall
from pages.manual_chat.retrieval import Passage

CHUNKS = [
    {"text": "To add a Power Window, open the Window palette.", "page": 10, "chapter_no": 3, "chapter_title": "Windows"},
    {"text": "Then click the shape you want, and drag its handles to", "page": 10, "chapter_no": 3, "chapter_title": "Windows"},
    {"text": "resize it. Softness is set in the same palette.", "page": 11, "chapter_no": 3, "chapter_title": "Windows"},
    {"text": "Window window window window window window", "page": 40, "chapter_no": 7, "chapter_title": "Fusion"},
    {"text": "Keyframes animate a parameter over time.", "page": 41, "chapter_no": 7, "chapter_title": "Fusion"},
    {"text": "Render settings for delivery are on the Deliver page.", "page": 90, "chapter_no": 9, "chapter_title": "Deliver"},
]


def bundle(chunks=CHUNKS):
    folder = Path(tempfile.mkdtemp())
    (folder / retrieval.BUNDLE_CHUNKS).write_text("\n".join(json.dumps(c) for c in chunks), encoding="utf-8")
    return retrieval.ManualRetriever(folder, None, None)


class RetrievalTests(unittest.TestCase):
    def test_a_chunk_that_covers_the_question_beats_one_that_repeats_a_word(self):
        r = bundle()
        fused = [(3, 1.0), (0, 0.9)]                                    # six "window"s came first
        self.assertEqual([i for i, _ in r._rerank("open the window palette", fused)], [0, 3])
        self.assertEqual(r._rerank("the", fused), fused)                # nothing to go on: left alone
        self.assertEqual(r.search("add a power window", 1)[0].page, 10)

    def test_the_best_hit_brings_what_follows_it(self):
        found = bundle().search("add a power window", 1)[0]
        self.assertIn("open the Window palette", found.text)
        self.assertIn("Then click the shape", found.text)                # the chunk after it, same chapter
        self.assertEqual((found.page, found.chapter_no), (10, 3))

    def test_neighbouring_hits_become_one_passage_and_the_freed_places_are_filled(self):
        found = bundle().search("window palette shape handles resize softness", 3)
        self.assertIn("open the Window palette", found[0].text)          # chunks 0-2, as one
        self.assertIn("resize it.", found[0].text)
        self.assertEqual([p.index for p in found], [0, 3])               # and the next best takes a freed place

    def test_context_stops_at_the_chapter(self):
        found = bundle().search("resize softness palette", 1)[0]
        self.assertNotIn("Window window", found.text)                    # the next chunk is another chapter's

    def test_whole_pages(self):
        r = bundle()
        self.assertEqual([p.text for p in r.page_passages(10)], [CHUNKS[0]["text"], CHUNKS[1]["text"]])
        self.assertEqual(len(r.page_passages(10, 11)), 3)
        self.assertEqual(r.page_passages(500), [])
        self.assertEqual(r.last_page, 90)


class DocsTests(unittest.TestCase):
    README = ("# Buddy\n\nA toolkit.\n\n## Ask Buddy\n\nChats are saved in conversations.json.\n\n"
              "### Changing your project\n\nOff by default.\n\n```\n# not a heading\n```\n\n## Pets\n\nA blob walks about.\n")

    def test_sections_carry_their_heading_path(self):
        titles = [(p.title, p.text.split("\n")[0]) for p in buddy_docs.split_markdown(self.README)]
        self.assertIn(("Buddy > Ask Buddy", "Chats are saved in conversations.json."), titles)
        self.assertIn(("Buddy > Ask Buddy > Changing your project", "Off by default."), titles)
        self.assertIn(("Buddy > Pets", "A blob walks about."), titles)
        self.assertFalse(any("not a heading" in title for title, _ in titles))

    def test_search_finds_the_readme_and_the_tools(self):
        folder = Path(tempfile.mkdtemp())
        (folder / "README.md").write_text(self.README, encoding="utf-8")
        docs = buddy_docs.BuddyDocs(None, folder / "README.md")
        self.assertEqual(docs.search("where are chats saved")[0].title, "Buddy > Ask Buddy")
        self.assertTrue(any(p.title == "Tool: Media Relink" for p in docs.search("relink offline media files")))

    def test_the_real_readme_is_found(self):
        self.assertIsNotNone(buddy_docs.readme_path())


class Scripted:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def chat(self, system, messages, tools):
        self.calls.append(list(messages))
        return self.replies.pop(0)


class AgentToolTests(unittest.TestCase):
    def test_reading_a_page_counts_as_a_source(self):
        fake = Scripted(Reply("", [ToolCall("1", "read_manual_page", {"page": 10, "through": 99})]),
                        Reply("Drag its handles (Chapter 3, p. 11)."))
        result = ManualAgent(bundle(), fake).ask("How do I resize a window?")
        sent = fake.calls[1][-1]["content"]
        self.assertIn("Then click the shape", sent)
        self.assertIn("resize it.", sent)                                # page 11: `through` is capped, not refused
        self.assertNotIn("Window window", sent)                          # ...at three pages
        self.assertEqual({p.page for p in result.passages}, {10, 11})
        self.assertEqual(result.events[0].summary, "pp.10–12")

    def test_a_page_with_nothing_on_it_says_how_far_the_manual_goes(self):
        fake = Scripted(Reply("", [ToolCall("1", "read_manual_page", {"page": 5000})]), Reply("Not there."))
        ManualAgent(bundle(), fake).ask("Q")
        self.assertIn("from 1 to 90", fake.calls[1][-1]["content"])

    def test_buddys_own_docs(self):
        fake = Scripted(Reply("", [ToolCall("1", "search_buddy_docs", {"query": "relink offline media"})]),
                        Reply("Media Relink does that (Buddy docs)."))
        result = ManualAgent(bundle(), fake).ask("Can Buddy relink?")
        self.assertIn("[Buddy docs – ", fake.calls[1][-1]["content"])
        self.assertEqual(result.events[0].name, "search_buddy_docs")

    def test_an_offer_carries_a_prefill_only_for_a_tool_that_takes_one(self):
        self.assertIn("footage_search", PREFILL_TOOLS)
        registry = None
        agent = ManualAgent(bundle(), Scripted(), registry)
        from core import tools_kb
        with mock.patch.object(tools_kb.ToolInfo, "is_available", True):
            from pages.manual_chat.agent import AgentResult
            result = AgentResult()
            agent._do_offer_tool({"tool_id": "footage_search", "reason": "r", "prefill": " a drone  shot "}, result)
            self.assertEqual(result.offered_prefill, "a drone shot")
            said = agent._do_offer_tool({"tool_id": "media_relink", "reason": "r", "prefill": "x"}, result)
            self.assertEqual(result.offered_prefill, "")
            self.assertIn("doesn't take a prefill", said)

    def test_the_new_tools_are_on_offer(self):
        names = [spec["name"] for spec in tool_specs([("media_relink", "Media Relink")])]
        self.assertEqual(names, ["search_manual", "read_manual_page", "search_buddy_docs", "project_state",
                                 "describe_tool", "offer_tool"])
        with_writes = tool_specs([("media_relink", "Media Relink")], allow_writes=True)[-1]
        self.assertEqual(with_writes["name"], "propose_action")
        self.assertIn("steps", with_writes["parameters"]["properties"])


class MarkdownTests(unittest.TestCase):
    def test_tables(self):
        out = md_to_html("| Codec | Use |\n|---|:-:|\n| ProRes | **edit** |\n| H.264 |\nafter")
        self.assertIn("<table><thead><tr><th>Codec</th><th>Use</th></tr></thead>", out)
        self.assertIn("<tr><td>ProRes</td><td><b>edit</b></td></tr>", out)
        self.assertIn("<tr><td>H.264</td><td></td></tr>", out)           # a short row is padded
        self.assertTrue(out.endswith("<p>after</p>"))
        self.assertNotIn("<table>", md_to_html("a | b\nnot a rule"))
        self.assertIn("&lt;script&gt;", md_to_html("| <script> |\n|---|\n| x |"))

    def test_nested_lists(self):
        out = md_to_html("1. one\n   - a\n   - b\n     1. deep\n2. two")
        self.assertEqual(out, "<ol><li>one<ul><li>a</li><li>b<ol><li>deep</li></ol></li></ul></li><li>two</li></ol>")
        self.assertEqual(md_to_html("- a\n- b"), "<ul><li>a</li><li>b</li></ul>")
        self.assertEqual(md_to_html("- a\n1. b"), "<ul><li>a</li></ul><ol><li>b</li></ol>")

    def test_every_source_label_the_prompt_asks_for_is_styled(self):
        for label in ("general knowledge", "your project", "Buddy docs", "their project"):
            self.assertIn(f'<span class="source">({label})</span>', md_to_html(f"A fact ({label})"), label)


class CitationTests(unittest.TestCase):
    def test_cited_pages(self):
        text = "a (Chapter 3, p. 12) b (Chapter 55 – Fades – pp.1198–1202) c (Chapter 2, pp. 4, 9 and 11) d (p. 7)"
        self.assertEqual(cited_pages(text), [(12, 12), (1198, 1202), (4, 4), (9, 9), (11, 11)])

    def test_a_page_never_read_is_named(self):
        answer = "One (Chapter 3, p. 12). Two (Chapter 55, pp.1198–1202). Three (Chapter 9, p. 400)."
        self.assertEqual(unverified_pages(answer, {12, 1200}), ["400"])  # a range passes on any page in it
        self.assertEqual(unverified_pages(answer, set()), ["12", "1198–1202", "400"])
        self.assertEqual(unverified_pages("No citations (general knowledge).", set()), [])

    def test_sources_are_one_per_page_with_the_start_of_the_text(self):
        passages = [Passage("A " * 300, 10, 3, "Windows"), Passage("again", 10, 3, "Windows"),
                    Passage("Deliver it.", 90, 9, "Deliver"), Passage("no page")]
        sources = sources_from(passages)
        self.assertEqual([(s["page"], s["cite"]) for s in sources],
                         [(10, "Chapter 3 – Windows – p.10"), (90, "Chapter 9 – Deliver – p.90")])
        self.assertTrue(sources[0]["snippet"].endswith("…"))

    def test_followups_come_off_the_answer(self):
        answer, asks = split_followups("Press M.\n\nFollow-ups: How do I colour one? | How do I delete one? | "
                                       "Can I name it? | A fourth?")
        self.assertEqual(answer, "Press M.")
        self.assertEqual(asks, ["How do I colour one?", "How do I delete one?", "Can I name it?"])
        self.assertEqual(split_followups("Just an answer."), ("Just an answer.", []))
        self.assertEqual(split_followups("Body\n**Follow-up questions:**\n- One thing?\n- Another thing?")[1],
                         ["One thing?", "Another thing?"])
        self.assertEqual(hide_followups("Press M.\n\nFollow-ups: How do I col"), "Press M.")    # while it's written
        self.assertEqual(hide_followups("Press M."), "Press M.")


class ConversationTests(unittest.TestCase):
    def turn(self, chats, question, answer, limit=2):
        chats.add(YOU, question, raw=True, asked=True)
        chats.add(BUDDY, answer, raw=True, answer=True)
        chats.record_turn(question, answer, limit)

    def test_the_last_turn_can_be_taken_off(self):
        chats = ChatSessions()
        self.assertIsNone(chats.last_turn())
        self.turn(chats, "first?", "one")
        self.turn(chats, "second?", "two")
        self.assertEqual(chats.last_turn(), (3, 4))
        self.assertEqual(chats.drop_last_turn()["body"], "second?")
        self.assertEqual([m["content"] for m in chats.history], ["first?", "one"])
        self.assertEqual([b["body"] for b in chats.blocks][1:], ["first?", "one"])

    def test_a_question_that_errored_or_was_stopped_can_be_asked_again(self):
        chats = ChatSessions()
        self.turn(chats, "first?", "one")
        chats.add(YOU, "second?", asked=True)
        chats.add("Error", "HTTP 500", error=True)
        self.assertEqual(chats.last_turn(), (3, None))
        chats.drop_last_turn()
        self.assertEqual(len(chats.history), 2)                          # the first turn's, untouched
        chats.add(YOU, "third?", asked=True)
        chats.add(BUDDY, "half an ans", answer=True, stopped=True)       # stopped: never in the history
        self.assertEqual(chats.last_turn(), (3, 4))
        chats.drop_last_turn()
        self.assertEqual([m["content"] for m in chats.history], ["first?", "one"])

    def test_only_a_question_to_the_model_counts(self):
        chats = ChatSessions()
        chats.add(YOU, "help")
        chats.add(BUDDY, "I can...")
        self.assertIsNone(chats.last_turn())                             # answered here, not by the model
        self.turn(chats, "real?", "yes")
        chats.add(BUDDY, "Applied.")
        self.assertIsNone(chats.last_turn())                             # something has happened since

    def test_turns_leaving_the_history_wait_to_be_summarised(self):
        chats = ChatSessions()
        for n in range(2 + SUMMARY_BATCH - 1):
            self.turn(chats, f"q{n}", f"a{n}", limit=2)
        self.assertEqual(chats.to_fold(), 0)                             # not enough waiting yet
        self.assertEqual(len(chats.history_for_model()), (2 + SUMMARY_BATCH - 1) * 2)   # ...so they still go
        self.turn(chats, "one more", "ok", limit=2)
        self.assertEqual(chats.to_fold(), SUMMARY_BATCH * 2)
        chats.folded("They asked q0 to q2.", chats.to_fold())
        self.assertEqual((chats.current.summary, chats.current.overflow), ("They asked q0 to q2.", []))
        self.assertEqual(len(chats.history_for_model()), 4)
        chats.trim(0)                                                    # memory switched off: nothing is kept
        self.assertEqual((chats.current.summary, chats.history_for_model()), ("", []))

    def test_everything_new_survives_a_save(self):
        chats = ChatSessions()
        chats.add(YOU, "q", asked=True)
        chats.add(BUDDY, "a (Chapter 3, p. 10)", answer=True, followups=["Next?"], unverified=["400"],
                  sources=[{"cite": "Chapter 3 – Windows – p.10", "page": 10, "snippet": "To add"}])
        chats.record_turn("q", "a (Chapter 3, p. 10)", 6)
        chats.current.summary = "Earlier: a trailer."
        chats.current.overflow = [{"role": "user", "content": "x"}, {"role": "assistant", "content": "y"}]
        chats.current.offer = {"tool_id": "footage_search", "label": "Open Footage Search", "reason": "r",
                               "prefill": "a drone shot"}
        chats.pin(0, True)
        again = ChatSessions.from_data(json.loads(json.dumps(chats.to_data())))
        self.assertEqual(again.to_data(), chats.to_data())
        self.assertEqual(again.last_turn(), (1, 2))
        view = block_view(2, again.blocks[2])
        self.assertEqual((view["sources"][0]["page"], view["unverified"]), (10, ["400"]))
        self.assertEqual(followups_view(again.blocks[2]), {"items": ["Next?"], "raw": True})

    def test_a_damaged_save_doesnt_bring_bad_sources_in(self):
        data = ChatSessions().to_data()
        data["chats"][0]["blocks"][0].update(sources=[{"cite": 5, "page": "10"}, "x", {"cite": "ok", "page": 3}],
                                             followups=[1, "Fine?"], unverified="12")
        data["chats"][0].update(overflow=[{"role": "user", "content": "half a turn"}], summary=7, pinned="yes")
        chat = ChatSessions.from_data(data).current
        self.assertEqual(chat.blocks[0]["sources"], [{"cite": "ok", "page": 3, "snippet": ""}])
        self.assertEqual((chat.blocks[0]["followups"], chat.blocks[0]["unverified"]), (["Fine?"], []))
        self.assertEqual((chat.overflow, chat.summary, chat.pinned), ([], "", False))

    def test_pinned_chats_come_first(self):
        chats = ChatSessions()
        chats.add(YOU, "oldest")
        chats.new_chat()
        chats.add(YOU, "middle")
        chats.new_chat()
        chats.add(YOU, "newest")
        self.assertEqual([s["title"] for s in chats.summaries()], ["newest", "middle", "oldest"])
        self.assertTrue(chats.pin(0, True))
        self.assertEqual([(s["title"], s["pinned"]) for s in chats.summaries()],
                         [("oldest", True), ("newest", False), ("middle", False)])
        self.assertFalse(chats.pin(9, True))

    def test_markdown_export_keeps_the_answer_as_written(self):
        chats = ChatSessions()
        chats.add(YOU, "How do I add a window?")
        chats.add(BUDDY, "1. Open the **Window** palette (Chapter 3, p. 10)", trace=["search_manual: x"],
                  sources=[{"cite": "Chapter 3 – Windows – p.10", "page": 10, "snippet": ""}], unverified=["400"])
        out = chats.export_markdown()
        self.assertTrue(out.startswith("# How do I add a window?\n"))
        self.assertIn("1. Open the **Window** palette (Chapter 3, p. 10)", out)
        self.assertIn("**Sources**\n- Chapter 3 – Windows – p.10", out)
        self.assertIn("p. 400", out)
        self.assertNotIn("**Window**", chats.export_text())             # the plain one still strips


if __name__ == "__main__":
    unittest.main()
