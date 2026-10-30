from __future__ import annotations

import os
import queue
import re
import threading
import time
from tkinter import filedialog, messagebox

from PIL import ImageGrab

try:
    import tomllib
except ImportError:  # pragma: no cover
    import tomli as tomllib

from src.agents.pi_runtime_factory import detect_pi_availability
from src.gui.screenshot_chat.pi_harness import (
    SLASH_COMMANDS,
    export_session_html,
    extract_message_structured,
    init_pi_project_harness,
)
from src.gui.chat_text_sanitizer import strip_repeated_author_prefixes
from src.gui.screenshot_chat.mentions import parse_mentions
from src.gui.screenshot_chat.models import (
    ScreenshotChatAttachment,
    ScreenshotChatMessage,
    ScreenshotChatResponder,
    ScreenshotChatSession,
)
from src.gui.screenshot_chat.runner import RunnerEvent, ScreenshotChatRunner
from src.gui.screenshot_chat.store import ScreenshotChatStore
from src.plugins.snap_history import SnapHistoryManager
from src.utils.ConfigManager import ConfigManager


_PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_PACKAGE_DIR)))

PI_MODEL_ID_PREFIX = "pi://agent/"
PI_MODEL_LABEL = "Pi Agent"
DEFAULT_PI_AGENT_NAME = "pi"


def sanitize_speech_text(text: str) -> str:
    """Reduce assistant Markdown to useful spoken prose."""
    value = re.sub(r"```.*?```", " ", str(text or ""), flags=re.DOTALL)
    kept_lines = []
    for line in value.splitlines():
        lowered = line.strip().lower()
        if lowered.startswith(("tool:", "mermaid:", "[tool", "<tool")):
            continue
        line = re.sub(r"^\s{0,3}#{1,6}\s*", "", line)
        line = re.sub(r"^\s*(?:[-+*>]|\d+[.)])\s+", "", line)
        kept_lines.append(line)
    value = "\n".join(kept_lines)
    value = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", value)
    value = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", value)
    value = re.sub(r"[*_~`]", "", value)
    return re.sub(r"\s+", " ", value).strip()


