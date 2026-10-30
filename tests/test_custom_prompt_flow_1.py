import unittest
from unittest.mock import MagicMock, patch
import main
from src.handlers.InputHandler import InputHandler
from src.services.snap_modifier_actions import (
    ACTION_CUSTOM_PROMPT,
    ACTION_CUSTOM_PROMPT_NEW_SESSION,
    ACTION_TEXT_FILE_PROMPT,
    SnapActionRouter,
)


class TestInputHandlerModifiers(unittest.TestCase):
    def setUp(self):
        self.captured_args = []

        def fake_on_capture(*args, **kwargs):
            self.captured_args.append((args, kwargs))

        self.input_handler = InputHandler(
            fake_on_capture, key_state_checker=lambda k: k == "ctrl"
        )
        self.router = SnapActionRouter.from_config(
            [
                {
                    "modifiers": ["ctrl"],
                    "name": ACTION_CUSTOM_PROMPT,
                    "keep_force_new_window": False,
                }
            ]
        )

    def test_ctrl_modifier_resolves_custom_prompt(self):
        modifiers = {"ctrl": True, "shift": False, "alt": False}
        action = self.router.resolve(modifiers)
        self.assertEqual(action.name, ACTION_CUSTOM_PROMPT)


class TestScreenshotAppCustomPromptIntegration(unittest.TestCase):
    def test_process_capture_job_routes_action_custom_prompt_to_dialog(self):
        app = main.ScreenshotApp.__new__(main.ScreenshotApp)
        app.screenshots_dir = "fake_dir"
        app.held_images = []
        app._show_hold_count_tooltip = MagicMock()
        app._hide_hold_count_tooltip = MagicMock()
        app._show_custom_prompt_for_capture = MagicMock()
        app._route_snap_to_chat_screen = MagicMock()

        with patch("PIL.ImageGrab.grab") as mock_grab:
            mock_image = MagicMock()
            mock_grab.return_value.__enter__.return_value = mock_image

            app._process_capture_job(
                left=0,
                top=0,
                right=100,
                bottom=100,
                timestamp_str="20260529_233549",
                force_new_window=False,
                action_name=ACTION_CUSTOM_PROMPT,
                modifiers={"ctrl": True},
            )

            app._show_custom_prompt_for_capture.assert_called_once()
            app._route_snap_to_chat_screen.assert_not_called()

    def test_process_capture_job_routes_action_custom_prompt_new_session_to_chat_screen(self):
        app = main.ScreenshotApp.__new__(main.ScreenshotApp)
        app.screenshots_dir = "fake_dir"
        app.held_images = []
        app._show_hold_count_tooltip = MagicMock()
        app._hide_hold_count_tooltip = MagicMock()
        app._show_custom_prompt_for_capture = MagicMock()
        app._route_snap_to_chat_screen = MagicMock()

        with patch("PIL.ImageGrab.grab") as mock_grab:
            mock_image = MagicMock()
            mock_grab.return_value.__enter__.return_value = mock_image

            app._process_capture_job(
                left=0,
                top=0,
                right=100,
                bottom=100,
                timestamp_str="20260529_233549",
                force_new_window=False,
                action_name=ACTION_CUSTOM_PROMPT_NEW_SESSION,
                modifiers={"ctrl": True, "shift": True},
            )

            app._show_custom_prompt_for_capture.assert_not_called()
            app._route_snap_to_chat_screen.assert_called_once()

    def test_process_capture_job_routes_alt_snap_to_text_file_quick_popup(self):
        app = main.ScreenshotApp.__new__(main.ScreenshotApp)
        app.root = MagicMock()
        app.screenshots_dir = "fake_dir"
        app.held_images = []
        app._show_hold_count_tooltip = MagicMock()
        app._hide_hold_count_tooltip = MagicMock()
        app._show_text_file_quick_popup = MagicMock()

        with patch("PIL.ImageGrab.grab") as mock_grab:
            mock_image = MagicMock()
            mock_grab.return_value.__enter__.return_value = mock_image

            app._process_capture_job(
                left=0,
                top=0,
                right=100,
                bottom=100,
                timestamp_str="20260529_233549",
                force_new_window=False,
                action_name=ACTION_TEXT_FILE_PROMPT,
                modifiers={"alt": True},
            )

            app.root.after.assert_called_once()

    def test_show_custom_prompt_for_capture_calls_show_custom_prompt_dialog(self):
        app = main.ScreenshotApp.__new__(main.ScreenshotApp)
        app.root = MagicMock()
        app.custom_prompt_history = MagicMock()
        app.model_client = MagicMock()
        app.model_client.get_prompt.return_value = "Default Prompt"
        app.model_client.prompt_type = "default"
        app.model_client.model = "provider/model-a"

        with patch("main.show_custom_prompt_dialog") as mock_dialog:
            app._show_custom_prompt_for_capture(["fake_path.png"], "t1", False)
            mock_dialog.assert_called_once()


if __name__ == "__main__":
    unittest.main()