"""Ask Buddy's LLM transports (app/pages/manual_chat/llm.py): the request
shapes each provider insists on, where each one lives, when a key is
needed, and Test connection. Never a real network request - _post and
urlopen are replaced."""

import io
import json
import unittest
from unittest import mock

import _paths  # noqa: F401
from pages.manual_chat import llm


class GeminiTests(unittest.TestCase):
    def test_parallel_tool_results_go_in_one_user_turn(self):
        # Gemini answers HTTP 400 unless every functionResponse for one
        # model turn arrives in a single user turn.
        sent = {}

        def fake_post(url, payload, headers, label, timeout):
            sent.update(payload)
            return {"candidates": [{"content": {"parts": [{"text": "done"}]}}]}

        client = llm.LLMClient(llm.PROVIDER_GEMINI, "key", "gemini-model")
        messages = [
            {"role": "user", "content": "What's in my project?"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "a", "name": "list_bins", "arguments": {}},
                {"id": "b", "name": "list_timelines", "arguments": {}}]},
            {"role": "tool", "name": "list_bins", "content": "Master"},
            {"role": "tool", "name": "list_timelines", "content": "Edit 1"},
            {"role": "user", "content": "Thanks"},
        ]
        with mock.patch.object(llm, "_post", fake_post):
            reply = client.chat("system", messages)
        self.assertEqual(reply.content, "done")
        roles = [c["role"] for c in sent["contents"]]
        self.assertEqual(roles, ["user", "model", "user", "user"])
        results = sent["contents"][2]["parts"]
        self.assertEqual([p["functionResponse"]["name"] for p in results],
                         ["list_bins", "list_timelines"])
        self.assertEqual(sent["contents"][3]["parts"], [{"text": "Thanks"}])


def fake_openai(sent, tool_call=False):
    def post(url, payload, headers, label, timeout):
        sent.update(url=url, payload=payload, headers=headers, label=label, timeout=timeout)
        message = {"content": "hi"}
        if tool_call:
            message["tool_calls"] = [{"id": "1", "function": {"name": "ping", "arguments": "{}"}}]
        return {"choices": [{"message": message}]}
    return post


