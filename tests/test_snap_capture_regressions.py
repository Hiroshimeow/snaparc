import queue
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image
import pytest

import main
from src.gui.quick_prompt_popup import QuickPromptPopup
from src.gui.screenshot_chat.controller import ScreenshotChatController
from src.handlers.InputHandler import InputHandler
from src.platform.win32_mouse import Button


@pytest.fixture
def right_mouse_snap():
    pressed_keys = set()
    clock = 100.0
    capture_time = SimpleNamespace(time=lambda: clock, strftime=lambda _format: "20261120_120000")

    def inline_thread(target, args):
        return SimpleNamespace(start=lambda: target(*args))

    with (
        patch("src.handlers.InputHandler.time", capture_time),
        patch("src.handlers.InputHandler.threading.Thread", side_effect=inline_thread),
    ):
        def drag(handler, start_keys=(), end_keys=()):
            nonlocal clock
            pressed_keys.update(start_keys)
            handler.on_click(0, 0, Button.right, True)
            pressed_keys.clear()
            pressed_keys.update(end_keys)
            clock += 0.25
            handler.on_click(80, 80, Button.right, False)
            pressed_keys.clear()

        yield pressed_keys, drag


@pytest.mark.parametrize("end_keys", [(), ("shift",), ("ctrl",), ("alt",), ("ctrl", "shift")])
def test_shift_at_mouse_down_survives_early_release_without_overriding_other_modifiers(
    right_mouse_snap, end_keys
):
    pressed_keys, drag = right_mouse_snap
    callback = Mock()
    handler = InputHandler(callback, key_state_checker=pressed_keys.__contains__)

    drag(handler, start_keys=("shift",), end_keys=end_keys)
    drag(handler)

    first, second = callback.call_args_list
    expected = {
        "shift": not end_keys or "shift" in end_keys,
        "ctrl": "ctrl" in end_keys,
        "alt": "alt" in end_keys,
    }
    assert first.args[5:] == (expected["shift"], expected)
    assert second.args[5:] == (False, {"ctrl": False, "shift": False, "alt": False})


def test_first_shift_capture_schedules_visible_count():
    app = main.ScreenshotApp.__new__(main.ScreenshotApp)
    app.root = Mock()
    app._hide_hold_count_tooltip = Mock()

    app._show_hold_count_tooltip(1)

    app.root.after.assert_called_once()
    app._hide_hold_count_tooltip.assert_not_called()


@pytest.mark.parametrize(
    ("second_keys", "receiver"),
    [((), "_start_image_processing"), (("ctrl",), "_show_custom_prompt_for_capture")],
)
def test_shift_then_next_snap_in_same_second_preserves_both_files(
    right_mouse_snap, tmp_path, second_keys, receiver
):
    pressed_keys, drag = right_mouse_snap
    app = main.ScreenshotApp.__new__(main.ScreenshotApp)
    app.run_flag = 0
    app.snap_action_router = main.SnapActionRouter()
    app._capture_queue = queue.Queue()
    app.screenshots_dir = tmp_path
    app.held_images = []
    app._show_hold_count_tooltip = Mock()
    app._hide_hold_count_tooltip = Mock()
    on_result = Mock()
    setattr(app, receiver, on_result)
    handler = InputHandler(app._capture_and_process, key_state_checker=pressed_keys.__contains__)

    drag(handler, start_keys=("shift",), end_keys=("shift",))
    drag(handler, start_keys=second_keys, end_keys=second_keys)

    with patch.object(
        main.ImageGrab,
        "grab",
        side_effect=[Image.new("RGB", (2, 2), "red"), Image.new("RGB", (2, 2), "blue")],
    ):
        app._process_capture_job(*app._capture_queue.get_nowait())
        app._process_capture_job(*app._capture_queue.get_nowait())

    paths = on_result.call_args.args[0]
    assert len(set(paths)) == 2
    colors = []
    for path in paths:
        with Image.open(path) as image:
            colors.append(image.getpixel((0, 0)))
    assert colors == [(255, 0, 0), (0, 0, 255)]


@pytest.mark.parametrize("add_bitmap", [False, True])
def test_quick_prompt_to_pi_imports_each_image_once(tmp_path, add_bitmap):
    image_path = tmp_path / "capture.png"
    with Image.new("RGB", (2, 2), "red") as file_image:
        file_image.save(image_path)
    chat = ScreenshotChatController.__new__(ScreenshotChatController)
    chat.project_root = tmp_path
    chat.pending_attachments = []
    chat.open_or_focus = Mock()
    chat.set_status = Mock()
    chat.refresh_window = Mock()
    chat._ensure_active_session = Mock()
    chat._is_empty_session = Mock(return_value=True)
    chat.is_pi_primary_mode = Mock(return_value=True)

    with Image.new("RGB", (2, 2), "blue") as bitmap:
        popup = QuickPromptPopup(
            None,
            None,
            initial_files=[str(image_path)],
            initial_images=[bitmap] if add_bitmap else [],
            on_escalate_pi=chat.escalate_from_quick_popup,
        )
        assert popup.submit(target="pi_agent") is not None

    colors = []
    for attachment in chat.pending_attachments:
        with Image.open(attachment.path) as image:
            colors.append(image.getpixel((0, 0)))
    assert colors == ([(255, 0, 0), (0, 0, 255)] if add_bitmap else [(255, 0, 0)])


def test_long_snap_chip_labels_remain_distinct_and_remove_uses_original_path(tmp_path):
    paths = [
        tmp_path / f"screenshot_20261120_120000_{digit * 32}.png"
        for digit in ("1", "2")
    ]
    for path in paths:
        path.touch()
    original_paths = [str(path) for path in paths]
    popup = QuickPromptPopup(None, None, initial_files=original_paths)
    popup.chips_frame = Mock()
    popup.chips_frame.winfo_children.return_value = []

    with (
        patch("src.gui.quick_prompt_popup.tk.Frame"),
        patch("src.gui.quick_prompt_popup.tk.Label") as label,
        patch("src.gui.quick_prompt_popup.tk.Button") as button,
    ):
        popup._render_attachment_chips()
        texts = [call.kwargs["text"] for call in label.call_args_list]
        assert len(set(texts)) == 2
        assert all("…" in text and len(text) <= 40 for text in texts)
        assert [att.path for att in popup.attachments] == original_paths
        button.call_args_list[1].kwargs["command"]()

    assert [att.path for att in popup.attachments] == original_paths[:1]
