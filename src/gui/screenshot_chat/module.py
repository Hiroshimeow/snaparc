from __future__ import annotations

from src.gui.screenshot_chat.controller import ScreenshotChatController


HOTKEY_MODES = ("toggle", "show", "show_import_held", "show_import_clipboard")


class ChatbotModule:
    """Reusable lifecycle facade for Screenshot Chat (Ctrl + Shift + Alt + X)."""

    def __init__(
        self,
        root,
        *,
        config_manager,
        project_root: str,
        held_images_getter=None,
        held_images_consumer=None,
        llm_service=None,
        model_client_factory=None,
        model_state_store=None,
    ):
        extra = {"model_state_store": model_state_store} if model_state_store is not None else {}
        self.controller = ScreenshotChatController(
            root,
            config_manager=config_manager,
            project_root=project_root,
            held_images_getter=held_images_getter,
            held_images_consumer=held_images_consumer,
            llm_service=llm_service,
            model_client_factory=model_client_factory,
            **extra,
        )

    def prewarm(self):
        return self.controller.prewarm_window()

    def show(self):
        return self.controller.open_or_focus()

    def hide(self):
        return self.controller.hide_window()

    def toggle(self):
        return self.controller.toggle_window()

    def run_hotkey_action(self, mode: str = "toggle"):
        mode = str(mode or "toggle")
        if mode not in HOTKEY_MODES:
            print(f"ScreenshotChat: unknown hotkey mode {mode!r}; falling back to toggle.")
            mode = "toggle"
        if mode == "toggle":
            return self.controller.toggle_window()
        self.controller.open_or_focus()
        if mode == "show_import_held":
            return self.controller.import_held_snaps()
        if mode == "show_import_clipboard":
            return self.controller.import_clipboard_image(show_empty_status=True)
        return None

    def shutdown(self):
        return self.controller.shutdown()
