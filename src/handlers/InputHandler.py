# src/handlers/InputHandler.py
import ctypes
import math
import sys
import threading
import time
import keyboard
from pynput import mouse


_WINDOWS_VIRTUAL_KEYS = {
    "ctrl": 0x11,
    "control": 0x11,
    "shift": 0x10,
    "alt": 0x12,
}


def _default_key_state_checker(key_name):
    if sys.platform == "win32" or hasattr(ctypes, "windll"):
        virtual_key = _WINDOWS_VIRTUAL_KEYS.get(key_name)
        if virtual_key is not None and hasattr(ctypes.windll, "user32"):
            return bool(ctypes.windll.user32.GetAsyncKeyState(virtual_key) & 0x8000)
    return keyboard.is_pressed(key_name)


class InputHandler:
    """
    Mouse & modifier input handler for Screens-Trans-Chatbot v2.4:
    1. Right-mouse (or Left-mouse) drag (`>= 0.2s`, `dist > 50px`):
       - Default / Shift+Snap / Ctrl+Snap (Image Popup) / Alt+Snap (Text/File Popup)
    2. Alt + Hold/Click (`>= 0.2s` even without dragging > 50px):
       - Opens Unified Quick Prompt Popup in Text/File mode (`on_alt_quick_popup`)
    3. Ctrl + Alt + Left/Right Mouse (Radial Plugin Wheel — Mode 1 & Mode 2):
       - Mode 1: Hold `Ctrl + Alt`, press & hold Left (or Right) mouse button, drag to
         select a slice on the radial wheel, and release.
       - Mode 2: Hold `Ctrl + Alt`, grab/drag a file (or start dragging a file with Left
         mouse and press `Ctrl + Alt`), move into the target plugin slice (e.g. `pdf2png`,
         `png_resize`), and release.
    """

    def __init__(
        self,
        on_capture,
        key_state_checker=None,
        on_radial_open=None,
        on_radial_move=None,
        on_radial_release=None,
        on_alt_quick_popup=None,
        radial_hold_seconds=0.18,
    ):
        self.on_capture = on_capture
        self.key_state_checker = key_state_checker or _default_key_state_checker
        self.on_radial_open = on_radial_open
        self.on_radial_move = on_radial_move
        self.on_radial_release = on_radial_release
        self.on_alt_quick_popup = on_alt_quick_popup
        self.radial_hold_seconds = float(radial_hold_seconds if radial_hold_seconds is not None else 0.18)

        self.mouse_listener = None
        self.start_x = 0
        self.start_y = 0
        self.is_capturing = False
        self.right_click_start_time = None
        self.right_click_start_pos = None
        self.trigger_button = mouse.Button.right
        self.left_click_mode_enabled = False

        # Radial wheel state (Ctrl + Alt + Hold/Drag Left/Right Mouse)
        self._radial_timer = None
        self._radial_active = False
        self._radial_button = None
        self._radial_origin = None
        self._held_mouse_button = None
        self._held_mouse_origin = None

    def toggle_left_click_mode(self):
        """Toggle between Right-Mouse and Left-Mouse drag snap capture."""
        self.left_click_mode_enabled = not self.left_click_mode_enabled
        if self.left_click_mode_enabled:
            self.trigger_button = mouse.Button.left
            print("Capture Mode switched to: LEFT MOUSE")
        else:
            self.trigger_button = mouse.Button.right
            print("Capture Mode switched to: RIGHT MOUSE")
        return self.left_click_mode_enabled

    def _cancel_radial_timer(self):
        timer = self._radial_timer
        self._radial_timer = None
        if timer is not None:
            try:
                timer.cancel()
            except Exception:
                pass

    def _activate_radial_wheel(self, x, y):
        self._radial_timer = None
        if self._radial_active:
            return
        modifiers = self._read_modifiers()
        if not (modifiers.get("ctrl") and modifiers.get("alt")):
            return
        self._radial_active = True
        self.is_capturing = False
        if self.on_radial_open is not None:
            try:
                self.on_radial_open(int(x), int(y))
            except Exception as exc:
                print(f"Error in on_radial_open: {exc}")

    def on_move(self, x, y):
        if self._radial_active:
            if self.on_radial_move is not None:
                try:
                    self.on_radial_move(int(x), int(y))
                except Exception as exc:
                    print(f"Error in on_radial_move: {exc}")
            return

        # Fast-drag activation: if Ctrl+Alt+Click was pressed and user already started dragging >= 14px,
        # open the radial wheel immediately at `_radial_origin` and update cursor to `(x, y)`!
        if self._radial_button is not None and self._radial_origin is not None:
            ox, oy = self._radial_origin
            if math.hypot(x - ox, y - oy) >= 14.0:
                self._cancel_radial_timer()
                self._activate_radial_wheel(ox, oy)
                if self._radial_active and self.on_radial_move is not None:
                    try:
                        self.on_radial_move(int(x), int(y))
                    except Exception:
                        pass
                return

        # Mode 2 File-Drag Support: if user started dragging a file with Left Mouse first,
        # and then pressed Ctrl + Alt while dragging, open the Radial Wheel at current cursor!
        if self._held_mouse_button == mouse.Button.left and not self._radial_active:
            modifiers = self._read_modifiers()
            if modifiers.get("ctrl") and modifiers.get("alt"):
                self._cancel_radial_timer()
                self._radial_button = mouse.Button.left
                self._radial_origin = (int(x), int(y))
                self._activate_radial_wheel(int(x), int(y))

    def on_click(self, x, y, button, pressed):
        is_left_or_right = button in (mouse.Button.left, mouse.Button.right)

        if pressed:
            if is_left_or_right:
                self._held_mouse_button = button
                self._held_mouse_origin = (int(x), int(y))

            modifiers = self._read_modifiers()
            # Check for Ctrl + Alt + Hold/Drag (Left or Right mouse button)
            if is_left_or_right and modifiers.get("ctrl") and modifiers.get("alt"):
                self._cancel_radial_timer()
                self._radial_button = button
                self._radial_origin = (int(x), int(y))
                self._radial_active = False
                self.is_capturing = False
                if self.radial_hold_seconds <= 0:
                    self._activate_radial_wheel(x, y)
                else:
                    timer = threading.Timer(
                        self.radial_hold_seconds,
                        self._activate_radial_wheel,
                        args=(x, y),
                    )
                    timer.daemon = True
                    self._radial_timer = timer
                    timer.start()
                return

            if button == self.trigger_button:
                self.right_click_start_time = time.time()
                self.right_click_start_pos = (x, y)
                self.is_capturing = True
        else:
            if button == self._held_mouse_button:
                self._held_mouse_button = None
                self._held_mouse_origin = None

            # Button released
            if button == self._radial_button:
                was_radial_active = self._radial_active
                self._cancel_radial_timer()
                self._radial_button = None
                self._radial_origin = None
                self._radial_active = False

                if was_radial_active:
                    if self.on_radial_release is not None:
                        try:
                            self.on_radial_release(int(x), int(y))
                        except Exception as exc:
                            print(f"Error in on_radial_release: {exc}")
                    return
                modifiers = self._read_modifiers()
                if modifiers.get("ctrl") and modifiers.get("alt") and self.on_radial_open is not None:
                    return

            if button == self.trigger_button:
                if (
                    self.is_capturing
                    and self.right_click_start_time
                    and self.right_click_start_pos
                ):
                    elapsed = time.time() - self.right_click_start_time
                    dist = (
                        (x - self.right_click_start_pos[0]) ** 2
                        + (y - self.right_click_start_pos[1]) ** 2
                    ) ** 0.5
                    modifiers = self._read_modifiers()

                    # Ignore if Ctrl+Alt is held (reserved for Radial Wheel)
                    if modifiers.get("ctrl") and modifiers.get("alt"):
                        self.right_click_start_time = None
                        self.right_click_start_pos = None
                        self.is_capturing = False
                        return

                    if elapsed >= 0.2 and dist > 50:
                        is_shift_pressed = modifiers.get("shift", False)
                        print(f"Snap modifiers: {modifiers}")

                        left = min(self.right_click_start_pos[0], x)
                        top = min(self.right_click_start_pos[1], y)
                        right = max(self.right_click_start_pos[0], x)
                        bottom = max(self.right_click_start_pos[1], y)
                        ts = time.strftime("%Y%m%d_%H%M%S")

                        threading.Thread(
                            target=self.on_capture,
                            args=(
                                left,
                                top,
                                right,
                                bottom,
                                ts,
                                is_shift_pressed,
                                modifiers,
                            ),
                        ).start()
                    elif (
                        elapsed >= 0.2
                        and modifiers.get("alt")
                        and not modifiers.get("ctrl")
                        and self.on_alt_quick_popup is not None
                    ):
                        threading.Thread(
                            target=self.on_alt_quick_popup,
                            args=(int(x), int(y), modifiers),
                            daemon=True,
                        ).start()

                self.right_click_start_time = None
                self.right_click_start_pos = None
                self.is_capturing = False

    def _is_key_pressed(self, key_name):
        try:
            return bool(self.key_state_checker(key_name))
        except Exception as e:
            print(
                f"Warning: Could not check {key_name} key state - {e}. Assuming False."
            )
            return False

    def _read_modifiers(self):
        return {
            "ctrl": self._is_key_pressed("ctrl") or self._is_key_pressed("control"),
            "shift": self._is_key_pressed("shift"),
            "alt": self._is_key_pressed("alt"),
        }

    def start_mouse_listener(self):
        if self.mouse_listener and self.mouse_listener.is_alive():
            return
        try:
            self.mouse_listener = mouse.Listener(
                on_click=self.on_click,
                on_move=self.on_move,
            )
            self.mouse_listener.start()
            print("Mouse listener started.")
        except Exception as e:
            print(f"Error starting mouse listener: {e}")
            self.mouse_listener = None

    def stop_mouse_listener(self):
        self._cancel_radial_timer()
        if self.mouse_listener and self.mouse_listener.is_alive():
            try:
                self.mouse_listener.stop()
                print("Mouse listener stopped.")
            except Exception as e:
                print(f"Error stopping mouse listener: {e}")
        self.mouse_listener = None
