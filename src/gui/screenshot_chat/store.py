from __future__ import annotations

import json
import os
import threading
from typing import Callable, Iterable

from src.gui.chat_text_sanitizer import is_error_only_assistant_text, sanitize_assistant_text
from src.gui.screenshot_chat.models import (
    ScreenshotChatAttachment,
    ScreenshotChatMessage,
    ScreenshotChatSession,
    now_ts,
)


class ScreenshotChatStore:
    MAX_SESSIONS = 100
    MAX_MESSAGES_PER_SESSION = 300

    def __init__(self, save_path: str, on_evict: Callable[[ScreenshotChatSession], None] | None = None):
        self.save_path = save_path
        self._lock = threading.Lock()
        self._sessions: list[ScreenshotChatSession] = []
        self._on_evict = on_evict
        self._load()

    def set_eviction_callback(self, callback: Callable[[ScreenshotChatSession], None] | None):
        self._on_evict = callback

    def _load(self):
        try:
            if not os.path.exists(self.save_path):
                self._sessions = []
                return
            with open(self.save_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            raw_sessions = payload.get("sessions", []) if isinstance(payload, dict) else []
            self._sessions = [
                self._normalize_session(ScreenshotChatSession.from_dict(item))
                for item in raw_sessions
                if isinstance(item, dict)
            ]
            self._sort_and_cap()
        except Exception as exc:
            print(f"ScreenshotChatStore load error: {exc}")
            self._sessions = []

    def _save_unlocked(self):
        try:
            os.makedirs(os.path.dirname(self.save_path), exist_ok=True)
            payload = {
                "version": 1,
                "sessions": [item.to_dict() for item in self._sessions[: self.MAX_SESSIONS]],
            }
            temp_path = f"{self.save_path}.tmp"
            with open(temp_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
            os.replace(temp_path, self.save_path)
        except Exception as exc:
            print(f"ScreenshotChatStore save error: {exc}")

    def _sort_and_cap(self):
        self._sessions.sort(key=lambda item: (0 if item.is_pinned else 1, -item.updated_at))
        evicted = self._sessions[self.MAX_SESSIONS :]
        self._sessions = self._sessions[: self.MAX_SESSIONS]
        if self._on_evict is not None:
            for session in evicted:
                try:
                    self._on_evict(self._clone(session))
                except Exception:
                    pass

    def _normalize_session(self, session: ScreenshotChatSession) -> ScreenshotChatSession:
        session.mode = session.mode if session.mode in {"chat", "note"} else "chat"
        session.messages = [
            self._normalize_message(message)
            for message in session.messages[-self.MAX_MESSAGES_PER_SESSION :]
        ]
        session.pending_job_ids = list(dict.fromkeys(session.pending_job_ids))
        clean_title = (session.title or "").strip()
        session.title = self.default_title(session) if not clean_title or clean_title == "New Chat" else clean_title
        return session

    def _normalize_message(self, message: ScreenshotChatMessage) -> ScreenshotChatMessage:
        normalized = ScreenshotChatMessage.from_dict(message.to_dict())
        if normalized.role == "assistant":
            original_text = normalized.text or ""
            sanitized = sanitize_assistant_text(original_text, normalized.author)
            if sanitized != original_text:
                normalized.text = sanitized
                normalized.metadata.setdefault("sanitized_at_load", True)
            if is_error_only_assistant_text(normalized.text):
                normalized.status = "error"
                normalized.metadata["error_only"] = True
        return normalized

    def _clone(self, session: ScreenshotChatSession) -> ScreenshotChatSession:
        return ScreenshotChatSession.from_dict(session.to_dict())

    def default_title(self, session: ScreenshotChatSession) -> str:
        for message in session.messages:
            text = (message.text or "").strip()
            if text:
                return text.splitlines()[0][:60]
        for attachment in session.attachments:
            if attachment.path:
                return os.path.basename(attachment.path)
        for message in session.messages:
            for attachment in message.attachments:
                if attachment.path:
                    return os.path.basename(attachment.path)
        return "New Chat"

    def list_sessions(self) -> list[ScreenshotChatSession]:
        with self._lock:
            return [self._clone(item) for item in self._sessions]

    def get_session(self, session_id: str) -> ScreenshotChatSession | None:
        with self._lock:
            for session in self._sessions:
                if session.id == session_id:
                    return self._clone(session)
        return None

    def create_session(
        self,
        model_name: str = "",
        prompt_type: str = "prompt_translate",
        mode: str = "chat",
        system_prompt: str = "",
        system_prompt_label: str = "Blank",
        title: str = "New Chat",
        attachments: Iterable[ScreenshotChatAttachment] | None = None,
    ) -> ScreenshotChatSession:
        with self._lock:
            ts = now_ts()
            session = ScreenshotChatSession(
                title=title,
                created_at=ts,
                updated_at=ts,
                model_name=model_name or "",
                prompt_type=prompt_type or "prompt_translate",
                mode=mode if mode in {"chat", "note"} else "chat",
                system_prompt=system_prompt or "",
                system_prompt_label=system_prompt_label or "Blank",
                attachments=list(attachments or []),
            )
            self._sessions.insert(0, self._normalize_session(session))
            self._sort_and_cap()
            self._save_unlocked()
            return self._clone(session)

    def save_session(self, session: ScreenshotChatSession) -> ScreenshotChatSession:
        normalized = self._normalize_session(ScreenshotChatSession.from_dict(session.to_dict()))
        normalized.updated_at = now_ts()
        with self._lock:
            for index, existing in enumerate(self._sessions):
                if existing.id == normalized.id:
                    self._sessions[index] = normalized
                    break
            else:
                self._sessions.insert(0, normalized)
            self._sort_and_cap()
            self._save_unlocked()
            return self._clone(normalized)

    def rename_session(self, session_id: str, title: str) -> ScreenshotChatSession | None:
        clean_title = (title or "").strip()
        with self._lock:
            for session in self._sessions:
                if session.id == session_id:
                    session.title = clean_title or self.default_title(session)
                    session.updated_at = now_ts()
                    self._sort_and_cap()
                    self._save_unlocked()
                    return self._clone(session)
        return None

    def delete_session(self, session_id: str) -> bool:
        with self._lock:
            original_len = len(self._sessions)
            self._sessions = [item for item in self._sessions if item.id != session_id]
            changed = len(self._sessions) != original_len
            if changed:
                self._save_unlocked()
            return changed

    def set_pinned(self, session_id: str, is_pinned: bool) -> ScreenshotChatSession | None:
        with self._lock:
            for session in self._sessions:
                if session.id == session_id:
                    session.is_pinned = bool(is_pinned)
                    session.updated_at = now_ts()
                    self._sort_and_cap()
                    self._save_unlocked()
                    return self._clone(session)
        return None

    def append_message(
        self,
        session_id: str,
        message: ScreenshotChatMessage,
    ) -> ScreenshotChatSession | None:
        with self._lock:
            for session in self._sessions:
                if session.id == session_id:
                    session.messages.append(self._normalize_message(message))
                    session.messages = session.messages[-self.MAX_MESSAGES_PER_SESSION :]
                    if not session.title or session.title == "New Chat":
                        session.title = self.default_title(session)
                    session.updated_at = now_ts()
                    self._sort_and_cap()
                    self._save_unlocked()
                    return self._clone(session)
        return None

    def replace_message_by_job(
        self,
        session_id: str,
        job_id: str,
        replacement: ScreenshotChatMessage,
    ) -> ScreenshotChatSession | None:
        with self._lock:
            for session in self._sessions:
                if session.id != session_id:
                    continue
                for index, message in enumerate(session.messages):
                    if message.job_id == job_id:
                        session.messages[index] = self._normalize_message(replacement)
                        session.updated_at = now_ts()
                        if job_id in session.pending_job_ids:
                            session.pending_job_ids.remove(job_id)
                        self._sort_and_cap()
                        self._save_unlocked()
                        return self._clone(session)
        return None

    def append_message_delta(
        self,
        session_id: str,
        job_id: str,
        text_delta: str,
        save: bool = True,
        return_session: bool = True,
    ) -> ScreenshotChatSession | None:
        if not text_delta:
            return self.get_session(session_id)
        with self._lock:
            for session in self._sessions:
                if session.id != session_id:
                    continue
                for message in session.messages:
                    if message.job_id == job_id:
                        if message.text == "Thinking":
                            message.text = ""
                        message.text += text_delta
                        message.status = "thinking"
                        session.updated_at = now_ts()
                        if save:
                            self._sort_and_cap()
                            self._save_unlocked()
                        return self._clone(session) if return_session else None
        return None

    def append_message_event(
        self,
        session_id: str,
        job_id: str,
        event_dict: dict,
        save: bool = True,
    ) -> ScreenshotChatSession | None:
        if not isinstance(event_dict, dict):
            return self.get_session(session_id)
        with self._lock:
            for session in self._sessions:
                if session.id != session_id:
                    continue
                for message in session.messages:
                    if message.job_id == job_id:
                        message.events.append(dict(event_dict))
                        session.updated_at = now_ts()
                        if save:
                            self._sort_and_cap()
                            self._save_unlocked()
                        return self._clone(session)
        return None

    def update_message_metadata(
        self,
        session_id: str,
        job_id: str,
        metadata_patch: dict,
        save: bool = True,
    ) -> ScreenshotChatSession | None:
        if not isinstance(metadata_patch, dict):
            return self.get_session(session_id)
        with self._lock:
            for session in self._sessions:
                if session.id != session_id:
                    continue
                for message in session.messages:
                    if message.job_id == job_id:
                        message.metadata.update(metadata_patch)
                        session.updated_at = now_ts()
                        if save:
                            self._sort_and_cap()
                            self._save_unlocked()
                        return self._clone(session)
        return None

    def update_message_status(
        self,
        session_id: str,
        message_id: str,
        status: str,
    ) -> ScreenshotChatSession | None:
        with self._lock:
            for session in self._sessions:
                if session.id != session_id:
                    continue
                for message in session.messages:
                    if message.id == message_id:
                        message.status = status or "done"
                        session.updated_at = now_ts()
                        self._sort_and_cap()
                        self._save_unlocked()
                        return self._clone(session)
        return None

    def set_pending_job(self, session_id: str, job_id: str, is_pending: bool) -> ScreenshotChatSession | None:
        with self._lock:
            for session in self._sessions:
                if session.id == session_id:
                    if is_pending and job_id not in session.pending_job_ids:
                        session.pending_job_ids.append(job_id)
                    if not is_pending and job_id in session.pending_job_ids:
                        session.pending_job_ids.remove(job_id)
                    session.updated_at = now_ts()
                    self._save_unlocked()
                    return self._clone(session)
        return None

    def delete_message(self, session_id: str, message_id: str) -> ScreenshotChatSession | None:
        with self._lock:
            for session in self._sessions:
                if session.id != session_id:
                    continue
                original_len = len(session.messages)
                session.messages = [item for item in session.messages if item.id != message_id]
                if len(session.messages) == original_len:
                    return None
                if not session.title or session.title == "New Chat":
                    session.title = self.default_title(session)
                session.updated_at = now_ts()
                self._sort_and_cap()
                self._save_unlocked()
                return self._clone(session)
        return None
