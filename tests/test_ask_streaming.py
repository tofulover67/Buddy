"""Ask Buddy's answers as they're written, stopped, and remembered:
llm.py's streamed requests for each provider (never a real one - _stream
and _post are replaced), Anthropic's cache points, and the agent's side of
it (app/pages/manual_chat/agent.py): the draft cleared when the model goes
on to a tool, Stop, and older turns folded into a summary."""

import unittest
from unittest import mock

import _paths  # noqa: F401
from pages.manual_chat import llm
from pages.manual_chat.agent import ManualAgent
from pages.manual_chat.llm import LLMCancelled, LLMError, Reply, ToolCall

ASK = [{"role": "user", "content": "How do I add a marker?"}]
PING = [{"name": "search_manual", "description": "", "parameters": {"type": "object", "properties": {}}}]


def streamed(events, sent=None):
    def stream(url, payload, headers, label, timeout, cancelled=None):
        if sent is not None:
            sent.update(url=url, payload=payload, headers=headers)
        for event in events:
            if cancelled is not None and cancelled():
                raise LLMCancelled("Stopped.")
            yield event
    return stream


class StreamTests(unittest.TestCase):
    def setUp(self):
        llm._NO_STREAM.clear()
        self.addCleanup(llm._NO_STREAM.clear)

    def test_openai_shaped_text_and_a_tool_call_in_pieces(self):
        events = [
            {"choices": []},                                            # Azure's opening event
            {"choices": [{"delta": {"content": "Press "}}]},
            {"choices": [{"delta": {"content": "M."}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1",
                                                    "function": {"name": "search_manual", "arguments": '{"que'}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'ry": "markers"}'}}]}}]},
        ]
        heard, sent = [], {}
        with mock.patch.object(llm, "_stream", streamed(events, sent)):
            reply = llm.LLMClient(llm.PROVIDER_OPENAI_API, "key", "m").chat("sys", ASK, PING, on_text=heard.append)
        self.assertEqual((heard, reply.content), (["Press ", "M."], "Press M."))
        self.assertEqual([(c.id, c.name, c.arguments) for c in reply.tool_calls],
                         [("c1", "search_manual", {"query": "markers"})])
        self.assertIs(sent["payload"]["stream"], True)

    def test_anthropic_blocks(self):
        events = [
            {"type": "message_start"},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Looking"}},
            {"type": "content_block_start", "index": 1,
             "content_block": {"type": "tool_use", "id": "t1", "name": "search_manual", "input": {}}},
            {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": '{"query":'}},
            {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": ' "fades"}'}},
            {"type": "message_delta", "delta": {"stop_reason": "tool_use"}},
        ]
        heard = []
        with mock.patch.object(llm, "_stream", streamed(events)):
            reply = llm.LLMClient(llm.PROVIDER_ANTHROPIC, "key", "m").chat("sys", ASK, PING, on_text=heard.append)
        self.assertEqual((heard, reply.content), (["Looking"], "Looking"))
        self.assertEqual([(c.id, c.name, c.arguments) for c in reply.tool_calls],
                         [("t1", "search_manual", {"query": "fades"})])

    def test_gemini_pieces_keep_the_thought_signature(self):
        events = [
            {"candidates": [{"content": {"parts": [{"text": "It's "}]}}]},
            {"candidates": [{"content": {"parts": [{"text": "thinking aloud", "thought": True}, {"text": "M."}]}}]},
            {"candidates": [{"content": {"parts": [{"functionCall": {"name": "search_manual", "args": {"query": "x"}},
                                                    "thoughtSignature": "sig"}]}}]},
        ]
        heard, sent = [], {}
        with mock.patch.object(llm, "_stream", streamed(events, sent)):
            reply = llm.LLMClient(llm.PROVIDER_GEMINI, "key", "gem").chat("sys", ASK, PING, on_text=heard.append)
        self.assertEqual((heard, reply.content), (["It's ", "M."], "It's M."))      # its working isn't the answer
        self.assertEqual(reply.tool_calls[0].signature, "sig")
        self.assertIn("streamGenerateContent?alt=sse", sent["url"])

    def test_a_model_that_starts_thinking_again_after_its_answer_is_cut_short(self):
        # Measured with a local thinking model: the answer written, then
        # minutes of "reasoning" and nothing more to show.
        thinking = [{"choices": [{"delta": {"content": "", "reasoning": "hm " * 20}}]}] * 200
        events = (thinking[:5]                                          # thinking first is how they work
                  + [{"choices": [{"delta": {"content": "Press M."}}]}, {"choices": [{"delta": {"content": "\n\n"}}]}]
                  + thinking + [{"choices": [{"delta": {"content": "never reached"}}]}])
        read = []

        def stream(*_a, **_k):
            for event in events:
                read.append(event)
                yield event

        with mock.patch.object(llm, "_stream", stream):
            reply = llm.LLMClient(llm.PROVIDER_OLLAMA, "", "m").chat("sys", ASK, PING, on_text=lambda piece: None)
        self.assertEqual(reply.content.strip(), "Press M.")
        self.assertLess(len(read), 60)                                  # let go of long before the end

    def test_thinking_between_the_answers_words_or_before_a_tool_call_is_left_alone(self):
        some = {"choices": [{"delta": {"content": "", "reasoning": "x" * (llm.AFTERTHOUGHT_CHARS // 2)}}]}
        call = {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1",
                                                       "function": {"name": "search_manual", "arguments": "{}"}}]}}]}
        events = [{"choices": [{"delta": {"content": "Let me look. "}}]}, some,
                  {"choices": [{"delta": {"content": "One moment."}}]}, some, call, some, some, some]
        with mock.patch.object(llm, "_stream", streamed(events)):
            reply = llm.LLMClient(llm.PROVIDER_OLLAMA, "", "m").chat("sys", ASK, PING, on_text=lambda piece: None)
        self.assertEqual(reply.content, "Let me look. One moment.")
        self.assertEqual([c.name for c in reply.tool_calls], ["search_manual"])   # a call under way is never dropped

    def test_stopping_part_way(self):
        events = [{"choices": [{"delta": {"content": word}}]} for word in ("one ", "two ", "three")]
        heard = []
        with mock.patch.object(llm, "_stream", streamed(events)), self.assertRaises(LLMCancelled):
            llm.LLMClient(llm.PROVIDER_OPENAI_API, "key", "m").chat(
                "sys", ASK, on_text=heard.append, cancelled=lambda: len(heard) >= 2)
        self.assertEqual(heard, ["one ", "two "])

    def test_a_server_that_cant_stream_is_asked_the_ordinary_way_and_remembered(self):
        asked = []

        def refuse(*_a, **_k):
            asked.append("stream")
            error = LLMError("LLM: HTTP 400 – stream is not supported with tools")
            error.code = 400
            raise error
            yield                               # (a generator, like the real one)

        def post(url, payload, headers, label, timeout):
            asked.append("plain")
            return {"choices": [{"message": {"content": "whole"}}]}

        client = llm.LLMClient(llm.PROVIDER_OPENAI, "", "m", "http://127.0.0.1:9/v1")
        with mock.patch.object(llm, "_stream", refuse), mock.patch.object(llm, "_post", post):
            self.assertEqual(client.chat("sys", ASK, on_text=lambda piece: None).content, "whole")
            self.assertEqual(client.chat("sys", ASK, on_text=lambda piece: None).content, "whole")
        self.assertEqual(asked, ["stream", "plain", "plain"])          # not tried a second time

    def test_a_wrong_key_is_not_mistaken_for_no_streaming(self):
        def refuse(*_a, **_k):
            error = LLMError("OpenAI: HTTP 401 – bad key")
            error.code = 401
            raise error
            yield

        with mock.patch.object(llm, "_stream", refuse), mock.patch.object(llm, "_post") as post, \
                self.assertRaises(LLMError) as caught:
            llm.LLMClient(llm.PROVIDER_OPENAI_API, "key", "m").chat("sys", ASK, on_text=lambda piece: None)
        self.assertIn("401", str(caught.exception))
        post.assert_not_called()

    def test_events_are_read_off_the_wire(self):
        lines = [b": keep-alive\n", b"event: message\n", b'data: {"n": 1}\n', b"\n", b"data: not json\n",
                 b'data: {"n": 2}\n', b"data: [DONE]\n", b'data: {"n": 3}\n']

        class Response(list):
            def __enter__(self):
                return iter(self)

            def __exit__(self, *_a):
                return False

        with mock.patch.object(llm.safe_http, "urlopen", return_value=Response(lines)):
            got = list(llm._stream("https://api.example.com/v1", {}, {}, "LLM", 5))
        self.assertEqual(got, [{"n": 1}, {"n": 2}])


class CachePointTests(unittest.TestCase):
    def test_anthropic_marks_the_system_prompt_and_the_newest_message(self):
        sent = {}
        messages = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "t1", "name": "search_manual", "arguments": {}}]},
            {"role": "tool", "tool_call_id": "t1", "name": "search_manual", "content": "an excerpt"},
        ]
        with mock.patch.object(llm, "_post", lambda url, payload, *a: sent.update(payload) or
                               {"content": [{"type": "text", "text": "ok"}]}):
            llm.LLMClient(llm.PROVIDER_ANTHROPIC, "key", "m").chat("the rules", messages, PING)
        point = {"type": "ephemeral"}
        self.assertEqual(sent["system"], [{"type": "text", "text": "the rules", "cache_control": point}])
        self.assertNotIn("cache_control", sent["messages"][0]["content"][0])
        self.assertEqual(sent["messages"][-1]["content"][-1]["cache_control"], point)