class ProviderTests(unittest.TestCase):
    def test_addresses(self):
        n = llm.normalize_base_url
        self.assertEqual(n(llm.PROVIDER_LLAMACPP, "192.168.1.20:8080"), "http://192.168.1.20:8080/v1")
        self.assertEqual(n(llm.PROVIDER_OLLAMA, "http://box:11434/"), "http://box:11434/v1")
        self.assertEqual(n(llm.PROVIDER_LMSTUDIO, "http://127.0.0.1:1234/v1"), "http://127.0.0.1:1234/v1")
        self.assertEqual(n(llm.PROVIDER_OPENAI, "https://api.x.ai/v1/"), "https://api.x.ai/v1")   # custom: as typed
        self.assertEqual(llm.LLMClient(llm.PROVIDER_GROQ, "k", "m").base_url, "https://api.groq.com/openai/v1")
        for url, local in (("http://localhost:8080", True), ("http://192.168.0.4", True), ("http://10.1.2.3", True),
                           ("http://172.20.0.1", True), ("http://172.40.0.1", False), ("https://api.openai.com", False),
                           ("http://studio-gpu.local:8000", True)):
            self.assertEqual(llm.is_local_url(url), local, url)

    def test_keys_are_only_asked_for_where_needed(self):
        self.assertEqual(llm.LLMClient(llm.PROVIDER_LLAMACPP, "", "").validate(), "")   # no key, no model: fine
        self.assertIn("No API key", llm.LLMClient(llm.PROVIDER_OPENAI_API, "", "gpt").validate())
        self.assertIn("No model", llm.LLMClient(llm.PROVIDER_LMSTUDIO, "", "").validate())
        custom = llm.LLMClient(llm.PROVIDER_OPENAI, "", "m", "http://192.168.1.9:8000/v1")
        self.assertEqual(custom.validate(), "")                 # keyless over http on the network
        self.assertTrue(custom.local)
        keyed = llm.LLMClient(llm.PROVIDER_OPENAI, "secret", "m", "http://192.168.1.9:8000/v1")
        self.assertIn("unencrypted", keyed.validate())          # a key never goes out over plain http...
        self.assertEqual(llm.LLMClient(llm.PROVIDER_LLAMACPP, "secret", "", "localhost:8080").validate(), "")  # ...except to this PC
        self.assertIn("base URL", llm.LLMClient(llm.PROVIDER_OPENAI, "", "m").validate())
        self.assertIn("endpoint", llm.LLMClient(llm.PROVIDER_AZURE, "k", "dep").validate())
        self.assertIn("deployment", llm.LLMClient(llm.PROVIDER_AZURE, "k", "", "https://r.openai.azure.com").validate())
        self.assertFalse(llm.LLMClient(llm.PROVIDER_OPENAI_API, "k", "m").local)
        self.assertTrue(llm.LLMClient(llm.PROVIDER_OLLAMA, "", "gemma4:12b").local)
        self.assertFalse(llm.LLMClient(llm.PROVIDER_OLLAMA, "", "deepseek-v4-pro:cloud").local)   # Ollama's cloud
        self.assertEqual(llm.LLMClient(llm.PROVIDER_LMSTUDIO, "", "m").timeout, llm.LOCAL_TIMEOUT)

    def test_request_shapes(self):
        sent = {}
        with mock.patch.object(llm, "_post", fake_openai(sent)):
            llm.LLMClient(llm.PROVIDER_LLAMACPP, "", "").chat("sys", [{"role": "user", "content": "hi"}])
        self.assertEqual(sent["url"], "http://127.0.0.1:8080/v1/chat/completions")
        self.assertNotIn("Authorization", sent["headers"])     # no key, no header
        self.assertEqual((sent["label"], sent["payload"]["model"]), ("llama.cpp", "local"))
        with mock.patch.object(llm, "_post", fake_openai(sent)):
            llm.LLMClient(llm.PROVIDER_OPENROUTER, "or-key", "meta/llama").chat("sys", [])
        self.assertEqual(sent["headers"]["Authorization"], "Bearer or-key")
        self.assertEqual(sent["url"], "https://openrouter.ai/api/v1/chat/completions")
        with mock.patch.object(llm, "_post", fake_openai(sent)):
            llm.LLMClient(llm.PROVIDER_AZURE, "az-key", "my gpt", "https://r.openai.azure.com/").chat("sys", [])
        self.assertEqual(sent["url"], "https://r.openai.azure.com/openai/deployments/my%20gpt/chat/completions"
                                      f"?api-version={llm.AZURE_API_VERSION}")
        self.assertEqual(sent["headers"]["api-key"], "az-key")
        self.assertNotIn("Authorization", sent["headers"])

    def test_connection_test(self):
        client = llm.LLMClient(llm.PROVIDER_OPENAI_API, "k", "gpt-4.1")
        with mock.patch.object(llm, "_post", fake_openai({}, tool_call=True)):
            self.assertEqual(client.test(), (True, "Connected – gpt-4.1 answered and can use tools."))
        with mock.patch.object(llm, "_post", fake_openai({})):
            ok, text = llm.LLMClient(llm.PROVIDER_LLAMACPP, "", "").test()
        self.assertFalse(ok)
        self.assertIn("--jinja", text)
        with mock.patch.object(llm, "_post", side_effect=llm.LLMError("Groq: HTTP 401 - bad key")):
            self.assertEqual(llm.LLMClient(llm.PROVIDER_GROQ, "k", "m").test(), (False, "Groq: HTTP 401 - bad key"))
        self.assertEqual(llm.LLMClient(llm.PROVIDER_GROQ, "", "m").test()[0], False)   # stopped before sending

    def test_model_lists_leave_out_what_cant_chat(self):
        body = io.BytesIO(json.dumps({"data": [{"id": "gpt-4.1"}, {"id": "text-embedding-3-large"},
                                               {"id": "whisper-1"}, {"id": "gpt-4.1"}, {"id": "o4-mini"}]}).encode())
        with mock.patch.object(llm.safe_http, "urlopen", return_value=body) as opened:
            self.assertEqual(llm.list_openai_models("https://api.openai.com/v1", "k"), ["gpt-4.1", "o4-mini"])
        request = opened.call_args[0][0]
        self.assertEqual((request.full_url, request.get_header("Authorization")),
                         ("https://api.openai.com/v1/models", "Bearer k"))
        tags = io.BytesIO(json.dumps({"models": [{"name": "qwen3:14b"}, {"name": "nomic-embed-text"}]}).encode())
        with mock.patch.object(llm.safe_http, "urlopen", return_value=tags) as opened:
            self.assertEqual(llm.list_ollama_models("192.168.1.20:11434"), ["qwen3:14b"])
        self.assertEqual(opened.call_args[0][0], "http://192.168.1.20:11434/api/tags")
        with mock.patch.object(llm.safe_http, "urlopen", side_effect=OSError("refused")):
            self.assertEqual(llm.list_ollama_models(), [])
            with self.assertRaises(llm.LLMError):
                llm.list_openai_models("http://127.0.0.1:1234/v1")


