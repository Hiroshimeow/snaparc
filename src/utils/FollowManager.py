import os
from pathlib import Path
import threading
from src.utils.yaml_io import load_yaml_or_json

try:
    import tomllib
except ImportError:  # pragma: no cover
    import tomli as tomllib


class FollowManager:
    DEFAULT_LIMIT = 10
    MAX_EXCHANGES = 100

    def __init__(self, settings_path="config.yaml"):
        resolved = Path(settings_path)
        if not resolved.exists() and resolved.name == "prompts.toml":
            yaml_cand = resolved.with_name("config.yaml")
            if yaml_cand.exists():
                resolved = yaml_cand
        self.settings_path = os.path.abspath(str(resolved))
        self._enabled = False
        self._limit = self.DEFAULT_LIMIT
        self._settings_mtime = None
        self._exchanges = []
        self._lock = threading.Lock()
        self.reload_settings(force=True)

    def reload_settings(self, force=False):
        try:
            current_mtime = os.path.getmtime(self.settings_path)
        except OSError:
            current_mtime = None

        with self._lock:
            if (
                not force
                and self._settings_mtime is not None
                and current_mtime == self._settings_mtime
            ):
                return self._limit

            limit = self.DEFAULT_LIMIT
            if current_mtime is not None and os.path.isfile(self.settings_path):
                try:
                    ext = Path(self.settings_path).suffix.lower()
                    if ext in (".yaml", ".yml", ".json"):
                        with open(self.settings_path, "r", encoding="utf-8") as handle:
                            payload = load_yaml_or_json(handle.read())
                    else:
                        with open(self.settings_path, "rb") as handle:
                            payload = tomllib.load(handle)
                    settings = payload.get("settings", {}) if isinstance(payload, dict) else {}
                    raw_limit = settings.get("follow_history_limit", self.DEFAULT_LIMIT)
                    parsed_limit = int(raw_limit)
                    if parsed_limit < 0:
                        parsed_limit = 0
                    limit = parsed_limit
                except Exception:
                    limit = self.DEFAULT_LIMIT

            self._limit = limit
            self._settings_mtime = current_mtime
            return self._limit

    def is_enabled(self):
        with self._lock:
            return self._enabled

    def get_limit(self):
        self.reload_settings()
        with self._lock:
            return self._limit

    def get_active_count(self):
        self.reload_settings()
        with self._lock:
            return self._limit if self._enabled else 0

    def get_state(self):
        self.reload_settings()
        with self._lock:
            return {
                "enabled": self._enabled,
                "history_count": self._limit if self._enabled else 0,
                "limit": self._limit,
            }

    def set_enabled(self, enabled):
        self.reload_settings()
        with self._lock:
            self._enabled = bool(enabled)
            return {
                "enabled": self._enabled,
                "history_count": self._limit if self._enabled else 0,
                "limit": self._limit,
            }

    def toggle(self):
        self.reload_settings()
        with self._lock:
            self._enabled = not self._enabled
            return {
                "enabled": self._enabled,
                "history_count": self._limit if self._enabled else 0,
                "limit": self._limit,
            }

    def add_exchange(self, user_text, assistant_text):
        clean_user = (user_text or "").strip()
        clean_assistant = (assistant_text or "").strip()
        if not clean_user and not clean_assistant:
            return

        with self._lock:
            self._exchanges.append(
                {
                    "user": clean_user,
                    "assistant": clean_assistant,
                }
            )
            if len(self._exchanges) > self.MAX_EXCHANGES:
                self._exchanges = self._exchanges[-self.MAX_EXCHANGES :]

    def build_history(self):
        self.reload_settings()
        with self._lock:
            if not self._enabled or self._limit <= 0:
                return []
            exchanges = list(self._exchanges[-self._limit :])

        payload = []
        for exchange in exchanges:
            user_text = exchange.get("user", "")
            assistant_text = exchange.get("assistant", "")
            if user_text:
                payload.append({"role": "user", "parts": [user_text]})
            if assistant_text:
                payload.append({"role": "model", "parts": [assistant_text]})
        return payload
