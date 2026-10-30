from dataclasses import dataclass, field


def coerce_bool(value, default=True):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def coerce_timeout(value, default=120.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def platform_from_api(api: str) -> str:
    del api
    return "openai"


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    api: str = "openai-completions"
    platform: str = "openai"
    base_url: str = ""
    api_key: str = ""
    timeout: float = 120.0
    trust_env: bool = True
    extra_headers: dict | None = None
    models: tuple[str, ...] = ()
    display_name: str = ""
    stream: bool = True
    supports_images: bool = True
    supports_audio: bool = True
    raw: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ModelTarget:
    provider: ProviderConfig
    model_name: str
    model_full_name: str
