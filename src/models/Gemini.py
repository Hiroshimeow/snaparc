"""
Unified OpenAI-Compatible Model Facade (`src/models/Gemini.py`) — v2.4
======================================================================
Retains the `Gemini` class name for backward compatibility with existing
callers across `main.py`, `FloatingWindow.py`, and tests, while routing 100%
of text, image, and audio requests through the single `openai-completions`
(`OpenAICompatibleProvider` / `LLMService`) pipeline.

All direct vendor-specific Gemini SDK and legacy vendor-key code paths have been removed.
"""

from __future__ import annotations

import os
try:
    import tomllib
except ImportError:
    import tomli as tomllib
import xml.etree.ElementTree as ET

from src.llm.service import LLMService
from src.services.float_audio_service import FloatAudioModelResolver
from src.utils.ConfigManager import ConfigManager


def _coerce_bool(value, default=True):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _coerce_timeout(value, default=120.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


class Gemini:
    """Unified LLM client facade backed strictly by OpenAI-compatible completions (`openai-completions`)."""

    def __init__(self, config_path="data/app_state.xml"):
        self.config_path = config_path
        self.prompts_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "config.yaml")
        )
        self.config_manager = ConfigManager()
        self.billing_models = []

        # --- Config caching ---
        self._cached_config = None
        self._cached_model_list = None
        self._config_mtime = 0
        self._cached_prompts = None
        self._prompts_mtime = 0

        config, _model_list = self._load_config()

        primary_model = self.config_manager.get("agents.defaults.model.primary")
        if not isinstance(primary_model, str):
            primary_model = ""
        self.model = primary_model.strip()
        self.provider = self.model.split("/")[0] if "/" in self.model else ""

        prompt_type = self.config_manager.get("agents.defaults.prompt_type")
        if not isinstance(prompt_type, str):
            prompt_type = config.get("prompt_type", "prompt_translate")
        self.prompt_type = prompt_type

        self.img = None
        self.top_flag = 0
        self.current_prompt = None
        self._providers_cache = {}
        self.audio_model_resolver = FloatAudioModelResolver(self.config_manager)
        self.llm_service = LLMService(
            config_manager=self.config_manager,
            prompt_loader=self.get_prompt,
            default_model=self.model,
            prompt_type=self.prompt_type,
        )

    def _ensure_llm_service(self):
        service = getattr(self, "llm_service", None)
        if service is None:
            service = LLMService(
                config_manager=self.config_manager,
                prompt_loader=self.get_prompt,
                default_model=self.model,
                prompt_type=self.prompt_type,
            )
            self.llm_service = service
        service.model = self.model
        service.provider = self.provider
        service.prompt_type = self.prompt_type
        service.current_prompt = self.current_prompt
        return service

    def _sync_from_llm_service(self):
        service = getattr(self, "llm_service", None)
        if service is None:
            return
        self.model = service.model
        self.provider = service.provider
        self.current_prompt = service.current_prompt

    def _get_provider_instance(self, provider_name):
        """Get or create an OpenAI-compatible provider instance from `config.yaml`."""
        if provider_name in self._providers_cache:
            return self._providers_cache[provider_name]

        p_config = self.config_manager.get_provider_config(provider_name)
        if not p_config or p_config.get("api", "openai-completions") != "openai-completions":
            return None

        try:
            from src.llm.providers.openai import OpenAICompatibleProvider
        except ImportError:
            from llm.providers.openai import OpenAICompatibleProvider

        instance = OpenAICompatibleProvider(
            api_key=p_config.get("apiKey"),
            base_url=p_config.get("baseUrl"),
            model=None,
            provider_name=provider_name,
            timeout=p_config.get("timeout", 120.0),
            trust_env=p_config.get("trustEnv", True),
            extra_headers=p_config.get("extraHeaders"),
        )
        self._providers_cache[provider_name] = instance
        return instance

    def _load_config(self):
        """Load unified settings & prompts from ConfigManager (config.yaml)."""
        if getattr(self, "_cached_config", None) is not None and getattr(self, "_cached_model_list", None) is not None:
            return self._cached_config, self._cached_model_list

        settings_cfg = self.config_manager.get("settings") or {}
        config = {
            "temperature": float(settings_cfg.get("temperature", 0.7)) if isinstance(settings_cfg, dict) else 0.7,
            "topP": float(settings_cfg.get("topP", 0.9)) if isinstance(settings_cfg, dict) else 0.9,
            "prompt_type": self.config_manager.get("agents.defaults.prompt_type", "prompt_translate") or "prompt_translate",
        }
        model = {}
        self.billing_models = []
        if self.config_path and os.path.isfile(self.config_path) and str(self.config_path).endswith(".xml"):
            try:
                tree = ET.parse(self.config_path)
                root_el = tree.getroot()
                for element in root_el.findall("*"):
                    if element.tag not in ["generationConfig", "model", "local_llm"]:
                        config[element.tag] = element.text.strip() if element.text else None
            except Exception:
                pass

        config.update(self._load_prompts())
        self._cached_config = config
        self._cached_model_list = model
        return config, model

    def _load_prompts(self):
        default_prompts = {
            "prompt_translate": "Please extract and process the text in this image.",
            "prompt_summarize": "",
            "prompt_fix_grammar": "",
            "prompt_extract_text": "",
        }
        prompts_path = getattr(self, "prompts_path", "config.yaml")
        try:
            current_mtime = os.path.getmtime(prompts_path)
            if (
                getattr(self, "_cached_prompts", None) is not None
                and current_mtime == getattr(self, "_prompts_mtime", 0)
            ):
                return self._cached_prompts
        except OSError:
            current_mtime = 0

        prompts = default_prompts.copy()
        cm = getattr(self, "config_manager", None)
        if cm is not None:
            cfg_prompts = cm.get("prompts")
            if isinstance(cfg_prompts, dict):
                for k, v in cfg_prompts.items():
                    if isinstance(v, str):
                        prompts[k] = v

        if os.path.isfile(prompts_path):
            try:
                if str(prompts_path).endswith((".yaml", ".yml", ".json")):
                    from src.utils.yaml_io import load_yaml_or_json
                    with open(prompts_path, "r", encoding="utf-8") as f:
                        data = load_yaml_or_json(f.read())
                else:
                    with open(prompts_path, "rb") as f:
                        data = tomllib.load(f)
                prompt_section = data.get("prompts", {}) if isinstance(data, dict) else {}
                if isinstance(prompt_section, dict):
                    for key, value in prompt_section.items():
                        if isinstance(value, str):
                            prompts[key] = value
            except Exception:
                pass

        self._cached_prompts = prompts
        self._prompts_mtime = current_mtime
        return prompts

    def get_prompt(self, prompt_key, fallback_key="prompt_translate"):
        prompts = self._load_prompts()
        prompt_value = prompts.get(prompt_key, "")
        if isinstance(prompt_value, str) and prompt_value.strip():
            return prompt_value

        if fallback_key:
            fallback_value = prompts.get(fallback_key, "")
            if isinstance(fallback_value, str) and fallback_value.strip():
                return fallback_value

        return "Please extract and process the text in this image."

    def reload_config(self):
        self.config_manager.load_config(force=True)
        self._config_mtime = 0
        self._cached_config = None
        self._cached_prompts = None
        self._prompts_mtime = 0
        self._providers_cache = {}
        if getattr(self, "llm_service", None) is not None:
            self.llm_service.reload_config()
        return self._load_config()

    def _history_to_openai_messages(self, history):
        messages = []
        if not history:
            return messages

        for item in history:
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            if role == "model":
                role = "assistant"

            content = ""
            parts = item.get("parts", [])
            if isinstance(parts, list):
                for part in parts:
                    if isinstance(part, str):
                        content += part
            elif isinstance(parts, str):
                content = parts

            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})

        return messages

    def _save_config(self):
        """Persist active model and prompt_type into config.yaml via ConfigManager."""
        updates = {
            "agents.defaults.model.primary": self.model,
            "agents.defaults.prompt_type": self.prompt_type,
        }
        if self.model and not str(self.model).startswith("pi://"):
            updates["screenshot_chat.default_model"] = self.model
        self.config_manager.set_many(updates)
        if self.config_path and os.path.isfile(self.config_path) and str(self.config_path).endswith(".xml"):
            try:
                tree = ET.parse(self.config_path)
                root_el = tree.getroot()
                sub = root_el.find("prompt_type")
                if sub is None:
                    sub = ET.SubElement(root_el, "prompt_type")
                sub.text = str(self.prompt_type)
                tree.write(self.config_path, encoding="utf-8", xml_declaration=True)
            except Exception:
                pass

    def set_provider(self, provider_name):
        self.provider = provider_name
        p_config = self.config_manager.get_provider_config(provider_name)
        models = p_config.get("models") if isinstance(p_config, dict) else []
        if models:
            first_model = models[0]
            if isinstance(first_model, dict):
                first_model = first_model.get("id") or first_model.get("name") or ""
            first_model = str(first_model).strip()
            if first_model.startswith(f"{provider_name}/"):
                self.model = first_model
            elif first_model:
                self.model = f"{provider_name}/{first_model}"
        if getattr(self, "llm_service", None) is not None:
            self.llm_service.set_model(self.model)
        self._save_config()

    def set_model(self, model_full_name):
        self.model = model_full_name
        if "/" in model_full_name:
            self.provider = model_full_name.split("/")[0]
        if getattr(self, "llm_service", None) is not None:
            self.llm_service.set_model(model_full_name)
        self._save_config()

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
            effective_prompt = custom_prompt_text
        else:
            active_prompt = custom_prompt_type or self.prompt_type
            effective_prompt = self.get_prompt(active_prompt)

        self.current_prompt = effective_prompt
        model_full_name = model_full_name or self.model
        provider_name = (
            model_full_name.split("/", 1)[0]
            if "/" in model_full_name
            else self.provider
        )
        model_name = (
            model_full_name.split("/", 1)[1] if "/" in model_full_name else model_full_name
        )

        p_config = self.config_manager.get_provider_config(provider_name)
        if p_config:
            if getattr(self, "llm_service", None) is not None and (
                p_config.get("models") or hasattr(getattr(self, "llm_service", None), "process_image")
            ):
                try:
                    service = self._ensure_llm_service()
                    result = service.process_image(
                        image_paths,
                        callback=callback,
                        history=history,
                        custom_prompt_text=effective_prompt,
                        model_full_name=model_full_name,
                    )
                    self._sync_from_llm_service()
                    return result
                except Exception as exc:
                    print(f"LLMService image route failed, using direct OpenAI provider: {exc}")

            provider_inst = self._get_provider_instance(provider_name)
            if provider_inst:
                provider_inst.process_image(
                    image_paths, effective_prompt, callback, history, model_name
                )
                return None

        provider_inst = self._get_provider_instance(provider_name)
        if provider_inst:
            provider_inst.process_image(
                image_paths, effective_prompt, callback, history, model_name
            )
            return None

        if callback:
            callback(None)
        return None

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
        effective_full_model = model_name if (isinstance(model_name, str) and "/" in model_name) else self.model
        provider_name = (
            effective_full_model.split("/", 1)[0]
            if isinstance(effective_full_model, str) and "/" in effective_full_model
            else self.provider
        )
        model_id = (
            model_name.split("/", 1)[1]
            if isinstance(model_name, str) and "/" in model_name
            else (model_name or (self.model.split("/", 1)[1] if "/" in self.model else self.model))
        )

        p_config = self.config_manager.get_provider_config(provider_name)
        if p_config:
            if getattr(self, "llm_service", None) is not None and (
                p_config.get("models") or hasattr(getattr(self, "llm_service", None), "send_text_message")
            ):
                try:
                    service = self._ensure_llm_service()
                    result = service.send_text_message(
                        text_prompt,
                        callback=callback,
                        history=history,
                        system_prompt=system_prompt,
                        temperature=0.7 if temperature is None else temperature,
                        model_name=model_id,
                        image=image,
                    )
                    self._sync_from_llm_service()
                    return result
                except Exception as exc:
                    print(f"LLMService text route failed, using direct OpenAI provider: {exc}")

            provider_inst = self._get_provider_instance(provider_name)
            if provider_inst:
                provider_inst.send_text_message(
                    text_prompt,
                    callback,
                    history,
                    system_prompt,
                    0.7 if temperature is None else temperature,
                    model_id,
                    image,
                )
                return None

        provider_inst = self._get_provider_instance(provider_name)
        if provider_inst:
            provider_inst.send_text_message(
                text_prompt,
                callback,
                history,
                system_prompt,
                0.7 if temperature is None else temperature,
                model_id,
                image,
            )
            return None

        if callback:
            callback(None)
        return None

    def process_audio(
        self,
        audio_path,
        callback=None,
        prompt="Transcribe this audio",
        model_full_name=None,
        resolution=None,
    ):
        request_model = model_full_name or self.model
        resolved_audio_model = resolution or self.audio_model_resolver.resolve(
            selected_model=request_model
        )
        if not resolved_audio_model.success:
            if callback:
                callback(None)
            return None

        if getattr(self, "llm_service", None) is not None:
            return self.llm_service.process_audio(
                audio_path,
                callback=callback,
                prompt=prompt,
                model_full_name=request_model,
                resolution=resolved_audio_model,
            )

        provider = self._get_provider_instance(resolved_audio_model.provider_name)
        if provider is None:
            if callback:
                callback(None)
            return None
        return provider.process_audio(
            audio_path,
            callback=callback,
            prompt=prompt,
            model=resolved_audio_model.model_name,
        )

    def count_token(self, text):
        return max(1, len(str(text or "")) // 4)
