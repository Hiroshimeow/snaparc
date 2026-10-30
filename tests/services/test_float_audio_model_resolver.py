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
        self.config_path = os.path.join(self.temp_dir, "stc.json")
        ConfigManager._instance = None
        self._write_config(
            {
                "models": {
                    "providers": {
                        "google-gemini-cli": {
                            "api": "openai-completions",
                            "models": ["gemini-3-flash", "gemini-2.5-flash"],
                        },
                        "aistudio_api": {
                            "api": "openai-completions",
                            "models": ["gemini-2.5-pro"],
                        },
                        "cliproxy": {
                            "api": "openai-completions",
                            "models": ["gemini-3-flash", "gpt-5.4"],
                        },
                    }
                },
                "agents": {
                    "defaults": {
                        "model": {
                            "primary": "cliproxy/gpt-5.4",
                            "fallbacks": [],
                        }
                    }
                },
                "audio": {
                    "stt": {
                        "preferred_model": "google-gemini-cli/gemini-3-flash",
                        "fallback_model": "aistudio_api/gemini-2.5-pro",
                        "audio_capable_models": [
                            "google-gemini-cli/gemini-3-flash",
                            "cliproxy/gemini-3-flash",
                        ],
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

    def test_selected_audio_capable_google_gemini_model_is_used(self):
        resolution = self.resolver.resolve(
            selected_model="google-gemini-cli/gemini-3-flash"
        )

        self.assertTrue(resolution.success)
        self.assertEqual(resolution.provider_name, "google-gemini-cli")
        self.assertEqual(resolution.provider_api, "openai-completions")
        self.assertEqual(resolution.model_name, "gemini-3-flash")
        self.assertEqual(
            resolution.model_full_name, "google-gemini-cli/gemini-3-flash"
        )
        self.assertEqual(resolution.source, "selected_model")

    def test_non_audio_model_falls_back_to_configured_audio_model(self):
        resolution = self.resolver.resolve(selected_model="cliproxy/gpt-5.4")

        self.assertTrue(resolution.success)
        self.assertEqual(
            resolution.model_full_name, "google-gemini-cli/gemini-3-flash"
        )
        self.assertEqual(resolution.provider_api, "openai-completions")
        self.assertEqual(resolution.source, "preferred_model")

    def test_preferred_and_fallback_candidates_must_also_be_audio_capable(self):
        self.config_manager.config["audio"]["stt"]["preferred_model"] = (
            "aistudio_api/gemini-2.5-pro"
        )
        self.config_manager.config["audio"]["stt"]["fallback_model"] = (
            "google-gemini-cli/gemini-2.5-flash"
        )

        resolution = self.resolver.resolve(selected_model="cliproxy/gpt-5.4")

        self.assertTrue(resolution.success)
        self.assertEqual(
            resolution.model_full_name, "google-gemini-cli/gemini-2.5-flash"
        )
        self.assertEqual(resolution.source, "fallback_model")
        self.assertEqual(
            [item["source"] for item in resolution.rejections],
            ["selected_model", "preferred_model"],
        )
        self.assertTrue(
            all(
                item["reason_code"] == "not_audio_capable"
                for item in resolution.rejections
            )
        )

    def test_candidate_model_must_exist_in_provider_model_list(self):
        self.config_manager.config["audio"]["stt"]["preferred_model"] = (
            "google-gemini-cli/typo-model"
        )
        self.config_manager.config["audio"]["stt"]["fallback_model"] = (
            "aistudio_api/typo-model"
        )
        self.config_manager.config["audio"]["stt"]["audio_capable_models"] = [
            "google-gemini-cli/typo-model",
            "aistudio_api/typo-model",
        ]

        resolution = self.resolver.resolve(selected_model="cliproxy/gpt-5.4")

        self.assertFalse(resolution.success)
        self.assertEqual(
            [item["reason_code"] for item in resolution.rejections],
            [
                "not_audio_capable",
                "model_not_declared_for_provider",
                "model_not_declared_for_provider",
            ],
        )

    def test_audio_capable_openai_completions_model_is_used_when_explicitly_allowed(self):
        self.config_manager.config["audio"]["stt"]["audio_capable_models"].append(
            "cliproxy/gpt-5.4"
        )

        resolution = self.resolver.resolve(selected_model="cliproxy/gpt-5.4")

        self.assertTrue(resolution.success)
        self.assertEqual(resolution.model_full_name, "cliproxy/gpt-5.4")
        self.assertEqual(resolution.source, "selected_model")
        self.assertEqual(resolution.provider_api, "openai-completions")

    def test_unsupported_openai_model_falls_back_to_audio_capable_google_model(self):
        resolution = self.resolver.resolve(selected_model="cliproxy/gemini-3-flash")

        self.assertTrue(resolution.success)
        self.assertEqual(resolution.model_full_name, "cliproxy/gemini-3-flash")
        self.assertEqual(resolution.source, "selected_model")
        self.assertEqual(resolution.provider_api, "openai-completions")
        self.assertEqual(resolution.rejections, ())

    def test_empty_audio_capable_list_still_allows_configured_fallback_model(self):
        self.config_manager.config["audio"]["stt"]["preferred_model"] = ""
        self.config_manager.config["audio"]["stt"]["fallback_model"] = (
            "google-gemini-cli/gemini-3-flash"
        )
        self.config_manager.config["audio"]["stt"]["audio_capable_models"] = []

        resolution = self.resolver.resolve(selected_model="cliproxy/gpt-5.4")

        self.assertTrue(resolution.success)
        self.assertEqual(
            resolution.model_full_name, "google-gemini-cli/gemini-3-flash"
        )
        self.assertEqual(resolution.source, "fallback_model")

    def test_resolved_model_is_frozen_per_job(self):
        initial_resolution = self.resolver.resolve(selected_model="cliproxy/gpt-5.4")

        self.config_manager.config["audio"]["stt"]["audio_capable_models"].append(
            "aistudio_api/gemini-2.5-pro"
        )
        self.config_manager.config["audio"]["stt"][
            "preferred_model"
        ] = "aistudio_api/gemini-2.5-pro"
        updated_resolution = self.resolver.resolve(selected_model="cliproxy/gpt-5.4")

        self.assertEqual(
            initial_resolution.model_full_name, "google-gemini-cli/gemini-3-flash"
        )
        self.assertEqual(
            updated_resolution.model_full_name, "aistudio_api/gemini-2.5-pro"
        )
        self.assertNotEqual(
            initial_resolution.model_full_name, updated_resolution.model_full_name
        )


if __name__ == "__main__":
    unittest.main()
