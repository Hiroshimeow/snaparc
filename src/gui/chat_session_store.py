import json
import os
import threading
import time
import uuid

from src.gui.chat_text_sanitizer import sanitize_assistant_text
from src.utils.atomic_json import write_json_atomic


class ChatSessionStore:
    MAX_SESSIONS = 100

    def __init__(self, save_path):
        self.save_path = save_path
        self._lock = threading.Lock()
        self._sessions = []
        self._load()

    def _load(self):
        try:
            if not os.path.exists(self.save_path):
                self._sessions = []
                return
            with open(self.save_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            sessions = payload.get("sessions", []) if isinstance(payload, dict) else []
            self._sessions = [self._normalize_session(item) for item in sessions if isinstance(item, dict)]
            self._sessions.sort(key=lambda item: item.get("updated_at", 0), reverse=True)
        except Exception as exc:
            print(f"ChatSessionStore load error: {exc}")
            self._sessions = []

    def _save(self):
        try:
            payload = {"sessions": self._sessions[: self.MAX_SESSIONS]}
            write_json_atomic(self.save_path, payload)
        except Exception as exc:
            print(f"ChatSessionStore save error: {exc}")

    def _now(self):
        return time.time()

    def _new_id(self, prefix):
        return f"{prefix}_{uuid.uuid4().hex[:12]}"

    def _normalize_message(self, message):
        role = message.get("role", "assistant")
        text = message.get("text", "") or ""
        if role == "assistant":
            text = sanitize_assistant_text(text, message.get("author", ""))
        return {
            "id": message.get("id") or self._new_id("msg"),
            "role": role,
            "text": text,
            "image_path": message.get("image_path", "") or "",
            "created_at": float(message.get("created_at", self._now())),
        }

    def _normalize_session(self, session):
        created_at = float(session.get("created_at", self._now()))
        updated_at = float(session.get("updated_at", created_at))
        messages = session.get("messages", [])
        normalized = {
            "id": session.get("id") or self._new_id("chat"),
            "title": (session.get("title") or "").strip(),
            "source_id": session.get("source_id", "") or "",
            "is_pinned": bool(session.get("is_pinned", False)),
            "created_at": created_at,
            "updated_at": updated_at,
            "model_name": session.get("model_name", "") or "",
            "prompt_name": session.get("prompt_name", "") or "",
            "messages": [
                self._normalize_message(message)
                for message in messages
                if isinstance(message, dict)
            ],
        }
        self._touch_session_title(normalized)
        return normalized

    def _touch_session_title(self, session):
        if session.get("title"):
            return
        session["title"] = self.default_title(session)

    def default_title(self, session):
        for message in session.get("messages", []):
            text = (message.get("text") or "").strip()
            if text:
                return text.splitlines()[0][:60]
        image_message = next(
            (message for message in session.get("messages", []) if message.get("image_path")),
            None,
        )
        if image_message:
            return os.path.basename(image_message["image_path"])
        return "New Chat"

    def list_sessions(self):
        with self._lock:
            sessions = [self._clone_session(item) for item in self._sessions]
        return self._sort_clones(sessions)

    def _sort_clones(self, sessions):
        return sorted(
            sessions,
            key=lambda item: (0 if item.get("is_pinned") else 1, -item.get("updated_at", 0)),
        )

    def _clone_session(self, session):
        return {
            "id": session["id"],
            "title": session["title"],
            "source_id": session.get("source_id", ""),
            "is_pinned": bool(session.get("is_pinned", False)),
            "created_at": session["created_at"],
            "updated_at": session["updated_at"],
            "model_name": session["model_name"],
            "prompt_name": session["prompt_name"],
            "messages": [dict(message) for message in session["messages"]],
        }

    def get_session(self, session_id):
        with self._lock:
            for session in self._sessions:
                if session["id"] == session_id:
                    return self._clone_session(session)
        return None

    def create_session(self, model_name="", prompt_name="", title="", messages=None, source_id=""):
        with self._lock:
            now = self._now()
            session = self._normalize_session(
                {
                    "id": self._new_id("chat"),
                    "title": title,
                    "source_id": source_id,
                    "created_at": now,
                    "updated_at": now,
                    "model_name": model_name,
                    "prompt_name": prompt_name,
                    "messages": messages or [],
                }
            )
            self._touch_session_title(session)
            self._sessions.insert(0, session)
            self._sessions = self._sessions[: self.MAX_SESSIONS]
            self._save()
            return self._clone_session(session)

    def save_session(self, session):
        normalized = self._normalize_session(session)
        normalized["updated_at"] = self._now()
        self._touch_session_title(normalized)
        with self._lock:
            for index, existing in enumerate(self._sessions):
                if existing["id"] == normalized["id"]:
                    self._sessions[index] = normalized
                    break
            else:
                self._sessions.insert(0, normalized)
            self._sessions.sort(key=lambda item: item.get("updated_at", 0), reverse=True)
            self._sessions = self._sessions[: self.MAX_SESSIONS]
            self._save()
        return self._clone_session(normalized)

    def rename_session(self, session_id, title):
        clean_title = (title or "").strip()
        with self._lock:
            for session in self._sessions:
                if session["id"] == session_id:
                    session["title"] = clean_title or self.default_title(session)
                    session["updated_at"] = self._now()
                    self._sessions.sort(key=lambda item: item.get("updated_at", 0), reverse=True)
                    self._save()
                    return self._clone_session(session)
        return None

    def delete_session(self, session_id):
        with self._lock:
            original_len = len(self._sessions)
            self._sessions = [item for item in self._sessions if item["id"] != session_id]
            changed = len(self._sessions) != original_len
            if changed:
                self._save()
        return changed

    def duplicate_session(self, session_id):
        with self._lock:
            source = next((item for item in self._sessions if item["id"] == session_id), None)
            if source is None:
                return None
            clone = self._clone_session(source)
            clone["id"] = self._new_id("chat")
            clone["source_id"] = ""
            clone["is_pinned"] = False
            clone["title"] = f"{clone['title']} Copy".strip()
            clone["created_at"] = self._now()
            clone["updated_at"] = clone["created_at"]
            clone["messages"] = [self._normalize_message(message) for message in clone["messages"]]
            self._sessions.insert(0, clone)
            self._sessions = self._sessions[: self.MAX_SESSIONS]
            self._save()
            return self._clone_session(clone)

    def find_by_source_id(self, source_id):
        if not source_id:
            return None
        with self._lock:
            for session in self._sessions:
                if session.get("source_id") == source_id:
                    return self._clone_session(session)
        return None

    def set_pinned(self, session_id, is_pinned):
        with self._lock:
            for session in self._sessions:
                if session["id"] == session_id:
                    session["is_pinned"] = bool(is_pinned)
                    session["updated_at"] = self._now()
                    self._save()
                    return self._clone_session(session)
        return None
