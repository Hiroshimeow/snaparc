import unittest
from unittest.mock import MagicMock
from src.models.Gemini import Gemini


class TestGeminiCustomPromptText(unittest.TestCase):
    def setUp(self):
        self.gemini = Gemini()
        self.gemini.config_manager = MagicMock()
        self.gemini.config_manager.get_provider_config.return_value = {
            "api": "openai-completions",
            "apiKey": "fake-api-key",
            "baseUrl": "http://127.0.0.1:8317/v1",
        }
        self.gemini.provider = "cliproxy"
        self.gemini.model = "cliproxy/gpt-5.4"

    def test_process_image_with_custom_prompt_text(self):
        custom_text = "This is a custom prompt text."
        fake_provider = MagicMock()
        self.gemini.llm_service = None
        self.gemini._get_provider_instance = MagicMock(return_value=fake_provider)

        self.gemini.process_image("dummy_image.png", custom_prompt_text=custom_text)

        fake_provider.process_image.assert_called_once_with(
            "dummy_image.png", custom_text, None, None, "gpt-5.4"
        )
        self.assertEqual(self.gemini.current_prompt, custom_text)


if __name__ == "__main__":
    unittest.main()
