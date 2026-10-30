from src.utils.yaml_io import load_yaml_or_json
import ast
import json
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image

import main


class HotkeyRoutingStateTests(unittest.TestCase):
    def make_app(self):
        app = main.ScreenshotApp.__new__(main.ScreenshotApp)
        app.PROMPT_DISPLAY_NAMES = {
            "prompt_fix_grammar": "Fix prompt",
            "prompt_summarize": "Sum",
            "prompt_translate": "Trans",
            "prompt_extract_text": "Original",
            "prompt_expert": "Expert",
            "custom_prompt": "Custom",
        }
        app.animator = MagicMock()
        app.follow_manager = types.SimpleNamespace(
            build_history=lambda: [], add_exchange=lambda *_args: None
        )
        app.gemini = MagicMock()
        app.gemini.model = "provider/model-a"
        app.gemini.prompt_type = "prompt_translate"
        app.gemini.get_prompt.side_effect = lambda key, fallback_key="prompt_translate": f"prompt:{key}"
        app.root = MagicMock()
        app._clipboard_transaction_lock = threading.Lock()
        app.run_flag = 0
        app.config_manager = MagicMock()
        app.config_manager.get.return_value = []
        app.icon = MagicMock()
        app.icon_fix = object()
        app.icon_sum = object()
        app.icon_trans = object()
        app.icon_spi = object()
        app.image = object()
        app.current_icon = app.icon_trans
        return app

    def test_pause_resume_tray_item_default_is_configurable_callable(self):
        tree = ast.parse(Path("main.py").read_text(encoding="utf-8"))
        pause_item = next(
            (
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "MenuItem"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "Pause/Resume"
            ),
            None,
        )
        self.assertIsNotNone(pause_item)
        default_kw = next((kw.value for kw in pause_item.keywords if kw.arg == "default"), None)
        self.assertIsNotNone(default_kw, "Pause/Resume must declare a default= keyword")
        self.assertIsInstance(
            default_kw,
            ast.Lambda,
            "Pause/Resume default must be a callable driven by ui.tray_left_click_action, not a constant.",
        )
        action = pause_item.args[1]
        self.assertEqual(ast.unparse(action), "self._tray_toggle_pause")

    def test_tray_left_click_action_defaults_to_toggle_pause_and_can_be_disabled(self):
        app = self.make_app()
        app.config_manager.get.side_effect = lambda key, default=None: default
        self.assertEqual(app._tray_left_click_action(), "toggle_pause")

        app.config_manager.get.side_effect = lambda key, default=None: " None "
        self.assertEqual(app._tray_left_click_action(), "none")

        app.config_manager.get.side_effect = lambda key, default=None: "bogus"
        with patch("builtins.print"):
            self.assertEqual(app._tray_left_click_action(), "toggle_pause")

    def test_tray_toggle_pause_debounces_double_click_to_single_toggle(self):
        app = self.make_app()
        with patch("main.time.monotonic", side_effect=[100.0, 100.1, 100.2, 101.0]):
            app._tray_toggle_pause()
            app._tray_toggle_pause()
            app._tray_toggle_pause()
            app._tray_toggle_pause()

        self.assertEqual(
            [call.args for call in app.root.after.call_args_list],
            [(0, app.toggle_pause)] * 2,
        )

    def test_toggle_pause_makes_animator_restore_pause_icon_and_title(self):
        app = self.make_app()
        app.image_pause = object()
        app.input_handler = MagicMock()
        seen = {}
        app.animator.stop_animation.side_effect = lambda: seen.setdefault("icon", app.animator.current_icon)

        app.toggle_pause()

        self.assertEqual(app.run_flag, 1)
        self.assertIs(seen["icon"], app.image_pause)
        self.assertIs(app.animator.current_icon, app.image_pause)
        self.assertIs(app.icon.icon, app.image_pause)
        self.assertEqual(app.icon.title, "SnapArc (Paused)")

        app.toggle_pause()

        self.assertEqual(app.run_flag, 0)
        self.assertIs(app.animator.current_icon, app.current_icon)
        self.assertIs(app.icon.icon, app.current_icon)
        self.assertEqual(app.icon.title, "SnapArc")

    def test_set_prompt_type_while_paused_keeps_pause_icon_on_animator(self):
        app = self.make_app()
        app.image_pause = object()
        app.input_handler = MagicMock()
        app.gemini._save_config = MagicMock()
        app.toggle_pause()
        new_icon = object()

        app.set_prompt_type("prompt_summarize", new_icon)

        self.assertIs(app.current_icon, new_icon)
        self.assertIs(app.animator.current_icon, app.image_pause)
        self.assertIs(app.icon.icon, app.image_pause)

        app.toggle_pause()
        self.assertIs(app.animator.current_icon, new_icon)

    def test_set_prompt_type_while_running_updates_animator_icon(self):
        app = self.make_app()
        app.gemini._save_config = MagicMock()
        new_icon = object()

        app.set_prompt_type("prompt_summarize", new_icon)

        self.assertIs(app.animator.current_icon, new_icon)
        self.assertIs(app.icon.icon, new_icon)

    def test_sample_hotkeys_map_v_j_e_s_f_a_to_expected_actions(self):
        from src.utils.yaml_io import load_yaml_or_json
        config = load_yaml_or_json(Path("sample/config.example.yaml"))
        by_id = {item["id"]: item for item in config["hotkeys"]}

        self.assertEqual(by_id["TransToVietnamese"]["prompt_type"], "prompt_translate")
        self.assertIn("tiếng nhật", by_id["TransToJapanese"]["system_prompt"].lower())
        self.assertIn("tiếng anh", by_id["TransToEnglish"]["system_prompt"].lower())
        self.assertEqual(by_id["SummarizeContent"]["prompt_type"], "prompt_summarize")
        self.assertEqual(by_id["FixPrompt"]["prompt_type"], "prompt_fix_grammar")
        self.assertEqual(by_id["AnalyzeExpert"]["keys"], "ctrl + shift + alt + a")
        self.assertEqual(by_id["AnalyzeExpert"]["prompt_type"], "prompt_expert")

    def test_custom_hotkey_system_prompt_is_used_for_image_and_text(self):
        hotkey = {
            "id": "TransToJapanese",
            "prompt_type": "prompt_translate",
            "system_prompt": "translate to Japanese exactly",
        }

        image_app = self.make_app()
        image = Image.new("RGB", (1, 1))
        with patch("main.ImageGrab.grabclipboard", return_value=image):
            image_app._process_clipboard_on_main_thread(hotkey)
        image_kwargs = image_app.gemini.process_image.call_args.kwargs
        self.assertEqual(image_kwargs["custom_prompt_text"], hotkey["system_prompt"])

        text_app = self.make_app()
        text_app.root.clipboard_get.return_value = "hello"
        with patch("main.ImageGrab.grabclipboard", return_value=None):
            text_app._process_clipboard_on_main_thread(hotkey)
        text_kwargs = text_app.gemini.send_text_message.call_args.kwargs
        self.assertTrue(text_kwargs["text_prompt"].startswith(hotkey["system_prompt"] + "\n\nContent:\n"))

    def test_named_hotkeys_select_prompt_for_next_snap(self):
        app = self.make_app()
        app.root.after.side_effect = lambda _delay, fn, *args: fn(*args)
        app.gemini.prompt_type = "prompt_summarize"
        queued_workers = []

        class QueuedThread:
            def __init__(self, target, args=(), **_kwargs):
                self.target, self.args = target, args

            def start(self):
                queued_workers.append((self.target, self.args))

        cases = [
            ("FixPrompt", "prompt_fix_grammar", app.icon_fix),
            ("SummarizeContent", "prompt_summarize", app.icon_sum),
            ("TransToVietnamese", "prompt_translate", app.icon_trans),
        ]
        with patch("main.threading.Thread", QueuedThread), patch(
            "main.ImageGrab.grabclipboard", return_value=None
        ), patch("pyperclip.paste", return_value="selected text"), patch(
            "main.show_floating_window"
        ) as show_window:
            for hotkey_id, prompt_type, expected_icon in cases:
                with self.subTest(hotkey=hotkey_id):
                    app._process_clipboard(
                        {"id": hotkey_id, "prompt_type": prompt_type, "auto_copy": False}
                    )
                    self.assertEqual(app.gemini.prompt_type, prompt_type)
                    self.assertIs(app.current_icon, expected_icon)
                    self.assertIs(app.animator.current_icon, expected_icon)
                    self.assertIs(app.icon.icon, expected_icon)
                    app._start_image_processing("snap.png", hotkey_id, False)

            # Run snap workers only after all mode changes have happened.
            for callback, args in queued_workers:
                callback(*args)
            show_window.assert_not_called()

        self.assertEqual(app.gemini._save_config.call_count, len(cases))
        requests = app.gemini.process_image.call_args_list
        self.assertEqual(
            [request.kwargs["custom_prompt_text"] for request in requests],
            [f"prompt:{prompt_type}" for _, prompt_type, _ in cases],
        )
        self.assertEqual(
            [request.kwargs["custom_prompt_type"] for request in requests],
            [prompt_type for _, prompt_type, _ in cases],
        )

    def test_auto_copy_failure_selects_prompt_without_processing_stale_clipboard(self):
        app = self.make_app()
        app.root.after.side_effect = lambda _delay, fn, *args: fn(*args)
        hotkey = {"id": "FixPrompt", "prompt_type": "prompt_fix_grammar"}

        with patch("src.utils.HotkeyManager.simulate_ctrl_c", return_value=False), patch.object(
            app, "_snapshot_clipboard_payload"
        ) as snapshot:
            app._process_clipboard(hotkey)

        self.assertEqual(app.gemini.prompt_type, "prompt_fix_grammar")
        snapshot.assert_not_called()
        app.gemini.send_text_message.assert_not_called()
        app.gemini.process_image.assert_not_called()

    def test_empty_clipboard_still_selects_summary_for_snap(self):
        app = self.make_app()
        app.root.after.side_effect = lambda _delay, fn, *args: fn(*args)
        with patch.object(app, "_snapshot_clipboard_payload", return_value=None):
            app._process_clipboard(
                {"id": "SummarizeContent", "prompt_type": "prompt_summarize", "auto_copy": False}
            )

        self.assertEqual(app.gemini.prompt_type, "prompt_summarize")
        app.gemini.send_text_message.assert_not_called()
        app.gemini.process_image.assert_not_called()

    def test_custom_system_prompt_hotkey_keeps_selected_snap_mode(self):
        app = self.make_app()
        app.gemini.prompt_type = "prompt_summarize"
        app.root.after.side_effect = lambda _delay, fn, *args: fn(*args)
        with patch.object(app, "_snapshot_clipboard_payload", return_value=("text", "hello")):
            app._process_clipboard(
                {
                    "id": "TransToJapanese",
                    "prompt_type": "prompt_translate",
                    "system_prompt": "Translate to Japanese",
                    "auto_copy": False,
                }
            )

        self.assertEqual(app.gemini.prompt_type, "prompt_summarize")
        app.gemini._save_config.assert_not_called()
        self.assertTrue(
            app.gemini.send_text_message.call_args.kwargs["text_prompt"].startswith("Translate to Japanese")
        )

    def test_paused_hotkey_does_not_select_prompt(self):
        app = self.make_app()
        app.run_flag = 1
        app._process_clipboard({"id": "FixPrompt", "prompt_type": "prompt_fix_grammar"})

        app.root.after.assert_not_called()
        self.assertEqual(app.gemini.prompt_type, "prompt_translate")

    def test_auto_copy_false_processes_existing_clipboard_without_copy_transaction(self):
        app = self.make_app()
        app.run_flag = 0
        app.config_manager = MagicMock()
        app.config_manager.get.return_value = []
        hotkey = {
            "id": "FixPrompt",
            "prompt_type": "prompt_fix_grammar",
            "auto_copy": False,
        }

        with patch("src.utils.HotkeyManager.simulate_ctrl_c") as copy_mock, patch(
            "main.ImageGrab.grabclipboard", return_value=None
        ), patch("pyperclip.paste", return_value="existing clipboard"):
            app._process_clipboard(hotkey)

        copy_mock.assert_not_called()
        app.root.after.assert_any_call(
            0, app._process_clipboard_on_main_thread, hotkey, ("text", "existing clipboard")
        )

    def test_overlapping_text_hotkeys_route_their_own_frozen_payloads(self):
        app = self.make_app()
        app.run_flag = 0
        app.config_manager = MagicMock()
        app.config_manager.get.return_value = []
        scheduled = []
        app.root.after.side_effect = lambda _delay, fn, *args: scheduled.append((fn, args))
        clipboard = {"value": ""}
        copied_values = iter(("TEXT_A", "TEXT_B"))

        def copy_next():
            clipboard["value"] = next(copied_values)
            return True

        app.root.clipboard_get.side_effect = lambda: clipboard["value"]
        hotkey_a = {"id": "A", "prompt_type": "prompt_translate"}
        hotkey_b = {"id": "B", "prompt_type": "prompt_translate"}

        with patch("src.utils.HotkeyManager.simulate_ctrl_c", side_effect=copy_next), patch(
            "main.ImageGrab.grabclipboard", return_value=None
        ), patch("pyperclip.paste", side_effect=lambda: clipboard["value"]):
            app._process_clipboard(hotkey_a)
            app._process_clipboard(hotkey_b)
            for callback, args in scheduled:
                callback(*args)

        prompts = [
            call.kwargs["text_prompt"]
            for call in app.gemini.send_text_message.call_args_list
        ]
        self.assertTrue(prompts[0].endswith("Content:\nTEXT_A"))
        self.assertTrue(prompts[1].endswith("Content:\nTEXT_B"))

    def test_copy_and_snapshot_transaction_is_serialized_across_workers(self):
        app = self.make_app()
        app.run_flag = 0
        app.config_manager = MagicMock()
        app.config_manager.get.return_value = []
        scheduled = []
        app.root.after.side_effect = lambda _delay, fn, *args: scheduled.append((fn, args))
        clipboard = {"value": ""}
        a_snapshot_started = threading.Event()
        release_a_snapshot = threading.Event()
        b_copied = threading.Event()

        def simulate_copy():
            if threading.current_thread().name == "hotkey-a":
                clipboard["value"] = "TEXT_A"
            else:
                clipboard["value"] = "TEXT_B"
                b_copied.set()
            return True

        def grab_image():
            if threading.current_thread().name == "hotkey-a":
                a_snapshot_started.set()
                self.assertTrue(release_a_snapshot.wait(timeout=1))
            return None

        with patch("src.utils.HotkeyManager.simulate_ctrl_c", side_effect=simulate_copy), patch(
            "main.ImageGrab.grabclipboard", side_effect=grab_image
        ), patch("pyperclip.paste", side_effect=lambda: clipboard["value"]):
            thread_a = threading.Thread(
                name="hotkey-a",
                target=app._process_clipboard,
                args=({"id": "A", "prompt_type": "prompt_translate"},),
            )
            thread_b = threading.Thread(
                name="hotkey-b",
                target=app._process_clipboard,
                args=({"id": "B", "prompt_type": "prompt_translate"},),
            )
            thread_a.start()
            self.assertTrue(a_snapshot_started.wait(timeout=1))
            thread_b.start()
            try:
                self.assertFalse(b_copied.wait(timeout=0.1))
            finally:
                release_a_snapshot.set()
            thread_a.join(timeout=1)
            thread_b.join(timeout=1)

            self.assertFalse(thread_a.is_alive())
            self.assertFalse(thread_b.is_alive())
            for callback, args in scheduled:
                callback(*args)

        prompts = [
            call.kwargs["text_prompt"]
            for call in app.gemini.send_text_message.call_args_list
        ]
        self.assertTrue(prompts[0].endswith("Content:\nTEXT_A"))
        self.assertTrue(prompts[1].endswith("Content:\nTEXT_B"))

    def test_image_text_overlap_routes_each_invocations_own_payload(self):
        app = self.make_app()
        app.run_flag = 0
        app.config_manager = MagicMock()
        app.config_manager.get.return_value = []
        scheduled = []
        app.root.after.side_effect = lambda _delay, fn, *args: scheduled.append((fn, args))
        image_a = Image.new("RGB", (1, 1), "red")
        clipboard = {"value": None}
        copied_values = iter((image_a, "TEXT_B"))

        def copy_next():
            clipboard["value"] = next(copied_values)
            return True

        app.root.clipboard_get.side_effect = lambda: clipboard["value"]
        hotkey_a = {"id": "A", "prompt_type": "prompt_translate"}
        hotkey_b = {"id": "B", "prompt_type": "prompt_translate"}

        with patch("src.utils.HotkeyManager.simulate_ctrl_c", side_effect=copy_next), patch(
            "main.ImageGrab.grabclipboard",
            side_effect=lambda: clipboard["value"] if isinstance(clipboard["value"], Image.Image) else None,
        ), patch(
            "pyperclip.paste",
            side_effect=lambda: clipboard["value"] if isinstance(clipboard["value"], str) else "",
        ):
            app._process_clipboard(hotkey_a)
            app._process_clipboard(hotkey_b)
            for callback, args in scheduled:
                callback(*args)

        app.gemini.process_image.assert_called_once()
        routed_image = app.gemini.process_image.call_args.args[0]
        self.assertIsNot(routed_image, image_a)
        self.assertEqual(routed_image.getpixel((0, 0)), image_a.getpixel((0, 0)))
        app.gemini.send_text_message.assert_called_once()
        self.assertTrue(
            app.gemini.send_text_message.call_args.kwargs["text_prompt"].endswith(
                "Content:\nTEXT_B"
            )
        )

    def test_auto_copy_false_freezes_current_clipboard_before_ui_dispatch(self):
        app = self.make_app()
        app.run_flag = 0
        app.config_manager = MagicMock()
        app.config_manager.get.return_value = []
        scheduled = []
        app.root.after.side_effect = lambda _delay, fn, *args: scheduled.append((fn, args))
        clipboard = {"value": "TEXT_A"}
        app.root.clipboard_get.side_effect = lambda: clipboard["value"]
        hotkey = {
            "id": "FixPrompt",
            "prompt_type": "prompt_fix_grammar",
            "auto_copy": False,
        }

        with patch("src.utils.HotkeyManager.simulate_ctrl_c") as copy_mock, patch(
            "main.ImageGrab.grabclipboard", return_value=None
        ), patch("pyperclip.paste", side_effect=lambda: clipboard["value"]):
            app._process_clipboard(hotkey)
            clipboard["value"] = "TEXT_B"
            for callback, args in scheduled:
                callback(*args)

        copy_mock.assert_not_called()
        self.assertTrue(
            app.gemini.send_text_message.call_args.kwargs["text_prompt"].endswith(
                "Content:\nTEXT_A"
            )
        )

    def test_image_dispatch_captures_model_before_later_tray_change(self):
        app = self.make_app()
        app._process_image_async = MagicMock()
        app.gemini.model = "provider/model-a"

        class ImmediateThread:
            def __init__(self, target, args, **_kwargs):
                self.target = target
                self.args = args

            def start(self):
                self.target(*self.args)

        with patch("main.threading.Thread", ImmediateThread):
            app._start_image_processing(
                "image.png", "20260808_120000", False, source_label="Mouse snap"
            )

        app.gemini.model = "provider/model-b"
        args = app._process_image_async.call_args.args
        self.assertEqual(args[-2], "provider/model-a")
        self.assertEqual(args[-1], "Trans")

    def test_image_worker_uses_frozen_dispatch_model_and_prompt(self):
        app = self.make_app()
        app.gemini.model = "provider/model-b"

        app._process_image_async(
            "image.png",
            "20260808_120000",
            False,
            False,
            custom_prompt_type="prompt_translate",
            custom_prompt_text="prompt A",
            request_model_name="provider/model-a",
            request_prompt_name="Trans",
        )

        kwargs = app.gemini.process_image.call_args.kwargs
        self.assertEqual(kwargs["custom_prompt_text"], "prompt A")
        self.assertEqual(kwargs["model_full_name"], "provider/model-a")

    def test_inflight_result_uses_dispatch_time_model_and_prompt_identity(self):
        app = self.make_app()
        app.gemini.model = "provider/model-b"
        app.gemini.prompt_type = "prompt_summarize"
        app.root.after.side_effect = lambda _delay, fn, *args: fn(*args)
        app.screenshots_dir = "missing-test-screenshots"
        app.history_manager = MagicMock()
        app.recorder = types.SimpleNamespace(get_duration=lambda: 0)

        packet = types.SimpleNamespace(ans="ok")
        with patch("main.DataPacket.from_gemini_result", return_value=packet) as packet_factory, patch(
            "main.show_floating_window"
        ):
            app.gemini_callback(
                {"text": "ok"},
                timestamp_str="20260808_120000",
                is_comparison_rerun=False,
                force_new_window=True,
                request_model_name="provider/model-a",
                request_prompt_name="TransToJapanese",
            )

        kwargs = packet_factory.call_args.kwargs
        self.assertEqual(kwargs["model_name"], "provider/model-a")
        self.assertEqual(kwargs["prompt_name"], "TransToJapanese")

    def test_set_model_schedules_existing_float_window_current_model_refresh(self):
        app = self.make_app()
        app.config_manager = MagicMock()
        app.config_manager.get_all_models.return_value = ["provider/model-a", "provider/model-b"]
        app._refresh_float_window_models = MagicMock()

        app.set_model("provider/model-b")

        app.gemini.set_model.assert_called_once_with("provider/model-b")
        app.root.after.assert_called_once_with(
            0, app._refresh_float_window_models, "provider/model-b"
        )

    def test_open_screenshot_chat_uses_fresh_mode_from_config(self):
        app = self.make_app()
        app.config_manager.get_screenshot_chat_config.return_value = {"enabled": True}
        app.config_manager.get_hotkey_configs.return_value = [
            {"id": "Other", "mode": "show"},
            {"id": "ScreenshotChatPopout", "mode": "show_import_held"},
        ]

        app._open_screenshot_chat({"id": "ScreenshotChatPopout", "mode": "toggle"})

        app.root.after.assert_called_once_with(0, app._open_screenshot_chat_on_main_thread, "show_import_held")

    def test_open_screenshot_chat_defaults_to_toggle_without_mode(self):
        app = self.make_app()
        app.config_manager.get_screenshot_chat_config.return_value = {"enabled": True}
        app.config_manager.get_hotkey_configs.return_value = [{"id": "ScreenshotChatPopout"}]

        app._open_screenshot_chat({"id": "ScreenshotChatPopout"})
        app._open_screenshot_chat(None)

        self.assertEqual(
            [call.args for call in app.root.after.call_args_list],
            [(0, app._open_screenshot_chat_on_main_thread, "toggle")] * 2,
        )

    def test_open_screenshot_chat_is_noop_when_disabled(self):
        app = self.make_app()
        app.config_manager.get_screenshot_chat_config.return_value = {"enabled": False}

        app._open_screenshot_chat({"id": "ScreenshotChatPopout", "mode": "show"})

        app.root.after.assert_not_called()

    def test_open_screenshot_chat_main_thread_runs_requested_mode(self):
        app = self.make_app()
        module = MagicMock()
        app._ensure_screenshot_chat_module = MagicMock(return_value=module)

        app._open_screenshot_chat_on_main_thread("show_import_clipboard")

        module.run_hotkey_action.assert_called_once_with("show_import_clipboard")


if __name__ == "__main__":
    unittest.main()
