import threading
import types
import unittest
from unittest.mock import patch

from src.llm.service import LLMService


class LLMServiceTests(unittest.TestCase):
    def make_config_manager(self):
        providers = {
            "cliproxy": {
                "api": "openai-completions",
                "baseUrl": "https://example.test/v1",
                "apiKey": "key",
                "models": ["gemini-3-flash"],
            }
        }
        return types.SimpleNamespace(
            get_provider_config=lambda provider_name: providers.get(provider_name, {}),
            get=lambda key, default=None: "cliproxy/gemini-3-flash"
            if key == "agents.defaults.model.primary"
            else default,
            load_config=lambda force=False: None,
        )

    def test_send_text_message_dispatches_to_openai_provider(self):
        calls = []

        class FakeProvider:
            def __init__(self, **kwargs):
                calls.append(("init", kwargs))

            def send_text_message(self, *args, **kwargs):
                calls.append(("send", args, kwargs))
                kwargs["callback"]({"text": "ok"})

            def supports(self, _feature):
                return True

        with patch("src.llm.service.OpenAICompatibleProvider", FakeProvider):
            service = LLMService(config_manager=self.make_config_manager())
            values = []
            service.send_text_message(
                "hello",
                callback=values.append,
                model_name="cliproxy/gemini-3-flash",
            )

        self.assertEqual(values, [{"text": "ok"}])
        self.assertEqual(calls[0][0], "init")
        self.assertEqual(calls[1][0], "send")
        self.assertEqual(calls[1][2]["model_name"], "gemini-3-flash")

    def test_provider_cache_reuses_same_instance_when_effective_config_is_unchanged(self):
        manager = self.make_config_manager()
        instances = []

        class FakeProvider:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                instances.append(self)

        with patch("src.llm.service.OpenAICompatibleProvider", FakeProvider):
            service = LLMService(config_manager=manager)
            first = service._get_provider("cliproxy", "gemini-3-flash")
            service.reload_config()
            second = service._get_provider("cliproxy", "gemini-3-flash")

        self.assertIs(first, second)
        self.assertEqual(len(instances), 1)

    def test_text_temperature_preserves_zero_and_defaults_only_none(self):
        temperatures = []

        class FakeProvider:
            def __init__(self, **_kwargs):
                pass

            def send_text_message(self, *_args, **kwargs):
                temperatures.append(kwargs["temperature"])

        with patch("src.llm.service.OpenAICompatibleProvider", FakeProvider):
            service = LLMService(config_manager=self.make_config_manager())
            for temperature in (0.0, None, 0.2):
                service.send_text_message("hello", temperature=temperature)

        self.assertEqual(temperatures, [0.0, 0.7, 0.2])

    def test_provider_cache_rebuilds_on_next_request_after_endpoint_changes(self):
        manager = self.make_config_manager()
        provider_config = manager.get_provider_config("cliproxy")
        instances = []

        class FakeProvider:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                instances.append(self)

            def send_text_message(self, *_args, **_kwargs):
                return None

        with patch("src.llm.service.OpenAICompatibleProvider", FakeProvider):
            service = LLMService(config_manager=manager)
            service.send_text_message(
                "first",
                model_name="cliproxy/gemini-3-flash",
            )
            provider_config["baseUrl"] = "https://changed.test/v1"
            service.send_text_message(
                "second",
                model_name="cliproxy/gemini-3-flash",
            )

        self.assertEqual(len(instances), 2)
        self.assertEqual(instances[0].kwargs["base_url"], "https://example.test/v1")
        self.assertEqual(instances[1].kwargs["base_url"], "https://changed.test/v1")

    def test_provider_cache_rebuilds_when_transport_fields_change(self):
        manager = self.make_config_manager()
        provider_config = manager.get_provider_config("cliproxy")
        instances = []

        class FakeProvider:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                instances.append(self)

        with patch("src.llm.service.OpenAICompatibleProvider", FakeProvider):
            service = LLMService(config_manager=manager)
            previous = service._get_provider("cliproxy", "gemini-3-flash")
            for key, value in (
                ("apiKey", "new-key"),
                ("timeout", 45),
                ("trustEnv", False),
                ("extraHeaders", {"X-Test": "new"}),
            ):
                with self.subTest(field=key):
                    provider_config[key] = value
                    current = service._get_provider("cliproxy", "gemini-3-flash")
                    self.assertIsNot(current, previous)
                    previous = current

        self.assertEqual(len(instances), 5)

    def test_provider_cache_rebuilds_when_api_kind_changes(self):
        manager = self.make_config_manager()
        provider_config = manager.get_provider_config("cliproxy")

        class FakeOpenAIProvider:
            def __init__(self, **_kwargs):
                pass

        with patch("src.llm.service.OpenAICompatibleProvider", FakeOpenAIProvider):
            service = LLMService(config_manager=manager)
            first = service._get_provider("cliproxy", "gemini-3-flash")
            provider_config["baseUrl"] = "https://new-endpoint.test/v1"
            second = service._get_provider("cliproxy", "gemini-3-flash")

        self.assertIsInstance(first, FakeOpenAIProvider)
        self.assertIsInstance(second, FakeOpenAIProvider)
        self.assertIsNot(first, second)

    def test_process_image_preserves_exact_custom_prompt_text(self):
        calls = []

        class FakeProvider:
            def __init__(self, **_kwargs):
                pass

            def process_image(self, image_paths, prompt, **kwargs):
                calls.append((image_paths, prompt, kwargs))

            def supports(self, _feature):
                return True

        with patch("src.llm.service.OpenAICompatibleProvider", FakeProvider):
            service = LLMService(config_manager=self.make_config_manager())
            service.process_image(
                "image.png",
                custom_prompt_text="  exact prompt  ",
                model_full_name="cliproxy/gemini-3-flash",
            )

        self.assertEqual(calls[0][1], "  exact prompt  ")

    def test_overlapping_image_requests_keep_request_local_prompts(self):
        calls = []
        first_request_paused = threading.Event()
        release_first_request = threading.Event()

        class FakeProvider:
            def __init__(self, **_kwargs):
                pass

            def process_image(self, image_paths, prompt, **_kwargs):
                calls.append((threading.current_thread().name, image_paths, prompt))

            def supports(self, _feature):
                return True

        with patch("src.llm.service.OpenAICompatibleProvider", FakeProvider):
            service = LLMService(config_manager=self.make_config_manager())
            resolve_target = service._resolve_target_or_callback

            def interleaved_resolve(model_full_name, callback):
                if threading.current_thread().name == "request-a":
                    first_request_paused.set()
                    self.assertTrue(release_first_request.wait(timeout=1))
                return resolve_target(model_full_name, callback)

            service._resolve_target_or_callback = interleaved_resolve
            request_a = threading.Thread(
                name="request-a",
                target=service.process_image,
                kwargs={
                    "image_paths": "a.png",
                    "custom_prompt_text": "prompt A",
                    "model_full_name": "cliproxy/gemini-3-flash",
                },
            )
            request_a.start()
            self.assertTrue(first_request_paused.wait(timeout=1))

            service.process_image(
                "b.png",
                custom_prompt_text="prompt B",
                model_full_name="cliproxy/gemini-3-flash",
            )
            release_first_request.set()
            request_a.join(timeout=1)

        self.assertFalse(request_a.is_alive())
        self.assertEqual(
            calls,
            [
                ("MainThread", "b.png", "prompt B"),
                ("request-a", "a.png", "prompt A"),
            ],
        )

    def test_missing_model_returns_clean_callback_error_instead_of_crashing(self):
        providers = {
            "cliproxy": {
                "api": "openai-completions",
                "baseUrl": "https://example.test/v1",
                "apiKey": "key",
                "models": ["gemini-3-flash"],
            }
        }
        manager = types.SimpleNamespace(
            get_provider_config=lambda provider_name: providers.get(provider_name, {}),
            get=lambda key, default=None: "cliproxy/missing"
            if key == "agents.defaults.model.primary"
            else default,
            load_config=lambda force=False: None,
        )
        service = LLMService(config_manager=manager)
        values = []

        result = service.send_text_message(
            "hello",
            callback=values.append,
            model_name="cliproxy/missing",
        )

        self.assertIsNone(result)
        self.assertEqual(len(values), 1)
        self.assertIn("Model not found in config.yaml: cliproxy/missing", values[0]["text"])
        self.assertEqual(values[0]["key_mask"], "Error")


if __name__ == "__main__":
    unittest.main()
