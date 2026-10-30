import unittest
from pathlib import Path


WINDOW_SOURCE = Path("src/gui/screenshot_chat/window.py").read_text(encoding="utf-8")
CONTROLLER_SOURCE = Path("src/gui/screenshot_chat/controller.py").read_text(encoding="utf-8")
MAIN_SOURCE = Path("main.py").read_text(encoding="utf-8")
COMPOSER_SOURCE = Path("src/gui/screenshot_chat/composer.py").read_text(encoding="utf-8")


class ScreenshotChatWindowLayoutTests(unittest.TestCase):
    def test_chat_area_does_not_force_footer_offscreen(self):
        self.assertNotIn("grid_rowconfigure(1, weight=1, minsize=", WINDOW_SOURCE)
        self.assertNotIn("height=420", WINDOW_SOURCE)

    def test_paste_binding_is_registered_once_per_case(self):
        self.assertEqual(COMPOSER_SOURCE.count('self.entry.bind("<Control-v>", self._on_paste)'), 1)
        self.assertEqual(COMPOSER_SOURCE.count('self.entry.bind("<Control-V>", self._on_paste)'), 1)

    def test_empty_status_and_attachment_rows_do_not_reserve_space(self):
        self.assertIn("self.status_label.grid_remove()", WINDOW_SOURCE)
        self.assertIn("self.attachment_tray.grid_remove()", COMPOSER_SOURCE)

    def test_input_box_auto_grows_with_content(self):
        self.assertIn("def _resize_entry_to_content", COMPOSER_SOURCE)
        self.assertIn("self._resize_entry_to_content()", COMPOSER_SOURCE)
        self.assertIn('"displaylines"', COMPOSER_SOURCE)
        self.assertIn("max_height = min(180", COMPOSER_SOURCE)
        self.assertNotIn("height=68", COMPOSER_SOURCE)


    def test_user_bubbles_do_not_render_author_label(self):
        self.assertIn("if not is_user:", WINDOW_SOURCE)
        self.assertNotIn("author = \"You\" if is_user else message.author", WINDOW_SOURCE)

    def test_pinned_snap_picker_uses_inline_menu_not_extra_window(self):
        pinned_block = WINDOW_SOURCE.split("def _show_pinned_snap_picker", 1)[1].split("def _insert_pinned_snap_and_close", 1)[0]
        self.assertIn("tk.Menu", pinned_block)
        self.assertNotIn("CTkToplevel", pinned_block)
        self.assertNotIn("pins[:3]", pinned_block)

    def test_enter_sends_even_when_pending_and_stop_is_button_action(self):
        self.assertIn("def _send_current_message", COMPOSER_SOURCE)
        self.assertIn("self._send_current_message()", COMPOSER_SOURCE)
        self.assertIn("self.controller.stop_active_session_jobs()", COMPOSER_SOURCE)

    def test_queued_messages_have_cancel_control_and_canceled_style(self):
        self.assertIn("cancel_queued_message", WINDOW_SOURCE)
        self.assertIn("message.status == \"queued\"", WINDOW_SOURCE)
        self.assertIn("message.status == \"canceled\"", WINDOW_SOURCE)

    def test_attachment_chips_are_scrollable_and_removable(self):
        self.assertIn('orientation="horizontal"', COMPOSER_SOURCE)
        self.assertIn("clear_pending_attachment", COMPOSER_SOURCE)
        self.assertNotIn("pending_attachments[:6]", COMPOSER_SOURCE)

    def test_drag_drop_hooks_are_registered_when_runtime_supports_them(self):
        self.assertIn("def _enable_drag_drop", WINDOW_SOURCE)
        self.assertIn("<<Drop>>", COMPOSER_SOURCE)
        self.assertIn("import_paths", COMPOSER_SOURCE)

    def test_scroll_to_bottom_button_uses_unseen_state_not_any_message(self):
        self.assertIn("scroll_bottom_button", WINDOW_SOURCE)
        self.assertIn("def scroll_to_bottom", WINDOW_SOURCE)
        scroll_block = WINDOW_SOURCE.split("def _show_scroll_bottom_button_if_needed", 1)[1].split("def scroll_to_bottom", 1)[0]
        self.assertNotIn("if session.messages", scroll_block)
        self.assertNotIn('text="v"', WINDOW_SOURCE)
        self.assertIn("_has_unseen_bottom_message", WINDOW_SOURCE)
        self.assertIn("def _bind_scroll_state", WINDOW_SOURCE)
        self.assertIn("yscrollcommand=self._on_message_scroll", WINDOW_SOURCE)
        self.assertIn("def _on_message_scroll", WINDOW_SOURCE)

    def test_message_text_uses_selectable_text_widget_for_copy(self):
        self.assertIn("tk.Text", WINDOW_SOURCE)
        self.assertIn("selectbackground", WINDOW_SOURCE)
        self.assertIn('state="disabled"', WINDOW_SOURCE)

    def test_tk_fallback_exposes_reviewed_ui_actions(self):
        self.assertIn("def _add_system_prompt_menu", WINDOW_SOURCE)
        self.assertIn("Copy all response", WINDOW_SOURCE)
        self.assertIn("def _show_message_context_menu", WINDOW_SOURCE)
        self.assertIn("def _render_attachment_preview", WINDOW_SOURCE)
        self.assertIn("def _open_image_preview", WINDOW_SOURCE)
        self.assertIn("Delete chat", WINDOW_SOURCE)
        self.assertIn("def _confirm_delete_session", WINDOW_SOURCE)

    def test_scroll_to_bottom_retries_after_layout_settles(self):
        self.assertIn("def _scroll_canvas_to_bottom", WINDOW_SOURCE)
        self.assertNotIn("for delay in (0, 30, 120, 260)", WINDOW_SOURCE)
        self.assertIn('delay = 180 if reason != "manual" else 20', WINDOW_SOURCE)
        self.assertIn("update_idletasks()", WINDOW_SOURCE)

    def test_message_bubbles_bound_very_long_responses_and_stream_incrementally(self):
        self.assertNotIn("width=36", WINDOW_SOURCE)
        self.assertNotIn("min(8", WINDOW_SOURCE)
        self.assertIn("MESSAGE_BUBBLE_WIDTH_RATIO = 0.7", WINDOW_SOURCE)
        self.assertIn("LONG_MESSAGE_CHAR_THRESHOLD = 20_000", WINDOW_SOURCE)
        self.assertIn("LONG_MESSAGE_VIEW_LINES = 32", WINDOW_SOURCE)
        self.assertIn("STREAMING_MESSAGE_VIEW_LINES = 18", WINDOW_SOURCE)
        self.assertIn("def _append_stream_delta", WINDOW_SOURCE)
        self.assertIn("text_scrollbar", WINDOW_SOURCE)
        self.assertIn("_stc_fixed_height", WINDOW_SOURCE)
        self.assertIn('syntax_highlight=message.status != "thinking"', WINDOW_SOURCE)
        self.assertNotIn("def _fit_visible_text_widgets", WINDOW_SOURCE)
        self.assertNotIn("for delay in (30, 120)", WINDOW_SOURCE)
        self.assertIn("def _fit_text_widget_to_content", WINDOW_SOURCE)
        self.assertIn('"displaylines"', WINDOW_SOURCE)
        self.assertIn("outer chat owns scrolling", WINDOW_SOURCE)
        self.assertIn("def _schedule_text_fit", WINDOW_SOURCE)

    def test_borderless_window_remains_resizable_and_has_keyboard_shortcuts(self):
        self.assertIn("self.resize_grip", WINDOW_SOURCE)
        self.assertIn("def _start_resize", WINDOW_SOURCE)
        self.assertIn("def _do_resize", WINDOW_SOURCE)
        self.assertIn('self.bind("<Control-w>"', WINDOW_SOURCE)
        self.assertIn('self.bind("<Control-n>"', WINDOW_SOURCE)
        self.assertIn('self.bind("<Control-l>"', WINDOW_SOURCE)

    def test_message_refresh_is_incremental_not_full_destroy(self):
        refresh_block = WINDOW_SOURCE.split("def _refresh_messages", 1)[1].split("def _clear_message_widgets", 1)[0]
        self.assertNotIn("for widget in self.message_frame.winfo_children():", refresh_block)
        self.assertNotIn("widget.destroy()", refresh_block)
        self.assertIn("_message_widgets", WINDOW_SOURCE)



    def test_show_path_defers_heavy_refresh_after_deiconify(self):
        self.assertIn("def show_fast", WINDOW_SOURCE)
        self.assertIn("deiconify()", WINDOW_SOURCE)
        self.assertIn("after(15", WINDOW_SOURCE)

    def test_window_can_prewarm_hidden_for_hotkey_latency(self):
        self.assertIn("def prewarm_hidden", WINDOW_SOURCE)
        self.assertIn("withdraw()", WINDOW_SOURCE)
        init_block = WINDOW_SOURCE.split("def __init__", 1)[1].split("def _setup_window", 1)[0]
        self.assertIn("self.withdraw()", init_block)


    def test_main_prewarms_screenshot_chat_after_startup_through_module_facade(self):
        self.assertIn("self.root.after(250, self._prewarm_screenshot_chat)", MAIN_SOURCE)
        self.assertIn("def _ensure_screenshot_chat_module", MAIN_SOURCE)
        self.assertIn("ChatbotModule(", MAIN_SOURCE)
        self.assertIn(".prewarm()", MAIN_SOURCE)

    def test_controller_show_path_uses_ensure_window_without_sync_refresh(self):
        self.assertIn("def _ensure_window", CONTROLLER_SOURCE)
        self.assertIn("def prewarm_window", CONTROLLER_SOURCE)
        open_block = CONTROLLER_SOURCE.split("def open_or_focus", 1)[1].split("def prewarm_window", 1)[0]
        self.assertNotIn("refresh_window", open_block)


    def test_composer_is_dedicated_component_not_window_inline_layout(self):
        self.assertIn("class ScreenshotChatComposer", COMPOSER_SOURCE)
        self.assertIn("self.composer = ScreenshotChatComposer", WINDOW_SOURCE)
        self.assertNotIn("self.input_panel = customtkinter.CTkFrame", WINDOW_SOURCE)
        self.assertNotIn("self.attachment_frame = customtkinter.CTkScrollableFrame", WINDOW_SOURCE)

    def test_composer_owns_attachment_tray_with_safe_preview_height(self):
        self.assertIn("attachment_tray", COMPOSER_SOURCE)
        self.assertIn('orientation="horizontal"', COMPOSER_SOURCE)
        self.assertIn("ATTACHMENT_PREVIEW_SIZE", COMPOSER_SOURCE)
        self.assertIn("_attachment_chip_height", COMPOSER_SOURCE)

    def test_composer_exposes_small_public_api_for_window(self):
        self.assertIn("def refresh", COMPOSER_SOURCE)
        self.assertIn("def focus", COMPOSER_SOURCE)
        self.assertIn("def bind_drop_target", COMPOSER_SOURCE)
        self.assertIn("def _send_current_message", COMPOSER_SOURCE)

    def test_composer_has_send_and_stop_as_separate_controls(self):
        self.assertIn("self.stop_button", COMPOSER_SOURCE)
        self.assertIn("command=self._send_current_message", COMPOSER_SOURCE)
        self.assertIn("command=self._stop_current_jobs", COMPOSER_SOURCE)
        self.assertIn("self.controller.stop_active_session_jobs()", COMPOSER_SOURCE)
        self.assertNotIn("def _send_or_stop", COMPOSER_SOURCE)

if __name__ == "__main__":
    unittest.main()