class ModelListTests(unittest.TestCase):
    """Settings' Model dropdown: each provider's own list, read with the key
    and address it's set up with - urlopen faked, never the network."""

    def listed(self, client, body):
        with mock.patch.object(llm.safe_http, "urlopen", return_value=io.BytesIO(json.dumps(body).encode())) as opened:
            models = client.list_models()
        request = opened.call_args[0][0]
        return models, request

    def test_gemini_lists_what_can_chat_without_the_prefix(self):
        models, request = self.listed(llm.LLMClient(llm.PROVIDER_GEMINI, "g-key", ""), {"models": [
            {"name": "models/gemini-3.8-flash", "supportedGenerationMethods": ["generateContent", "countTokens"]},
            {"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"]},
            {"name": "models/gemini-3.8-pro", "supportedGenerationMethods": ["generateContent"]}]})
        self.assertEqual(models, ["gemini-3.8-flash", "gemini-3.8-pro"])
        self.assertTrue(request.full_url.startswith(llm.GEMINI_MODELS_URL))
        self.assertEqual(request.get_header("X-goog-api-key"), "g-key")

    def test_anthropic(self):
        models, request = self.listed(llm.LLMClient(llm.PROVIDER_ANTHROPIC, "a-key", ""),
                                      {"data": [{"id": "claude-sonnet-5"}, {"id": "claude-opus-5"}], "has_more": False})
        self.assertEqual(models, ["claude-opus-5", "claude-sonnet-5"])
        self.assertTrue(request.full_url.startswith(llm.ANTHROPIC_MODELS_URL))
        self.assertEqual((request.get_header("X-api-key"), request.get_header("Anthropic-version")),
                         ("a-key", llm.ANTHROPIC_VERSION))

    def test_openai_shaped_providers_ask_their_own_address(self):
        for provider, base in ((llm.PROVIDER_OPENAI_API, "https://api.openai.com/v1"),
                               (llm.PROVIDER_OPENROUTER, "https://openrouter.ai/api/v1"),
                               (llm.PROVIDER_GROQ, "https://api.groq.com/openai/v1")):
            models, request = self.listed(llm.LLMClient(provider, "k", ""), {"data": [{"id": "m1"}, {"id": "m2"}]})
            self.assertEqual((models, request.full_url, request.get_header("Authorization")),
                             (["m1", "m2"], f"{base}/models", "Bearer k"))
        models, request = self.listed(llm.LLMClient(llm.PROVIDER_OPENAI, "", "", base_url="https://api.example.com/v1"),
                                      {"data": [{"id": "custom-1"}]})
        self.assertEqual((models, request.full_url, request.get_header("Authorization")),
                         (["custom-1"], "https://api.example.com/v1/models", None))
        models, request = self.listed(llm.LLMClient(llm.PROVIDER_LMSTUDIO, "", ""), {"data": [{"id": "qwen3-14b"}]})
        self.assertEqual(request.full_url, "http://127.0.0.1:1234/v1/models")

    def test_ollama_lists_its_installed_models(self):
        models, request = self.listed(llm.LLMClient(llm.PROVIDER_OLLAMA, "", "", base_url="192.168.1.20:11434"),
                                      {"models": [{"name": "qwen3:14b"}, {"name": "nomic-embed-text"}]})
        self.assertEqual((models, request), (["qwen3:14b"], "http://192.168.1.20:11434/api/tags"))
        with mock.patch.object(llm.safe_http, "urlopen", side_effect=OSError("refused")):
            with self.assertRaises(llm.LLMError) as caught:
                llm.LLMClient(llm.PROVIDER_OLLAMA, "", "").list_models()
        self.assertIn("Couldn't reach", str(caught.exception))

    def test_azure_lists_the_resources_deployments(self):
        client = llm.LLMClient(llm.PROVIDER_AZURE, "az-key", "", base_url="https://r.openai.azure.com")
        models, request = self.listed(client, {"data": [{"id": "my-gpt", "model": "gpt-4.1"}, {"id": "mini"}]})
        self.assertEqual(models, ["mini", "my-gpt"])
        self.assertEqual(request.full_url, "https://r.openai.azure.com/openai/deployments"
                                           f"?api-version={llm.AZURE_LIST_API_VERSION}")
        self.assertEqual((request.get_header("Api-key"), request.get_header("Authorization")), ("az-key", None))

    def test_nothing_is_sent_without_a_key_or_an_address(self):
        with mock.patch.object(llm.safe_http, "urlopen") as opened:
            for client, why in ((llm.LLMClient(llm.PROVIDER_GEMINI, "", ""), "Add the API key to list the models."),
                                (llm.LLMClient(llm.PROVIDER_GROQ, "", ""), "Add the API key to list the models."),
                                (llm.LLMClient(llm.PROVIDER_OPENAI, "", ""), "Enter the base URL to list the models."),
                                (llm.LLMClient(llm.PROVIDER_AZURE, "k", ""), "Enter the endpoint to list the deployments.")):
                self.assertEqual(client.list_problem(), why)
                with self.assertRaises(llm.LLMError) as caught:
                    client.list_models()
                self.assertEqual(str(caught.exception), why)
        opened.assert_not_called()
        self.assertEqual(llm.LLMClient(llm.PROVIDER_OLLAMA, "", "").list_problem(), "")      # no key needed

    def test_a_refused_key_says_so(self):
        import urllib.error
        refused = urllib.error.HTTPError("https://api.anthropic.com/v1/models", 401, "Unauthorized", {}, None)
        with mock.patch.object(llm.safe_http, "urlopen", side_effect=refused):
            with self.assertRaises(llm.LLMError) as caught:
                llm.LLMClient(llm.PROVIDER_ANTHROPIC, "wrong", "").list_models()
        self.assertEqual(str(caught.exception), "Couldn't list the models: HTTP 401 – check the API key.")


