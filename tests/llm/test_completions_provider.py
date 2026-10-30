import json
import os
import tempfile
import threading
import unittest
from unittest.mock import patch

import httpx

from src.llm.providers.completions import ChatCompletionsProvider, _guess_mime_type


class ChatCompletionsProviderTests(unittest.TestCase):
    def test_guess_mime_type_uses_image_suffixes(self):
        self.assertEqual(_guess_mime_type("shot.jpg"), "image/jpeg")
        self.assertEqual(_guess_mime_type("shot.png"), "image/png")
        self.assertEqual(_guess_mime_type("shot.webp"), "image/webp")
        self.assertEqual(_guess_mime_type("shot.unknown"), "image/jpeg")

    def test_provider_requires_configured_base_url(self):
        with self.assertRaisesRegex(ValueError, "requires baseUrl"):
            ChatCompletionsProvider(api_key="key", base_url="", model="model")

    def test_convert_history_maps_model_role(self):
        provider = ChatCompletionsProvider(
            api_key="key", base_url="https://example.test/v1", model="model"
        )
        try:
            self.assertEqual(
                provider._convert_history(
                    [
                        {"role": "user", "parts": ["hello", 123, " world"]},
                        {"role": "model", "parts": ["answer"]},
                        {"role": "assistant", "parts": ""},
                    ]
                ),
                [
                    {"role": "user", "content": "hello world"},
                    {"role": "assistant", "content": "answer"},
                ],
            )
        finally:
            provider.close()

    def test_http_request_targets_only_configured_chat_completions(self):
        requests = []

        def send(_client, request, **_kwargs):
            requests.append(request)
            return httpx.Response(
                200,
                request=request,
                headers={"content-type": "text/event-stream"},
                content=b'data: {"choices":[{"delta":{"content":"OK"}}]}\n\ndata: [DONE]\n\n',
            )

        with patch.object(httpx.Client, "send", send):
            provider = ChatCompletionsProvider(
                api_key="synthetic",
                base_url="https://proxy.example/v1",
                model="model",
                trust_env=False,
            )
            done = threading.Event()
            values = []
            provider.send_text_message(
                "hello",
                callback=lambda value: (values.append(value), done.set()),
            )
            self.assertTrue(done.wait(2))
            provider.close()

        self.assertEqual(len(requests), 1)
        request = requests[0]
        self.assertEqual(str(request.url), "https://proxy.example/v1/chat/completions")
        self.assertEqual(request.headers["authorization"], "Bearer synthetic")
        self.assertEqual(json.loads(request.content)["model"], "model")
        self.assertEqual(values[0]["text"], "OK")

    def test_audio_uses_input_audio_on_chat_completions(self):
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as audio_file:
            audio_file.write(b"fake-wav")
            audio_path = audio_file.name

        provider = ChatCompletionsProvider(
            api_key="key", base_url="https://example.test/v1", model="audio-model"
        )
        calls = []

        def fake_create_json(payload, *, timeout_seconds=None):
            calls.append((payload, timeout_seconds))
            return {"choices": [{"message": {"content": "transcript"}}]}

        provider.create_json = fake_create_json
        try:
            done = threading.Event()
            values = []
            provider.process_audio(
                audio_path,
                callback=lambda value: (values.append(value), done.set()),
                prompt="Transcribe",
            )
            self.assertTrue(done.wait(2))
            self.assertEqual(values, ["transcript"])
            payload = calls[0][0]
            self.assertEqual(payload["model"], "audio-model")
            self.assertFalse(payload["stream"])
            self.assertEqual(payload["messages"][0]["content"][1]["type"], "input_audio")
        finally:
            provider.close()
            os.unlink(audio_path)


if __name__ == "__main__":
    unittest.main()
