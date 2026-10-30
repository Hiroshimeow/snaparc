# snap_history.py
# Plugin: Global persistent snap history for FloatingWindow back/next navigation.
# Stores history as JSON in data/snap_history.json.

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from typing import TYPE_CHECKING

from src.utils.atomic_json import write_json_atomic

if TYPE_CHECKING:
    from src.utils.DataPacket import DataPacket

# Resolve project root (3 levels up from src/plugins/snap_history.py)
_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_PLUGIN_DIR))
_DEFAULT_SAVE_PATH = os.path.join(_PROJECT_ROOT, "data", "snap_history.json")


class SnapHistoryManager:
    """
    Global singleton that persists snap history to a JSON file.
    All FloatingWindow instances share the same history via get_instance().
    """

    _instance = None
    _lock = threading.Lock()
    MAX_HISTORY = 200

    @classmethod
    def get_instance(cls, save_path: str | None = None):
        """Return the singleton instance, creating it on first call."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(save_path or _DEFAULT_SAVE_PATH)
        return cls._instance

    def __init__(self, save_path: str = _DEFAULT_SAVE_PATH):
        self._save_path = save_path
        self._history: list[dict] = []
        self._pinned: list[dict] = []
        self._cursor: int = -1
        self._save_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._save_timer: threading.Timer | None = None
        self._save_delay_seconds = 0.2
        self._load()

    # --- Persistence ---

    def _load(self):
        """Load history from JSON file on disk."""
        try:
            if os.path.exists(self._save_path):
                with open(self._save_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    self._history = data[-self.MAX_HISTORY:]
                    self._cursor = len(self._history) - 1
                    print(f"SnapHistory: Loaded {len(self._history)} entries from {self._save_path}")
                elif isinstance(data, dict):
                    history_data = data.get("history", [])
                    pinned_data = data.get("pinned", [])
                    if isinstance(history_data, list):
                        self._history = history_data[-self.MAX_HISTORY:]
                        self._cursor = len(self._history) - 1
                    if isinstance(pinned_data, list):
                        self._pinned = pinned_data
                    print(
                        f"SnapHistory: Loaded {len(self._history)} history and {len(self._pinned)} pins from {self._save_path}"
                    )
                else:
                    print("SnapHistory: Invalid JSON format, starting fresh.")
        except Exception as e:
            print(f"SnapHistory: Error loading history: {e}")

    def _save_async(self):
        """Save history to JSON on a debounced background timer."""
        with self._save_lock:
            if self._save_timer is not None:
                self._save_timer.cancel()
            def _do_save():
                try:
                    self._save_latest()
                finally:
                    with self._save_lock:
                        if self._save_timer is timer:
                            self._save_timer = None

            timer = threading.Timer(self._save_delay_seconds, _do_save)
            self._save_timer = timer
            timer.daemon = True
            timer.start()

    def flush(self):
        """Force any pending save to complete now."""
        with self._save_lock:
            pending_timer = self._save_timer
            self._save_timer = None
        if pending_timer is not None:
            try:
                pending_timer.cancel()
            except Exception as e:
                print(f"SnapHistory: Error cancelling pending save: {e}")
        self._save_latest()

    def _save_latest(self):
        # A cancelled timer may already be running. Serialize writers and take
        # the current snapshot only after acquiring the disk-write lock.
        with self._write_lock:
            with self._save_lock:
                data_snapshot = {
                    "history": [dict(item) for item in self._history],
                    "pinned": [dict(item) for item in self._pinned],
                }
            try:
                write_json_atomic(self._save_path, data_snapshot)
            except Exception as e:
                print(f"SnapHistory: Error saving: {e}")

    # --- Core API ---

    def _entry_id_from_parts(self, timestamp: str, image_path: str, ans: str) -> str:
        stable_source = f"{timestamp}|{image_path}|{ans[:500]}"
        digest = hashlib.sha1(stable_source.encode("utf-8", errors="ignore")).hexdigest()
        return f"entry_{digest[:16]}"

    def _default_pin_title(self, entry: dict) -> str:
        ans = str(entry.get("ans", "") or "")
        for raw_line in ans.splitlines():
            line = raw_line.strip()
            if line:
                return line[:80]
        timestamp = entry.get("timestamp", "") or "Untitled"
        prompt_name = entry.get("prompt_name", "") or "Pinned"
        return f"{prompt_name} {timestamp}".strip()

    def _serialize_packet(self, packet: "DataPacket") -> dict:
        ans = getattr(packet, "ans", "") or ""
        image_path = getattr(packet, "image_path", "") or ""
        timestamp = getattr(packet, "original_timestamp_str", "") or ""
        entry = {
            "id": self._entry_id_from_parts(timestamp, image_path, ans),
            "ans": ans,
            "image_path": image_path,
            "timestamp": timestamp,
            "model_name": getattr(packet, "model_name", "Unknown"),
            "prompt_name": getattr(packet, "prompt_name", "Unknown"),
            "api_key_index": getattr(packet, "api_key_index", "?"),
            "api_key_mask": getattr(packet, "api_key_mask", "????"),
        }
        return entry

    def entry_from_packet(self, packet: "DataPacket") -> dict:
        return self._serialize_packet(packet)

    def push(self, packet: "DataPacket"):
        """Serialize packet data fields, append to history, and auto-save."""
        entry = self._serialize_packet(packet)
        with self._save_lock:
            self._history.append(entry)
            if len(self._history) > self.MAX_HISTORY:
                self._history = self._history[-self.MAX_HISTORY:]
            self._cursor = len(self._history) - 1
        self._save_async()

    def pin(self, packet: "DataPacket") -> dict:
        """Pin the current packet if not already pinned and return its stored entry."""
        entry = self._serialize_packet(packet)
        with self._save_lock:
            existing = next((item for item in self._pinned if item.get("id") == entry["id"]), None)
            if existing is not None:
                return dict(existing)
            entry["pin_title"] = None
            entry["pinned_at"] = time.time()
            self._pinned.insert(0, entry)
        self._save_async()
        return dict(entry)

    def unpin(self, entry_id: str) -> bool:
        with self._save_lock:
            original_len = len(self._pinned)
            self._pinned = [item for item in self._pinned if item.get("id") != entry_id]
            changed = len(self._pinned) != original_len
        if changed:
            self._save_async()
        return changed

    def rename_pin(self, entry_id: str, title: str | None) -> bool:
        clean_title = title.strip() if isinstance(title, str) else None
        if clean_title == "":
            clean_title = None
        changed = False
        with self._save_lock:
            for item in self._pinned:
                if item.get("id") == entry_id:
                    item["pin_title"] = clean_title
                    changed = True
                    break
        if changed:
            self._save_async()
        return changed

    def get_pins(self) -> list[dict]:
        with self._save_lock:
            pins = [dict(item) for item in self._pinned]
        for item in pins:
            item["display_title"] = self._default_pin_title(item) if not item.get("pin_title") else item["pin_title"]
        return pins

    def is_pinned(self, packet: "DataPacket") -> bool:
        entry_id = self._serialize_packet(packet)["id"]
        with self._save_lock:
            return any(item.get("id") == entry_id for item in self._pinned)

    def go_back(self) -> dict | None:
        """Move cursor back and return the entry dict, or None if at start."""
        if self._cursor > 0:
            self._cursor -= 1
            return self._history[self._cursor]
        return None

    def go_next(self) -> dict | None:
        """Move cursor forward and return the entry dict, or None if at end."""
        if self._cursor < len(self._history) - 1:
            self._cursor += 1
            return self._history[self._cursor]
        return None

    @property
    def can_go_back(self) -> bool:
        return self._cursor > 0

    @property
    def can_go_next(self) -> bool:
        return self._cursor < len(self._history) - 1

    @property
    def position_text(self) -> str:
        """Returns e.g. '2/5' for display in the UI counter label."""
        if not self._history:
            return ""
        return f"{self._cursor + 1}/{len(self._history)}"
