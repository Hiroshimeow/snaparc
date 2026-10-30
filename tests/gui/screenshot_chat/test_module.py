import os
import unittest
from types import SimpleNamespace
from unittest import mock


class ScreenshotChatModuleTests(unittest.TestCase):
    def test_facade_owns_one_controller_and_delegates_lifecycle(self):
        from src.gui.screenshot_chat.module import ChatbotModule

        controller = mock.MagicMock()
        with mock.patch(
            "src.gui.screenshot_chat.module.ScreenshotChatController",
            return_value=controller,
        ) as controller_cls:
            module = ChatbotModule(
                root="root",
                config_manager="config",
                project_root="/repo",
                held_images_getter="getter",
                held_images_consumer="consumer",
                llm_service="llm",
                model_client_factory="model_client_factory",
            )

        controller_cls.assert_called_once_with(
            "root",
            config_manager="config",
            project_root="/repo",
            held_images_getter="getter",
            held_images_consumer="consumer",
            llm_service="llm",
            model_client_factory="model_client_factory",
        )
        module.prewarm()
        module.show()
        module.hide()
        module.toggle()
        module.shutdown()
        controller.prewarm_window.assert_called_once_with()
        controller.open_or_focus.assert_called_once_with()
        controller.hide_window.assert_called_once_with()
        controller.toggle_window.assert_called_once_with()
        controller.shutdown.assert_called_once_with()
        self.assertIs(module.controller, controller)

    def _module_with_fake_controller(self):
        from src.gui.screenshot_chat.module import ChatbotModule

        controller = mock.MagicMock()
        with mock.patch("src.gui.screenshot_chat.module.ScreenshotChatController", return_value=controller):
            module = ChatbotModule(root="root", config_manager="config", project_root="/repo")
        return module, controller

    def test_run_hotkey_action_toggle_and_show_modes(self):
        module, controller = self._module_with_fake_controller()

        module.run_hotkey_action("toggle")
        controller.toggle_window.assert_called_once_with()
        controller.open_or_focus.assert_not_called()

        module.run_hotkey_action("show")
        controller.open_or_focus.assert_called_once_with()
        controller.toggle_window.assert_called_once_with()
        controller.import_held_snaps.assert_not_called()
        controller.import_clipboard_image.assert_not_called()

    def test_run_hotkey_action_show_import_held_opens_then_imports_held_snaps(self):
        module, controller = self._module_with_fake_controller()
        order = []
        controller.open_or_focus.side_effect = lambda: order.append("open")
        controller.import_held_snaps.side_effect = lambda: order.append("held")

        module.run_hotkey_action("show_import_held")

        self.assertEqual(order, ["open", "held"])
        controller.toggle_window.assert_not_called()
        controller.import_clipboard_image.assert_not_called()

    def test_run_hotkey_action_show_import_clipboard_opens_then_imports_clipboard(self):
        module, controller = self._module_with_fake_controller()
        order = []
        controller.open_or_focus.side_effect = lambda: order.append("open")
        controller.import_clipboard_image.side_effect = lambda **kwargs: order.append(("clip", kwargs))

        module.run_hotkey_action("show_import_clipboard")

        self.assertEqual(order, ["open", ("clip", {"show_empty_status": True})])
        controller.toggle_window.assert_not_called()
        controller.import_held_snaps.assert_not_called()

    def test_run_hotkey_action_unknown_mode_falls_back_to_toggle(self):
        module, controller = self._module_with_fake_controller()

        with mock.patch("builtins.print") as printed:
            module.run_hotkey_action("bogus")

        controller.toggle_window.assert_called_once_with()
        controller.open_or_focus.assert_not_called()
        self.assertIn("bogus", str(printed.call_args))

    def test_standalone_resolves_repo_stc_json_independent_of_cwd(self):
        from src.gui.screenshot_chat.standalone import resolve_config_path, resolve_project_root

        project_root = resolve_project_root()
        self.assertTrue(os.path.isabs(project_root))
        self.assertEqual(resolve_config_path(), os.path.join(project_root, "config.yaml"))

    def test_standalone_builds_config_before_runtime_dependencies(self):
        from src.gui.screenshot_chat import standalone

        calls = []
        config = mock.MagicMock()
        model_client = mock.MagicMock()
        model_client.llm_service = "llm"
        module = mock.MagicMock()

        with mock.patch.object(standalone, "ConfigManager", side_effect=lambda path: calls.append(("config", path)) or config), \
             mock.patch.object(standalone, "ModelClient", side_effect=lambda config: calls.append(("model_client", config)) or model_client), \
             mock.patch.object(standalone, "ChatbotModule", side_effect=lambda *args, **kwargs: calls.append(("module", kwargs["project_root"])) or module):
            result = standalone.build_module("root")

        self.assertIs(result, module)
        self.assertEqual(calls[0], ("config", standalone.resolve_config_path()))
        self.assertEqual(calls[1][0], "model_client")
        self.assertEqual(calls[2][0], "module")

    def test_standalone_main_shows_and_shutdowns_same_module(self):
        from src.gui.screenshot_chat import standalone

        root = mock.MagicMock()
        module = mock.MagicMock()
        with mock.patch.object(standalone.tk, "Tk", return_value=root), \
             mock.patch.object(standalone, "build_module", return_value=module):
            result = standalone.main()

        self.assertEqual(result, 0)
        root.withdraw.assert_called_once_with()
        module.show.assert_called_once_with()
        root.mainloop.assert_called_once_with()
        module.shutdown.assert_called_once_with()
        root.destroy.assert_called_once_with()

    def test_standalone_mainloop_exits_after_only_window_is_hidden(self):
        from src.gui.screenshot_chat import standalone

        class FakeWindow:
            def __init__(self):
                self.visibility_checks = 0

            def winfo_exists(self):
                return True

            def is_visible(self):
                self.visibility_checks += 1
                return self.visibility_checks == 1

        class FakeRoot:
            def __init__(self):
                self.callbacks = []
                self.quit_called = False
                self.destroy_called = False
                self.withdraw_called = False

            def withdraw(self):
                self.withdraw_called = True

            def after(self, _delay_ms, callback):
                self.callbacks.append(callback)

            def mainloop(self):
                if not self.callbacks:
                    raise AssertionError("standalone must schedule lifecycle observation before mainloop")
                for _ in range(5):
                    callback = self.callbacks.pop(0)
                    callback()
                    if self.quit_called:
                        return
                    if not self.callbacks:
                        break
                raise AssertionError("standalone mainloop did not exit after the chat window hid")

            def quit(self):
                self.quit_called = True

            def destroy(self):
                self.destroy_called = True

        class FakeModule:
            def __init__(self):
                self.controller = SimpleNamespace(window=FakeWindow())
                self.show_called = False
                self.shutdown_called = False

            def show(self):
                self.show_called = True

            def shutdown(self):
                self.shutdown_called = True

        root = FakeRoot()
        module = FakeModule()
        with mock.patch.object(standalone.tk, "Tk", return_value=root), \
             mock.patch.object(standalone, "build_module", return_value=module):
            result = standalone.main()

        self.assertEqual(result, 0)
        self.assertTrue(root.withdraw_called)
        self.assertTrue(root.quit_called)
        self.assertTrue(root.destroy_called)
        self.assertTrue(module.show_called)
        self.assertTrue(module.shutdown_called)


if __name__ == "__main__":
    unittest.main()
