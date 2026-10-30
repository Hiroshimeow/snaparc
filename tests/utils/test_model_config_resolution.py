import json
import os
import shutil
import tempfile
import unittest

from src.utils.ConfigManager import ConfigManager


class ConfigManagerModelResolutionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="config_manager_model_resolution_")
        self.config_path = os.path.join(self.temp_dir, "stc.json")
        ConfigManager._instance = None

    def tearDown(self):
        ConfigManager._instance = None
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write_config(self, data):
        with open(self.config_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle)

    def test_legacy_short_default_model_resolves_to_full_provider_model(self):
        self._write_config(
            {
                "models": {
                    "providers": {
                        "a5k": {
                            "api_mode": "chat_completions",
                            "base_url": "http://127.0.0.1:8316/v1",
                            "api" + "_key": "test-key",
                            "default_model": "qwen256a",
                        }
                    }
                },
                "agents": {"defaults": {"model": {"primary": "qwen256a"}}},
            }
        )

        cfg = ConfigManager(self.config_path)

        self.assertEqual(cfg.get("agents.defaults.model.primary"), "a5k/qwen256a")
        provider = cfg.get_provider_config("a5k")
        self.assertEqual(provider["api"], "openai-completions")
        self.assertEqual(provider["baseUrl"], "http://127.0.0.1:8316/v1")
        self.assertEqual(provider["apiKey"], "test-key")
        self.assertEqual(provider["models"], ["qwen256a"])

    def test_provider_model_list_config_is_preserved_and_resolves(self):
        self._write_config(
            {
                "models": {
                    "providers": {
                        "a5k": {
                            "api": "openai-completions",
                            "baseUrl": "http://127.0.0.1:8316/v1",
                            "models": ["qwen256a", "other-model"],
                        }
                    }
                },
                "agents": {"defaults": {"model": {"primary": "a5k/qwen256a"}}},
            }
        )

        cfg = ConfigManager(self.config_path)

        self.assertEqual(cfg.get("agents.defaults.model.primary"), "a5k/qwen256a")
        self.assertEqual(cfg.get_provider_config("a5k")["models"], ["qwen256a", "other-model"])
        self.assertIn("a5k/qwen256a", cfg.get_all_models())
        self.assertIn("a5k/other-model", cfg.get_all_models())

    def test_top_level_legacy_providers_are_exposed_without_deleting_models(self):
        self._write_config(
            {
                "providers": {
                    "a5k": {
                        "api_mode": "chat_completions",
                        "default_model": "qwen256a",
                    }
                },
                "agents": {"defaults": {"model": {"primary": "qwen256a"}}},
            }
        )

        cfg = ConfigManager(self.config_path)

        self.assertEqual(cfg.get("agents.defaults.model.primary"), "a5k/qwen256a")
        self.assertEqual(cfg.get_provider_config("a5k")["models"], ["qwen256a"])
        self.assertEqual(cfg.get("providers.a5k.default_model"), "qwen256a")

    def test_known_full_primary_registers_missing_provider_from_defaults(self):
        self._write_config(
            {
                "models": {"providers": {}},
                "agents": {
                    "defaults": {
                        "model": {"primary": "cliproxy/gemini-2.5-flash"}
                    }
                },
                "screenshot_chat": {"default_model": ""},
            }
        )

        cfg = ConfigManager(self.config_path)

        self.assertEqual(
            cfg.get("agents.defaults.model.primary"),
            "cliproxy/gemini-2.5-flash",
        )
        self.assertEqual(
            cfg.get("screenshot_chat.default_model"),
            "cliproxy/gemini-2.5-flash",
        )
        self.assertIn("cliproxy/gemini-2.5-flash", cfg.get_all_models())
        self.assertEqual(cfg.get_provider_config("cliproxy")["api"], "openai-completions")

    def test_invalid_full_primary_is_cleared_when_provider_is_missing(self):
        self._write_config(
            {
                "models": {"providers": {}},
                "agents": {
                    "defaults": {
                        "model": {"primary": "ghost/model"}
                    }
                },
            }
        )

        cfg = ConfigManager(self.config_path)

        self.assertEqual(cfg.get("agents.defaults.model.primary"), "")
        self.assertEqual(cfg.get_all_models(), {})


if __name__ == "__main__":
    unittest.main()
