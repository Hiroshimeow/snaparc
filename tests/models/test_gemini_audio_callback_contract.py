import types
import unittest

from src.models.Gemini import Gemini
from src.services.float_audio_service import FloatAudioModelResolution


class GeminiAudioCallbackContractTests(unittest.TestCase):
    def setUp(self):
        self.gemini = Gemini.__new__(Gemini)
        self.gemini.model = "cliproxy/gemini-2.5-flash"
        self.gemini.provider = "cliproxy"
        self.gemini._providers_cache = {}
        self.gemini.llm_service = None
        self.gemini.config_manager = types.SimpleNamespace(
            get_provider_config=lambda provider_name: {}
        )

    def test_process_audio_unsupported_provider_keeps_callback_failure_as_none(self):
        callback_values = []
        resolution = FloatAudioModelResolution(
            success=True,
            provider_name="cliproxy",
            provider_api="openai-completions",
            model_name="gemini-2.5-flash",
            model_full_name="cliproxy/gemini-2.5-flash",
            source="preferred_model",
        )
        self.gemini._get_provider_instance = lambda _p: None

        result = self.gemini.process_audio(
            "fake.wav",
            callback=callback_values.append,
            resolution=resolution,
        )

        self.assertIsNone(result)
        self.assertEqual(callback_values, [None])

    def test_process_audio_openai_provider_delegates_to_provider_instance(self):
        callback_values = []
        provider_calls = []
        resolution = FloatAudioModelResolution(
            success=True,
            provider_name="cliproxy",
            provider_api="openai-completions",
            model_name="gpt-5.4",
            model_full_name="cliproxy/gpt-5.4",
            source="selected_model",
        )

        class FakeProvider:
            def process_audio(self, audio_path, callback=None, prompt=None, model=None):
                provider_calls.append((audio_path, prompt, model))
                callback("plain transcript")

        self.gemini._get_provider_instance = lambda _provider_name: FakeProvider()

        result = self.gemini.process_audio(
            "fake.wav",
            callback=callback_values.append,
            resolution=resolution,
        )

        self.assertIsNone(result)
        self.assertEqual(
            provider_calls,
            [("fake.wav", "Transcribe this audio", "gpt-5.4")],
        )
        self.assertEqual(callback_values, ["plain transcript"])

    def test_process_audio_resolution_failure_keeps_callback_failure_as_none(self):
        callback_values = []
        resolution = FloatAudioModelResolution(
            success=False,
            provider_name="",
            provider_api="",
            model_name="",
            model_full_name="",
            source="unresolved",
            error_code="audio_model_resolution_failed",
            message="No supported audio model is available for file-audio transcription.",
        )

        result = self.gemini.process_audio(
            "fake.wav",
            callback=callback_values.append,
            resolution=resolution,
        )

        self.assertIsNone(result)
        self.assertEqual(callback_values, [None])

    def test_process_audio_uses_frozen_resolution_model_name(self):
        callback_values = []
        used_models = []
        resolution = FloatAudioModelResolution(
            success=True,
            provider_name="cliproxy",
            provider_api="openai-completions",
            model_name="gemini-2.5-flash",
            model_full_name="cliproxy/gemini-2.5-flash",
            source="selected_model",
        )

        class FakeProvider:
            def process_audio(self, audio_path, callback=None, prompt=None, model=None):
                used_models.append(model)
                if callback:
                    callback("plain transcript")

        self.gemini._get_provider_instance = lambda _p: FakeProvider()
        self.gemini.model = "cliproxy/gpt-5.4"

        result = self.gemini.process_audio(
            "fake.wav",
            callback=callback_values.append,
            resolution=resolution,
        )

        self.assertIsNone(result)
        self.assertEqual(used_models, ["gemini-2.5-flash"])
        self.assertEqual(callback_values, ["plain transcript"])


if __name__ == "__main__":
    unittest.main()
