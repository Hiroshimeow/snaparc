import ctypes
from unittest.mock import patch

from src.handlers.InputHandler import InputHandler
from src.services.snap_modifier_actions import (
    ACTION_CUSTOM_PROMPT,
    ACTION_CUSTOM_PROMPT_NEW_SESSION,
    ACTION_DEFAULT,
    SnapActionRouter,
)


VK_CONTROL = 0x11
VK_SHIFT = 0x10
VK_MENU = 0x12


def _read_first_modifiers(*pressed_virtual_keys):
    pressed = set(pressed_virtual_keys)

    def get_async_key_state(virtual_key):
        return 0x8000 if virtual_key in pressed else 0

    with (
        patch.object(
            ctypes.windll.user32,
            "GetAsyncKeyState",
            side_effect=get_async_key_state,
            create=True,
        ),
        patch(
            "src.handlers.InputHandler.keyboard.is_pressed",
            side_effect=AssertionError("Windows modifier sampling must not use event history"),
            create=True,
        ),
    ):
        return InputHandler(lambda *_args: None)._read_modifiers()


def test_cold_first_ctrl_uses_physical_state_and_routes_custom_prompt():
    modifiers = _read_first_modifiers(VK_CONTROL)

    assert modifiers == {"ctrl": True, "shift": False, "alt": False}
    assert SnapActionRouter().resolve(modifiers).name == ACTION_CUSTOM_PROMPT


def test_cold_first_ctrl_shift_routes_custom_prompt_new_session():
    modifiers = _read_first_modifiers(VK_CONTROL, VK_SHIFT)

    assert modifiers == {"ctrl": True, "shift": True, "alt": False}
    assert SnapActionRouter().resolve(modifiers).name == ACTION_CUSTOM_PROMPT_NEW_SESSION


def test_cold_first_shift_keeps_default_shift_stack_semantics():
    modifiers = _read_first_modifiers(VK_SHIFT)
    action = SnapActionRouter().resolve(modifiers)

    assert modifiers == {"ctrl": False, "shift": True, "alt": False}
    assert action.name == ACTION_DEFAULT
    assert action.modifiers.shift is True
    assert action.keep_force_new_window is True


def test_cold_first_no_modifiers_remains_default_capture():
    modifiers = _read_first_modifiers()
    action = SnapActionRouter().resolve(modifiers)

    assert modifiers == {"ctrl": False, "shift": False, "alt": False}
    assert action.name == ACTION_DEFAULT
