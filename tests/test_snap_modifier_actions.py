from src.services.snap_modifier_actions import (
    ACTION_CUSTOM_PROMPT,
    ACTION_CUSTOM_PROMPT_NEW_SESSION,
    ACTION_DEFAULT,
    ACTION_RADIAL_WHEEL,
    ACTION_TEXT_FILE_PROMPT,
    SnapActionRouter,
)


def test_shift_snap_resolves_default_so_pipeline_can_stack_until_next_capture():
    action = SnapActionRouter().resolve({"shift": True})

    assert action.name == ACTION_DEFAULT
    assert action.modifiers.shift is True
    assert action.keep_force_new_window is True


def test_ctrl_shift_snap_is_more_specific_chat_screen_new_session_action():
    action = SnapActionRouter().resolve({"ctrl": True, "shift": True})

    assert action.name == ACTION_CUSTOM_PROMPT_NEW_SESSION


def test_ctrl_snap_routes_to_image_custom_prompt_action():
    action = SnapActionRouter().resolve({"ctrl": True, "shift": False})

    assert action.name == ACTION_CUSTOM_PROMPT


def test_alt_snap_routes_to_text_file_quick_prompt_popup():
    action = SnapActionRouter().resolve({"alt": True})

    assert action.name == ACTION_TEXT_FILE_PROMPT


def test_ctrl_alt_routes_to_radial_plugin_wheel():
    action = SnapActionRouter().resolve({"ctrl": True, "alt": True})

    assert action.name == ACTION_RADIAL_WHEEL
