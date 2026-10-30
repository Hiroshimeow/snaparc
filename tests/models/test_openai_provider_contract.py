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


if __name__ == "__main__":
    unittest.main()
