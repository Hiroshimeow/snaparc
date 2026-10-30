from __future__ import annotations

from src.llm.service import LLMService
from src.services.float_audio_service import FloatAudioModelResolver
from src.utils.ConfigManager import ConfigManager


class ModelClient:
    """Minimal model/prompt facade for SnapArc.

    Provider and model identities are read only from config.yaml. All inference
    is delegated to LLMService; there is no direct-provider fallback path.
    """

    def __init__(self, config_manager=None):
        self.config_manager = config_manager or ConfigManager()
        self.model = str(
            self.config_manager.get("agents.defaults.model.primary", "") or ""
        ).strip()
        self.provider = self.model.split("/", 1)[0] if "/" in self.model else ""
        self.prompt_type = str(
            self.config_manager.get("agents.defaults.prompt_type", "prompt_translate")
            or "prompt_translate"
        )
        self.current_prompt = None
        self.audio_model_resolver = FloatAudioModelResolver(self.config_manager)
        self.llm_service = LLMService(
            config_manager=self.config_manager,
            prompt_loader=self.get_prompt,
            default_model=self.model,
            prompt_type=self.prompt_type,
        )

    def load_settings(self):
        settings = self.config_manager.get("settings") or {}
        if not isinstance(settings, dict):
            settings = {}
        return {
            "temperature": float(settings.get("temperature", 0.7)),
            "topP": float(settings.get("topP", 0.9)),
            "prompt_type": self.prompt_type,
            **self._load_prompts(),
        }

    def _load_prompts(self):
        prompts = {
            "prompt_translate": "Please extract and process the text in this image.",
            "prompt_summarize": "",
            "prompt_fix_grammar": "",
            "prompt_extract_text": "",
        }
        configured = self.config_manager.get("prompts") or {}
        if isinstance(configured, dict):
            for key, value in configured.items():
                if isinstance(value, str):
                    prompts[key] = value
        return prompts

    def get_prompt(self, prompt_key, fallback_key="prompt_translate"):
        prompts = self._load_prompts()
        value = prompts.get(prompt_key, "")
        if isinstance(value, str) and value.strip():
            return value
        if fallback_key:
            fallback = prompts.get(fallback_key, "")
            if isinstance(fallback, str) and fallback.strip():
                return fallback
        return "Please extract and process the text in this image."

    def reload_config(self):
        self.config_manager.load_config(force=True)
        self.model = str(
            self.config_manager.get("agents.defaults.model.primary", "") or ""
        ).strip()
        self.provider = self.model.split("/", 1)[0] if "/" in self.model else ""
        self.prompt_type = str(
            self.config_manager.get("agents.defaults.prompt_type", "prompt_translate")
            or "prompt_translate"
        )
        self.llm_service.reload_config()
        self.llm_service.set_model(self.model)
        self.llm_service.prompt_type = self.prompt_type
        return self.load_settings()

    def save_state(self):
        updates = {
            "agents.defaults.model.primary": self.model,
            "agents.defaults.prompt_type": self.prompt_type,
            "screenshot_chat.default_model": self.model,
        }
        self.config_manager.set_many(updates)

    def set_provider(self, provider_name):
        provider_name = str(provider_name or "").strip()
        provider_config = self.config_manager.get_provider_config(provider_name)
        models = provider_config.get("models") if isinstance(provider_config, dict) else []
        if not models:
            return
        first_model = models[0]
        if isinstance(first_model, dict):
            first_model = first_model.get("id") or first_model.get("name") or ""
        first_model = str(first_model or "").strip()
        if not first_model:
            return
        self.set_model(
            first_model
            if first_model.startswith(f"{provider_name}/")
            else f"{provider_name}/{first_model}"
        )

    def set_model(self, model_full_name):
        self.model = str(model_full_name or "").strip()
        self.provider = self.model.split("/", 1)[0] if "/" in self.model else ""
        self.llm_service.set_model(self.model)
        self.save_state()

    def set_prompt_type(self, prompt_type):
        self.prompt_type = str(prompt_type or "prompt_translate")
        self.llm_service.prompt_type = self.prompt_type
        self.save_state()

    def process_image(
        self,
        image_paths,
        callback=None,
        history=None,
        custom_prompt_type=None,
        custom_prompt_text=None,
        model_full_name=None,
    ):
        if isinstance(custom_prompt_text, str) and custom_prompt_text.strip():
            prompt = custom_prompt_text
        else:
            prompt = self.get_prompt(custom_prompt_type or self.prompt_type)
        self.current_prompt = prompt
        self.llm_service.current_prompt = prompt
        return self.llm_service.process_image(
            image_paths,
            callback=callback,
            history=history,
            custom_prompt_text=prompt,
            model_full_name=model_full_name or self.model,
        )

    def send_text_message(
        self,
        text_prompt,
        callback=None,
        history=None,
        system_prompt=None,
        temperature=None,
        model_name=None,
        image=None,
    ):
        return self.llm_service.send_text_message(
            text_prompt,
            callback=callback,
            history=history,
            system_prompt=system_prompt,
            temperature=0.7 if temperature is None else temperature,
            model_name=model_name,
            image=image,
        )

    def process_audio(
        self,
        audio_path,
        callback=None,
        prompt="Transcribe this audio",
        model_full_name=None,
        resolution=None,
    ):
        return self.llm_service.process_audio(
            audio_path,
            callback=callback,
            prompt=prompt,
            model_full_name=model_full_name or self.model,
            resolution=resolution,
        )

    @staticmethod
    def count_token(text):
        return max(1, len(str(text or "")) // 4)
