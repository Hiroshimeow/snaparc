
from src.llm.errors import ProviderResolutionError, ProviderUnsupportedError
from src.llm.provider_config import (
    ModelTarget,
    ProviderConfig,
    coerce_bool,
    coerce_timeout,
)
from src.utils.ConfigManager import ConfigManager


class ModelRegistry:
    def __init__(self, config_manager=None):
        self.config_manager = config_manager or ConfigManager()

    def reload(self):
        self.config_manager.load_config(force=True)

    def get_provider(self, provider_name: str) -> ProviderConfig | None:
        raw = self.config_manager.get_provider_config(provider_name)
        if not raw:
            return None
        api = self._normalize_api(raw.get("api"))
        platform = "openai-compatible"
        extra_headers = raw.get("extraHeaders")
        models = self._normalize_models(provider_name, raw)
        return ProviderConfig(
            name=provider_name,
            api=api,
            platform=platform,
            base_url=raw.get("baseUrl") or "",
            api_key=raw.get("apiKey") or "",
            timeout=coerce_timeout(raw.get("timeout"), 120.0),
            trust_env=coerce_bool(raw.get("trustEnv"), True),
            extra_headers=dict(extra_headers) if isinstance(extra_headers, dict) else None,
            models=tuple(models),
            display_name=raw.get("displayName") or provider_name,
            stream=coerce_bool(raw.get("stream"), True),
            supports_images=coerce_bool(raw.get("supportsImages"), True),
            supports_audio=coerce_bool(raw.get("supportsAudio"), True),
            raw=dict(raw),
        )

    def _normalize_api(self, api: str) -> str:
        value = str(api or "").strip().lower()
        if value != "openai-completions":
            raise ProviderUnsupportedError(
                f"Unsupported provider API {value!r}; SnapArc only supports openai-completions."
            )
        return value

    def _normalize_models(self, provider_name: str, raw: dict) -> tuple[str, ...]:
        raw_models = raw.get("models")
        if isinstance(raw_models, str):
            raw_models = [raw_models]
        candidates = list(raw_models) if isinstance(raw_models, list) else []
        seen = set()
        models = []
        for candidate in candidates:
            if isinstance(candidate, dict):
                candidate = candidate.get("id") or candidate.get("name") or ""
            if not isinstance(candidate, str):
                continue
            model_name = candidate.strip()
            if not model_name:
                continue
            prefix = f"{provider_name}/"
            if model_name.startswith(prefix):
                model_name = model_name[len(prefix):]
            if model_name not in seen:
                seen.add(model_name)
                models.append(model_name)
        return tuple(models)

    def resolve(self, model_full_name: str, default_model: str = "") -> ModelTarget:
        selected = model_full_name or default_model
        if not isinstance(selected, str) or not selected.strip():
            raise ProviderResolutionError("No model selected.")
        selected = selected.strip()
        provider_name, separator, model_name = selected.partition("/")
        if not separator or not model_name:
            raise ProviderResolutionError(f"Invalid model name: {selected}")
        provider = self.get_provider(provider_name)
        if provider is None:
            raise ProviderResolutionError(f"Unknown provider in config.yaml: {provider_name}")
        if model_name not in provider.models:
            available = ", ".join(
                f"{provider_name}/{known_model}" for known_model in provider.models
            ) or "<none>"
            raise ProviderResolutionError(
                f"Model not found in config.yaml: {selected}. "
                "Expected format: provider/model. "
                f"Available models: {available}"
            )
        return ModelTarget(
            provider=provider,
            model_name=model_name,
            model_full_name=selected,
        )

    def default_model(self) -> str:
        value = self.config_manager.get("agents.defaults.model.primary")
        if not isinstance(value, str) or not value.strip():
            return ""
        value = value.strip()
        if "/" in value:
            return value
        get_all_models = getattr(self.config_manager, "get_all_models", None)
        if callable(get_all_models):
            for model_full_name in get_all_models().keys():
                provider_name, separator, model_name = model_full_name.partition("/")
                if separator and model_name == value:
                    return f"{provider_name}/{model_name}"
        return value