class ScreenshotChatController:
    ALLOWED_SESSION_MODES = {"chat", "note"}

    def __init__(
        self,
        root,
        config_manager: ConfigManager | None = None,
        project_root: str = _PROJECT_ROOT,
        held_images_getter=None,
        held_images_consumer=None,
        snap_history_getter=None,
        llm_service=None,
        gemini_factory=None,
        runner: ScreenshotChatRunner | None = None,
        store: ScreenshotChatStore | None = None,
        model_state_store=None,
    ):
        self.root = root
        self.config_manager = config_manager or ConfigManager()
        self.project_root = project_root
        self.held_images_getter = held_images_getter or (lambda: [])
        self.held_images_consumer = held_images_consumer or (lambda paths: None)
        self.snap_history_getter = snap_history_getter or SnapHistoryManager.get_instance
        self.event_queue = queue.Queue()
        self.runner = runner or ScreenshotChatRunner(
            self.event_queue,
            llm_service=llm_service,
            gemini_factory=gemini_factory,
        )
        self.store = store or ScreenshotChatStore(self._history_path())
        if hasattr(self.store, "set_eviction_callback"):
            self.store.set_eviction_callback(self._on_store_session_evicted)
        self.window = None
        self.active_session_id = ""
        self.pending_attachments: list[ScreenshotChatAttachment] = []
        self._job_to_placeholder: dict[str, str] = {}
        self._job_to_user_message: dict[str, str] = {}
        self._canceled_job_ids: set[str] = set()
        self._status = ""
        self._runtime_status = {
            "state": "ready",
            "scope": "pi",
            "label": "Pi Sidecar Ready",
            "message": "",
            "detail": "",
            "retryable": False,
        }
        self._lock = threading.Lock()
        self._speech_lock = threading.RLock()
        self._audio_plugin = None
        self._playback_provider = None
        self._speech_generation = 0
        tts_config = self._tts_config()
        self._speech_auto_read = bool(tts_config.get("auto_read", False))
        self._speech_state = {
            "state": "idle",
            "message_id": "",
            "message": "",
        }
        self._webview_disabled_reason = ""
        self._prompt_definitions_cache: dict[str, str] = {}
        self._prompt_definitions_mtime: float | None = None
        self._prompt_load_error_signature = ""
        self.model_state_store = model_state_store
        self._favorite_models: list[str] = self._load_favorite_models()
        self._pi_thinking_level: str = str(self._pi_config().get("thinking_level") or "medium")
        self._pi_tool_mode: str = str(
            self._pi_config().get("tool_policy")
            or (self._pi_config().get("tools") or {}).get("mode")
            or "approve_mutations"
        )
        self._ensure_active_session()
        self._recover_interrupted_jobs()
        if self.model_state_store is not None and hasattr(self.model_state_store, "bus"):
            self.model_state_store.bus.subscribe("model_changed", self._on_global_model_changed)

    def _recover_interrupted_jobs(self):
        for session in reversed(self.store.list_sessions()):
            changed = bool(session.pending_job_ids)
            session.pending_job_ids = []
            for message in session.messages:
                if message.status not in {"thinking", "queued"}:
                    continue
                message.status = "canceled"
                message.metadata["interrupted_at_startup"] = True
                if message.role == "assistant" and message.text in {"", "Thinking"}:
                    message.text = "[Interrupted by restart]"
                changed = True
            if changed:
                self.store.save_session(session)

    def _config(self) -> dict:
        self.config_manager.reload_if_changed()
        return self.config_manager.get_screenshot_chat_config()

    def _history_path(self) -> str:
        config = self.config_manager.get_screenshot_chat_config()
        raw_path = config.get("history_path") or "data/screenshot_chat_sessions.json"
        return raw_path if os.path.isabs(raw_path) else os.path.join(self.project_root, raw_path)

    def _load_prompt_definitions(self) -> dict[str, str]:
        toml_path = os.path.join(self.project_root, "prompts.toml")
        yaml_path = os.path.join(self.project_root, "config.yaml")
        use_toml = os.path.exists(toml_path)
        prompt_path = toml_path if use_toml else yaml_path
        try:
            current_mtime = os.path.getmtime(prompt_path)
        except FileNotFoundError:
            self._prompt_definitions_cache = {}
            self._prompt_definitions_mtime = None
            self._prompt_load_error_signature = ""
            return {}
        except OSError as exc:
            self._log_prompt_load_error(exc)
            self._prompt_definitions_cache = {}
            self._prompt_definitions_mtime = None
            return {}

        if self._prompt_definitions_mtime == current_mtime:
            return dict(self._prompt_definitions_cache)

        try:
            if use_toml:
                with open(prompt_path, "rb") as handle:
                    payload = tomllib.load(handle)
            else:
                from src.utils.yaml_io import load_yaml_or_json
                with open(prompt_path, "r", encoding="utf-8") as handle:
                    payload = load_yaml_or_json(handle.read())
            prompts = payload.get("prompts", {}) if isinstance(payload, dict) else {}
            loaded = {}
            if isinstance(prompts, dict):
                loaded = {
                    str(key): str(value).strip()
                    for key, value in prompts.items()
                    if isinstance(value, str)
                }
            self._prompt_definitions_cache = loaded
            self._prompt_definitions_mtime = current_mtime
            self._prompt_load_error_signature = ""
            return dict(loaded)
        except Exception as exc:
            self._log_prompt_load_error(exc)
            self._prompt_definitions_cache = {}
            self._prompt_definitions_mtime = current_mtime
            return {}

    def _log_prompt_load_error(self, exc: Exception):
        signature = f"{type(exc).__name__}: {exc}"
        if signature != self._prompt_load_error_signature:
            print(f"ScreenshotChat: Could not load prompts config: {exc}")
            self._prompt_load_error_signature = signature

    def open_or_focus(self):
        if not self._ensure_window():
            return
        self.window.show()

    def prewarm_window(self):
        if self._ensure_window():
            self.window.prewarm_hidden()

    def _ensure_window(self) -> bool:
        config = self._config()
        if not config.get("enabled", True):
            print("ScreenshotChat: Popout disabled by config.")
            return False
        runtime = config.get("runtime") if isinstance(config.get("runtime"), dict) else {}
        mode = self._resolve_runtime_mode(runtime)
        if mode == "webview-process" and self._webview_disabled_reason:
            mode = "tk"
        if self.window is None or not self._window_exists():
            print(f"ScreenshotChat: Initializing window mode={mode}")
            if mode == "webview-process":
                self._set_runtime_status("connecting", scope="ui")
            try:
                if mode == "webview-process":
                    self.window = self._create_webview_window()
                    print("ScreenshotChat: Created legacy WebView window wrapper.")
                else:
                    self.window = self._create_tk_window()
                    print("ScreenshotChat: Created Tk window.")
            except Exception as exc:
                if mode == "webview-process":
                    self._fallback_to_tk(exc, "WebView construction failed")
                else:
                    raise
            try:
                self.window.prewarm_hidden()
                active_mode = "webview" if self.window.__class__.__name__ == "ScreenshotChatWebViewWindow" else "tk"
                if self._runtime_status.get("state") == "connecting":
                    self._set_runtime_status("ready", scope="pi")
                print(f"ScreenshotChat: Window prewarmed successfully using {active_mode}.")
            except Exception as exc:
                if hasattr(self.window, "shutdown"):
                    try:
                        self.window.shutdown()
                    except Exception:
                        pass
                self.window = None
                if mode == "webview-process":
                    self._fallback_to_tk(exc, "WebView startup failed")
                    self.window.prewarm_hidden()
                    print("ScreenshotChat: Tk fallback window prewarmed successfully.")
                else:
                    raise
        else:
            active_mode = "webview" if self.window.__class__.__name__ == "ScreenshotChatWebViewWindow" else "tk"
            print(f"ScreenshotChat: Reusing existing {active_mode} window.")
        return True

    def _resolve_runtime_mode(self, runtime: dict) -> str:
        mode = str(runtime.get("mode") or "tk").strip().lower()
        if mode in {"webview-process", "webview-legacy", "legacy-webview"}:
            return "webview-process"
        return "tk"

    def _fallback_to_tk(self, exc: Exception, context: str):
        reason = str(exc)
        self._webview_disabled_reason = reason
        self._status = f"{context}; using Tk UI. {reason}"
        self._set_runtime_status(
            "ready",
            scope="ui",
            message=f"{context}; using Tk fallback.",
            detail=reason,
        )
        print(f"ScreenshotChat: {context}, falling back to Tk UI: {reason}")
        self.window = self._create_tk_window()
        print("ScreenshotChat: Created Tk fallback window.")

    def _create_webview_window(self):
        from src.gui.screenshot_chat.webview_window import ScreenshotChatWebViewWindow

        return ScreenshotChatWebViewWindow(self)

    def _create_tk_window(self):
        from src.gui.screenshot_chat.window import ScreenshotChatWindow

        return ScreenshotChatWindow(self.root, self)

    def toggle_window(self):
        if self.window is not None and self._window_exists() and self.window.is_visible():
            self.hide_window()
            return
        self.open_or_focus()

    def _window_exists(self) -> bool:
        try:
            return bool(self.window and self.window.winfo_exists())
        except Exception:
            return False

    def hide_window(self):
        if self.window is not None and self._window_exists():
            self.window.hide()

    def refresh_window(self, messages_only: bool = False):
        if self.window is not None and self._window_exists():
            if messages_only and hasattr(self.window, "refresh_messages"):
                self.window.refresh_messages()
            else:
                self.window.refresh_all()

    def shutdown_window(self):
        self.stop_speech()
        try:
            if self._audio_plugin is not None:
                self._audio_plugin.close()
        except Exception:
            pass
        try:
            if self.runner is not None and hasattr(self.runner, "shutdown"):
                self.runner.shutdown()
        except Exception:
            pass
        if self.window is None:
            return
        try:
            if hasattr(self.window, "shutdown"):
                self.window.shutdown()
            elif self._window_exists():
                self.window.hide()
        finally:
            self.window = None

    def shutdown(self):
        self.shutdown_window()

    def _ensure_active_session(self) -> ScreenshotChatSession:
        session = self.store.get_session(self.active_session_id) if self.active_session_id else None
        if session is not None:
            return self._backfill_session_defaults(session)
        sessions = self.store.list_sessions()
        if sessions:
            self.active_session_id = sessions[0].id
            return self._backfill_session_defaults(sessions[0])
        config = self._config()
        created = self.store.create_session(
            model_name=self._default_model_name(),
            prompt_type=config.get("default_prompt_type", "prompt_translate"),
            mode="chat",
            system_prompt="",
            system_prompt_label="Blank",
            title="New Chat",
        )
        self.active_session_id = created.id
        return created

    def _backfill_session_defaults(self, session: ScreenshotChatSession) -> ScreenshotChatSession:
        changed = False
        if not session.model_name:
            session.model_name = self._default_model_name()
            changed = True
        if session.mode not in self.ALLOWED_SESSION_MODES:
            session.mode = "chat"
            changed = True
        if changed:
            saved = self.store.save_session(session)
            return saved or session
        return session

    def _default_model_name(self) -> str:
        return self._normal_default_model_name()

    def _normal_default_model_name(self) -> str:
        models = self.config_manager.get_all_models()
        config = self._config()
        get_config_value = getattr(self.config_manager, "get", None)
        primary = get_config_value("agents.defaults.model.primary", "") if callable(get_config_value) else ""
        global_model = (
            self.model_state_store.current_model
            if self.model_state_store is not None and hasattr(self.model_state_store, "current_model")
            else ""
        )
        candidates = [
            global_model,
            config.get("default_model", ""),
            primary,
        ]
        for candidate in candidates:
            if isinstance(candidate, str):
                clean_candidate = candidate.strip()
                if clean_candidate in models and not self._is_pi_model_name(clean_candidate):
                    return clean_candidate
        for model_name in models.keys():
            if not self._is_pi_model_name(model_name):
                return model_name
        return ""

    def _pi_config(self) -> dict:
        config = self._config()
        return config.get("pi") if isinstance(config.get("pi"), dict) else {}

    @property
    def pi_primary_mode(self) -> bool:
        return self.is_pi_primary_mode()

    @property
    def current_model(self) -> str:
        return str(self.get_active_session().model_name or self._default_model_name())

    def set_model(self, model_name: str, *, sync_global: bool = True) -> None:
        self.set_session_model(model_name, sync_global=sync_global)

    def register_custom_model(self, raw_model_id: str) -> str:
        return self.add_custom_model(raw_model_id)

    def is_pi_primary_mode(self) -> bool:
        """Returns True when Pi Sidecar is configured as the primary chatbot runtime (default in v2.4)."""
        pi_cfg = self._pi_config()
        if not pi_cfg.get("primary_mode", False):
            return False
        status = self.pi_selector_status()
        return bool(status.get("available", False))

    def get_pi_thinking_level(self) -> str:
        return self._pi_thinking_level

    def set_thinking_level(self, level: str) -> str:
        norm = str(level or "medium").strip().lower()
        if norm not in {"off", "minimal", "low", "medium", "high"}:
            norm = "medium"
        self._pi_thinking_level = norm
        self.set_status(f"🧠 Thinking level: {norm}")
        self.refresh_window()
        return norm

    def get_pi_tool_mode(self) -> str:
        return self._pi_tool_mode

    def set_tool_mode(self, mode: str) -> str:
        norm = str(mode or "approve_mutations").strip().lower()
        if norm not in {"none", "read_only", "approve_mutations"}:
            norm = "approve_mutations"
        self._pi_tool_mode = norm
        self.set_status(f"🛠️ Tool policy: {norm}")
        self.refresh_window()
        return norm

    def _pi_availability(self):
        try:
            return detect_pi_availability(self._pi_config())
        except Exception:
            return None

    def pi_selector_status(self) -> dict:
        pi_config = self._pi_config()
        availability = self._pi_availability()
        status = {
            "enabled": pi_config.get("enabled") is True,
            "auto_detect": pi_config.get("auto_detect", True) is not False,
            "configured_executable": str(pi_config.get("executable") or ""),
            "resolved_executable": "",
            "package_available": False,
            "package_origin": "",
            "python_executable": "",
            "available": False,
            "reason": "unavailable",
            "detail": "",
        }
        if availability is not None:
            status["resolved_executable"] = str(getattr(availability, "executable", "") or "")
            status["package_available"] = bool(getattr(availability, "package_available", False))
            status["package_origin"] = str(getattr(availability, "package_origin", "") or "")
            status["python_executable"] = str(getattr(availability, "python_executable", "") or "")
            status["available"] = bool(getattr(availability, "available", False))
            status["reason"] = str(getattr(availability, "reason", "") or status["reason"])
            status["detail"] = str(getattr(availability, "detail", "") or "")
            status["enabled"] = bool(status["available"]) or status["enabled"]
        return status

    def _pi_selector_reason(self, reason: str) -> str:
        return {
            "disabled": "Pi disabled",
            "disabled_by_user": "Pi disabled by user",
            "missing_executable": "Pi executable not found",
            "missing_node": "Node.js not found",
            "missing_bridge": "Pi SDK bridge not found",
            "missing_sdk_package": "Pi SDK package not installed",
            "legacy_backend_removed": "Legacy Pi backend removed",
            "available": "available",
            "unavailable": "Pi unavailable",
        }.get(reason or "", reason or "Pi unavailable")

    def _pi_model_selectable(self, model_name: str) -> tuple[bool, str]:
        if not self._is_pi_model_name(model_name):
            return True, ""
        for option in self.available_model_options():
            if option.get("id") == model_name:
                if option.get("available", True):
                    return True, ""
                return False, str(option.get("reason") or "Pi unavailable")
        return False, "Pi unavailable"

    def _pi_agent_names(self) -> list[str]:
        pi_config = self._pi_config()
        names = pi_config.get("agent_names", [DEFAULT_PI_AGENT_NAME])
        if not isinstance(names, list):
            names = [DEFAULT_PI_AGENT_NAME]
        clean_names = []
        for raw_name in names:
            name = str(raw_name or "").strip().lower()
            if name and name not in clean_names:
                clean_names.append(name)
        return clean_names or [DEFAULT_PI_AGENT_NAME]

    def _pi_model_id(self, agent_name: str = DEFAULT_PI_AGENT_NAME) -> str:
        name = str(agent_name or DEFAULT_PI_AGENT_NAME).strip().lower() or DEFAULT_PI_AGENT_NAME
        return f"{PI_MODEL_ID_PREFIX}{name}"

    def _is_pi_model_name(self, model_name: str) -> bool:
        return isinstance(model_name, str) and model_name.startswith(PI_MODEL_ID_PREFIX)

    def _pi_agent_name_from_model(self, model_name: str) -> str:
        if not self._is_pi_model_name(model_name):
            return ""
        return model_name[len(PI_MODEL_ID_PREFIX):].strip().lower() or DEFAULT_PI_AGENT_NAME

    def model_display_name(self, model_name: str) -> str:
        if self._is_pi_model_name(model_name):
            return PI_MODEL_LABEL
        if not model_name:
            return "Model"
        tail = model_name.split("/")[-1]
        return tail if len(tail) <= 20 else tail[:17] + "..."

    def available_model_options(self) -> list[dict]:
        options = []
        for model_name in self.available_models():
            if self._is_pi_model_name(model_name):
                continue
            tail = model_name.split("/")[-1] if model_name else "Model"
            options.append(
                {
                    "id": model_name,
                    "label": tail,
                    "runtime": "pi" if self.is_pi_primary_mode() else "llm",
                    "available": True,
                    "reason": "",
                    "favorite": model_name in self._favorite_models,
                }
            )
        pi_config = self._pi_config()
        show_unavailable = pi_config.get("show_unavailable_in_selector", True) is not False
        status = self.pi_selector_status()
        if status["available"] or show_unavailable:
            reason = self._pi_selector_reason(status.get("reason", ""))
            for name in self._pi_agent_names():
                options.append(
                    {
                        "id": self._pi_model_id(name),
                        "label": PI_MODEL_LABEL,
                        "runtime": "pi",
                        "available": bool(status["available"]),
                        "reason": "available" if status["available"] else reason,
                        "favorite": self._pi_model_id(name) in self._favorite_models,
                    }
                )
        return options

    def sync_remote_models(self) -> list[str]:
        """Fetch live models from `/v1/models` on configured OpenAI-compatible providers."""
        discovered: list[str] = []
        try:
            from src.core.secret_store import SecretStore
            from src.llm.providers.openai import OpenAICompatibleProvider

            providers = self.config_manager.get("models.providers")
            if not isinstance(providers, dict) or not providers:
                providers = {"cliproxy": self.config_manager.get_provider_config("cliproxy")}
            add_fn = getattr(self.config_manager, "add_provider_model", None)

            for p_name, p_cfg in list(providers.items()):
                if not isinstance(p_cfg, dict):
                    continue
                base_url = str(p_cfg.get("baseUrl") or "").strip()
                raw_key = str(p_cfg.get("apiKey") or "").strip()
                if not base_url:
                    continue
                resolved_key = SecretStore().resolve_config_secret(raw_key, provider_name=p_name) or raw_key
                prov = OpenAICompatibleProvider(
                    api_key=resolved_key,
                    base_url=base_url,
                    provider_name=p_name,
                    timeout=10.0,
                )
                for mid in prov.list_remote_models():
                    full_id = f"{p_name}/{mid}" if "/" not in mid else mid
                    if callable(add_fn):
                        add_fn(full_id)
                    discovered.append(full_id)
        except Exception as exc:
            self.set_status(f"Could not sync remote models: {exc}")
            return []

        if discovered:
            self.set_status(f"✅ Synced {len(discovered)} model(s) from OpenAI API.")
            self.refresh_window()
        else:
            self.set_status("ℹ️ Using configured models in config.yaml.")
        return discovered

    def add_custom_model(self, raw_model_id: str) -> str:
        """Add and select any custom OpenAI-compatible model ID (e.g. `cliproxy/deepseek-r1` or `qwen-max`)."""
        clean = str(raw_model_id or "").strip()
        if not clean:
            return ""
        add_fn = getattr(self.config_manager, "add_provider_model", None)
        if callable(add_fn) and not self._is_pi_model_name(clean):
            clean = add_fn(clean)
        elif "/" not in clean and not self._is_pi_model_name(clean):
            all_m = self.available_models()
            default_prov = all_m[0].split("/")[0] if all_m and "/" in all_m[0] else "cliproxy"
            clean = f"{default_prov}/{clean}"
        self.set_session_model(clean, sync_global=True)
        self.set_status(f"✅ Active model set to {clean}")
        return clean

    def get_active_session(self) -> ScreenshotChatSession:
        return self._ensure_active_session()

    def list_sessions(self) -> list[ScreenshotChatSession]:
        return self.store.list_sessions()

    def list_display_sessions(self) -> list[ScreenshotChatSession]:
        return [
            session
            for session in self.store.list_sessions()
            if session.id == self.active_session_id or not self._is_empty_session(session)
        ]

    def load_session(self, session_id: str):
        if self.store.get_session(session_id) is None:
            return
        self.active_session_id = session_id
        self.refresh_window()

    def _close_pi_session_state(self, session: ScreenshotChatSession):
        close_session = getattr(self.runner, "close_pi_session", None)
        if not callable(close_session):
            return
        responder_names = [PI_MODEL_LABEL, *self._pi_agent_names()]
        selected = self._pi_agent_name_from_model(session.model_name)
        if selected:
            responder_names.append(selected)
        close_session(session.id, responder_names, timeout_ms=3000)

    def _on_store_session_evicted(self, session: ScreenshotChatSession):
        self._close_pi_session_state(session)

    def delete_session(self, session_id: str) -> bool:
        session_id = session_id or ""
        session = self.store.get_session(session_id)
        if session is None:
            return False
        if session.pending_job_ids:
            self.runner.cancel_jobs(list(session.pending_job_ids))
            self._canceled_job_ids.update(session.pending_job_ids)
        deleted = self.store.delete_session(session_id)
        if not deleted:
            return False
        self._close_pi_session_state(session)
        if self.active_session_id == session_id:
            self.active_session_id = ""
            self.pending_attachments = []
            self._ensure_active_session()
        self.refresh_window()
        return True

    def create_session(self):
        active = self._ensure_active_session()
        if self._is_empty_session(active):
            self.pending_attachments = []
            self.refresh_window()
            return
        model_name = active.model_name or self._normal_default_model_name()
        if self._is_pi_model_name(model_name):
            model_name = self._normal_default_model_name()
        created = self.store.create_session(
            model_name=model_name,
            prompt_type="custom",
            mode=active.mode if active.mode in self.ALLOWED_SESSION_MODES else "chat",
            system_prompt="",
            system_prompt_label="Blank",
            title="New Chat",
        )
        self.active_session_id = created.id
        self.pending_attachments = []
        self.refresh_window()

    def set_session_options(self, model_name: str, prompt_type: str):
        selectable, reason = self._pi_model_selectable(model_name or "")
        if not selectable:
            self.set_status(f"{PI_MODEL_LABEL} unavailable: {reason}")
            return
        session = self._ensure_active_session()
        session.model_name = model_name or ""
        session.prompt_type = prompt_type or "prompt_translate"
        self.store.save_session(session)
        self.refresh_window()

    def set_session_model(self, model_name: str, *, sync_global: bool = True):
        clean_model = str(model_name or "").strip()
        selectable, reason = self._pi_model_selectable(clean_model)
        if not selectable:
            self.set_status(f"{PI_MODEL_LABEL} unavailable: {reason}")
            return

        # Automatically register custom OpenAI-compatible models if not already in config.yaml
        if clean_model and not self._is_pi_model_name(clean_model):
            known = self.config_manager.get_all_models()
            if clean_model not in known:
                add_fn = getattr(self.config_manager, "add_provider_model", None)
                if callable(add_fn):
                    clean_model = add_fn(clean_model)

        session = self._ensure_active_session()
        session.model_name = clean_model
        self.store.save_session(session)

        if sync_global and clean_model and not self._is_pi_model_name(clean_model):
            if self.model_state_store is not None:
                try:
                    self.model_state_store.set_model(clean_model, source="screenshot_chat", persist=True)
                except Exception:
                    pass
            else:
                set_many = getattr(self.config_manager, "set_many", None)
                if callable(set_many):
                    try:
                        set_many({
                            "screenshot_chat.default_model": clean_model,
                            "agents.defaults.model.primary": clean_model,
                        })
                    except Exception:
                        pass
        self.refresh_window()

    def set_session_mode(self, mode: str):
        normalized = mode if mode in self.ALLOWED_SESSION_MODES else "chat"
        session = self._ensure_active_session()
        if session.mode == normalized:
            return
        session.mode = normalized
        self.store.save_session(session)
        self.refresh_window()

    def session_mode(self) -> str:
        return self._ensure_active_session().mode or "chat"

    def set_session_system_prompt(self, prompt_type: str, system_prompt: str, label: str):
        session = self._ensure_active_session()
        session.prompt_type = prompt_type or "custom"
        session.system_prompt = system_prompt or ""
        session.system_prompt_label = label or "Blank"
        self.store.save_session(session)
        self.refresh_window()

    def available_models(self) -> list[str]:
        return list(self.config_manager.get_all_models().keys())

    def available_prompts(self) -> list[str]:
        prompts = self._load_prompt_definitions()
        return list(prompts.keys()) or ["prompt_translate"]

    def prompt_definitions(self) -> dict[str, str]:
        return self._load_prompt_definitions()

    def system_prompt_summary(self, session: ScreenshotChatSession | None = None) -> str:
        session = session or self._ensure_active_session()
        effective_prompt = self._resolve_session_system_prompt(session)
        if not effective_prompt:
            return "Blank"
        prompts = self._load_prompt_definitions()
        if session.prompt_type in prompts and prompts[session.prompt_type] == effective_prompt:
            return session.prompt_type
        if session.system_prompt_label and session.system_prompt_label != "Blank":
            return session.system_prompt_label
        return "Custom"

    def clear_pending_attachments(self):
        self.pending_attachments = []
        self.refresh_window()

    def is_pending_attachment_path(self, path: str) -> bool:
        return any(item.path == path for item in self.pending_attachments)

    def _tts_config(self) -> dict:
        config = self._config()
        value = config.get("tts")
        return dict(value) if isinstance(value, dict) else {}

    def get_speech_state(self) -> dict:
        with self._speech_lock:
            return {**self._speech_state, "auto_read": bool(self._speech_auto_read)}

    def set_auto_read(self, enabled: bool) -> bool:
        enabled = bool(enabled)
        with self._speech_lock:
            self._speech_auto_read = enabled
        raw = getattr(self.config_manager, "config", None)
        if isinstance(raw, dict):
            target = raw.get("screenshot_chat") if isinstance(raw.get("screenshot_chat"), dict) else raw
            tts = target.get("tts") if isinstance(target.get("tts"), dict) else {}
            target["tts"] = tts
            tts["auto_read"] = enabled
            save = getattr(self.config_manager, "save_config", None)
            if callable(save):
                save()
        return enabled

    def read_message(self, message_id: str, *, session_id: str = "") -> bool:
        session = self.store.get_session(session_id) if session_id else self.get_active_session()
        if session is None:
            return False
        message = next((item for item in session.messages if item.id == message_id), None)
        if message is None or message.role != "assistant" or message.status != "done":
            return False
        text = sanitize_speech_text(message.text)
        if not text:
            return False
        with self._speech_lock:
            self._speech_generation += 1
            generation = self._speech_generation
            self._speech_state = {"state": "synthesizing", "message_id": message.id, "message": ""}
        threading.Thread(
            target=self._speak_message_worker,
            args=(generation, message.id, text),
            name=f"screenshot-chat-tts-{generation}",
            daemon=True,
        ).start()
        return True

    def stop_speech(self) -> bool:
        with self._speech_lock:
            self._speech_generation += 1
            generation = self._speech_generation
            self._speech_state = {"state": "stopping", "message_id": "", "message": ""}
        threading.Thread(
            target=self._stop_speech_worker,
            args=(generation,),
            name="screenshot-chat-tts-stop",
            daemon=True,
        ).start()
        return True

    def _ensure_speech_runtime(self):
        from src.audio.plugin import LocalAudioPlugin
        from src.audio.providers import KorvaTTSProvider, WindowsWavePlaybackProvider

        with self._speech_lock:
            config = self._tts_config()
            if self._audio_plugin is None:
                provider = KorvaTTSProvider(
                    base_url=str(config.get("base_url") or "https://tts-r28-edge.hcu-lab.me"),
                    api_key=str(config.get("api_key") or ""),
                    timeout_seconds=float(config.get("timeout_seconds") or 60),
                )
                self._audio_plugin = LocalAudioPlugin(tts_provider=provider)
            if self._playback_provider is None:
                self._playback_provider = WindowsWavePlaybackProvider()
            return self._audio_plugin, self._playback_provider, config

    def _speak_message_worker(self, generation: int, message_id: str, text: str):
        output = None
        try:
            audio_plugin, playback, config = self._ensure_speech_runtime()
            playback.stop()
            output = audio_plugin.synthesize_speech(
                text,
                voice=str(config.get("voice") or "bao_kim"),
                language=str(config.get("language") or "vi"),
            )
            with self._speech_lock:
                if generation != self._speech_generation:
                    self._cleanup_temporary_audio(output)
                    return
                playback.play(output)
                self._speech_state = {"state": "playing", "message_id": message_id, "message": ""}
        except Exception:
            with self._speech_lock:
                if generation != self._speech_generation:
                    self._cleanup_temporary_audio(output)
                    return
                self._cleanup_temporary_audio(output)
                self._speech_state = {"state": "error", "message_id": message_id, "message": "TTS unavailable."}

    def _stop_speech_worker(self, generation: int):
        try:
            playback = self._playback_provider
            if playback is not None:
                playback.stop()
        finally:
            with self._speech_lock:
                if generation == self._speech_generation:
                    self._speech_state = {"state": "idle", "message_id": "", "message": ""}

    @staticmethod
    def _cleanup_temporary_audio(output):
        if output is None or not bool(getattr(output, "metadata", {}).get("temporary")):
            return
        try:
            output.path().unlink(missing_ok=True)
        except OSError:
            pass

    def get_status(self) -> str:
        return self._status

    def set_status(self, status: str):
        self._status = status
        if self.window is not None and self._window_exists():
            self.window.update_status(status)

    def get_runtime_status(self) -> dict:
        return dict(self._runtime_status)

    def _set_runtime_status(
        self,
        state: str,
        *,
        scope: str = "ui",
        label: str = "",
        message: str = "",
        detail: str = "",
        retryable: bool = False,
    ):
        labels = {
            "connecting": "Connecting...",
            "ready": "Ready",
            "streaming": "Generating...",
            "waiting_approval": "Approval required",
            "canceling": "Stopping...",
            "error": "Action needed",
            "offline": "Offline",
        }
        self._runtime_status = {
            "state": state,
            "scope": scope,
            "label": label or labels.get(state, state.replace("_", " ").title()),
            "message": message,
            "detail": detail,
            "retryable": bool(retryable),
        }

    @staticmethod
    def _user_facing_error(raw_error: str) -> str:
        lowered = raw_error.lower()
        if "pi protocol error" in lowered or "bridge request hello timed out" in lowered:
            return "Pi did not start correctly."
        return raw_error

    def send_message(self, text: str, model_name: str, prompt_type: str) -> bool:
        clean_text = (text or "").strip()
        session = self._ensure_active_session()
        attachments = list(self.pending_attachments)
        if not clean_text and not attachments:
            return False

        if clean_text.startswith("/") and not attachments and session.mode != "note":
            cmd_token = clean_text.split()[0].lower()
            known_cmds = {item["command"] for item in SLASH_COMMANDS}
            if cmd_token in known_cmds:
                self.execute_slash_command(clean_text)
                return True

        if session.mode == "note":
            note_message = ScreenshotChatMessage(
                role="user",
                author="note",
                text=clean_text,
                attachments=attachments,
                status="note",
            )
            if self.store.append_message(session.id, note_message) is None:
                return False
            self.pending_attachments = []
            self.refresh_window()
            return True

        if model_name:
            selectable, reason = self._pi_model_selectable(model_name)
            if not selectable:
                self.set_status(f"{PI_MODEL_LABEL} unavailable: {reason}")
                self.refresh_window()
                return False
            self.set_session_model(model_name)
        session = self._ensure_active_session()
        status = "queued" if session.pending_job_ids else "done"
        user_message = ScreenshotChatMessage(
            role="user",
            author="you",
            text=clean_text,
            attachments=attachments,
            status=status,
        )
        saved = self.store.append_message(session.id, user_message)
        if saved is None:
            return False
        session = saved
        self.pending_attachments = []

        self.refresh_window()
        if status == "queued":
            return True
        self._start_message_jobs(session.id, user_message.id)
        return True

    def _start_message_jobs(self, session_id: str, message_id: str):
        session = self.store.get_session(session_id)
        if session is None:
            return
        user_message = next((item for item in session.messages if item.id == message_id), None)
        if user_message is None or user_message.status in {"canceled", "note", "deleted"}:
            return
        self.store.update_message_status(session.id, user_message.id, "done")
        session = self.store.get_session(session.id) or session
        user_message = next((item for item in session.messages if item.id == message_id), user_message)

        config = self._config()
        pi_config = dict(config.get("pi") if isinstance(config.get("pi"), dict) else {})
        if self._pi_tool_mode:
            pi_config["tool_policy"] = self._pi_tool_mode
        agent_configs = self._agent_configs_with_pi(config)
        parsed = parse_mentions(
            user_message.text,
            agent_configs,
            selected_model=session.model_name,
            selected_prompt_type=session.prompt_type,
        )
        selected_pi_agent = self._pi_agent_name_from_model(session.model_name)
        effective_llm_model = (
            self._normal_default_model_name()
            if selected_pi_agent
            else (session.model_name or self._normal_default_model_name())
        )
        if selected_pi_agent:
            parsed.responders = [
                self._selected_pi_responder(responder, selected_pi_agent, effective_llm_model)
                if responder.kind == "main"
                else responder
                for responder in parsed.responders
            ]
        elif self.is_pi_primary_mode():
            # In v2.4, Pi Sidecar is the primary mode for all main chat turns!
            for responder in parsed.responders:
                if responder.kind == "main":
                    responder.runtime = "pi"
                    responder.model_name = effective_llm_model
                    responder.metadata.update({
                        "provider": "pi",
                        "agent_name": DEFAULT_PI_AGENT_NAME,
                        "model": effective_llm_model,
                        "model_name": effective_llm_model,
                        "thinkingLevel": self._pi_thinking_level,
                        "toolPolicy": self._pi_tool_mode,
                        "cwd": self.project_root,
                        "fallback_model_name": effective_llm_model,
                    })

        main_system_prompt = self._resolve_session_system_prompt(session)
        for responder in parsed.responders:
            if responder.kind == "main":
                responder.system_prompt = main_system_prompt
            if (responder.runtime or "").lower() == "pi" or responder.metadata.get("provider") == "pi":
                responder.metadata.setdefault("pi_config", dict(pi_config))
                responder.metadata.setdefault("model", effective_llm_model)
                responder.metadata.setdefault("model_name", effective_llm_model)
                responder.metadata.setdefault("thinkingLevel", self._pi_thinking_level)
                responder.metadata.setdefault("cwd", self.project_root)
                if responder.kind == "main":
                    responder.metadata.setdefault("fallback_model_name", effective_llm_model)
        if parsed.unknown_mentions:
            self.set_status("Unknown agent: " + ", ".join(parsed.unknown_mentions))

        jobs = self.runner.prepare_response_jobs(session, user_message, parsed.responders)
        if jobs:
            scope = "pi" if any((responder.runtime or "").lower() == "pi" for responder in parsed.responders) else "llm"
            self._set_runtime_status("streaming", scope=scope)
        for job in jobs:
            placeholder = ScreenshotChatMessage(
                role="assistant",
                author=job.responder_name,
                text="Thinking",
                job_id=job.id,
                status="thinking",
            )
            self._job_to_placeholder[job.id] = placeholder.id
            self._job_to_user_message[job.id] = user_message.id
            self.store.append_message(session.id, placeholder)
            self.store.set_pending_job(session.id, job.id, True)

        self.refresh_window()
        self.runner.start_prepared_jobs(
            session,
            user_message,
            parsed.prompt_text,
            parsed.responders,
            jobs,
            temperature=float(0.7 if config.get("temperature") is None else config.get("temperature")),
        )

    def _start_next_queued_message(self, session_id: str):
        session = self.store.get_session(session_id)
        if session is None or session.pending_job_ids:
            return
        for message in session.messages:
            if message.role == "user" and message.status == "queued":
                self._start_message_jobs(session.id, message.id)
                return

    def cancel_queued_message(self, message_id: str):
        session = self._ensure_active_session()
        self.store.update_message_status(session.id, message_id, "canceled")
        self.refresh_window()

    def delete_message(self, message_id: str) -> bool:
        session = self._ensure_active_session()
        message = next((item for item in session.messages if item.id == message_id), None)
        if message is None:
            return False
        job_ids = set()
        if message.job_id:
            job_ids.add(message.job_id)
        for job_id, user_message_id in list(self._job_to_user_message.items()):
            if user_message_id == message_id:
                job_ids.add(job_id)
        if job_ids:
            self.runner.cancel_jobs(list(job_ids))
        for job_id in job_ids:
            self._canceled_job_ids.add(job_id)
            self.store.set_pending_job(session.id, job_id, False)
            placeholder_id = self._job_to_placeholder.pop(job_id, "")
            self._job_to_user_message.pop(job_id, None)
            if placeholder_id and placeholder_id != message_id:
                self.store.delete_message(session.id, placeholder_id)
        deleted = self.store.delete_message(session.id, message_id) is not None
        self.refresh_window()
        if deleted:
            self._start_next_queued_message(session.id)
        return deleted

    def drain_runner_events(self, limit: int = 256, time_budget_ms: float = 12.0) -> bool:
        raw_events = []
        deadline = time.perf_counter() + max(1.0, float(time_budget_ms)) / 1000.0
        while len(raw_events) < max(1, int(limit)) and time.perf_counter() < deadline:
            try:
                raw_events.append(self.event_queue.get_nowait())
            except queue.Empty:
                break

        events = self._coalesce_runner_events(raw_events)
        for event in events:
            self._handle_runner_event(event)

        visible_events = [event for event in events if event.type != "job_started"]
        if visible_events:
            self.refresh_window(messages_only=all(event.type == "message_delta" for event in visible_events))
        return not self.event_queue.empty()

    @staticmethod
    def _coalesce_runner_events(events: list[RunnerEvent]) -> list[RunnerEvent]:
        coalesced = []
        pending_delta = None
        for event in events:
            if event.type == "message_delta":
                if (
                    pending_delta is not None
                    and pending_delta.session_id == event.session_id
                    and pending_delta.job_id == event.job_id
                ):
                    pending_delta.text += event.text
                    continue
                if pending_delta is not None:
                    coalesced.append(pending_delta)
                pending_delta = RunnerEvent(
                    type=event.type,
                    session_id=event.session_id,
                    job_id=event.job_id,
                    responder_name=event.responder_name,
                    message_id=event.message_id,
                    text=event.text,
                    error=event.error,
                    metadata=event.metadata,
                    partial=event.partial,
                )
                continue
            if pending_delta is not None:
                coalesced.append(pending_delta)
                pending_delta = None
            coalesced.append(event)
        if pending_delta is not None:
            coalesced.append(pending_delta)
        return coalesced

    def _handle_runner_event(self, event: RunnerEvent):
        if event.type == "job_started":
            self._set_runtime_status("streaming", scope="pi" if event.responder_name in ("pi", PI_MODEL_LABEL) or self.is_pi_primary_mode() else "llm")
            return
        if event.job_id in self._canceled_job_ids and event.type != "job_finished":
            return
        if event.type == "message_delta":
            self.store.append_message_delta(
                event.session_id,
                event.job_id,
                event.text,
                save=False,
                return_session=False,
            )
        elif event.type in {"runtime_notice", "tool_event", "approval_request", "approval_resolved"}:
            timeline_event = self._timeline_event_from_runner(event)
            self.store.append_message_event(event.session_id, event.job_id, timeline_event, save=True)
            if event.type == "approval_request":
                self._set_runtime_status(
                    "waiting_approval",
                    scope="pi",
                    message="Pi needs approval to continue.",
                )
                self._schedule_pi_approval_dialog(event)
            elif event.type == "approval_resolved":
                self._set_runtime_status("streaming", scope="pi")
        elif event.type in {"message_final", "job_error"}:
            raw_text = strip_repeated_author_prefixes(
                event.text or event.error or "[Error: No response received]",
                extra_authors=(event.responder_name,),
            )
            text = self._user_facing_error(raw_text) if event.type == "job_error" else raw_text
            existing = self.store.get_session(event.session_id)
            previous = None
            if existing is not None:
                previous = next((item for item in existing.messages if item.job_id == event.job_id), None)
            replacement = ScreenshotChatMessage(
                id=self._job_to_placeholder.get(event.job_id, ""),
                role="assistant",
                author=event.responder_name,
                text=text,
                job_id=event.job_id,
                status="error" if event.type == "job_error" else "done",
                events=list(previous.events) if previous is not None else [],
                metadata=dict(previous.metadata) if previous is not None else {},
            )
            if event.type == "job_error" and raw_text != text:
                replacement.metadata["detail"] = raw_text
            self.store.replace_message_by_job(event.session_id, event.job_id, replacement)
            if event.type == "message_final" and self._speech_auto_read and sanitize_speech_text(event.text):
                self.read_message(replacement.id, session_id=event.session_id)
            if event.type == "job_error":
                self.set_status("Action needed")
                self._set_runtime_status(
                    "error",
                    scope="pi" if event.responder_name == "pi" else "llm",
                    message=text,
                    detail=raw_text,
                    retryable=True,
                )
        elif event.type == "job_finished":
            self.store.set_pending_job(event.session_id, event.job_id, False)
            self._job_to_placeholder.pop(event.job_id, None)
            self._job_to_user_message.pop(event.job_id, None)
            self._canceled_job_ids.discard(event.job_id)
            self._start_next_queued_message(event.session_id)
            refreshed = self.store.get_session(event.session_id)
            if self._runtime_status.get("state") != "error":
                if refreshed is not None and refreshed.pending_job_ids:
                    self._set_runtime_status("streaming", scope=self._runtime_status.get("scope", "pi"))
                else:
                    self._set_runtime_status("ready", scope=self._runtime_status.get("scope", "pi"))

    def _timeline_event_from_runner(self, event: RunnerEvent) -> dict:
        metadata = event.metadata if isinstance(event.metadata, dict) else {}
        kind = str(metadata.get("kind") or event.type)
        title = str(metadata.get("title") or self._timeline_title(kind))
        text = str(metadata.get("text") or event.text or event.error or metadata.get("reason") or "")
        return {
            "id": f"evt_{int(time.time() * 1000)}_{event.job_id[-4:]}",
            "kind": kind,
            "title": title,
            "text": text,
            "status": str(metadata.get("status") or "done"),
            "job_id": event.job_id,
            "approval_id": str(metadata.get("approval_id") or ""),
            "tool_name": str(metadata.get("tool_name") or ""),
            "created_at": time.time(),
            "data": dict(metadata.get("data") or metadata),
        }

    def _timeline_title(self, kind: str) -> str:
        return {
            "runtime_notice": "Runtime",
            "assistant_delta": "Response",
            "tool_started": "Tool started",
            "tool_output": "Tool output",
            "tool_error": "Tool error",
            "approval_request": "Approval requested",
            "approval_resolved": "Approval resolved",
            "runtime_error": "Runtime error",
        }.get(kind, "Pi event")

    def _schedule_pi_approval_dialog(self, event: RunnerEvent):
        if event.job_id in self._canceled_job_ids:
            self.reject_pi_action(event.job_id, str((event.metadata or {}).get("approval_id") or ""), "session_deleted_or_job_canceled")
            return
        if getattr(self.root, "after", None) is None:
            self.reject_pi_action(event.job_id, str((event.metadata or {}).get("approval_id") or ""), "no_tk_root")
            return
        self.root.after(0, lambda ev=event: self._show_pi_approval_dialog(ev))

    def _show_pi_approval_dialog(self, event: RunnerEvent):
        metadata = event.metadata if isinstance(event.metadata, dict) else {}
        approval_id = str(metadata.get("approval_id") or "")
        if event.job_id in self._canceled_job_ids or not approval_id:
            self.reject_pi_action(event.job_id, approval_id, "session_deleted_or_job_canceled")
            return
        payload = metadata.get("payload") if isinstance(metadata.get("payload"), dict) else {}
        tool_name = str(metadata.get("tool_name") or "").strip()
        ui_type = str(metadata.get("ui_type") or "").strip()
        title = str(payload.get("title") or (f"Pi approval: {tool_name}" if tool_name else "") or ui_type or "Pi approval required")
        detail = str(metadata.get("summary") or payload.get("message") or payload.get("description") or "Allow Pi to continue?")
        expires_at = str(metadata.get("expires_at") or "").strip()
        if expires_at:
            detail = f"{detail}\n\nExpires: {expires_at}"
        if len(detail) > 1200:
            detail = detail[:1200] + "..."
        approved = messagebox.askyesno(title, detail, parent=self.root if self._is_tk_parent(self.root) else None)
        if approved:
            self.approve_pi_action(event.job_id, approval_id)
        else:
            self.reject_pi_action(event.job_id, approval_id, "denied_by_user")

    def approve_pi_action(self, job_id: str, approval_id: str, payload: dict | None = None) -> bool:
        if not hasattr(self.runner, "resolve_approval"):
            return False
        return self.runner.resolve_approval(job_id, approval_id, True, "approved_by_user", payload or {})

    def reject_pi_action(self, job_id: str, approval_id: str, reason: str = "denied_by_user") -> bool:
        if not hasattr(self.runner, "resolve_approval"):
            return False
        return self.runner.resolve_approval(job_id, approval_id, False, reason, {})

    def stop_active_session_jobs(self):
        session = self._ensure_active_session()
        job_ids = list(session.pending_job_ids)
        if not job_ids:
            self._start_next_queued_message(session.id)
            self.refresh_window()
            return
        self._set_runtime_status("canceling", scope=self._runtime_status.get("scope", "ui"))
        self.runner.cancel_jobs(job_ids)
        for job_id in job_ids:
            self._canceled_job_ids.add(job_id)
            user_message_id = self._job_to_user_message.get(job_id, "")
            if user_message_id:
                self.store.update_message_status(session.id, user_message_id, "canceled")
            replacement = ScreenshotChatMessage(
                id=self._job_to_placeholder.get(job_id, ""),
                role="assistant",
                author="system",
                text="[Canceled]",
                job_id=job_id,
                status="canceled",
            )
            self.store.replace_message_by_job(session.id, job_id, replacement)
            self._job_to_placeholder.pop(job_id, None)
            self._job_to_user_message.pop(job_id, None)
        self._start_next_queued_message(session.id)
        self.refresh_window()

    def import_held_snaps(self) -> int:
        paths = [path for path in self.held_images_getter() if path and os.path.exists(path)]
        if not paths:
            self.set_status("No held snaps available.")
            return 0
        attachments = [
            ScreenshotChatAttachment(
                path=path,
                source="held",
                label=os.path.basename(path),
            )
            for path in paths
        ]
        self.pending_attachments.extend(attachments)
        self.held_images_consumer(paths)
        self.set_status("")
        self.refresh_window()
        return len(paths)

    def list_pinned_snaps(self) -> list[dict]:
        try:
            pins = self.snap_history_getter().get_pins()
            return [
                item
                for item in pins
                if item.get("image_path") or (item.get("ans") or "").strip()
            ]
        except Exception:
            return []

    def import_pinned_snap(self, entry: dict) -> bool:
        path = entry.get("image_path", "")
        if self.is_pinned_snap_used(entry):
            self.set_status("Pinned snap is already in this chat.")
            return False
        text = (entry.get("ans") or entry.get("display_title") or "Pinned snap").strip()
        attachments = []
        if path and os.path.exists(path):
            attachments.append(
                ScreenshotChatAttachment(
                    path=path,
                    source="pinned",
                    label=entry.get("display_title") or os.path.basename(path),
                )
            )
        elif not text:
            self.set_status("Pinned snap has no usable content.")
            return False
        message = ScreenshotChatMessage(
            role="assistant",
            author="main",
            text=text,
            attachments=attachments,
        )
        session = self._ensure_active_session()
        saved = self.store.append_message(session.id, message)
        if saved is not None:
            self.active_session_id = saved.id
        self.set_status("Inserted pinned snap.")
        self.refresh_window()
        return True

    def is_pinned_snap_used(self, entry: dict) -> bool:
        entry_id = str(entry.get("id") or "")
        path = entry.get("image_path", "")
        session = self._ensure_active_session()
        for message in session.messages:
            if entry_id and message.text == (entry.get("ans") or entry.get("display_title") or "").strip():
                has_pinned_attachment = any(
                    attachment.source == "pinned" for attachment in message.attachments
                )
                if has_pinned_attachment or not path:
                    return True
            for attachment in message.attachments:
                if attachment.source == "pinned" and attachment.path == path:
                    return True
        return False

    def is_system_prompt_selected(self, prompt_type: str, system_prompt: str, label: str) -> bool:
        session = self._ensure_active_session()
        effective_prompt = self._resolve_session_system_prompt(session)
        if label == "Blank":
            return effective_prompt == ""
        if prompt_type and session.prompt_type == prompt_type:
            expected_prompt = system_prompt or self._load_prompt_definitions().get(prompt_type, "")
            return effective_prompt == expected_prompt
        return (
            session.prompt_type == (prompt_type or "custom")
            and effective_prompt == (system_prompt or "")
            and session.system_prompt_label == (label or "Blank")
        )

    def import_file(self):
        dialog_parent = self.root if self._is_tk_parent(self.root) else None
        path = filedialog.askopenfilename(
            parent=dialog_parent,
            title="Choose attachment",
            filetypes=[
                ("Supported files", "*.png;*.jpg;*.jpeg;*.bmp;*.gif;*.webp;*.pdf;*.txt;*.md;*.py;*.json;*.csv"),
                ("Images", "*.png;*.jpg;*.jpeg;*.bmp;*.gif;*.webp"),
                ("All files", "*.*"),
            ],
        )
        if path:
            self.import_paths([path], source="file")

    def _is_tk_parent(self, candidate) -> bool:
        return hasattr(candidate, "tk") and hasattr(candidate, "winfo_exists")

    def import_paths(self, paths: list[str], source: str = "file") -> int:
        added = 0
        for path in paths:
            if not path or not os.path.isfile(path):
                continue
            if self._append_pending_attachment(
                path=path,
                source=source,
                label=os.path.basename(path),
                kind="image" if self._is_image(path) else "file",
                refresh=False,
            ):
                added += 1
        if added:
            self.set_status("")
            self.refresh_window()
        return added

    def import_clipboard_image(self, show_empty_status: bool = False):
        try:
            clipboard_data = ImageGrab.grabclipboard()
            if clipboard_data is None:
                if show_empty_status:
                    self.set_status("Clipboard does not contain an image.")
                return False
            if hasattr(clipboard_data, "save"):
                path = self._store_clipboard_bitmap(clipboard_data)
                self._append_pending_attachment(path=path, source="clipboard", label=os.path.basename(path))
                return True
            if isinstance(clipboard_data, list):
                paths = [item for item in clipboard_data if isinstance(item, str)]
                added = self.import_paths(paths, source="clipboard")
                if added:
                    return True
            if show_empty_status:
                self.set_status("Clipboard does not contain an image.")
            return False
        except Exception as exc:
            self.set_status(f"Clipboard import failed: {exc}")
            return False

    def clear_pending_attachment(self, attachment_id: str):
        self.pending_attachments = [
            item for item in self.pending_attachments if item.id != attachment_id
        ]
        self.refresh_window()

    def _append_pending_attachment(
        self,
        path: str,
        source: str,
        label: str = "",
        kind: str = "image",
        refresh: bool = True,
    ):
        if not path:
            return False
        if self.is_pending_attachment_path(path):
            self.set_status(f"Already added: {os.path.basename(path)}")
            return False
        self.pending_attachments.append(
            ScreenshotChatAttachment(
                path=path,
                source=source,
                label=label or os.path.basename(path),
                kind=kind,
            )
        )
        self.set_status("")
        if refresh:
            self.refresh_window()
        return True

    def _store_clipboard_bitmap(self, image) -> str:
        save_dir = os.path.join(self.project_root, "data", "chat_images")
        os.makedirs(save_dir, exist_ok=True)
        filename = f"screenshot_chat_clipboard_{time.strftime('%Y%m%d_%H%M%S')}_{int(time.time() * 1000) % 1000:03d}.png"
        path = os.path.join(save_dir, filename)
        image.save(path)
        return path

    def _is_image(self, path: str) -> bool:
        return path.lower().endswith((".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"))

    def _selected_pi_responder(
        self,
        responder: ScreenshotChatResponder,
        agent_name: str,
        fallback_model: str,
    ) -> ScreenshotChatResponder:
        metadata = dict(responder.metadata or {})
        metadata.update(
            {
                "provider": "pi",
                "agent_name": agent_name,
                "pi_model_id": self._pi_model_id(agent_name),
                "model": fallback_model,
                "model_name": fallback_model,
                "thinkingLevel": self._pi_thinking_level,
                "toolPolicy": self._pi_tool_mode,
                "cwd": self.project_root,
                "fallback_model_name": fallback_model,
            }
        )
        return ScreenshotChatResponder(
            name=PI_MODEL_LABEL,
            kind=responder.kind,
            model_name=fallback_model,
            prompt_type=responder.prompt_type,
            system_prompt=responder.system_prompt,
            runtime="pi",
            metadata=metadata,
        )

    def _agent_configs_with_pi(self, config: dict) -> dict:
        raw_agents = config.get("agents") if isinstance(config.get("agents"), dict) else {}
        agent_configs = {str(name): dict(value) for name, value in raw_agents.items() if isinstance(value, dict)}
        status = self.pi_selector_status()
        if status.get("available"):
            pi_config = config.get("pi") if isinstance(config.get("pi"), dict) else {}
            names = pi_config.get("agent_names", ["pi"])
            if not isinstance(names, list):
                names = ["pi"]
            for raw_name in names:
                name = str(raw_name or "").strip().lower()
                if not name or name in agent_configs:
                    continue
                agent_configs[name] = {
                    "enabled": True,
                    "runtime": "pi",
                    "model": "",
                    "system_prompt": "",
                    "metadata": {"synthetic_pi_agent": True},
                }
        return agent_configs

    def _resolve_session_system_prompt(self, session: ScreenshotChatSession) -> str:
        if session.system_prompt:
            return session.system_prompt
        if session.prompt_type == "custom":
            return ""
        prompts = self._load_prompt_definitions()
        return prompts.get(session.prompt_type, "")

    def _is_empty_session(self, session: ScreenshotChatSession) -> bool:
        return (
            not session.messages
            and not session.attachments
            and not session.pending_job_ids
            and not session.is_pinned
        )

    def _on_global_model_changed(self, snapshot) -> None:
        """Keep active session model synchronized when provider/model changes in Tray or Popup."""
        if getattr(snapshot, "source", "") == "screenshot_chat":
            return
        new_model = getattr(snapshot, "full_model", "") or ""
        if not new_model:
            return
        session = self._ensure_active_session()
        if not self._is_pi_model_name(session.model_name):
            session.model_name = new_model
            self.store.save_session(session)
            self.refresh_window()

    def _load_favorite_models(self) -> list[str]:
        cfg = self._config()
        favs = cfg.get("favorite_models")
        if isinstance(favs, list):
            return [str(x).strip() for x in favs if str(x).strip()]
        return []

    def favorite_models(self) -> list[str]:
        return list(self._favorite_models)

    def toggle_favorite_model(self, model_name: str) -> list[str]:
        clean = str(model_name or "").strip()
        if not clean:
            return list(self._favorite_models)
        if clean in self._favorite_models:
            self._favorite_models.remove(clean)
        else:
            self._favorite_models.append(clean)
        raw = getattr(self.config_manager, "config", None)
        if isinstance(raw, dict):
            sc = raw.setdefault("screenshot_chat", {})
            if isinstance(sc, dict):
                sc["favorite_models"] = list(self._favorite_models)
                save = getattr(self.config_manager, "save_config", None)
                if callable(save):
                    save()
        self.refresh_window()
        return list(self._favorite_models)

    def slash_commands(self) -> list[dict]:
        return list(SLASH_COMMANDS)

    def execute_slash_command(self, raw_command: str) -> dict:
        text = (raw_command or "").strip()
        if not text.startswith("/"):
            return {"ok": False, "message": "Not a slash command"}
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        session = self._ensure_active_session()

        if cmd == "/new":
            self.create_session()
            self.set_status("Ready in clean session.")
            return {"ok": True, "command": cmd, "session_id": self.active_session_id}

        if cmd == "/pi":
            pi_id = self._pi_model_id(DEFAULT_PI_AGENT_NAME)
            if not self.is_pi_primary_mode():
                self.set_session_model(pi_id, sync_global=False)
            if arg:
                self.send_message(arg, session.model_name or pi_id, session.prompt_type)
            else:
                self._append_system_notice(
                    session.id,
                    f"🤖 **Pi Sidecar Active** — Model: `{session.model_name}` · Thinking: `{self._pi_thinking_level}` · Tools: `{self._pi_tool_mode}`",
                )
            return {"ok": True, "command": cmd, "model": session.model_name or pi_id}

        if cmd == "/model":
            if not arg:
                opts = ", ".join(f"`{m['id']}`" for m in self.available_model_options() if not self._is_pi_model_name(m["id"]))
                self._append_system_notice(
                    session.id,
                    f"**Active Pi Sidecar Model:** `{session.model_name}`\n**Available Models:** {opts}",
                )
                return {"ok": True, "command": cmd, "model": session.model_name}
            target = arg
            all_ids = [m["id"] for m in self.available_model_options()]
            if target not in all_ids:
                matches = [mid for mid in all_ids if target.lower() in mid.lower()]
                if matches:
                    target = matches[0]
                else:
                    target = self.add_custom_model(target)
            self.set_session_model(target)
            self._append_system_notice(session.id, f"✅ Switched Pi Sidecar model to `{target}`.")
            return {"ok": True, "command": cmd, "model": target}

        if cmd == "/thinking":
            level = self.set_thinking_level(arg or "medium")
            self._append_system_notice(session.id, f"🧠 Pi Sidecar thinking level set to `{level}`.")
            return {"ok": True, "command": cmd, "thinking_level": level}

        if cmd == "/tools":
            mode = self.set_tool_mode(arg or "read_only")
            self._append_system_notice(session.id, f"🛠️ Pi Sidecar tool permission mode set to `{mode}`.")
            return {"ok": True, "command": cmd, "tool_mode": mode}

        if cmd == "/skills":
            try:
                from src.core.skills_mcp import SkillRegistry
                skills = SkillRegistry().list_skills()
                bullet_list = "\n".join(f"- **`{s.id}`**: {s.description}" for s in skills)
            except Exception:
                bullet_list = "- `ocr_extract`\n- `translate_multilingual`\n- `explain_error`\n- `extract_markdown_table`"
            self._append_system_notice(session.id, f"### 🧩 Registered Pi & Multimodal Skills\n{bullet_list}")
            return {"ok": True, "command": cmd}

        if cmd == "/harness":
            ok, msg = init_pi_project_harness(self.project_root)
            self._append_system_notice(session.id, msg)
            return {"ok": ok, "command": cmd, "message": msg}

        if cmd == "/compact":
            compacted = self.compact_session(arg)
            return {"ok": True, "command": cmd, "session_id": compacted.id}

        if cmd == "/fork":
            forked = self.fork_session(arg)
            return {"ok": True, "command": cmd, "session_id": forked.id}

        if cmd == "/export":
            res = self.export_session_transcript(session.id)
            self._append_system_notice(
                session.id,
                f"📄 Exported full unabbreviated HTML transcript to `{res.get('path')}`.",
            )
            return res

        if cmd in {"/steer", "/followup"}:
            mode = "steer" if cmd == "/steer" else "followup"
            ok = self.steer_active_run(arg, mode=mode)
            return {"ok": ok, "command": cmd, "mode": mode}

        self._append_system_notice(session.id, f"Unknown command `{cmd}`. Type `/` to browse commands.")
        return {"ok": False, "command": cmd}

    def _append_system_notice(self, session_id: str, markdown_text: str):
        notice = ScreenshotChatMessage(
            role="assistant",
            author="pi-harness",
            text=markdown_text,
            status="done",
            metadata={"kind": "harness_notice"},
        )
        self.store.append_message(session_id, notice)
        self.refresh_window()

    def fork_session(self, new_title: str = "") -> ScreenshotChatSession:
        source = self._ensure_active_session()
        title = (new_title or f"{source.title or 'Chat'} (Fork)").strip()
        forked = self.store.create_session(
            model_name=source.model_name,
            prompt_type=source.prompt_type,
            mode=source.mode,
            system_prompt=source.system_prompt,
            system_prompt_label=source.system_prompt_label,
            title=title,
        )
        for msg in source.messages:
            cloned_msg = ScreenshotChatMessage(
                role=msg.role,
                author=msg.author,
                text=msg.text,
                attachments=list(msg.attachments),
                status=msg.status if msg.status not in {"thinking", "queued"} else "done",
                events=list(msg.events),
                metadata=dict(msg.metadata),
            )
            self.store.append_message(forked.id, cloned_msg)
        self.active_session_id = forked.id
        self.set_status(f"Forked session: {title}")
        self.refresh_window()
        return self.store.get_session(forked.id) or forked

    def compact_session(self, focus: str = "") -> ScreenshotChatSession:
        session = self._ensure_active_session()
        if len(session.messages) <= 2:
            self._append_system_notice(session.id, "ℹ️ Session is already compact.")
            return session
        kept_recent = session.messages[-2:]
        older = session.messages[:-2]
        digest_lines = [f"### 🗜️ Compacted {len(older)} Earlier Messages"]
        if focus:
            digest_lines.append(f"*Focus:* `{focus}`")
        for item in older[-8:]:
            snippet = (item.text or "").replace("\n", " ").strip()[:160]
            if snippet:
                digest_lines.append(f"- **{item.author}**: {snippet}")
        summary_msg = ScreenshotChatMessage(
            role="assistant",
            author="pi-compactor",
            text="\n".join(digest_lines),
            status="done",
            metadata={"compacted_count": len(older)},
        )
        session.messages = [summary_msg, *kept_recent]
        self.store.save_session(session)
        self.set_status(f"Compacted {len(older)} messages.")
        self.refresh_window()
        return session

    def export_session_transcript(self, session_id: str = "") -> dict:
        session = self.store.get_session(session_id) if session_id else self._ensure_active_session()
        if session is None:
            session = self._ensure_active_session()
        html_doc = export_session_html(session)
        export_dir = os.path.join(self.project_root, "data", "exports")
        os.makedirs(export_dir, exist_ok=True)
        out_path = os.path.join(export_dir, f"transcript_{session.id}.html")
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(html_doc)
        return {"ok": True, "session_id": session.id, "path": out_path, "html": html_doc}

    def steer_active_run(self, instruction: str, mode: str = "steer") -> bool:
        clean = (instruction or "").strip()
        if not clean:
            return False
        session = self._ensure_active_session()
        prefix = "🧭 [Live Steer]" if mode == "steer" else "⏭️ [Follow-up]"
        return self.send_message(f"{prefix} {clean}", session.model_name, session.prompt_type)

    def escalate_from_quick_popup(self, submission) -> bool:
        """Escalate a QuickPromptPopup submission (images, files, text, prompt) into a multi-turn Pi Chat session."""
        self.open_or_focus()
        session = self._ensure_active_session()
        if not self._is_empty_session(session):
            self.create_session()
            session = self._ensure_active_session()

        if not self.is_pi_primary_mode():
            pi_status = self.pi_selector_status()
            if pi_status.get("available"):
                self.set_session_model(self._pi_model_id(DEFAULT_PI_AGENT_NAME), sync_global=False)

        file_paths = [att.path for att in getattr(submission, "attachments", []) if getattr(att, "path", "")]
        if file_paths:
            self.import_paths(file_paths, source="popup")

        for img in getattr(submission, "images", []):
            if hasattr(img, "save"):
                try:
                    saved_path = self._store_clipboard_bitmap(img)
                    self._append_pending_attachment(path=saved_path, source="popup", label=os.path.basename(saved_path))
                except Exception:
                    pass

        combined_prompt = (
            submission.build_combined_prompt()
            if hasattr(submission, "build_combined_prompt")
            else str(getattr(submission, "prompt_text", "") or "")
        )
        if combined_prompt:
            return self.send_message(combined_prompt, session.model_name, session.prompt_type)
        self.refresh_window()
        return True

    def create_new_session(self):
        return self.create_session()

    def compact_active_session(self, focus: str = ""):
        return self.compact_session(focus)

    def export_session_html(self, session_id: str = ""):
        return self.export_session_transcript(session_id)

    def set_session_thinking_level(self, level: str = "medium"):
        return self.execute_slash_command(f"/thinking {level}")
