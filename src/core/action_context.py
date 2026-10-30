"""Action-Time Context Engine.

Captures all mutable user/desktop state (active window HWND & title, monitor DPI,
selected region, held screenshots, clipboard snapshot, active session ID, and
selected model/prompt) at the exact moment of user action so concurrent or delayed
async jobs can never mix sessions, models, prompts, or window contexts.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass, field
import sys
import time
from typing import Any
import uuid


@dataclass(frozen=True)
class ForegroundWindowInfo:
    hwnd: int = 0
    title: str = ""
    process_id: int = 0
    rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    ime_active: bool = False

    @classmethod
    def capture_current(cls) -> "ForegroundWindowInfo":
        if sys.platform != "win32" or not hasattr(ctypes, "windll"):
            return cls(hwnd=0, title="Desktop", process_id=0, rect=(0, 0, 1920, 1080), ime_active=False)
        try:
            import ctypes.wintypes

            user32 = ctypes.windll.user32
            hwnd = int(user32.GetForegroundWindow() or 0)
            if not hwnd:
                return cls()

            length = int(user32.GetWindowTextLengthW(ctypes.c_void_p(hwnd)) or 0)
            buf = ctypes.create_unicode_buffer(max(1, length + 1))
            user32.GetWindowTextW(ctypes.c_void_p(hwnd), buf, length + 1)
            title = str(buf.value or "")

            pid = ctypes.wintypes.DWORD(0)
            user32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(pid))

            class RECT(ctypes.Structure):
                _fields_ = [
                    ("left", ctypes.c_long),
                    ("top", ctypes.c_long),
                    ("right", ctypes.c_long),
                    ("bottom", ctypes.c_long),
                ]

            rc = RECT()
            user32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(rc))

            ime_active = False
            imm32 = getattr(ctypes.windll, "imm32", None)
            if imm32 is not None:
                try:
                    himc = imm32.ImmGetContext(ctypes.c_void_p(hwnd))
                    if himc:
                        ime_active = bool(imm32.ImmGetOpenStatus(ctypes.c_void_p(himc)))
                        imm32.ImmReleaseContext(ctypes.c_void_p(hwnd), ctypes.c_void_p(himc))
                except Exception:
                    pass

            return cls(
                hwnd=hwnd,
                title=title,
                process_id=int(pid.value),
                rect=(int(rc.left), int(rc.top), int(rc.right), int(rc.bottom)),
                ime_active=ime_active,
            )
        except Exception:
            return cls()


@dataclass(frozen=True)
class ActionTimeContext:
    """Immutable ledger of user context frozen at the instant an action is triggered."""
    context_id: str
    run_id: str
    captured_at_monotonic: float
    captured_at_iso: str
    trigger_source: str  # "mouse_snap" | "hotkey" | "voice_ptt" | "chat_ui" | "tray"
    session_id: str
    request_model_name: str
    request_provider_name: str
    request_prompt_type: str
    request_prompt_name: str
    request_system_prompt: str
    modifiers: dict[str, bool] = field(default_factory=dict)
    virtual_bbox: tuple[int, int, int, int] | None = None
    image_paths: tuple[str, ...] = field(default_factory=tuple)
    clipboard_kind: str = ""  # "" | "text" | "image"
    clipboard_text: str = ""
    foreground_window: ForegroundWindowInfo = field(default_factory=ForegroundWindowInfo)
    follow_history_snapshot: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_audit_dict(self) -> dict[str, Any]:
        return {
            "context_id": self.context_id,
            "run_id": self.run_id,
            "captured_at_iso": self.captured_at_iso,
            "trigger_source": self.trigger_source,
            "session_id": self.session_id,
            "request_model_name": self.request_model_name,
            "request_provider_name": self.request_provider_name,
            "request_prompt_type": self.request_prompt_type,
            "request_prompt_name": self.request_prompt_name,
            "modifiers": dict(self.modifiers),
            "virtual_bbox": list(self.virtual_bbox) if self.virtual_bbox else None,
            "image_count": len(self.image_paths),
            "clipboard_kind": self.clipboard_kind,
            "foreground_window": {
                "hwnd": self.foreground_window.hwnd,
                "title": self.foreground_window.title,
                "process_id": self.foreground_window.process_id,
                "rect": list(self.foreground_window.rect),
                "ime_active": self.foreground_window.ime_active,
            },
        }


class ActionContextBuilder:
    """Builds frozen ActionTimeContext objects at gesture/hotkey trigger boundaries."""

    @staticmethod
    def capture(
        *,
        trigger_source: str,
        session_id: str = "",
        model_name: str = "",
        prompt_type: str = "prompt_translate",
        prompt_name: str = "",
        system_prompt: str = "",
        modifiers: dict[str, bool] | None = None,
        virtual_bbox: tuple[int, int, int, int] | None = None,
        image_paths: list[str] | tuple[str, ...] = (),
        clipboard_kind: str = "",
        clipboard_text: str = "",
        follow_history: list[dict[str, Any]] | None = None,
        foreground_window: ForegroundWindowInfo | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ActionTimeContext:
        now_mono = time.monotonic()
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        clean_model = str(model_name or "").strip()
        provider = clean_model.split("/", 1)[0] if "/" in clean_model else ""
        uid = uuid.uuid4().hex[:12]
        return ActionTimeContext(
            context_id=f"ctx_{uid}",
            run_id=f"run_{uid}",
            captured_at_monotonic=now_mono,
            captured_at_iso=now_iso,
            trigger_source=str(trigger_source or "unknown"),
            session_id=str(session_id or "default"),
            request_model_name=clean_model,
            request_provider_name=provider,
            request_prompt_type=str(prompt_type or "prompt_translate"),
            request_prompt_name=str(prompt_name or prompt_type or "Trans"),
            request_system_prompt=str(system_prompt or ""),
            modifiers=dict(modifiers or {}),
            virtual_bbox=tuple(virtual_bbox) if virtual_bbox else None,
            image_paths=tuple(str(p) for p in (image_paths or ())),
            clipboard_kind=str(clipboard_kind or ""),
            clipboard_text=str(clipboard_text or ""),
            foreground_window=foreground_window or ForegroundWindowInfo.capture_current(),
            follow_history_snapshot=tuple(dict(item) for item in (follow_history or [])),
            metadata=dict(metadata or {}),
        )
