import json
import os
import shutil
import tempfile
import unittest

from src.services.float_audio_service import FloatAudioModelResolver
from src.utils.ConfigManager import ConfigManager


class FloatAudioModelResolverTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="float_audio_resolver_")
        self.config_path = os.path.join(self.temp_dir, "config.yaml")
        ConfigManager._instance = None
        self._write_config(
            {
                "models": {
                    "providers": {
                        "provider": {
                            "api": "openai-completions",
                            "baseUrl": "https://example.test/v1",
                            "models": ["main-model", "audio-model"],
                        }
                    }
                },
                "agents": {
                    "defaults": {
                        "model": {"primary": "provider/main-model"},
                    }
                },
                "audio": {
                    "stt": {
                        "preferred_model": "provider/audio-model",
                        "audio_capable_models": ["provider/audio-model"],
                    }
                },
            }
        )
        self.config_manager = ConfigManager(self.config_path)
        self.resolver = FloatAudioModelResolver(self.config_manager)

    def tearDown(self):
        ConfigManager._instance = None
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write_config(self, data):
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def test_selected_audio_capable_model_is_used(self):
        resolution = self.resolver.resolve(selected_model="provider/audio-model")
        self.assertTrue(resolution.success)
        self.assertEqual(resolution.source, "selected_model")
        self.assertEqual(resolution.provider_name, "provider")
        self.assertEqual(resolution.model_name, "audio-model")
        self.assertEqual(resolution.provider_api, "openai-completions")

    def test_non_audio_selected_model_uses_explicit_preferred_model(self):
        resolution = self.resolver.resolve(selected_model="provider/main-model")
        self.assertTrue(resolution.success)
        self.assertEqual(resolution.model_full_name, "provider/audio-model")
        self.assertEqual(resolution.source, "preferred_model")
        self.assertEqual(
            [item["reason_code"] for item in resolution.rejections],
            ["not_audio_capable"],
        )

    def test_no_hidden_fallback_when_preferred_model_is_empty(self):
        self.config_manager.config["audio"]["stt"]["preferred_model"] = ""
        resolution = self.resolver.resolve(selected_model="provider/main-model")
        self.assertFalse(resolution.success)
        self.assertEqual(resolution.error_code, "audio_model_resolution_failed")
        self.assertEqual(
            [item["reason_code"] for item in resolution.rejections],
            ["not_audio_capable"],
        )

    def test_preferred_model_must_be_declared_by_provider(self):
        self.config_manager.config["audio"]["stt"]["preferred_model"] = "provider/missing"
        self.config_manager.config["audio"]["stt"]["audio_capable_models"] = ["provider/missing"]
        resolution = self.resolver.resolve(selected_model="provider/main-model")
        self.assertFalse(resolution.success)
        self.assertEqual(
            [item["reason_code"] for item in resolution.rejections],
            ["not_audio_capable", "model_not_declared_for_provider"],
        )

    def test_provider_api_must_be_openai_completions(self):
        self.config_manager.config["models"]["providers"]["provider"]["api"] = "vendor-api"
        resolution = self.resolver.resolve(selected_model="provider/audio-model")
        self.assertFalse(resolution.success)
        self.assertEqual(
            [item["reason_code"] for item in resolution.rejections],
            ["unsupported_provider_api", "unsupported_provider_api"],
        )


if __name__ == "__main__":
    unittest.main()
