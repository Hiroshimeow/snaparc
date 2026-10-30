import types
import unittest
from unittest.mock import MagicMock

from src.llm.client import ModelClient


class ModelClientTests(unittest.TestCase):
    def make_config(self):
        values = {
            "agents.defaults.model.primary": "provider/model-a",
            "agents.defaults.prompt_type": "prompt_translate",
            "prompts": {"prompt_translate": "Translate exactly."},
            "settings": {"temperature": 0.2},
        }
        return types.SimpleNamespace(
            get=lambda key, default=None: values.get(key, default),
            get_provider_config=lambda name: {
                "api": "openai-completions",
                "baseUrl": "https://example.test/v1",
                "apiKey": "key",
                "models": ["model-a", "model-b"],
            } if name == "provider" else {},
            set_many=MagicMock(),
            load_config=lambda force=False: None,
        )

    def test_initial_state_comes_from_config(self):
        client = ModelClient(self.make_config())
        self.assertEqual(client.model, "provider/model-a")
        self.assertEqual(client.provider, "provider")
        self.assertEqual(client.prompt_type, "prompt_translate")
        self.assertEqual(client.get_prompt("prompt_translate"), "Translate exactly.")

    def test_custom_image_prompt_delegates_only_to_llm_service(self):
        client = ModelClient(self.make_config())
        client.llm_service = MagicMock()
        client.process_image(
            "image.png",
            custom_prompt_text="custom prompt",
            model_full_name="provider/model-b",
        )
        self.assertEqual(client.current_prompt, "custom prompt")
        client.llm_service.process_image.assert_called_once_with(
            "image.png",
            callback=None,
            history=None,
            custom_prompt_text="custom prompt",
            model_full_name="provider/model-b",
        )

    def test_set_model_updates_service_and_persists(self):
        config = self.make_config()
        client = ModelClient(config)
        client.llm_service = MagicMock()
        client.set_model("provider/model-b")
        self.assertEqual(client.model, "provider/model-b")
        self.assertEqual(client.provider, "provider")
        client.llm_service.set_model.assert_called_once_with("provider/model-b")
        config.set_many.assert_called_once()

    def test_process_audio_delegates_to_llm_service(self):
        client = ModelClient(self.make_config())
        service = MagicMock()
        client.llm_service = service
        resolution = object()
        client.process_audio(
            "audio.wav",
            callback="callback",
            prompt="Transcribe",
            model_full_name="provider/model-b",
            resolution=resolution,
        )
        service.process_audio.assert_called_once_with(
            "audio.wav",
            callback="callback",
            prompt="Transcribe",
            model_full_name="provider/model-b",
            resolution=resolution,
        )


if __name__ == "__main__":
    unittest.main()
