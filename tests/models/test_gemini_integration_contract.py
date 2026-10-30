import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from src.models.Gemini import Gemini


class GeminiIntegrationContractTests(unittest.TestCase):
    def make_gemini(self):
        gemini = Gemini.__new__(Gemini)
        gemini.model = "cliproxy-2080/gemini-3-flash"
        gemini.provider = "cliproxy-2080"
        gemini.prompt_type = "prompt_translate"
        gemini.current_prompt = None
        gemini._providers_cache = {}
        gemini.config_manager = types.SimpleNamespace(
            get_provider_config=lambda provider_name: {
                "api": "openai-completions",
                "apiKey": "secret-key",
                "baseUrl": "https://cliproxy.example/v1",
                "timeout": 55,
                "trustEnv": False,
                "extraHeaders": {"X-STC": "1"},
                "models": ["gemini-3-flash"],
            }
            if provider_name == "cliproxy-2080"
            else {}
        )
        gemini.get_prompt = lambda prompt_key: f"prompt:{prompt_key}"
        return gemini

    def test_get_provider_instance_preserves_openai_compatible_config(self):
        gemini = self.make_gemini()
        created = []

        class FakeProvider:
            def __init__(self, **kwargs):
                created.append(kwargs)

        with patch("src.llm.providers.openai.OpenAICompatibleProvider", FakeProvider):
            first = gemini._get_provider_instance("cliproxy-2080")
            second = gemini._get_provider_instance("cliproxy-2080")

        self.assertIs(first, second)
        self.assertEqual(len(created), 1)
        self.assertEqual(
            created[0],
            {
                "api_key": "secret-key",
                "base_url": "https://cliproxy.example/v1",
                "model": None,
                "provider_name": "cliproxy-2080",
                "timeout": 55,
                "trust_env": False,
                "extra_headers": {"X-STC": "1"},
            },
        )

    def test_process_image_openai_provider_uses_prompt_history_and_model_id(self):
        gemini = self.make_gemini()
        calls = []

        class FakeProvider:
            def process_image(self, image_paths, prompt, callback, history, model):
                calls.append((image_paths, prompt, callback, history, model))
                callback({"text": "ok"})

        callback_values = []
        history = [{"role": "user", "parts": ["previous"]}]
        gemini._get_provider_instance = lambda provider_name: FakeProvider()

        gemini.process_image(
            ["first.png", "second.webp"],
            callback=callback_values.append,
            history=history,
            custom_prompt_text="Analyze these images",
        )

        self.assertEqual(gemini.current_prompt, "Analyze these images")
        self.assertEqual(
            calls,
            [
                (
                    ["first.png", "second.webp"],
                    "Analyze these images",
                    callback_values.append,
                    history,
                    "gemini-3-flash",
                )
            ],
        )
        self.assertEqual(callback_values, [{"text": "ok"}])

    def test_send_text_message_openai_provider_passes_explicit_model_id(self):
        gemini = self.make_gemini()
        calls = []

        class FakeProvider:
            def send_text_message(self, *args):
                calls.append(args)
                args[1]({"text": "ok"})

        callback_values = []
        history = [{"role": "model", "parts": ["answer"]}]
        gemini._get_provider_instance = lambda provider_name: FakeProvider()

        gemini.send_text_message(
            "hello",
            callback=callback_values.append,
            history=history,
            system_prompt="Be concise.",
            temperature=0.2,
            model_name="override-model",
            image="shot.png",
        )

        self.assertEqual(
            calls,
            [
                (
                    "hello",
                    callback_values.append,
                    history,
                    "Be concise.",
                    0.2,
                    "override-model",
                    "shot.png",
                )
            ],
        )
        self.assertEqual(callback_values, [{"text": "ok"}])

    def test_history_to_openai_messages_keeps_text_roles_only(self):
        gemini = self.make_gemini()

        result = gemini._history_to_openai_messages(
            [
                {"role": "user", "parts": ["hello", 123, " world"]},
                {"role": "model", "parts": ["answer"]},
                {"role": "assistant", "parts": "direct"},
                {"role": "tool", "parts": ["ignored"]},
                "invalid",
            ]
        )

        self.assertEqual(
            result,
            [
                {"role": "user", "content": "hello world"},
                {"role": "assistant", "content": "answer"},
                {"role": "assistant", "content": "direct"},
            ],
        )

    def test_text_temperature_preserves_zero_in_service_and_legacy_provider_routes(self):
        for use_service in (False, True):
            with self.subTest(use_service=use_service):
                gemini = self.make_gemini()
                temperatures = []

                def send(*args, **kwargs):
                    temperatures.append(kwargs["temperature"] if kwargs else args[4])

                provider = types.SimpleNamespace(send_text_message=send)
                if use_service:
                    gemini.llm_service = provider
                else:
                    gemini._get_provider_instance = lambda _name: provider
                for temperature in (0.0, None, 0.2):
                    gemini.send_text_message("hello", temperature=temperature)

                self.assertEqual(temperatures, [0.0, 0.7, 0.2])

    def test_service_request_does_not_restore_prompt_selected_before_hotkey_change(self):
        for route in ("process_image", "send_text_message"):
            with self.subTest(route=route):
                gemini = self.make_gemini()
                gemini.prompt_type = "prompt_summarize"
                completed = object()

                def complete_request(_content, callback=None, **_kwargs):
                    callback({"text": "done"})
                    return completed

                gemini.llm_service = types.SimpleNamespace(**{route: complete_request})

                def select_fix(_result):
                    # A UI/hotkey can select a mode before the old call returns.
                    gemini.prompt_type = "prompt_fix_grammar"

                result = getattr(gemini, route)("input", callback=select_fix)

                self.assertIs(result, completed)
                self.assertEqual(gemini.prompt_type, "prompt_fix_grammar")
                snap_prompts = []
                gemini.llm_service.process_image = lambda _image, **kwargs: snap_prompts.append(
                    kwargs["custom_prompt_text"]
                )
                gemini.process_image("next-snap.png")
                self.assertEqual(snap_prompts, ["prompt:prompt_fix_grammar"])

    def test_reload_config_resets_legacy_prompt_and_provider_caches(self):
        gemini = self.make_gemini()
        gemini._config_mtime = 123
        gemini._cached_config = {"old": True}
        gemini._cached_prompts = {"prompt_translate": "old"}
        gemini._prompts_mtime = 456
        gemini._providers_cache = {"cliproxy-2080": object()}
        load_calls = []
        gemini.config_manager = types.SimpleNamespace(
            load_config=lambda force=False: load_calls.append(force)
        )
        gemini._load_config = lambda: ("loaded-config", "loaded-models")

        result = gemini.reload_config()

        self.assertEqual(result, ("loaded-config", "loaded-models"))
        self.assertEqual(load_calls, [True])
        self.assertEqual(gemini._config_mtime, 0)
        self.assertIsNone(gemini._cached_config)
        self.assertIsNone(gemini._cached_prompts)
        self.assertEqual(gemini._prompts_mtime, 0)
        self.assertEqual(gemini._providers_cache, {})

    def test_prompt_loader_keeps_arbitrary_prompt_key_and_exact_nonblank_text(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            prompts_path = Path(temp_dir) / "prompts.toml"
            prompts_path.write_text(
                '[prompts]\nprompt_translate = "translate"\nprompt_expert = "  expert prompt  "\nprompt_fix_grammar = "  arbitrary fix prompt  "\n',
                encoding="utf-8",
            )
            gemini = Gemini.__new__(Gemini)
            gemini.prompts_path = str(prompts_path)
            gemini._cached_prompts = None
            gemini._prompts_mtime = 0

            self.assertEqual(gemini.get_prompt("prompt_expert"), "  expert prompt  ")
            self.assertEqual(
                gemini.get_prompt("prompt_fix_grammar"), "  arbitrary fix prompt  "
            )



if __name__ == "__main__":
    unittest.main()