class PictureTests(unittest.TestCase):
    """A question with a picture goes the way each provider takes images."""
    IMAGE = {"mime": "image/jpeg", "data": "QUJD"}
    ASK = [{"role": "user", "content": "What's this panel?", "images": [IMAGE]}]

    def sent(self, provider, reply):
        sent = {}

        def post(url, payload, headers, label, timeout):
            sent.update(payload)
            return reply

        with mock.patch.object(llm, "_post", post):
            llm.LLMClient(provider, "key", "model", base_url="http://127.0.0.1:1/v1").chat("system", self.ASK)
        return sent

    def test_gemini_inline_data(self):
        sent = self.sent(llm.PROVIDER_GEMINI, {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})
        self.assertEqual(sent["contents"][0]["parts"], [
            {"text": "What's this panel?"}, {"inline_data": {"mime_type": "image/jpeg", "data": "QUJD"}}])

    def test_anthropic_image_block_before_the_question(self):
        sent = self.sent(llm.PROVIDER_ANTHROPIC, {"content": [{"type": "text", "text": "ok"}]})
        self.assertEqual(sent["messages"][0]["content"], [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "QUJD"}},
            {"type": "text", "text": "What's this panel?"}])

    def test_openai_shaped_ones_get_a_data_url(self):
        for provider in (llm.PROVIDER_OPENAI_API, llm.PROVIDER_OLLAMA, llm.PROVIDER_OPENROUTER):
            sent = self.sent(provider, {"choices": [{"message": {"content": "ok"}}]})
            self.assertEqual(sent["messages"][1]["content"], [
                {"type": "text", "text": "What's this panel?"},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,QUJD"}}], provider)

    def test_turns_without_pictures_stay_plain_text(self):
        sent = {}
        with mock.patch.object(llm, "_post", lambda url, payload, *a: sent.update(payload) or
                               {"choices": [{"message": {"content": "ok"}}]}):
            llm.LLMClient(llm.PROVIDER_OPENAI_API, "key", "m").chat("s", [{"role": "user", "content": "hi"}])
        self.assertEqual(sent["messages"][1]["content"], "hi")


if __name__ == "__main__":
    unittest.main()
