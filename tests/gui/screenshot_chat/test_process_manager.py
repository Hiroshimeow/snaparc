import unittest
from pathlib import Path
from unittest import mock

from src.gui.screenshot_chat.process_manager import ScreenshotChatProcessManager


class FakeController:
    project_root = "."

    def __init__(self):
        self.config = {
            "runtime": {
                "mode": "webview-process",
                "host": "127.0.0.1",
                "port": 0,
                "startup_timeout_ms": 1500,
                "poll_interval_ms": 900,
            }
        }

    def _config(self):
        return dict(self.config)


class FakeProcess:
    def __init__(self):
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 0

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.returncode = -9


class ScreenshotChatProcessManagerTests(unittest.TestCase):
    def test_resolve_child_python_prefers_project_venv_with_webview(self):
        controller = FakeController()
        manager = ScreenshotChatProcessManager(controller)

        with mock.patch("pathlib.Path.exists", autospec=True, side_effect=lambda self: str(self).endswith(".venv\\Scripts\\python.exe")):
            with mock.patch.object(manager, "_can_import_webview", side_effect=lambda path: path.endswith(".venv\\Scripts\\python.exe")):
                chosen = manager._resolve_child_python()

        self.assertTrue(chosen.endswith(".venv\\Scripts\\python.exe"))

    def test_spawn_child_passes_token_in_env_not_process_args(self):
        controller = FakeController()
        manager = ScreenshotChatProcessManager(controller)
        manager.server = mock.Mock(base_url="http://127.0.0.1:1234", token="s3cret-token")
        captured = {}

        def fake_popen(command, **kwargs):
            captured["command"] = list(command)
            captured["env"] = dict(kwargs["env"])
            ready_file = command[command.index("--ready-file") + 1]
            Path(ready_file).write_text("ready", encoding="utf-8")
            return FakeProcess()

        with mock.patch.object(manager, "_resolve_child_python", return_value="python.exe"),              mock.patch("src.gui.screenshot_chat.process_manager.subprocess.Popen", side_effect=fake_popen),              mock.patch("builtins.print") as printed:
            manager._spawn_child()

        command = captured["command"]
        self.assertNotIn("--token=s3cret-token", command)
        self.assertEqual(captured["env"]["SNAPARC_CHAT_SESSION_TOKEN"], "s3cret-token")
        logged = " ".join(str(call.args[0]) for call in printed.call_args_list if call.args)
        self.assertIn("child command=", logged)
        self.assertNotIn("s3cret-token", logged)
        self.assertNotIn("--token=", logged)

    def test_frozen_build_uses_snaparc_executable_child_mode(self):
        import sys
        controller = FakeController()
        manager = ScreenshotChatProcessManager(controller)
        manager.server = mock.Mock(base_url="http://127.0.0.1:1234", token="session-test")
        captured = {}

        def fake_popen(command, **kwargs):
            captured["command"] = list(command)
            captured["env"] = dict(kwargs["env"])
            Path(command[command.index("--ready-file") + 1]).write_text("ready", encoding="utf-8")
            return FakeProcess()

        with mock.patch.object(sys, "frozen", True, create=True), \
             mock.patch("src.gui.screenshot_chat.process_manager.subprocess.Popen", side_effect=fake_popen), \
             mock.patch("builtins.print"):
            manager._spawn_child()
        self.assertEqual(captured["command"][0], sys.executable)
        self.assertEqual(captured["command"][1], "--snaparc-webview-child")
        self.assertNotIn("-m", captured["command"])
        self.assertEqual(captured["env"]["SNAPARC_CHAT_SESSION_TOKEN"], "session-test")
        self.assertNotIn("session-test", " ".join(captured["command"]))

    def test_dash_prefixed_token_survives_child_argument_parsing(self):
        from src.gui.screenshot_chat import webview_app

        token = "-dash_prefixed-token"
        controller = FakeController()
        manager = ScreenshotChatProcessManager(controller)
        manager.server = mock.Mock(base_url="http://127.0.0.1:1234", token=token)
        captured = {}

        def fake_popen(command, **kwargs):
            captured["command"] = list(command)
            captured["env"] = dict(kwargs["env"])
            Path(command[command.index("--ready-file") + 1]).write_text("ready", encoding="utf-8")
            return FakeProcess()

        with mock.patch.object(manager, "_resolve_child_python", return_value="python.exe"), \
             mock.patch("src.gui.screenshot_chat.process_manager.subprocess.Popen", side_effect=fake_popen), \
             mock.patch("builtins.print") as printed:
            manager._spawn_child()

        command = captured["command"]
        self.assertEqual(command[2], "src.gui.screenshot_chat.webview_app")
        with mock.patch.dict("os.environ", captured["env"]), mock.patch("sys.argv", ["webview_app", *command[3:]]):
            args = webview_app._parse_args()
        self.assertEqual(captured["env"]["SNAPARC_CHAT_SESSION_TOKEN"], token)
        self.assertFalse(any("--token=" in arg for arg in command))
        logged = " ".join(str(call.args[0]) for call in printed.call_args_list if call.args)
        self.assertNotIn(token, logged)
        self.assertNotIn("--token=", logged)

        # The previous two-entry form is what argparse rejected for dash-prefixed tokens.
        legacy = [part for part in command[3:] if not part.startswith("--token=")] + ["--token", token]
        with mock.patch("sys.argv", ["webview_app", *legacy]), mock.patch("sys.stderr"):
            with self.assertRaises(SystemExit):
                webview_app._parse_args()

    def test_ensure_server_enforces_token_and_forwards_poll_interval(self):
        controller = FakeController()
        controller.config["runtime"]["poll_interval_ms"] = 450
        manager = ScreenshotChatProcessManager(controller)
        manager._config = controller._config()

        with mock.patch("src.gui.screenshot_chat.process_manager.ScreenshotChatServer") as server_cls:
            manager._ensure_server()

        self.assertEqual(server_cls.call_args.kwargs["poll_interval_ms"], 450)

    def test_show_starts_server_and_requests_visible_ui(self):
        controller = FakeController()
        manager = ScreenshotChatProcessManager(controller)

        with mock.patch("src.gui.screenshot_chat.process_manager.ScreenshotChatServer") as server_cls:
            server = server_cls.return_value
            server.base_url = "http://127.0.0.1:1234"
            with mock.patch.object(manager, "_spawn_child", side_effect=lambda: setattr(manager, "process", FakeProcess())):
                manager.show()

        server.start.assert_called_once_with()
        server.request_show.assert_called_once_with()
        self.assertTrue(manager.visible)

    def test_shutdown_stops_child_and_server(self):
        controller = FakeController()
        manager = ScreenshotChatProcessManager(controller)
        server = mock.Mock()
        manager.server = server
        manager.process = FakeProcess()

        manager.shutdown()

        server.request_hide.assert_called_once_with()
        server.stop.assert_called_once_with()
        self.assertIsNone(manager.process)
