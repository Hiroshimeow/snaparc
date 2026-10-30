from __future__ import annotations

import time
from src.core.privacy_gate import ConsentScope, PrivacyConsentGate, TokenBudgetController
from src.core.secret_store import SecretStore
from src.llm.providers.openai import OpenAICompatibleProvider
from src.llm.registry import ModelRegistry
from src.services.float_audio_service import FloatAudioModelResolver
from src.utils.ConfigManager import ConfigManager


class LLMService:
    """Unified LLM Service (v2.4) routing exclusively through OpenAI-compatible completions (`openai-completions`)."""

    def __init__(
        self,
        config_manager=None,
        prompt_loader=None,
        default_model=None,
        prompt_type="prompt_translate",
        secret_store: SecretStore | None = None,
        consent_gate: PrivacyConsentGate | None = None,
        budget_controller: TokenBudgetController | None = None,
        **_legacy_ignored,
    ):
        self.config_manager = config_manager or ConfigManager()
        self.registry = ModelRegistry(self.config_manager)
        self.prompt_loader = prompt_loader or (lambda prompt_key: "")
        self.model = default_model or self.registry.default_model()
        self.provider = self.model.split("/", 1)[0] if "/" in self.model else ""
        self.prompt_type = prompt_type
        self.current_prompt = None
        self.secret_store = secret_store or SecretStore()
        self.consent_gate = consent_gate or PrivacyConsentGate()
        self.budget_controller = budget_controller or TokenBudgetController()
        self.audio_model_resolver = FloatAudioModelResolver(self.config_manager)
        self._providers_cache = {}

    def reload_config(self):
        self.registry.reload()

    def set_model(self, model_full_name):
        self.model = model_full_name
        if "/" in model_full_name:
            self.provider = model_full_name.split("/", 1)[0]

    def set_provider(self, provider_name):
        self.provider = provider_name
        provider_config = self.config_manager.get_provider_config(provider_name)
        models = provider_config.get("models") if isinstance(provider_config, dict) else None
        if models:
            self.model = f"{provider_name}/{models[0]}"

    def check_readiness(self, model_full_name: str | None = None) -> dict:
        """Zero-token provider readiness check. Never sends billable completion tokens."""
        candidate = model_full_name or self.model
        try:
            target = self._resolve_target(candidate)
        except Exception as exc:
            return {
                "ready": False,
                "model": candidate,
                "provider": "",
                "tokens_consumed": 0,
                "reason": str(exc),
            }
        if target is None:
            return {
                "ready": False,
                "model": candidate,
                "provider": "",
                "tokens_consumed": 0,
                "reason": "Model not found in config.yaml",
            }
        resolved_key = self.secret_store.resolve_config_secret(
            target.provider.api_key, provider_name=target.provider.name
        )
        has_key = bool(resolved_key and not (resolved_key.startswith("<") and resolved_key.endswith(">")))
        if not self.consent_gate.check_permission(ConsentScope.CLOUD_UPLOAD, provider=target.provider.name):
            return {
                "ready": False,
                "model": f"{target.provider.name}/{target.model_name}",
                "provider": target.provider.name,
                "tokens_consumed": 0,
                "reason": "Blocked by offline mode or privacy consent gate",
            }
        return {
            "ready": has_key or bool(target.provider.base_url),
            "model": f"{target.provider.name}/{target.model_name}",
            "provider": target.provider.name,
            "api": "openai-completions",
            "key_configured": has_key,
            "tokens_consumed": 0,
            "reason": "ready" if (has_key or target.provider.base_url) else "api_key_unconfigured",
        }

    def process_image(
        self,
        image_paths,
        callback=None,
        history=None,
        custom_prompt_type=None,
        custom_prompt_text=None,
        model_full_name=None,
        enable_failover: bool = False,
        max_output_tokens=None,
        timeout_seconds=None,
    ):
        bounds = self._request_bounds(max_output_tokens, timeout_seconds)
        if isinstance(custom_prompt_text, str) and custom_prompt_text.strip():
            effective_prompt = custom_prompt_text
        else:
            effective_prompt = self.prompt_loader(custom_prompt_type or self.prompt_type)
        self.current_prompt = effective_prompt

        primary_model = model_full_name or self.model
        target = self._resolve_target_or_callback(primary_model, callback)
        if target is None:
            return None
        provider = self._get_provider(target.provider.name, target.model_name)
        if provider is None:
            if callback:
                callback(self._error_payload(f"Provider not found in config.yaml: {target.provider.name}"))
            return None

        if not enable_failover:
            return provider.process_image(
                image_paths,
                effective_prompt,
                callback=callback,
                history=history,
                model=target.model_name,
                **bounds,
            )

        return self._run_with_failover(
            primary_full_name=f"{target.provider.name}/{target.model_name}",
            invoke_fn=lambda prov, mod_name, cb: prov.process_image(
                image_paths,
                effective_prompt,
                callback=cb,
                history=history,
                model=mod_name,
                **bounds,
            ),
            prompt_text=effective_prompt,
            callback=callback,
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
        enable_failover: bool = False,
        max_output_tokens=None,
        timeout_seconds=None,
    ):
        bounds = self._request_bounds(max_output_tokens, timeout_seconds)
        model_full_name = model_name if self._is_full_model_name(model_name) else self.model
        target = self._resolve_target_or_callback(model_full_name, callback)
        if target is None:
            return None
        request_model = (
            model_name
            if model_name and not self._is_full_model_name(model_name)
            else target.model_name
        )
        provider = self._get_provider(target.provider.name, target.model_name)
        if provider is None:
            if callback:
                callback(self._error_payload(f"Provider not found in config.yaml: {target.provider.name}"))
            return None

        effective_temp = 0.7 if temperature is None else temperature
        if not enable_failover:
            return provider.send_text_message(
                text_prompt,
                callback=callback,
                history=history,
                system_prompt=system_prompt,
                temperature=effective_temp,
                model_name=request_model,
                image=image,
                **bounds,
            )

        return self._run_with_failover(
            primary_full_name=f"{target.provider.name}/{target.model_name}",
            invoke_fn=lambda prov, mod_name, cb: prov.send_text_message(
                text_prompt,
                callback=cb,
                history=history,
                system_prompt=system_prompt,
                temperature=effective_temp,
                model_name=mod_name,
                image=image,
                **bounds,
            ),
            prompt_text=text_prompt,
            callback=callback,
        )

    @staticmethod
    def _request_bounds(max_output_tokens, timeout_seconds):
        from src.llm.request_bounds import completion_options
        completion_options(max_output_tokens, timeout_seconds)  # Validate before dispatch.
        return {key: value for key, value in {
            "max_output_tokens": max_output_tokens, "timeout_seconds": timeout_seconds,
        }.items() if value is not None}

    def _run_with_failover(self, primary_full_name: str, invoke_fn, prompt_text: str, callback=None):
        fallbacks = self.config_manager.get("agents.defaults.model.fallbacks", [])
        if not isinstance(fallbacks, list):
            fallbacks = []
        candidates = [primary_full_name, *[str(f) for f in fallbacks if str(f) and str(f) != primary_full_name]]
        started = time.perf_counter()

        def try_index(idx: int):
            candidate_full = candidates[idx]
            target = self._resolve_target(candidate_full)
            if target is None:
                if idx + 1 < len(candidates):
                    return try_index(idx + 1)
                if callback:
                    callback(self._error_payload(f"All models failed starting from {primary_full_name}"))
                return None
            prov = self._get_provider(target.provider.name, target.model_name)
            if prov is None:
                if idx + 1 < len(candidates):
                    return try_index(idx + 1)
                if callback:
                    callback(self._error_payload(f"Provider unavailable: {target.provider.name}"))
                return None

            def on_candidate_result(res):
                is_err = (
                    res is None
                    or (isinstance(res, dict) and str(res.get("text") or "").startswith(("Error:", "[Error:")))
                    or (isinstance(res, str) and res.startswith(("Error:", "[Error:")))
                )
                if is_err and idx + 1 < len(candidates):
                    try_index(idx + 1)
                    return
                latency = (time.perf_counter() - started) * 1000.0
                ans_text = res.get("text", "") if isinstance(res, dict) else str(res or "")
                fallback_origin = primary_full_name if idx > 0 else ""
                self.budget_controller.record_usage(
                    run_id=f"run_{int(time.time() * 1000)}",
                    provider=target.provider.name,
                    model=f"{target.provider.name}/{target.model_name}",
                    prompt_text=prompt_text,
                    completion_text=ans_text,
                    latency_ms=latency,
                    fallback_from=fallback_origin,
                )
                if isinstance(res, dict) and idx > 0:
                    enriched = dict(res)
                    enriched["model_used"] = f"{target.provider.name}/{target.model_name}"
                    enriched["fallback_from"] = primary_full_name
                    if callback:
                        callback(enriched)
                    return
                if callback:
                    callback(res)

            try:
                return invoke_fn(prov, target.model_name, on_candidate_result)
            except Exception as exc:
                if idx + 1 < len(candidates):
                    return try_index(idx + 1)
                if callback:
                    callback(self._error_payload(exc))
                return None

        return try_index(0)

    def process_audio(
        self,
        audio_path,
        callback=None,
        prompt="Transcribe this audio",
        model_full_name=None,
        resolution=None,
    ):
        selected_model = model_full_name or self.model
        resolved = resolution or self.audio_model_resolver.resolve(selected_model=selected_model)
        if not resolved.success:
            if callback:
                callback(None)
            return None
        provider = self._get_provider(resolved.provider_name, resolved.model_name)
        if provider is None or not provider.supports("audio"):
            if callback:
                callback(None)
            return None
        return provider.process_audio(
            audio_path,
            callback=callback,
            prompt=prompt,
            model=resolved.model_name,
        )

    def _resolve_target(self, model_full_name):
        return self.registry.resolve(model_full_name, default_model=self.model)

    def _resolve_target_or_callback(self, model_full_name, callback=None):
        try:
            target = self._resolve_target(model_full_name)
        except Exception as exc:
            if callback:
                callback(self._error_payload(exc))
            return None
        if target is None:
            selected = model_full_name or self.model or "<none>"
            message = f"Model not found in config.yaml: {selected}. Expected format: provider/model."
            if callback:
                callback(self._error_payload(message))
            return None
        return target

    def _error_payload(self, exc):
        return {
            "text": f"Error: {str(exc)}",
            "api_index": self.provider,
            "key_mask": "Error",
        }

    def _get_provider(self, provider_name, model_name=None):
        cache_key = (provider_name, model_name or "")
        provider_config = self.registry.get_provider(provider_name)
        if provider_config is None:
            self._providers_cache.pop(cache_key, None)
            return None

        effective_key = (
            self.secret_store.resolve_config_secret(provider_config.api_key, provider_name=provider_name)
            or provider_config.api_key
        )
        fingerprint = self._provider_fingerprint(provider_config, effective_key)
        cached = self._providers_cache.get(cache_key)
        if cached is not None and cached[0] == fingerprint:
            return cached[1]

        provider = OpenAICompatibleProvider(
            api_key=effective_key,
            base_url=provider_config.base_url,
            model=model_name,
            provider_name=provider_name,
            timeout=provider_config.timeout,
            trust_env=provider_config.trust_env,
            extra_headers=provider_config.extra_headers,
        )
        self._providers_cache[cache_key] = (fingerprint, provider)
        return provider

    @staticmethod
    def _provider_fingerprint(provider_config, effective_key=None):
        headers = tuple(
            sorted(
                (str(key), str(value))
                for key, value in (provider_config.extra_headers or {}).items()
            )
        )
        return (
            provider_config.api,
            provider_config.platform,
            provider_config.base_url,
            effective_key if effective_key is not None else provider_config.api_key,
            provider_config.timeout,
            provider_config.trust_env,
            headers,
        )

    @staticmethod
    def _is_full_model_name(model_name):
        return isinstance(model_name, str) and "/" in model_name
