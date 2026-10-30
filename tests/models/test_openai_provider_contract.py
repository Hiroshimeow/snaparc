import os
import tempfile
import unittest
from unittest.mock import patch

from src.llm.providers.openai import OpenAICompatibleProvider, _guess_mime_type
from src.models.new_api import NewApiProvider


class OpenAIProviderContractTests(unittest.TestCase):
    def test_legacy_new_api_import_is_shim(self):
        self.assertIs(NewApiProvider, OpenAICompatibleProvider)

    def test_guess_mime_type_uses_image_suffixes(self):
        self.assertEqual(_guess_mime_type("shot.jpg"), "image/jpeg")
        self.assertEqual(_guess_mime_type("shot.jpeg"), "image/jpeg")
        self.assertEqual(_guess_mime_type("shot.png"), "image/png")
        self.assertEqual(_guess_mime_type("shot.webp"), "image/webp")
        self.assertEqual(_guess_mime_type("shot.gif"), "image/gif")
        self.assertEqual(_guess_mime_type("shot.bmp"), "image/bmp")
        self.assertEqual(_guess_mime_type("shot.unknown"), "image/jpeg")

    def test_encode_image_returns_mime_and_base64_content(self):
        with tempfile.NamedTemporaryFile(suffix=".webp", delete=False) as image_file:
            image_file.write(b"fake-image")
            image_path = image_file.name

        try:
            with patch("src.llm.providers.openai.OpenAI"), patch(
                "src.llm.providers.openai.httpx.Client"
            ):
                provider = OpenAICompatibleProvider(
                    api_key="key",
                    base_url="https://example.test/v1",
                    model="model",
                )

            self.assertEqual(
                provider._encode_image(image_path),
                ("image/webp", "ZmFrZS1pbWFnZQ=="),
            )
        finally:
            os.unlink(image_path)

    def test_convert_history_skips_empty_content_and_maps_model_role(self):
        with patch("src.llm.providers.openai.OpenAI"), patch(
            "src.llm.providers.openai.httpx.Client"
        ):
            provider = OpenAICompatibleProvider(
                api_key="key",
                base_url="https://example.test/v1",
                model="model",
            )

        result = provider._convert_history(
            [
                {"role": "user", "parts": ["hello", 123, " world"]},
                {"role": "model", "parts": ["answer"]},
                {"role": "assistant", "parts": ""},
            ]
        )

        self.assertEqual(
            result,
            [
                {"role": "user", "content": "hello world"},
                {"role": "assistant", "content": "answer"},
            ],
        )

    def test_provider_requires_configured_base_url(self):
        with patch("src.llm.providers.openai.OpenAI"), patch(
            "src.llm.providers.openai.httpx.Client"
        ):
            with self.assertRaisesRegex(ValueError, "requires baseUrl"):
                OpenAICompatibleProvider(api_key="key", base_url="", model="model")

    def test_audio_uses_chat_completions_input_audio(self):
        import threading
        import types

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as audio_file:
            audio_file.write(b"fake-wav")
            audio_path = audio_file.name

        try:
            provider = OpenAICompatibleProvider.__new__(OpenAICompatibleProvider)
            provider.model = "audio-model"
            provider.provider_name = "test"
            provider.base_url = "https://example.test/v1"
            provider.extra_headers = None
            calls = []

            def create(**kwargs):
                calls.append(kwargs)
                return types.SimpleNamespace(
                    choices=[
                        types.SimpleNamespace(
                            message=types.SimpleNamespace(content="transcript")
                        )
                    ]
                )

            provider.client = types.SimpleNamespace(
                chat=types.SimpleNamespace(
                    completions=types.SimpleNamespace(create=create)
                )
            )
            done = threading.Event()
            values = []
            provider.process_audio(
                audio_path,
                callback=lambda value: (values.append(value), done.set()),
                prompt="Transcribe",
                model="audio-model",
            )
            self.assertTrue(done.wait(2))
            self.assertEqual(values, ["transcript"])
            self.assertEqual(len(calls), 1)
            request = calls[0]
            self.assertEqual(request["model"], "audio-model")
            self.assertFalse(request["stream"])
            audio_part = request["messages"][0]["content"][1]
            self.assertEqual(audio_part["type"], "input_audio")
            self.assertEqual(audio_part["input_audio"]["format"], "wav")
        finally:
            os.unlink(audio_path)


if __name__ == "__main__":
    unittest.main()
