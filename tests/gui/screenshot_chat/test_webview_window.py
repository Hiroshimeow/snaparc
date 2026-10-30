import unittest
from pathlib import Path


WINDOW_SOURCE = Path("src/gui/screenshot_chat/webview_window.py").read_text(encoding="utf-8") if Path("src/gui/screenshot_chat/webview_window.py").exists() else ""
APP_SOURCE = Path("src/gui/screenshot_chat/webview_app.py").read_text(encoding="utf-8") if Path("src/gui/screenshot_chat/webview_app.py").exists() else ""


class ScreenshotChatWebViewWindowSourceTests(unittest.TestCase):
    def test_webview_window_is_process_managed_client_wrapper(self):
        self.assertIn("class ScreenshotChatWebViewWindow", WINDOW_SOURCE)
        self.assertIn("ScreenshotChatProcessManager", WINDOW_SOURCE)
        self.assertIn("self.manager = ScreenshotChatProcessManager", WINDOW_SOURCE)

    def test_webview_window_exposes_tk_compatible_lifecycle_methods(self):
        for method in ("show", "hide", "is_visible", "refresh_all", "winfo_exists", "prewarm_hidden", "shutdown"):
            self.assertIn(f"def {method}", WINDOW_SOURCE)
        self.assertIn("self.manager.show()", WINDOW_SOURCE)
        self.assertIn("self.manager.hide()", WINDOW_SOURCE)
        self.assertIn("self.manager.shutdown()", WINDOW_SOURCE)

    def test_child_bridge_does_not_expose_native_window_to_js_api(self):
        bridge_block = APP_SOURCE.split("class ScreenshotChatChildBridge", 1)[1].split("class WindowCommandPoller", 1)[0]
        self.assertIn("self._window", bridge_block)
        self.assertIn("def set_window", bridge_block)
        self.assertNotIn("self.window", bridge_block)
        self.assertIn("bridge.set_window(window)", APP_SOURCE)
        self.assertNotIn("bridge.window = window", APP_SOURCE)

    def test_child_loads_server_page_same_origin_with_launch_token(self):
        self.assertIn("url=page_url", APP_SOURCE)
        self.assertNotIn("html=", APP_SOURCE)
        self.assertIn("/?t={args.token}", APP_SOURCE)
        self.assertIn('"X-STC-Token"', APP_SOURCE)
        self.assertIn("/ui", APP_SOURCE)
        self.assertNotIn("/state", APP_SOURCE)


if __name__ == "__main__":
    unittest.main()
