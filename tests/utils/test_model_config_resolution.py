import json
import os
import shutil
import tempfile
import unittest

from src.llm.errors import ProviderUnsupportedError
from src.llm.registry import ModelRegistry
from src.utils.ConfigManager import ConfigManager


class ConfigManagerModelResolutionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="config_manager_model_resolution_")
        self.config_path = os.path.join(self.temp_dir, "config.yaml")
        ConfigManager._instance = None

    def tearDown(self):
        ConfigManager._instance = None
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write_config(self, data):
        with open(self.config_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle)

    def test_canonical_provider_model_is_preserved_and_resolves(self):
        self._write_config(
            {
                "models": {
                    "providers": {
                        "provider-a": {
                            "api": "openai-completions",
                            "baseUrl": "http://127.0.0.1:8316/v1",
                            "apiKey": "test-key",
                            "models": ["model-a", "model-b"],
                        }
                    }
                },
                "agents": {"defaults": {"model": {"primary": "provider-a/model-a"}}},
            }
        )
        cfg = ConfigManager(self.config_path)

        self.assertEqual(cfg.get("agents.defaults.model.primary"), "provider-a/model-a")
        self.assertEqual(cfg.get_provider_config("provider-a")["models"], ["model-a", "model-b"])
        self.assertIn("provider-a/model-a", cfg.get_all_models())
        target = ModelRegistry(cfg).resolve("provider-a/model-a")
        self.assertEqual(target.provider.name, "provider-a")
        self.assertEqual(target.model_name, "model-a")

    def test_short_primary_is_not_inferred_or_rewritten(self):
        self._write_config(
            {
                "models": {
                    "providers": {
                        "provider-a": {
                            "api": "openai-completions",
                            "baseUrl": "http://127.0.0.1:8316/v1",
                            "models": ["model-a"],
                        }
                    }
                },
                "agents": {"defaults": {"model": {"primary": "model-a"}}},
            }
        )
        cfg = ConfigManager(self.config_path)
        self.assertEqual(cfg.get("agents.defaults.model.primary"), "")

    def test_missing_provider_is_not_auto_registered(self):
        self._write_config(
            {
                "models": {"providers": {}},
                "agents": {"defaults": {"model": {"primary": "missing/model-a"}}},
            }
        )
        cfg = ConfigManager(self.config_path)
        self.assertEqual(cfg.get("agents.defaults.model.primary"), "")
        self.assertEqual(cfg.get_all_models(), {})

    def test_top_level_legacy_providers_are_ignored(self):
        self._write_config(
            {
                "providers": {
                    "legacy": {
                        "api": "openai-completions",
                        "baseUrl": "http://127.0.0.1:8316/v1",
                        "models": ["model-a"],
                    }
                },
                "agents": {"defaults": {"model": {"primary": "legacy/model-a"}}},
            }
        )
        cfg = ConfigManager(self.config_path)
        self.assertEqual(cfg.get_provider_config("legacy"), {})
        self.assertEqual(cfg.get("agents.defaults.model.primary"), "")

    def test_legacy_api_alias_is_not_normalized(self):
        self._write_config(
            {
                "models": {
                    "providers": {
                        "provider-a": {
                            "api_mode": "chat_completions",
                            "base_url": "http://127.0.0.1:8316/v1",
                            "models": ["model-a"],
                        }
                    }
                },
                "agents": {"defaults": {"model": {"primary": "provider-a/model-a"}}},
            }
        )
        cfg = ConfigManager(self.config_path)
        provider = cfg.get_provider_config("provider-a")
        self.assertEqual(provider.get("api"), "")
        with self.assertRaises(ProviderUnsupportedError):
            ModelRegistry(cfg).resolve("provider-a/model-a")


if __name__ == "__main__":
    unittest.main()
