from __future__ import annotations

import hashlib
from typing import Any

from src.gui.screenshot_chat.pi_harness import SLASH_COMMANDS, extract_message_structured


class ScreenshotChatStateAdapter:
    def __init__(self, controller, media_registry=None):
        self.controller = controller
        self.media_registry = media_registry

    def get_state(self) -> dict[str, Any]:
        session = self.controller.get_active_session()
        sessions = self.controller.list_display_sessions()
        pinned = self.controller.list_pinned_snaps()
        model_options = self._model_options()
        fav_fn = getattr(self.controller, "favorite_models", None)
        favorites = fav_fn() if callable(fav_fn) else []
        return {
            "active_session_id": session.id,
            "version": self.state_version(),
            "status": self.controller.get_status(),
            "runtime_status": self._runtime_status(),
            "speech": self._speech_state(),
            "models": self.controller.available_models(),
            "model_options": model_options,
            "favorite_models": favorites,
            "slash_commands": SLASH_COMMANDS,
            "model_label": self._model_label(session.model_name, model_options),
            "prompts": self.controller.prompt_definitions(),
            "session": self._session_payload(session, model_options),
            "sessions": [self._session_summary(item, session.id) for item in sessions],
            "messages": [self._message_payload(item) for item in session.messages],
            "pending_attachments": [
                self._attachment_payload(item)
                for item in getattr(self.controller, "pending_attachments", [])
            ],
            "pinned_snaps": [self._pinned_payload(item) for item in pinned],
        }

    def drain_events(self, cursor: str = "", limit: int = 6) -> dict[str, Any]:
        before = self.state_version()
        try:
            self.controller.drain_runner_events(limit=limit)
        except TypeError:
            self.controller.drain_runner_events()
        after = self.state_version()
        if before == after and (not cursor or cursor == after):
            return {"unchanged": True, "version": after}
        return self.get_state()

    def state_version(self) -> str:
        session = self.controller.get_active_session()
        pending = ",".join(item.id for item in getattr(self.controller, "pending_attachments", []))
        message_fingerprint = "|".join(
            f"{message.id}:{message.status}:{self._message_signature(message)}"
            for message in session.messages
        )
        model_options_fingerprint = self._model_options_fingerprint(self._model_options())
        runtime = self._runtime_status()
        runtime_fingerprint = ":".join(
            str(runtime.get(key, ""))
            for key in ("state", "scope", "label", "message", "detail", "retryable")
        )
        speech = self._speech_state()
        speech_fingerprint = ":".join(
            str(speech.get(key, ""))
            for key in ("state", "message_id", "message", "auto_read")
        )
        raw = (
            f"{session.id}:{session.updated_at}:{session.pending_job_ids}:{session.mode}:{session.model_name}:{pending}:"
            f"{self.controller.get_status()}:{runtime_fingerprint}:{speech_fingerprint}:{model_options_fingerprint}:{message_fingerprint}"
        )
        return hashlib.blake2s(raw.encode("utf-8"), digest_size=8).hexdigest()

    def _runtime_status(self) -> dict[str, Any]:
        provider = getattr(self.controller, "get_runtime_status", None)
        if callable(provider):
            value = provider()
            if isinstance(value, dict):
                return {
                    "state": str(value.get("state") or "ready"),
                    "scope": str(value.get("scope") or "ui"),
                    "label": str(value.get("label") or "Ready"),
                    "message": str(value.get("message") or ""),
                    "detail": str(value.get("detail") or ""),
                    "retryable": bool(value.get("retryable", False)),
                }
        return {
            "state": "ready",
            "scope": "ui",
            "label": "Ready",
            "message": str(self.controller.get_status() or ""),
            "detail": "",
            "retryable": False,
        }

    def _speech_state(self) -> dict[str, Any]:
        provider = getattr(self.controller, "get_speech_state", None)
        if callable(provider):
            value = provider()
            if isinstance(value, dict):
                return {
                    "state": str(value.get("state") or "idle"),
                    "message_id": str(value.get("message_id") or ""),
                    "message": str(value.get("message") or ""),
                    "auto_read": bool(value.get("auto_read", False)),
                }
        return {"state": "idle", "message_id": "", "message": "", "auto_read": False}

    def _session_summary(self, session, active_session_id: str) -> dict[str, Any]:
        return {
            "id": session.id,
            "title": session.title or "New Chat",
            "updated_at": session.updated_at,
            "pending": bool(session.pending_job_ids),
            "active": session.id == active_session_id,
        }

    def _session_payload(self, session, model_options: list[dict] | None = None) -> dict[str, Any]:
        model_options = model_options if model_options is not None else self._model_options()
        return {
            "id": session.id,
            "title": session.title or "New Chat",
            "model_name": session.model_name,
            "model_label": self._model_label(session.model_name, model_options),
            "prompt_type": session.prompt_type,
            "mode": session.mode or "chat",
            "system_prompt": session.system_prompt,
            "system_prompt_label": self.controller.system_prompt_summary(session),
            "pending_job_ids": list(session.pending_job_ids),
        }

    def _model_options(self) -> list[dict[str, Any]]:
        provider = getattr(self.controller, "available_model_options", None)
        if callable(provider):
            return [self._model_option_payload(option) for option in provider()]
        return [
            self._model_option_payload(
                {
                    "id": model_name,
                    "label": model_name.rsplit("/", 1)[-1] if model_name else "Model",
                    "runtime": "llm",
                    "available": True,
                    "reason": "",
                }
            )
            for model_name in self.controller.available_models()
        ]

    def _model_option_payload(self, option: dict) -> dict[str, Any]:
        model_id = str(option.get("id") or "")
        label = str(option.get("label") or model_id.rsplit("/", 1)[-1] or "Model")
        return {
            "id": model_id,
            "label": label,
            "runtime": str(option.get("runtime") or "llm"),
            "available": bool(option.get("available", True)),
            "reason": str(option.get("reason") or ""),
            "favorite": bool(option.get("favorite", False)),
        }

    def _model_label(self, model_name: str, model_options: list[dict] | None = None) -> str:
        for option in model_options or self._model_options():
            if option.get("id") == model_name:
                return str(option.get("label") or model_name)
        display = getattr(self.controller, "model_display_name", None)
        if callable(display):
            return display(model_name)
        if not model_name:
            return "Model"
        return model_name.rsplit("/", 1)[-1]

    def _model_options_fingerprint(self, model_options: list[dict]) -> str:
        return "|".join(
            f"{option.get('id')}:{option.get('label')}:{option.get('runtime')}:{option.get('available')}:{option.get('reason')}:{option.get('favorite')}"
            for option in model_options
        )

    def _message_payload(self, message) -> dict[str, Any]:
        structured = extract_message_structured(message)
        return {
            "id": message.id,
            "role": message.role,
            "author": message.author,
            "text": message.text,
            "created_at": message.created_at,
            "job_id": message.job_id,
            "status": message.status,
            "signature": self._message_signature(message),
            "attachments": [self._attachment_payload(item) for item in message.attachments],
            "events": [dict(item) for item in getattr(message, "events", []) if isinstance(item, dict)],
            "metadata": dict(getattr(message, "metadata", {}) or {}),
            "structured": structured,
        }

    def _attachment_payload(self, attachment) -> dict[str, Any]:
        return {
            "id": attachment.id,
            "path": attachment.path,
            "kind": attachment.kind,
            "source": attachment.source,
            "label": attachment.label,
            "url": self._file_url(attachment.path),
        }

    def _pinned_payload(self, entry: dict) -> dict[str, Any]:
        path = entry.get("image_path", "")
        return {
            "image_path": path,
            "display_title": entry.get("display_title") or path.rsplit("\\", 1)[-1].rsplit("/", 1)[-1],
            "ans": entry.get("ans", ""),
            "used": self.controller.is_pinned_snap_used(entry),
            "url": self._file_url(path),
        }

    def _file_url(self, path: str) -> str:
        if not path:
            return ""
        if self.media_registry is not None:
            return self.media_registry.url_for(path)
        return "file:///" + path.replace("\\", "/")

    def _message_signature(self, message) -> str:
        raw = "|".join(
            [
                message.id,
                message.role,
                message.author,
                message.status,
                message.text,
                ",".join(f"{item.id}:{item.path}:{item.label}:{item.kind}" for item in message.attachments),
                ",".join(
                    f"{item.get('id', '')}:{item.get('kind', '')}:{item.get('status', '')}"
                    for item in getattr(message, "events", [])
                    if isinstance(item, dict)
                ),
            ]
        )
        return hashlib.blake2s(raw.encode("utf-8"), digest_size=8).hexdigest()