class Scripted:
    """An LLM that gives its replies in turn, streaming each one's text first."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def chat(self, system, messages, tools, on_text=None, cancelled=None):
        self.calls.append({"system": system, "messages": list(messages), "tools": tools})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if on_text is not None:
            for word in reply.content.split(" "):
                if cancelled is not None and cancelled():
                    raise LLMCancelled("Stopped.")
                on_text(word + " ")
        return reply


class Manual:
    tier = "bundle-bm25"

    def search(self, query, limit=5):
        return []

    def describe_tier(self):
        return "Keyword search."


class AgentStreamTests(unittest.TestCase):
    def test_what_came_before_a_tool_call_is_cleared_from_the_draft(self):
        fake = Scripted(Reply("Let me look.", [ToolCall("1", "search_manual", {"query": "markers"})]),
                        Reply("Press M.\n\nFollow-ups: How do I colour one? | How do I delete one?"))
        heard = []
        result = ManualAgent(Manual(), fake).ask("How do I add a marker?", on_text=heard.append)
        self.assertEqual(result.answer, "Press M.")                      # the follow-ups line is taken off...
        self.assertEqual(result.followups, ["How do I colour one?", "How do I delete one?"])   # ...and kept
        self.assertEqual(heard.count(None), 1)
        self.assertLess(heard.index("look. "), heard.index(None))        # cleared after the preamble
        self.assertLess(heard.index(None), heard.index("Press "))

    def test_stop_between_steps(self):
        fake = Scripted(Reply("", [ToolCall("1", "search_manual", {"query": "x"})]), Reply("never asked for"))
        stopped = []
        agent = ManualAgent(Manual(), fake)
        result = agent.ask("Q", cancelled=lambda: bool(stopped), progress=lambda m: m.startswith("Searching") and stopped.append(1))
        self.assertTrue(result.cancelled)
        self.assertEqual((result.answer, result.error), ("", ""))
        self.assertEqual(len(fake.calls), 1)

    def test_stop_while_it_streams(self):
        fake = Scripted(Reply("one two three four"))
        heard = []
        result = ManualAgent(Manual(), fake).ask("Q", on_text=heard.append, cancelled=lambda: len(heard) >= 2)
        self.assertTrue(result.cancelled)
        self.assertEqual(heard, ["one ", "two "])

    def test_without_streaming_the_llm_is_called_as_it_always_was(self):
        class Plain:
            def chat(self, system, messages, tools):         # no on_text: an older fake, or Transcribe's
                return Reply("Whole.")
        self.assertEqual(ManualAgent(Manual(), Plain()).ask("Q").answer, "Whole.")

    def test_older_turns_are_folded_into_the_summary_first(self):
        fake = Scripted(Reply("They cut a 24 fps trailer."), Reply("Use 24."))
        history = [{"role": "user", "content": "I'm cutting a trailer at 24 fps"},
                   {"role": "assistant", "content": "Noted."},
                   {"role": "user", "content": "Which codec?"}, {"role": "assistant", "content": "ProRes."}]
        result = ManualAgent(Manual(), fake).ask("What frame rate again?", history, summary="", fold=2)
        self.assertEqual(result.summary, "They cut a 24 fps trailer.")
        summarising, answering = fake.calls
        self.assertIsNone(summarising["tools"])                          # a plain request, no tools
        self.assertIn("I'm cutting a trailer", summarising["messages"][0]["content"])
        self.assertNotIn("Which codec?", summarising["messages"][0]["content"])     # still in the history proper
        self.assertIn("They cut a 24 fps trailer.", answering["system"])
        self.assertEqual([m["content"] for m in answering["messages"]],
                         ["Which codec?", "ProRes.", "What frame rate again?"])

    def test_a_summary_that_fails_leaves_the_turns_as_they_were(self):
        fake = Scripted(LLMError("LLM: HTTP 500"), Reply("Use 24."))
        history = [{"role": "user", "content": "old question"}, {"role": "assistant", "content": "old answer"}]
        result = ManualAgent(Manual(), fake).ask("New?", history, summary="Earlier: a trailer.", fold=2)
        self.assertIsNone(result.summary)
        self.assertEqual(result.answer, "Use 24.")
        self.assertEqual(len(fake.calls[1]["messages"]), 3)              # nothing dropped
        self.assertIn("Earlier: a trailer.", fake.calls[1]["system"])    # the old summary still goes


if __name__ == "__main__":
    unittest.main()
