from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import threading

from PIL import Image


_LRESULT = ctypes.c_ssize_t
_WNDPROC = ctypes.WINFUNCTYPE(
    _LRESULT,
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
)


class MenuItem:
    def __init__(
        self,
        text,
        action,
        *,
        checked=None,
        radio=False,
        default=False,
        enabled=True,
    ):
        self.text = text
        self.action = action
        self.checked = checked
        self.radio = bool(radio)
        self.default = default
        self.enabled = enabled

    def label(self):
        return str(self.text(self) if callable(self.text) else self.text)

    def is_checked(self):
        return bool(self.checked(self) if callable(self.checked) else self.checked)

    def is_default(self):
        return bool(self.default(self) if callable(self.default) else self.default)

    def is_enabled(self):
        return bool(self.enabled(self) if callable(self.enabled) else self.enabled)


class Menu(tuple):
    def __new__(cls, *items):
        return super().__new__(cls, items)


class Icon:
    """Native Windows notification-area icon with a pystray-sized API surface."""

    WM_TRAY = 0x8000 + 0x51
    WM_CLOSE = 0x0010
    WM_DESTROY = 0x0002
    WM_LBUTTONUP = 0x0202
    WM_RBUTTONUP = 0x0205

    NIM_ADD = 0
    NIM_MODIFY = 1
    NIM_DELETE = 2
    NIF_MESSAGE = 0x0001
    NIF_ICON = 0x0002
    NIF_TIP = 0x0004

    IMAGE_ICON = 1
    LR_LOADFROMFILE = 0x0010
    LR_DEFAULTSIZE = 0x0040

    MF_STRING = 0x0000
    MF_POPUP = 0x0010
    MF_SEPARATOR = 0x0800
    MF_GRAYED = 0x0001
    MF_CHECKED = 0x0008
    MF_MENUBREAK = 0x0040
    MF_RADIOCHECK = 0x0200

    TPM_RIGHTBUTTON = 0x0002
    TPM_RETURNCMD = 0x0100

    class NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("hWnd", wintypes.HWND),
            ("uID", wintypes.UINT),
            ("uFlags", wintypes.UINT),
            ("uCallbackMessage", wintypes.UINT),
            ("hIcon", wintypes.HICON),
            ("szTip", wintypes.WCHAR * 128),
            ("dwState", wintypes.DWORD),
            ("dwStateMask", wintypes.DWORD),
            ("szInfo", wintypes.WCHAR * 256),
            ("uTimeoutOrVersion", wintypes.UINT),
            ("szInfoTitle", wintypes.WCHAR * 64),
            ("dwInfoFlags", wintypes.DWORD),
            ("guidItem", ctypes.c_byte * 16),
            ("hBalloonIcon", wintypes.HICON),
        ]

    def __init__(self, name, icon, title, menu):
        if sys.platform != "win32":
            raise RuntimeError("Native tray is only available on Windows")
        self.name = str(name)
        self.menu = menu if isinstance(menu, Menu) else Menu(*menu)
        self._icon_image = icon
        self._title = str(title or self.name)
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._hwnd = None
        self._wndproc = None
        self._class_name = f"SnapArcTray_{os.getpid()}_{id(self)}"
        self._started = threading.Event()
        self._stopped = threading.Event()
        self._start_error: BaseException | None = None
        self._lock = threading.RLock()
        self._icon_cache: dict[str, tuple[str, int]] = {}
        self._temp_dir = Path(tempfile.mkdtemp(prefix="snaparc_tray_"))

    @property
    def icon(self):
        return self._icon_image

    @icon.setter
    def icon(self, value):
        with self._lock:
            self._icon_image = value
            if self._hwnd:
                self._modify_icon()

    @property
    def title(self):
        return self._title

    @title.setter
    def title(self, value):
        with self._lock:
            self._title = str(value or self.name)
            if self._hwnd:
                self._modify_icon()

    def update_menu(self):
        # Menus are generated on demand, so dynamic labels/check state are fresh.
        return None

    def run_detached(self):
        if self._thread and self._thread.is_alive():
            return
        self._started.clear()
        self._stopped.clear()
        self._start_error = None
        self._thread = threading.Thread(target=self._thread_main, name="SnapArcTray", daemon=True)
        self._thread.start()
        if not self._started.wait(timeout=3):
            raise RuntimeError("Timed out starting native tray")
        if self._start_error is not None:
            raise RuntimeError(f"Failed starting native tray: {self._start_error}") from self._start_error
        if not self._thread.is_alive():
            raise RuntimeError("Native tray thread exited during startup")

    def stop(self):
        hwnd = self._hwnd
        if hwnd:
            ctypes.windll.user32.PostMessageW(hwnd, self.WM_CLOSE, 0, 0)
        if self._thread and self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=3)

    def _thread_main(self):
        try:
            self._run()
        except BaseException as exc:
            self._start_error = exc
            self._started.set()
            self._stopped.set()

    def _run(self):
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        shell32 = ctypes.windll.shell32

        lresult_t = _LRESULT
        wndproc_type = _WNDPROC

        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE
        kernel32.GetCurrentThreadId.argtypes = []
        kernel32.GetCurrentThreadId.restype = wintypes.DWORD

        user32.DefWindowProcW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.DefWindowProcW.restype = lresult_t
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HWND,
            wintypes.HMENU,
            wintypes.HINSTANCE,
            ctypes.c_void_p,
        ]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.DestroyWindow.argtypes = [wintypes.HWND]
        user32.DestroyWindow.restype = wintypes.BOOL
        user32.PostMessageW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.PostMessageW.restype = wintypes.BOOL
        user32.PostQuitMessage.argtypes = [ctypes.c_int]
        user32.PostQuitMessage.restype = None
        user32.GetMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
        ]
        user32.GetMessageW.restype = ctypes.c_int
        user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.TranslateMessage.restype = wintypes.BOOL
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.DispatchMessageW.restype = lresult_t
        user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
        user32.UnregisterClassW.restype = wintypes.BOOL
        user32.LoadImageW.argtypes = [
            wintypes.HINSTANCE,
            wintypes.LPCWSTR,
            wintypes.UINT,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        user32.LoadImageW.restype = wintypes.HANDLE
        user32.DestroyIcon.argtypes = [wintypes.HICON]
        user32.DestroyIcon.restype = wintypes.BOOL
        user32.CreatePopupMenu.argtypes = []
        user32.CreatePopupMenu.restype = wintypes.HMENU
        user32.AppendMenuW.argtypes = [
            wintypes.HMENU,
            wintypes.UINT,
            ctypes.c_size_t,
            wintypes.LPCWSTR,
        ]
        user32.AppendMenuW.restype = wintypes.BOOL
        user32.SetMenuDefaultItem.argtypes = [
            wintypes.HMENU,
            wintypes.UINT,
            wintypes.BOOL,
        ]
        user32.SetMenuDefaultItem.restype = wintypes.BOOL
        user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        user32.GetCursorPos.restype = wintypes.BOOL
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.SetForegroundWindow.restype = wintypes.BOOL
        user32.TrackPopupMenu.argtypes = [
            wintypes.HMENU,
            wintypes.UINT,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HWND,
            ctypes.c_void_p,
        ]
        user32.TrackPopupMenu.restype = wintypes.UINT
        user32.DestroyMenu.argtypes = [wintypes.HMENU]
        user32.DestroyMenu.restype = wintypes.BOOL
        shell32.Shell_NotifyIconW.argtypes = [
            wintypes.DWORD,
            ctypes.POINTER(self.NOTIFYICONDATAW),
        ]
        shell32.Shell_NotifyIconW.restype = wintypes.BOOL

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [
                ("style", wintypes.UINT),
                ("lpfnWndProc", wndproc_type),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR),
            ]

        user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
        user32.RegisterClassW.restype = wintypes.ATOM

        @wndproc_type
        def wndproc(hwnd, msg, wparam, lparam):
            if msg == self.WM_TRAY:
                event = int(lparam)
                if event == self.WM_LBUTTONUP:
                    self._invoke_default()
                    return 0
                if event == self.WM_RBUTTONUP:
                    self._show_menu()
                    return 0
            if msg == self.WM_CLOSE:
                user32.DestroyWindow(hwnd)
                return 0
            if msg == self.WM_DESTROY:
                self._delete_icon()
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._wndproc = wndproc
        hinstance = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSW()
        wc.lpfnWndProc = wndproc
        wc.hInstance = hinstance
        wc.lpszClassName = self._class_name
        atom = user32.RegisterClassW(ctypes.byref(wc))
        if not atom and ctypes.get_last_error() not in (0, 1410):
            self._started.set()
            raise OSError(ctypes.get_last_error(), "RegisterClassW failed")

        hwnd = user32.CreateWindowExW(
            0,
            self._class_name,
            self.name,
            0,
            0,
            0,
            0,
            0,
            None,
            None,
            hinstance,
            None,
        )
        if not hwnd:
            self._started.set()
            raise OSError(ctypes.get_last_error(), "CreateWindowExW failed")

        self._hwnd = hwnd
        self._thread_id = int(kernel32.GetCurrentThreadId())
        self._add_icon()
        self._started.set()

        message = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        finally:
            self._hwnd = None
            self._thread_id = 0
            self._cleanup_icons()
            try:
                user32.UnregisterClassW(self._class_name, hinstance)
            except Exception:
                pass
            self._stopped.set()

    def _notify_data(self):
        data = self.NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(self.NOTIFYICONDATAW)
        data.hWnd = self._hwnd
        data.uID = 1
        data.uFlags = self.NIF_MESSAGE | self.NIF_ICON | self.NIF_TIP
        data.uCallbackMessage = self.WM_TRAY
        data.hIcon = self._hicon_for_image(self._icon_image)
        data.szTip = self._title[:127]
        return data

    def _add_icon(self):
        ctypes.windll.shell32.Shell_NotifyIconW(self.NIM_ADD, ctypes.byref(self._notify_data()))

    def _modify_icon(self):
        if self._hwnd:
            ctypes.windll.shell32.Shell_NotifyIconW(self.NIM_MODIFY, ctypes.byref(self._notify_data()))

    def _delete_icon(self):
        if not self._hwnd:
            return
        data = self.NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(self.NOTIFYICONDATAW)
        data.hWnd = self._hwnd
        data.uID = 1
        ctypes.windll.shell32.Shell_NotifyIconW(self.NIM_DELETE, ctypes.byref(data))

    def _hicon_for_image(self, image):
        if image is None:
            return None
        if not isinstance(image, Image.Image):
            image = Image.open(image)
        rgba = image.convert("RGBA")
        digest = hashlib.sha256(rgba.tobytes() + str(rgba.size).encode("ascii")).hexdigest()
        cached = self._icon_cache.get(digest)
        if cached:
            return cached[1]

        path = self._temp_dir / f"{digest}.ico"
        rgba.save(path, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
        hicon = ctypes.windll.user32.LoadImageW(
            None,
            str(path),
            self.IMAGE_ICON,
            0,
            0,
            self.LR_LOADFROMFILE | self.LR_DEFAULTSIZE,
        )
        if not hicon:
            raise OSError(ctypes.get_last_error(), "LoadImageW failed for tray icon")
        self._icon_cache[digest] = (str(path), int(hicon))
        return hicon

    def _cleanup_icons(self):
        user32 = ctypes.windll.user32
        for _path, hicon in self._icon_cache.values():
            try:
                user32.DestroyIcon(hicon)
            except Exception:
                pass
        self._icon_cache.clear()
        try:
            for path in self._temp_dir.glob("*.ico"):
                path.unlink(missing_ok=True)
            self._temp_dir.rmdir()
        except Exception:
            pass

    def _invoke_default(self):
        for item in self.menu:
            if isinstance(item, MenuItem) and item.is_enabled() and item.is_default():
                self._invoke(item)
                return

    def _invoke(self, item):
        action = item.action
        if callable(action):
            action(self, item)

    def _show_menu(self):
        user32 = ctypes.windll.user32
        command_map: dict[int, MenuItem] = {}
        next_id = [1000]

        def build(menu):
            handle = user32.CreatePopupMenu()
            for item in menu:
                if not isinstance(item, MenuItem):
                    continue
                label = item.label()
                if label.strip().startswith("---") and item.action is None:
                    user32.AppendMenuW(handle, self.MF_SEPARATOR, 0, None)
                    continue
                if isinstance(item.action, Menu):
                    submenu = build(item.action)
                    flags = self.MF_POPUP
                    if not item.is_enabled():
                        flags |= self.MF_GRAYED
                    user32.AppendMenuW(handle, flags, submenu, label)
                    continue

                command_id = next_id[0]
                next_id[0] += 1
                command_map[command_id] = item
                flags = self.MF_STRING
                if not item.is_enabled():
                    flags |= self.MF_GRAYED
                if item.is_checked():
                    flags |= self.MF_CHECKED
                if item.radio:
                    flags |= self.MF_RADIOCHECK
                user32.AppendMenuW(handle, flags, command_id, label)
                if item.is_default():
                    user32.SetMenuDefaultItem(handle, command_id, False)
            return handle

        popup = build(self.menu)
        point = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(point))
        user32.SetForegroundWindow(self._hwnd)
        try:
            command_id = user32.TrackPopupMenu(
                popup,
                self.TPM_RIGHTBUTTON | self.TPM_RETURNCMD,
                point.x,
                point.y,
                0,
                self._hwnd,
                None,
            )
            item = command_map.get(int(command_id or 0))
            if item is not None and item.is_enabled():
                self._invoke(item)
        finally:
            user32.DestroyMenu(popup)
