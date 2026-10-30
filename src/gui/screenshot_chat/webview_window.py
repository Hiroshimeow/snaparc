from __future__ import annotations

from src.gui.screenshot_chat.process_manager import ScreenshotChatProcessManager


class ScreenshotChatWebViewUnavailable(RuntimeError):
    pass


class ScreenshotChatWebViewWindow:
    def __init__(self, controller):
        self.controller = controller
        self.manager = ScreenshotChatProcessManager(controller)

    def prewarm_hidden(self):
        self.manager.prewarm_hidden()

    def show(self):
        self.manager.show()

    def hide(self):
        self.manager.hide()

    def shutdown(self):
        self.manager.shutdown()

    def is_visible(self):
        return self.manager.visible

    def winfo_exists(self):
        return self.manager.is_alive()

    def refresh_all(self):
        return None

    def update_status(self, status):
        return None
