"""Thread-safe Application Event Bus (Observer / Pub-Sub pattern).

Decouples Tray, FloatingWindow, QuickPromptPopup, RadialWheel, ChatScreen, and ModelStateStore
so provider/model switches and state updates propagate instantaneously without circular imports.
"""

from __future__ import annotations

from collections import defaultdict
import threading
from typing import Any, Callable


class AppEventBus:
    _instance: "AppEventBus | None" = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._subscribers: dict[str, list[Callable[[dict[str, Any]], None]]] = defaultdict(list)

    @classmethod
    def get_instance(cls) -> "AppEventBus":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def subscribe(self, event_type: str, callback: Callable[[dict[str, Any]], None]) -> Callable[[], None]:
        topic = str(event_type or "").strip()
        with self._lock:
            if callback not in self._subscribers[topic]:
                self._subscribers[topic].append(callback)

        def unsubscribe() -> None:
            with self._lock:
                if callback in self._subscribers.get(topic, []):
                    self._subscribers[topic].remove(callback)

        return unsubscribe

    def publish(self, event_type: str, payload: dict[str, Any] | None = None) -> int:
        topic = str(event_type or "").strip()
        data = dict(payload) if isinstance(payload, dict) else payload
        with self._lock:
            callbacks = list(self._subscribers.get(topic, ()))
        delivered = 0
        for cb in callbacks:
            try:
                cb(data)
                delivered += 1
            except Exception:
                pass
        return delivered

    def clear(self) -> None:
        with self._lock:
            self._subscribers.clear()