class ScreenshotChatWebBridge(ScreenshotChatStateAdapter):
    def __init__(self, controller, window=None, media_registry=None):
        super().__init__(controller, media_registry=media_registry)
        self.window = window

    def send_message(self, text: str, model_name: str = "", prompt_type: str = "") -> dict[str, Any]:
        return self._with_suspended_refresh(
            lambda: self.controller.send_message(text or "", model_name or "", prompt_type or "")
        )

    def stop_jobs(self) -> dict[str, Any]:
        return self._with_suspended_refresh(self.controller.stop_active_session_jobs)

    def create_session(self) -> dict[str, Any]:
        return self._with_suspended_refresh(self.controller.create_session)

    def load_session(self, session_id: str) -> dict[str, Any]:
        return self._with_suspended_refresh(lambda: self.controller.load_session(session_id or ""))

    def delete_session(self, session_id: str) -> dict[str, Any]:
        return self._with_suspended_refresh(lambda: self.controller.delete_session(session_id or ""))

    def import_held_snaps(self) -> dict[str, Any]:
        return self._with_suspended_refresh(self.controller.import_held_snaps)

    def import_clipboard_image(self) -> dict[str, Any]:
        return self._with_suspended_refresh(lambda: self.controller.import_clipboard_image(show_empty_status=True))

    def import_file(self, paths: list[str] | None = None) -> dict[str, Any]:
        return self._with_suspended_refresh(
            lambda: self.controller.import_paths(paths or [], source="file")
            if paths is not None
            else self.controller.import_file()
        )

    def import_pinned_snap(self, entry: dict) -> dict[str, Any]:
        return self._with_suspended_refresh(
            lambda: self.controller.import_pinned_snap(entry) if isinstance(entry, dict) else None
        )

    def clear_pending_attachment(self, attachment_id: str) -> dict[str, Any]:
        return self._with_suspended_refresh(lambda: self.controller.clear_pending_attachment(attachment_id or ""))

    def set_model(self, model_name: str) -> dict[str, Any]:
        return self._with_suspended_refresh(lambda: self.controller.set_session_model(model_name or ""))

    def toggle_favorite_model(self, model_name: str) -> dict[str, Any]:
        return self._with_suspended_refresh(
            lambda: self.controller.toggle_favorite_model(model_name or "")
            if hasattr(self.controller, "toggle_favorite_model")
            else None
        )

    def execute_slash_command(self, command_text: str) -> dict[str, Any]:
        return self._with_suspended_refresh(
            lambda: self.controller.execute_slash_command(command_text or "")
            if hasattr(self.controller, "execute_slash_command")
            else None
        )

    def fork_session(self, title: str = "") -> dict[str, Any]:
        return self._with_suspended_refresh(
            lambda: self.controller.fork_session(title or "")
            if hasattr(self.controller, "fork_session")
            else None
        )

    def compact_session(self, focus: str = "") -> dict[str, Any]:
        return self._with_suspended_refresh(
            lambda: self.controller.compact_session(focus or "")
            if hasattr(self.controller, "compact_session")
            else None
        )

    def export_session(self) -> dict[str, Any]:
        return self._with_suspended_refresh(
            lambda: self.controller.export_session_transcript()
            if hasattr(self.controller, "export_session_transcript")
            else None
        )

    def steer_run(self, instruction: str, mode: str = "steer") -> dict[str, Any]:
        return self._with_suspended_refresh(
            lambda: self.controller.steer_active_run(instruction or "", mode=mode or "steer")
            if hasattr(self.controller, "steer_active_run")
            else None
        )

    def set_mode(self, mode: str) -> dict[str, Any]:
        return self._with_suspended_refresh(lambda: self.controller.set_session_mode(mode or "chat"))

    def delete_message(self, message_id: str) -> dict[str, Any]:
        return self._with_suspended_refresh(lambda: self.controller.delete_message(message_id or ""))

    def set_system_prompt(self, prompt_type: str, system_prompt: str, label: str) -> dict[str, Any]:
        return self._with_suspended_refresh(
            lambda: self.controller.set_session_system_prompt(prompt_type or "", system_prompt or "", label or "Blank")
        )

    def read_message(self, message_id: str) -> dict[str, Any]:
        return self._with_suspended_refresh(lambda: self.controller.read_message(message_id or ""))

    def stop_speech(self) -> dict[str, Any]:
        return self._with_suspended_refresh(self.controller.stop_speech)

    def set_auto_read(self, enabled: bool) -> dict[str, Any]:
        return self._with_suspended_refresh(lambda: self.controller.set_auto_read(bool(enabled)))

    def hide(self) -> bool:
        if self.window is not None:
            self.window.hide()
        return True

    def _with_suspended_refresh(self, action) -> dict[str, Any]:
        if self.window is not None and hasattr(self.window, "begin_bridge_action"):
            self.window.begin_bridge_action()
        try:
            action()
        finally:
            if self.window is not None and hasattr(self.window, "end_bridge_action"):
                self.window.end_bridge_action()
        return self.get_state()
