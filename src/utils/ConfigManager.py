import json
import os
from pathlib import Path
import threading
from src.utils.yaml_io import dump_yaml_or_json, load_yaml_or_json


class ConfigManager:
    _instance = None
    _lock = threading.Lock()
    _KNOWN_PROVIDER_DEFAULTS = {
        "cliproxy": {
            "api": "openai-completions",
            "platform": "openai",
            "displayName": "CLIProxy / OpenAI-Compatible API",
            "baseUrl": "",
            "supportsImages": True,
            "supportsAudio": True,
            "models": ["gpt-5.4", "claude-sonnet-4-6", "gemini-2.5-pro", "gemini-2.5-flash", "gpt-4o-mini"],
        }
    }

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(ConfigManager, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self, config_path="config.yaml"):
        if self._initialized:
            return
        p = Path(config_path)
        if not p.exists() and p.name == "stc.json" and p.with_name("config.yaml").exists():
            config_path = str(p.with_name("config.yaml"))
        self.config_path = config_path
        self.config = {}
        self._io_lock = threading.Lock()
        self._config_mtime = None
        self.load_config()
        self._initialized = True

    def load_config(self, force=False):
        with self._io_lock:
            if os.path.exists(self.config_path):
                try:
                    current_mtime = os.path.getmtime(self.config_path)
                    if (
                        not force
                        and self.config
                        and self._config_mtime is not None
                        and current_mtime == self._config_mtime
                    ):
                        return self.config
                    with open(self.config_path, "r", encoding="utf-8") as f:
                        self.config = load_yaml_or_json(f.read())
                    self._apply_model_resolution_defaults()
                    self._apply_audio_stt_defaults()
                    self._apply_screenshot_chat_defaults()
                    self._config_mtime = current_mtime
                except Exception as e:
                    print(f"Error loading config: {e}")
                    self.config = self._get_default_config()
                    self._config_mtime = None
            else:
                self.config = self._get_default_config()
                self._save_config_unlocked()
            return self.config

    def reload_if_changed(self):
        return self.load_config(force=False)

    def save_config(self):
        with self._io_lock:
            self._save_config_unlocked()

    def _save_config_unlocked(self):
        try:
            Path(self.config_path).parent.mkdir(parents=True, exist_ok=True)
            as_json = str(self.config_path).lower().endswith(".json")
            with open(self.config_path, "w", encoding="utf-8") as f:
                f.write(dump_yaml_or_json(self.config, as_json=as_json))
            try:
                self._config_mtime = os.path.getmtime(self.config_path)
            except OSError:
                self._config_mtime = None
        except Exception as e:
            print(f"Error saving config: {e}")

    def _get_default_config(self):
        return {
            "models": {"providers": {}},
            "audio": {
                "recording": {"max_minutes": 10},
                "stt": {
                    "preferred_model": "",
                    "fallback_model": "cliproxy/gemini-2.5-flash",
                    "audio_capable_models": [],
                    "max_retry": 2,
                    "continue_on_error": True,
                }
            },
            "agents": {
                "defaults": {
                    "model": {
                        "primary": "",
                        "fallbacks": [],
                    },
                    "prompt_type": "prompt_translate",
                }
            },
            "hotkeys": [],
            "screenshot_chat": self._get_default_screenshot_chat_config(),
            "ui": {"animation_speed": 0.1, "ani_folder": "src/animate", "tray_left_click_action": "toggle_pause"},
        }

    def _get_default_screenshot_chat_config(self):
        return {
            "enabled": True,
            "hotkey": "ctrl + shift + alt + x",
            "hotkey_mode": "toggle",
            "history_path": "data/screenshot_chat_sessions.json",
            "default_model": "",
            "default_prompt_type": "prompt_translate",
            "temperature": 0.7,
            "runtime": {
                "mode": "tk",
                "legacy_webview_enabled": False,
                "gui": "edgechromium",
                "host": "127.0.0.1",
                "port": 0,
                "startup_timeout_ms": 6000,
                "poll_interval_ms": 220,
                "fallback_tk_debug": False,
            },
            "tts": {
                "base_url": "https://tts-r28-edge.hcu-lab.me",
                "api_key": "",
                "voice": "bao_kim",
                "language": "vi",
                "auto_read": False,
                "timeout_seconds": 60,
            },
            "pi": {
                "backend": "rpc",
                "enabled": True,
                "primary_mode": True,
                "auto_detect": True,
                "executable": "",
                "agent_names": ["pi"],
                "fallback_to_llm": True,
                "show_timeline": True,
                "show_unavailable_in_selector": True,
                "approval_mode": "tk_modal",
                "approval_timeout_ms": 120000,
                "tool_policy": "approve_mutations",
                "trusted_roots": ["."],
                "startup_timeout_ms": 15000,
                "first_event_timeout_ms": 30000,
                "shutdown_timeout_ms": 3000,
                "crash_fallback_to_llm": False,
            },
            "agents": {
                "research": {
                    "enabled": True,
                    "model": "",
                    "system_prompt": "Analyze carefully and surface alternatives.",
                },
                "critic": {
                    "enabled": True,
                    "model": "",
                    "system_prompt": "Review risks, edge cases, and weak assumptions.",
                },
            },
        }

    def _apply_model_resolution_defaults(self):
        models_config = self.config.get("models")
        if not isinstance(models_config, dict):
            models_config = {}
            self.config["models"] = models_config

        providers = models_config.get("providers")
        if not isinstance(providers, dict):
            providers = {}
            models_config["providers"] = providers

        legacy_providers = self.config.get("providers")
        if isinstance(legacy_providers, dict):
            for provider_name, provider_config in legacy_providers.items():
                if isinstance(provider_config, dict) and provider_name not in providers:
                    providers[provider_name] = dict(provider_config)

        self._ensure_known_referenced_providers(providers)

        for provider_name, provider_config in list(providers.items()):
            if isinstance(provider_config, dict):
                self._normalize_provider_config(provider_name, provider_config)

        agents = self.config.get("agents")
        if not isinstance(agents, dict):
            agents = {}
            self.config["agents"] = agents
        defaults = agents.get("defaults")
        if not isinstance(defaults, dict):
            defaults = {}
            agents["defaults"] = defaults
        model_defaults = defaults.get("model")
        if not isinstance(model_defaults, dict):
            model_defaults = {}
            defaults["model"] = model_defaults
        model_defaults.setdefault("fallbacks", [])

        primary = model_defaults.get("primary")
        normalized_primary = self._normalize_model_name(primary, providers)
        if normalized_primary:
            model_defaults["primary"] = normalized_primary
        else:
            first_model = self._first_provider_model(providers)
            model_defaults["primary"] = first_model or ""

        fallbacks = model_defaults.get("fallbacks")
        if not isinstance(fallbacks, list):
            fallbacks = []
        model_defaults["fallbacks"] = [
            normalized
            for normalized in (self._normalize_model_name(item, providers) for item in fallbacks)
            if normalized
        ]
        defaults.setdefault("prompt_type", "prompt_translate")

    def _ensure_known_referenced_providers(self, providers):
        for full_name in self._referenced_full_model_names():
            provider_name, _, model_name = full_name.partition("/")
            if not provider_name or not model_name or provider_name in providers:
                continue
            defaults = self._KNOWN_PROVIDER_DEFAULTS.get(provider_name)
            if defaults is None:
                continue
            provider_config = dict(defaults)
            models = list(provider_config.get("models") or [])
            if model_name not in models:
                models.append(model_name)
            provider_config["models"] = models
            providers[provider_name] = provider_config

    def _referenced_full_model_names(self):
        refs = []

        def add(value):
            if isinstance(value, str):
                value = value.strip()
                if "/" in value and value.partition("/")[2]:
                    refs.append(value)

        model_defaults = (((self.config.get("agents") or {}).get("defaults") or {}).get("model") or {})
        if isinstance(model_defaults, dict):
            add(model_defaults.get("primary"))
            fallbacks = model_defaults.get("fallbacks")
            if isinstance(fallbacks, list):
                for fallback in fallbacks:
                    add(fallback)

        chat_config = self.config.get("screenshot_chat")
        if isinstance(chat_config, dict):
            add(chat_config.get("default_model"))
            agents = chat_config.get("agents")
            if isinstance(agents, dict):
                for agent_config in agents.values():
                    if isinstance(agent_config, dict):
                        add(agent_config.get("model"))

        stt_config = ((self.config.get("audio") or {}).get("stt") or {})
        if isinstance(stt_config, dict):
            add(stt_config.get("preferred_model"))
            add(stt_config.get("fallback_model"))
            postprocess = stt_config.get("postprocess")
            if isinstance(postprocess, dict):
                add(postprocess.get("model"))

        return refs

    def _normalize_provider_config(self, provider_name, provider_config):
        alias_map = {
            "api_mode": "api",
            "base_url": "baseUrl",
            "api" + "_key": "apiKey",
            "extra_headers": "extraHeaders",
            "display_name": "displayName",
            "trust_env": "trustEnv",
            "supports_images": "supportsImages",
            "supports_audio": "supportsAudio",
        }
        for legacy_key, canonical_key in alias_map.items():
            if canonical_key not in provider_config and legacy_key in provider_config:
                provider_config[canonical_key] = provider_config[legacy_key]

        provider_config["api"] = self._normalize_provider_api(provider_config.get("api"))
        provider_config["platform"] = "openai"

        models = []
        raw_models = provider_config.get("models")
        if isinstance(raw_models, str):
            raw_models = [raw_models]
        if isinstance(raw_models, list):
            models.extend(raw_models)
        for legacy_model_key in ("default_model", "defaultModel", "model"):
            legacy_model = provider_config.get(legacy_model_key)
            if isinstance(legacy_model, str) and legacy_model.strip():
                models.append(legacy_model)
        provider_config["models"] = self._dedupe_model_names(provider_name, models)

    def _normalize_provider_api(self, api):
        value = str(api or "openai-completions").strip().lower()
        aliases = {
            "chat_completions",
            "chat-completions",
            "openai_chat_completions",
        }
        if value in aliases:
            return "openai-completions"
        return value or "openai-completions"

    def _dedupe_model_names(self, provider_name, model_names):
        seen = set()
        result = []
        for raw_model in model_names:
            if isinstance(raw_model, dict):
                raw_model = raw_model.get("id") or raw_model.get("name") or ""
            if not isinstance(raw_model, str):
                continue
            model_name = raw_model.strip()
            if not model_name:
                continue
            prefix = f"{provider_name}/"
            if model_name.startswith(prefix):
                model_name = model_name[len(prefix):]
            if model_name not in seen:
                seen.add(model_name)
                result.append(model_name)
        return result

    def _normalize_model_name(self, selected, providers):
        if not isinstance(selected, str) or not selected.strip():
            return ""
        selected = selected.strip()
        provider_name, separator, model_name = selected.partition("/")
        if separator and model_name:
            provider_config = providers.get(provider_name)
            if not isinstance(provider_config, dict):
                return ""
            models = provider_config.setdefault("models", [])
            if not isinstance(models, list):
                provider_config["models"] = []
                models = provider_config["models"]
            if model_name not in models:
                models.append(model_name)
            return selected

        for candidate_provider, provider_config in providers.items():
            if not isinstance(provider_config, dict):
                continue
            models = provider_config.get("models")
            if isinstance(models, list) and selected in models:
                return f"{candidate_provider}/{selected}"

        provider_items = [
            (provider_name, provider_config)
            for provider_name, provider_config in providers.items()
            if isinstance(provider_config, dict)
        ]
        if len(provider_items) == 1:
            provider_name, provider_config = provider_items[0]
            models = provider_config.setdefault("models", [])
            if not isinstance(models, list):
                provider_config["models"] = []
                models = provider_config["models"]
            if selected not in models:
                models.append(selected)
            return f"{provider_name}/{selected}"
        return ""

    def _first_provider_model(self, providers):
        for provider_name, provider_config in providers.items():
            if not isinstance(provider_config, dict):
                continue
            models = provider_config.get("models")
            if isinstance(models, list) and models:
                first = models[0]
                if isinstance(first, str) and first.strip():
                    return f"{provider_name}/{first.strip()}"
        return ""

    def _apply_screenshot_chat_defaults(self):
        defaults = self._get_default_screenshot_chat_config()
        chat_config = self.config.get("screenshot_chat")
        if not isinstance(chat_config, dict):
            chat_config = {}
            self.config["screenshot_chat"] = chat_config

        for key, value in defaults.items():
            if key == "agents":
                agents = chat_config.get("agents")
                if not isinstance(agents, dict):
                    agents = {}
                    chat_config["agents"] = agents
                for agent_name, agent_defaults in value.items():
                    agent_config = agents.get(agent_name)
                    if not isinstance(agent_config, dict):
                        agent_config = {}
                        agents[agent_name] = agent_config
                    for agent_key, agent_value in agent_defaults.items():
                        agent_config.setdefault(agent_key, agent_value)
            elif key == "runtime":
                runtime = chat_config.get("runtime")
                if not isinstance(runtime, dict):
                    runtime = {}
                    chat_config["runtime"] = runtime
                for runtime_key, runtime_value in value.items():
                    runtime.setdefault(runtime_key, runtime_value)
                self._normalize_screenshot_chat_runtime(runtime)
            elif key == "tts":
                tts_config = chat_config.get("tts")
                if not isinstance(tts_config, dict):
                    tts_config = {}
                    chat_config["tts"] = tts_config
                for tts_key, tts_value in value.items():
                    tts_config.setdefault(tts_key, tts_value)
            elif key == "pi":
                pi_config = chat_config.get("pi")
                if not isinstance(pi_config, dict):
                    pi_config = {}
                    chat_config["pi"] = pi_config
                for pi_key, pi_value in value.items():
                    pi_config.setdefault(pi_key, pi_value)
                backend = str(pi_config.get("backend") or "auto").strip().lower()
                if backend not in {"legacy", "sdk", "auto", "rpc"}:
                    backend = "auto"
                pi_config["backend"] = backend
            else:
                chat_config.setdefault(key, value)

        providers = self.config.get("models", {}).get("providers", {})
        if isinstance(providers, dict):
            default_model = self._normalize_model_name(chat_config.get("default_model"), providers)
            if not default_model:
                primary = (((self.config.get("agents") or {}).get("defaults") or {}).get("model") or {}).get("primary")
                if isinstance(primary, str) and self._normalize_model_name(primary, providers):
                    default_model = primary.strip()
            chat_config["default_model"] = default_model or ""
            agents = chat_config.get("agents")
            if isinstance(agents, dict):
                for agent_config in agents.values():
                    if not isinstance(agent_config, dict):
                        continue
                    normalized = self._normalize_model_name(agent_config.get("model"), providers)
                    agent_config["model"] = normalized or ""

    def _normalize_screenshot_chat_runtime(self, runtime: dict):
        mode = str(runtime.get("mode") or "tk").strip().lower()
        if mode in {"webview-legacy", "legacy-webview"}:
            mode = "webview-process"
        runtime["mode"] = mode if mode in {"tk", "webview-process"} else "tk"

    def get_hotkey_configs(self):
        with self._io_lock:
            hotkeys = self.config.get("hotkeys")
            if not isinstance(hotkeys, list):
                hotkeys = []
            result = [
                dict(item)
                for item in hotkeys
                if isinstance(item, dict)
            ]
            return self._with_screenshot_chat_hotkey(result)

    def _with_screenshot_chat_hotkey(self, hotkeys):
        chat_config = self.config.get("screenshot_chat")
        if not isinstance(chat_config, dict):
            chat_config = self._get_default_screenshot_chat_config()

        desired_keys = str(chat_config.get("hotkey") or "ctrl + shift + alt + x")
        desired_mode = str(chat_config.get("hotkey_mode") or "toggle")
        for hotkey in hotkeys:
            if isinstance(hotkey, dict) and hotkey.get("id") == "ScreenshotChatPopout":
                hotkey.setdefault("command", "open_screenshot_chat")
                hotkey.setdefault("mode", desired_mode)
                hotkey["keys"] = desired_keys
                return hotkeys
        return [
            {
                "id": "ScreenshotChatPopout",
                "keys": desired_keys,
                "command": "open_screenshot_chat",
                "mode": desired_mode,
            },
            *hotkeys,
        ]

    def get_screenshot_chat_config(self):
        with self._io_lock:
            self._apply_screenshot_chat_defaults()
            return dict(self.config.get("screenshot_chat", {}))

    def _apply_audio_stt_defaults(self):
        default_stt = {
            "preferred_model": "",
            "fallback_model": "cliproxy/gemini-2.5-flash",
            "audio_capable_models": [],
            "max_retry": 2,
            "continue_on_error": True,
            "max_workers": 4,
            "parallel_safe_provider_apis": ["openai-completions"],
            "prompt": "Transcribe this audio in Vietnamese-first mode. Keep the original language when it is not Vietnamese. Return only the transcript.",
            "preprocess": {
                "enabled": False,
                "provider": "energy",
                "target_sample_rate": 16000,
                "frame_ms": 30,
                "min_speech_ms": 350,
                "min_silence_ms": 500,
                "speech_pad_ms": 180,
                "merge_gap_ms": 250,
                "energy_db_threshold": None,
            },
            "postprocess": {
                "mode": "correct_vi",
                "model": "",
                "timeout_seconds": 120,
            },
        }
        audio_config = self.config.get("audio")
        if not isinstance(audio_config, dict):
            audio_config = {}
            self.config["audio"] = audio_config

        recording_config = audio_config.get("recording")
        if not isinstance(recording_config, dict):
            recording_config = {}
            audio_config["recording"] = recording_config
        recording_defaults = {"max_minutes": 10}
        for key, value in recording_defaults.items():
            recording_config.setdefault(key, value)

        stt_config = audio_config.get("stt")
        if not isinstance(stt_config, dict):
            stt_config = {}
            audio_config["stt"] = stt_config

        for key, value in default_stt.items():
            if isinstance(value, dict):
                existing = stt_config.get(key)
                if not isinstance(existing, dict):
                    existing = {}
                    stt_config[key] = existing
                for nested_key, nested_value in value.items():
                    existing.setdefault(nested_key, nested_value)
            else:
                stt_config.setdefault(key, value)

    def get(self, key, default=None):
        with self._io_lock:
            keys = key.split(".")
            value = self.config
            for k in keys:
                if isinstance(value, dict) and k in value:
                    value = value[k]
                else:
                    return default
            return value if value is not None else default

    def set(self, key, value):
        with self._io_lock:
            keys = key.split(".")
            target = self.config
            for k in keys[:-1]:
                if k not in target or not isinstance(target[k], dict):
                    target[k] = {}
                target = target[k]
            target[keys[-1]] = value
            self._save_config_unlocked()

    def set_many(self, updates):
        with self._io_lock:
            for key, value in updates.items():
                keys = key.split(".")
                target = self.config
                for k in keys[:-1]:
                    if k not in target or not isinstance(target[k], dict):
                        target[k] = {}
                    target = target[k]
                target[keys[-1]] = value
            self._save_config_unlocked()

    def add_provider_model(self, model_full_name: str) -> str:
        """Register a custom or discovered `provider/model` in config.yaml and return its normalized name."""
        clean = str(model_full_name or "").strip()
        if not clean:
            return ""
        with self._io_lock:
            providers = self.config.setdefault("models", {}).setdefault("providers", {})
            if "/" in clean:
                prov_name, _, mod_name = clean.partition("/")
            else:
                prov_name = next(iter(providers.keys()), "cliproxy")
                mod_name = clean
                clean = f"{prov_name}/{mod_name}"
            prov = providers.setdefault(
                prov_name,
                {
                    "api": "openai-completions",
                    "displayName": prov_name,
                    "baseUrl": "",
                    "apiKey": "",
                    "models": [],
                },
            )
            models = prov.setdefault("models", [])
            if mod_name not in models:
                models.append(mod_name)
                self._save_config_unlocked()
            return clean

    def get_provider_config(self, provider_name):
        config = self.get(f"models.providers.{provider_name}")
        if not isinstance(config, dict):
            config = self.get(f"providers.{provider_name}")
        if isinstance(config, dict):
            normalized = dict(config)
            self._normalize_provider_config(provider_name, normalized)
            return normalized
        return {}

    def get_all_models(self):
        models = {}
        providers = self.get("models.providers")
        if not isinstance(providers, dict):
            providers = {}
        legacy_providers = self.get("providers")
        if isinstance(legacy_providers, dict):
            providers = {**legacy_providers, **providers}

        for p_name, p_config in providers.items():
            if not isinstance(p_config, dict):
                continue
            provider_copy = dict(p_config)
            self._normalize_provider_config(p_name, provider_copy)
            for m_name in provider_copy.get("models", []):
                display_name = f"{p_name}/{m_name}"
                models[display_name] = {
                    "provider": p_name,
                    "model": m_name,
                    "baseUrl": provider_copy.get("baseUrl"),
                    "apiKey": provider_copy.get("apiKey"),
                    "api": "openai-completions",
                }
        return models
