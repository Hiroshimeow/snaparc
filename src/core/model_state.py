"""Single Source of Truth for Active Provider & Model State (v1.1)."""

from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Any

from src.core.event_bus import AppEventBus


@dataclass(frozen=True)
class ModelSnapshot:
    full_model: str
    provider: str
    model_id: str
    source: str = "init"

    @property
    def full_name(self) -> str:
        return self.full_model

    def __getitem__(self, key: str) -> str:
        mapping = {
            "full_name": self.full_model,
            "full_model": self.full_model,
            "provider": self.provider,
            "model": self.model_id,
            "model_id": self.model_id,
            "source": self.source,
        }
        return mapping[key]


class ModelStateStore:
    def __init__(
        self,
        config_manager: Any,
        event_bus_or_gemini: Any = None,
        event_bus: AppEventBus | None = None,
    ) -> None:
        self.config_manager = config_manager
        if isinstance(event_bus_or_gemini, AppEventBus):
            self.gemini_engine = None
            self.event_bus = event_bus_or_gemini
        else:
            self.gemini_engine = event_bus_or_gemini
            self.event_bus = event_bus or AppEventBus.get_instance()
        self.bus = self.event_bus
        self._lock = threading.RLock()
        self._provider: str = ""
        self._model_id: str = ""
        self._full_name: str = ""
        self._source: str = "init"
        self.sync_from_config()

    @property
    def full_name(self) -> str:
        with self._lock:
            return self._full_name

    @property
    def current_model(self) -> str:
        with self._lock:
            return self._full_name

    @property
    def provider(self) -> str:
        with self._lock:
            return self._provider

    @property
    def current_provider(self) -> str:
        with self._lock:
            return self._provider

    @property
    def model_id(self) -> str:
        with self._lock:
            return self._model_id

    def sync_from_config(self) -> str:
        with self._lock:
            if hasattr(self.config_manager, "reload_if_changed"):
                self.config_manager.reload_if_changed()
            all_models = (
                self.config_manager.get_all_models()
                if hasattr(self.config_manager, "get_all_models")
                else {}
            )
            primary = ""
            if hasattr(self.config_manager, "get"):
                primary = str(self.config_manager.get("agents.defaults.model.primary", "") or "").strip()
                if not primary:
                    primary = str(self.config_manager.get("screenshot_chat.default_model", "") or "").strip()
            if (not primary or (all_models and primary not in all_models)) and self.gemini_engine is not None:
                cand = str(getattr(self.gemini_engine, "model", "") or "").strip()
                if cand and (not all_models or cand in all_models):
                    primary = cand
            if not primary and all_models:
                primary = next(iter(all_models.keys()))
            self._apply_locked(primary, source="config")
            return self._full_name

    def _apply_locked(self, full_name: str, source: str = "update") -> None:
        clean = str(full_name or "").strip()
        if "/" in clean:
            prov, mod = clean.split("/", 1)
        else:
            prov, mod = "", clean
        self._full_name = clean
        self._provider = prov
        self._model_id = mod
        self._source = source

    def set_model(self, model_full_name: str, *, source: str = "user", persist: bool = True) -> ModelSnapshot:
        clean = str(model_full_name or "").strip()
        if not clean:
            return self.snapshot()

        with self._lock:
            if "/" not in clean and self._provider:
                clean = f"{self._provider}/{clean}"
            self._apply_locked(clean, source=source)

            if self.gemini_engine is not None:
                self.gemini_engine.model = self._full_name
                self.gemini_engine.provider = self._provider
                llm_svc = getattr(self.gemini_engine, "llm_service", None)
                if llm_svc is not None:
                    llm_svc.model = self._full_name
                    llm_svc.provider = self._provider

            if persist and hasattr(self.config_manager, "set_many"):
                try:
                    self.config_manager.set_many(
                        {
                            "agents.defaults.model.primary": self._full_name,
                            "screenshot_chat.default_model": self._full_name,
                        }
                    )
                except Exception:
                    pass
            state = self.snapshot()

        self.event_bus.publish("model_changed", state)
        return state

    def set_provider(self, provider_name: str, *, source: str = "user", persist: bool = True) -> ModelSnapshot:
        prov = str(provider_name or "").strip()
        if not prov:
            return self.snapshot()

        target_full = ""
        if hasattr(self.config_manager, "get_provider_config"):
            p_cfg = self.config_manager.get_provider_config(prov) or {}
            models = p_cfg.get("models") if isinstance(p_cfg, dict) else []
            if isinstance(models, list) and models:
                first_mod = models[0]
                if isinstance(first_mod, dict):
                    first_mod = first_mod.get("id") or first_mod.get("name") or ""
                first_mod = str(first_mod).strip()
                if first_mod.startswith(f"{prov}/"):
                    target_full = first_mod
                else:
                    target_full = f"{prov}/{first_mod}"

        if not target_full and hasattr(self.config_manager, "get_all_models"):
            for key in self.config_manager.get_all_models().keys():
                if str(key).startswith(f"{prov}/"):
                    target_full = str(key)
                    break

        if not target_full:
            target_full = f"{prov}/{self._model_id or 'default'}"

        return self.set_model(target_full, source=source, persist=persist)

    def snapshot(self) -> ModelSnapshot:
        with self._lock:
            return ModelSnapshot(
                full_model=self._full_name,
                provider=self._provider,
                model_id=self._model_id,
                source=self._source,
            )
