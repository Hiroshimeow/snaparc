import types
import unittest

from src.llm.errors import ProviderResolutionError
from src.llm.registry import ModelRegistry


class ModelRegistryTests(unittest.TestCase):
    def test_resolve_openai_provider_from_existing_api_field(self):
        config = {
            "api": "openai-completions",
            "baseUrl": "https://example.test/v1",
            "apiKey": "key",
            "timeout": "55",
            "trustEnv": "false",
            "extraHeaders": {"X-Test": "1"},
            "models": ["gemini-3-flash"],
        }
        manager = types.SimpleNamespace(
            get_provider_config=lambda provider_name: config if provider_name == "cliproxy" else {},
            get=lambda key, default=None: default,
        )
        target = ModelRegistry(manager).resolve("cliproxy/gemini-3-flash")

        self.assertEqual(target.provider.name, "cliproxy")
        self.assertEqual(target.provider.platform, "openai")
        self.assertEqual(target.provider.timeout, 55.0)
        self.assertFalse(target.provider.trust_env)
        self.assertEqual(target.provider.extra_headers, {"X-Test": "1"})
        self.assertEqual(target.model_name, "gemini-3-flash")

    def test_resolve_gemini_provider_from_existing_api_field(self):
        manager = types.SimpleNamespace(
            get_provider_config=lambda provider_name: {
                "api": "google-gemini",
                "models": ["gemini-2.5-flash"],
            },
            get=lambda key, default=None: default,
        )
        target = ModelRegistry(manager).resolve("google-gemini-cli/gemini-2.5-flash")

        self.assertEqual(target.provider.platform, "openai")

    def test_default_model_normalizes_short_name_from_model_list(self):
        manager = types.SimpleNamespace(
            get_provider_config=lambda provider_name: {},
            get=lambda key, default=None: "qwen256a"
            if key == "agents.defaults.model.primary"
            else default,
            get_all_models=lambda: {"a5k/qwen256a": {}, "a5k/other-model": {}},
        )

        self.assertEqual(ModelRegistry(manager).default_model(), "a5k/qwen256a")

    def test_resolve_missing_model_raises_clear_error(self):
        manager = types.SimpleNamespace(
            get_provider_config=lambda provider_name: {
                "api": "openai-completions",
                "models": ["qwen256a"],
            },
            get=lambda key, default=None: default,
        )

        with self.assertRaisesRegex(
            ProviderResolutionError,
            "Model not found in config.yaml: a5k/missing",
        ):
            ModelRegistry(manager).resolve("a5k/missing")

if __name__ == "__main__":
    unittest.main()
