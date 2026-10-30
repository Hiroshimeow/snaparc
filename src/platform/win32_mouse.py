from __future__ import annotations

import ctypes
from enum import Enum
import sys
import threading


class Button(Enum):
    left = "left"
    right = "right"
    middle = "middle"


class Listener:
    """Minimal Windows low-level mouse listener implemented with Win32 APIs."""

    WH_MOUSE_LL = 14
    WM_QUIT = 0x0012
    WM_MOUSEMOVE = 0x0200
    WM_LBUTTONDOWN = 0x0201
    WM_LBUTTONUP = 0x0202
    WM_RBUTTONDOWN = 0x0204
    WM_RBUTTONUP = 0x0205
    WM_MBUTTONDOWN = 0x0207
    WM_MBUTTONUP = 0x0208

    def __init__(self, *, on_click=None, on_move=None):
        self.on_click = on_click
        self.on_move = on_move
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._hook = None
        self._proc = None
        self._started = threading.Event()

    def start(self):
        if self.is_alive():
            return self
        if sys.platform != "win32":
            raise RuntimeError("Win32 mouse listener is only available on Windows")
        self._thread = threading.Thread(target=self._run, name="SnapArcMouseHook", daemon=True)
        self._thread.start()
        self._started.wait(timeout=2)
        return self

    def is_alive(self):
        return bool(self._thread and self._thread.is_alive())

    def stop(self):
        if not self.is_alive():
            return
        if self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, self.WM_QUIT, 0, 0)
        self._thread.join(timeout=2)

    def _run(self):
        from ctypes import wintypes

        class POINT(ctypes.Structure):
            _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

        class MSLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [
                ("pt", POINT),
                ("mouseData", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_void_p),
            ]

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._thread_id = int(kernel32.GetCurrentThreadId())

        low_level_proc = ctypes.WINFUNCTYPE(
            ctypes.c_longlong,
            ctypes.c_int,
            wintypes.WPARAM,
            wintypes.LPARAM,
        )

        button_messages = {
            self.WM_LBUTTONDOWN: (Button.left, True),
            self.WM_LBUTTONUP: (Button.left, False),
            self.WM_RBUTTONDOWN: (Button.right, True),
            self.WM_RBUTTONUP: (Button.right, False),
            self.WM_MBUTTONDOWN: (Button.middle, True),
            self.WM_MBUTTONUP: (Button.middle, False),
        }

        @low_level_proc
        def hook_proc(n_code, w_param, l_param):
            if n_code >= 0:
                info = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                x, y = int(info.pt.x), int(info.pt.y)
                message = int(w_param)
                try:
                    if message == self.WM_MOUSEMOVE and self.on_move is not None:
                        self.on_move(x, y)
                    elif message in button_messages and self.on_click is not None:
                        button, pressed = button_messages[message]
                        self.on_click(x, y, button, pressed)
                except Exception:
                    pass
            return user32.CallNextHookEx(self._hook, n_code, w_param, l_param)

        self._proc = hook_proc
        user32.SetWindowsHookExW.argtypes = [
            ctypes.c_int,
            low_level_proc,
            wintypes.HINSTANCE,
            wintypes.DWORD,
        ]
        user32.SetWindowsHookExW.restype = wintypes.HHOOK
        self._hook = user32.SetWindowsHookExW(self.WH_MOUSE_LL, hook_proc, None, 0)
        if not self._hook:
            self._started.set()
            raise OSError(ctypes.get_last_error(), "SetWindowsHookExW(WH_MOUSE_LL) failed")

        self._started.set()
        message = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        finally:
            if self._hook:
                user32.UnhookWindowsHookEx(self._hook)
            self._hook = None
            self._proc = None
            self._thread_id = 0
