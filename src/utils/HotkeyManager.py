import ctypes
import ctypes.wintypes
import os
import threading
import time
from src.utils.ConfigManager import ConfigManager

# Win32 constants
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
WM_USER = 0x0400
WM_APP_QUIT = WM_USER + 1
WM_APP_RELOAD = WM_USER + 2

# SendInput constants
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.wintypes.WORD),
        ("wScan", ctypes.wintypes.WORD),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("time", ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.wintypes.DWORD),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("time", ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class INPUT(ctypes.Structure):
    class _INPUT(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]

    _anonymous_ = ("_input",)
    _fields_ = [
        ("type", ctypes.wintypes.DWORD),
        ("_input", _INPUT),
    ]


def _make_key_input(vk, flags=0):
    ki = KEYBDINPUT(wVk=vk, wScan=0, dwFlags=flags, time=0, dwExtraInfo=0)
    inp = INPUT(type=INPUT_KEYBOARD)
    inp._input.ki = ki
    return inp


def simulate_ctrl_c():
    """
    Release held modifiers (from hotkey), send Ctrl+C, then release.
    Polls the clipboard sequence number to ensure copy finishes.
    """
    user32 = ctypes.windll.user32

    VK_CONTROL = 0x11
    VK_SHIFT = 0x10
    VK_MENU = 0x12
    VK_C = 0x43

    release_mods = [
        _make_key_input(VK_MENU, KEYEVENTF_KEYUP),
        _make_key_input(VK_SHIFT, KEYEVENTF_KEYUP),
        _make_key_input(VK_CONTROL, KEYEVENTF_KEYUP),
    ]

    ctrl_c = [
        _make_key_input(VK_CONTROL),
        _make_key_input(VK_C),
        _make_key_input(VK_C, KEYEVENTF_KEYUP),
        _make_key_input(VK_CONTROL, KEYEVENTF_KEYUP),
    ]

    seq_before = user32.GetClipboardSequenceNumber()

    arr_release = (INPUT * len(release_mods))(*release_mods)
    user32.SendInput(len(release_mods), arr_release, ctypes.sizeof(INPUT))

    time.sleep(0.05)

    arr_copy = (INPUT * len(ctrl_c))(*ctrl_c)
    user32.SendInput(len(ctrl_c), arr_copy, ctypes.sizeof(INPUT))

    deadline = time.monotonic() + 0.5
    while time.monotonic() < deadline:
        if user32.GetClipboardSequenceNumber() != seq_before:
            return True
        time.sleep(0.02)
    return False


MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

VK_MAP = {
    "a": 0x41,
    "b": 0x42,
    "c": 0x43,
    "d": 0x44,
    "e": 0x45,
    "f": 0x46,
    "g": 0x47,
    "h": 0x48,
    "i": 0x49,
    "j": 0x4A,
    "k": 0x4B,
    "l": 0x4C,
    "m": 0x4D,
    "n": 0x4E,
    "o": 0x4F,
    "p": 0x50,
    "q": 0x51,
    "r": 0x52,
    "s": 0x53,
    "t": 0x54,
    "u": 0x55,
    "v": 0x56,
    "w": 0x57,
    "x": 0x58,
    "y": 0x59,
    "z": 0x5A,
    "0": 0x30,
    "1": 0x31,
    "2": 0x32,
    "3": 0x33,
    "4": 0x34,
    "5": 0x35,
    "6": 0x36,
    "7": 0x37,
    "8": 0x38,
    "9": 0x39,
    "f1": 0x70,
    "f2": 0x71,
    "f3": 0x72,
    "f4": 0x73,
    "f5": 0x74,
    "f6": 0x75,
    "f7": 0x76,
    "f8": 0x77,
    "f9": 0x78,
    "f10": 0x79,
    "f11": 0x7A,
    "f12": 0x7B,
    "space": 0x20,
    "enter": 0x0D,
    "esc": 0x1B,
    "tab": 0x09,
    "backspace": 0x08,
    "up": 0x26,
    "down": 0x28,
    "left": 0x25,
    "right": 0x27,
}

MOD_MAP = {
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
}

MOD_DISPLAY_ORDER = ("ctrl", "alt", "shift", "win")
MOD_CANONICAL_NAME = {
    "control": "ctrl",
    "ctrl": "ctrl",
    "alt": "alt",
    "shift": "shift",
    "win": "win",
}


class HotkeyManager:
    def __init__(self, callback_map=None):
        self.config_manager = ConfigManager()
        self.callback_map = callback_map or {}
        self._thread = None
        self._watcher_thread = None
        self._thread_id = None
        self._running = False
        self._hotkeys = []
        self._hotkey_lookup = {}
        self._registered_signature = None
        self._last_config_mtime = None

    def start(self, on_error=None):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, kwargs={"on_error": on_error}, daemon=True)
        self._thread.start()
        self._watcher_thread = threading.Thread(
            target=self._watch_config_loop, daemon=True
        )
        self._watcher_thread.start()

    def stop(self):
        self._running = False
        if self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_APP_QUIT, 0, 0)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1)
            if self._thread.is_alive():
                print("HotkeyManager: Warning - Thread did not terminate in time.")
        if self._watcher_thread and self._watcher_thread.is_alive():
            self._watcher_thread.join(timeout=1)

    def request_reload(self):
        if self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(
                self._thread_id, WM_APP_RELOAD, 0, 0
            )

    def _normalize_hotkey(self, hotkey_str):
        parts = [
            p.strip().lower()
            for p in hotkey_str.replace("-", "+").split("+")
            if p.strip()
        ]
        modifiers = []
        key = None
        for part in parts:
            if part in MOD_CANONICAL_NAME:
                canonical = MOD_CANONICAL_NAME[part]
                if canonical not in modifiers:
                    modifiers.append(canonical)
            elif part in VK_MAP:
                key = part
            elif len(part) == 1:
                key = part.lower()
        ordered_modifiers = [m for m in MOD_DISPLAY_ORDER if m in modifiers]
        return "+".join([*ordered_modifiers, key] if key else ordered_modifiers)

    def _parse_hotkey(self, hotkey_str):
        normalized = self._normalize_hotkey(hotkey_str)
        if not normalized:
            return 0, 0, ""

        modifiers = 0
        vk = 0
        for part in normalized.split("+"):
            if part in MOD_MAP:
                modifiers |= MOD_MAP[part]
            elif part in VK_MAP:
                vk = VK_MAP[part]
            elif len(part) == 1:
                vk = ord(part.upper())

        return modifiers, vk, normalized

    def _build_hotkey_plan(self, hotkey_configs):
        plan = []
        seen_keys = {}
        seen_ids = set()

        for index, cfg in enumerate(hotkey_configs):
            if not isinstance(cfg, dict):
                print(f"HotkeyManager: Skipping non-dict hotkey config at index {index}.")
                continue

            hotkey_id = str(cfg.get("id") or f"hotkey_{index}").strip()
            keys = str(cfg.get("keys") or "").strip()
            command = str(cfg.get("command") or "").strip()
            modifiers, vk, normalized = self._parse_hotkey(keys)

            if not hotkey_id:
                print(f"HotkeyManager: Skipping hotkey with empty id at index {index}.")
                continue
            if hotkey_id in seen_ids:
                print(f"HotkeyManager: Duplicate hotkey id '{hotkey_id}' skipped.")
                continue
            if not normalized or vk == 0:
                print(
                    f"HotkeyManager: Invalid hotkey '{keys}' for id '{hotkey_id}' skipped."
                )
                continue
            if not command:
                print(
                    f"HotkeyManager: Missing command for hotkey '{hotkey_id}' skipped."
                )
                continue
            if command not in self.callback_map:
                print(
                    f"HotkeyManager: Unsupported command '{command}' for hotkey '{hotkey_id}' skipped."
                )
                continue
            if normalized in seen_keys:
                print(
                    f"HotkeyManager: Duplicate key binding '{normalized}' skipped for '{hotkey_id}'. "
                    f"Already used by '{seen_keys[normalized]}'."
                )
                continue

            seen_ids.add(hotkey_id)
            seen_keys[normalized] = hotkey_id
            plan.append(
                {
                    **cfg,
                    "id": hotkey_id,
                    "keys": keys,
                    "command": command,
                    "_normalized_keys": normalized,
                    "_modifiers": modifiers,
                    "_vk": vk,
                }
            )

        return plan

    def _unregister_hotkeys(self, user32):
        for hk in self._hotkeys:
            hk_id = hk.get("_hk_id")
            if hk_id is not None:
                user32.UnregisterHotKey(None, hk_id)
        self._hotkeys = []
        self._hotkey_lookup = {}
        self._registered_signature = None

    def _reload_hotkeys(self, user32, on_error=None):
        self.config_manager.load_config(force=True)
        if hasattr(self.config_manager, "get_hotkey_configs"):
            hotkey_configs = self.config_manager.get_hotkey_configs()
        else:
            hotkey_configs = self.config_manager.get("hotkeys", [])
        plan = self._build_hotkey_plan(hotkey_configs)
        signature = tuple(
            (item["id"], item["_normalized_keys"], item["command"])
            for item in plan
        )
        if signature == self._registered_signature:
            return

        self._unregister_hotkeys(user32)

        registered_count = 0
        for i, cfg in enumerate(plan):
            modifiers = cfg["_modifiers"]
            vk = cfg["_vk"]
            if user32.RegisterHotKey(None, i, modifiers | MOD_NOREPEAT, vk):
                hk_info = cfg.copy()
                hk_info["_hk_id"] = i
                self._hotkeys.append(hk_info)
                self._hotkey_lookup[i] = hk_info
                registered_count += 1
            else:
                print(
                    f"HotkeyManager: Failed to register '{cfg['keys']}' for '{cfg['id']}' "
                    f"(Error: {ctypes.windll.kernel32.GetLastError()})."
                )
                if on_error:
                    on_error(cfg)

        self._registered_signature = tuple(
            (item["id"], item["_normalized_keys"], item["command"])
            for item in self._hotkeys
        )
        print(f"HotkeyManager: Registered {registered_count} hotkeys.")

    def _watch_config_loop(self):
        while self._running:
            try:
                config_path = self.config_manager.config_path
                current_mtime = os.path.getmtime(config_path) if os.path.exists(config_path) else None
                if self._last_config_mtime is None:
                    self._last_config_mtime = current_mtime
                elif current_mtime != self._last_config_mtime:
                    self._last_config_mtime = current_mtime
                    print("HotkeyManager: Detected config.yaml change, reloading hotkeys.")
                    self.request_reload()
            except Exception as e:
                print(f"HotkeyManager: Config watch error: {e}")
            time.sleep(1.0)

    def _run_loop(self, on_error=None):
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        user32 = ctypes.windll.user32

        try:
            self._reload_hotkeys(user32, on_error=on_error)

            msg = ctypes.wintypes.MSG()
            while self._running:
                ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ret == 0 or ret == -1:
                    break

                if msg.message == WM_HOTKEY:
                    hk = self._hotkey_lookup.get(msg.wParam)
                    if hk:
                        command = hk["command"]
                        if command in self.callback_map:
                            threading.Thread(
                                target=self.callback_map[command],
                                args=(hk,),
                                daemon=True,
                            ).start()
                elif msg.message == WM_APP_RELOAD:
                    self._reload_hotkeys(user32, on_error=on_error)
                elif msg.message == WM_APP_QUIT:
                    break

                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            self._unregister_hotkeys(user32)
            print("HotkeyManager: Stopped.")
