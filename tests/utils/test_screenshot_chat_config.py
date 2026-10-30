import json
import os
import shutil
import tempfile
import unittest

from src.utils.ConfigManager import ConfigManager


class ConfigManagerScreenshotChatConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="config_manager_screenshot_chat_")
        self.config_path = os.path.join(self.temp_dir, "stc.json")
        ConfigManager._instance = None

    def tearDown(self):
        ConfigManager._instance = None
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write_config(self, data):
        with open(self.config_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle)

    def test_legacy_config_gets_screenshot_chat_defaults_and_hotkey(self):
        self._write_config({"models": {"providers": {}}, "hotkeys": []})

        cfg = ConfigManager(self.config_path)

        chat_config = cfg.get_screenshot_chat_config()
        self.assertTrue(chat_config["enabled"])
        self.assertEqual(chat_config["hotkey"], "ctrl + shift + alt + x")
        self.assertEqual(chat_config["runtime"]["mode"], "tk")
        self.assertFalse(chat_config["runtime"]["legacy_webview_enabled"])
        self.assertEqual(chat_config["runtime"]["gui"], "edgechromium")
        self.assertFalse(chat_config["runtime"]["fallback_tk_debug"])
        self.assertEqual(cfg.get("hotkeys"), [])
        self.assertEqual(
            cfg.get_hotkey_configs()[0],
            {
                "id": "ScreenshotChatPopout",
                "keys": "ctrl + shift + alt + x",
                "command": "open_screenshot_chat",
                "mode": "toggle",
            },
        )

    def test_screenshot_chat_hotkey_mode_is_propagated_to_hotkey_entry(self):
        self._write_config(
            {
                "models": {"providers": {}},
                "screenshot_chat": {"hotkey_mode": "show_import_held"},
                "hotkeys": [],
            }
        )

        cfg = ConfigManager(self.config_path)

        self.assertEqual(cfg.get_hotkey_configs()[0]["mode"], "show_import_held")

    def test_explicit_hotkey_entry_mode_wins_over_screenshot_chat_hotkey_mode(self):
        self._write_config(
            {
                "models": {"providers": {}},
                "screenshot_chat": {"hotkey_mode": "show"},
                "hotkeys": [
                    {
                        "id": "ScreenshotChatPopout",
                        "keys": "ctrl + shift + alt + x",
                        "command": "open_screenshot_chat",
                        "mode": "show_import_clipboard",
                    }
                ],
            }
        )

        cfg = ConfigManager(self.config_path)

        self.assertEqual(cfg.get_hotkey_configs()[0]["mode"], "show_import_clipboard")

    def test_screenshot_chat_hotkey_updates_existing_entry(self):
        self._write_config(
            {
                "models": {"providers": {}},
                "screenshot_chat": {"hotkey": "ctrl + alt + x"},
                "hotkeys": [
                    {
                        "id": "ScreenshotChatPopout",
                        "keys": "ctrl + shift + alt + x",
                        "command": "open_screenshot_chat",
                    }
                ],
            }
        )

        cfg = ConfigManager(self.config_path)

        self.assertEqual(cfg.get("hotkeys")[0]["keys"], "ctrl + shift + alt + x")
        self.assertEqual(cfg.get_hotkey_configs()[0]["keys"], "ctrl + alt + x")

    def test_runtime_defaults_are_merged_into_partial_screenshot_chat_config(self):
        self._write_config(
            {
                "models": {"providers": {}},
                "screenshot_chat": {"runtime": {"poll_interval_ms": 1200}},
            }
        )

        cfg = ConfigManager(self.config_path)

        runtime = cfg.get_screenshot_chat_config()["runtime"]
        self.assertEqual(runtime["mode"], "tk")
        self.assertFalse(runtime["legacy_webview_enabled"])
        self.assertEqual(runtime["gui"], "edgechromium")
        self.assertEqual(runtime["poll_interval_ms"], 1200)
        self.assertEqual(runtime["startup_timeout_ms"], 6000)

    def test_pi_backend_defaults_to_auto_and_normalizes_case_whitespace(self):
        self._write_config({"models": {"providers": {}}, "screenshot_chat": {"pi": {"backend": "  SDK  "}}})
        cfg = ConfigManager(self.config_path)
        self.assertEqual(cfg.get_screenshot_chat_config()["pi"]["backend"], "sdk")

        cfg.config["screenshot_chat"]["pi"].pop("backend")
        self.assertIn(cfg.get_screenshot_chat_config()["pi"]["backend"], ("auto", "rpc"))

    def test_invalid_pi_backend_normalizes_to_auto(self):
        self._write_config({"models": {"providers": {}}, "screenshot_chat": {"pi": {"backend": "future"}}})
        cfg = ConfigManager(self.config_path)
        self.assertEqual(cfg.get_screenshot_chat_config()["pi"]["backend"], "auto")

    def test_pi_defaults_are_merged_into_partial_screenshot_chat_config(self):
        self._write_config(
            {
                "models": {"providers": {}},
                "screenshot_chat": {"pi": {"enabled": True}},
            }
        )

        cfg = ConfigManager(self.config_path)

        pi_config = cfg.get_screenshot_chat_config()["pi"]
        self.assertTrue(pi_config["enabled"])
        self.assertIn(pi_config["backend"], ("auto", "rpc"))
        self.assertTrue(pi_config["auto_detect"])
        self.assertEqual(pi_config["agent_names"], ["pi"])
        self.assertEqual(pi_config["approval_mode"], "tk_modal")
        self.assertEqual(pi_config["approval_timeout_ms"], 120000)
        self.assertIn(pi_config["tool_policy"], ("read_only", "approve_mutations", "workspace_write"))
        self.assertEqual(pi_config["trusted_roots"], ["."])
        self.assertFalse(pi_config["crash_fallback_to_llm"])

    def test_tts_defaults_are_merged_into_partial_screenshot_chat_config(self):
        self._write_config(
            {
                "models": {"providers": {}},
                "screenshot_chat": {"tts": {"auto_read": True, "voice": "custom_voice"}},
            }
        )

        cfg = ConfigManager(self.config_path)

        tts = cfg.get_screenshot_chat_config()["tts"]
        self.assertEqual(tts["base_url"], "https://tts-r28-edge.hcu-lab.me")
        self.assertEqual(tts["api_key"], "")
        self.assertEqual(tts["voice"], "custom_voice")
        self.assertEqual(tts["language"], "vi")
        self.assertTrue(tts["auto_read"])
        self.assertEqual(tts["timeout_seconds"], 60)

    def test_webview_process_runtime_does_not_require_legacy_opt_in(self):
        self._write_config(
            {
                "models": {"providers": {}},
                "screenshot_chat": {"runtime": {"mode": "webview-process"}},
            }
        )

        cfg = ConfigManager(self.config_path)

        runtime = cfg.get_screenshot_chat_config()["runtime"]
        self.assertEqual(runtime["mode"], "webview-process")
        self.assertFalse(runtime["legacy_webview_enabled"])

    def test_explicit_tk_runtime_remains_supported(self):
        self._write_config(
            {
                "models": {"providers": {}},
                "screenshot_chat": {"runtime": {"mode": "tk"}},
            }
        )

        cfg = ConfigManager(self.config_path)

        runtime = cfg.get_screenshot_chat_config()["runtime"]
        self.assertEqual(runtime["mode"], "tk")

    def test_legacy_webview_runtime_requires_explicit_opt_in(self):
        self._write_config(
            {
                "models": {"providers": {}},
                "screenshot_chat": {
                    "runtime": {
                        "mode": "webview-process",
                        "legacy_webview_enabled": True,
                    }
                },
            }
        )

        cfg = ConfigManager(self.config_path)

        runtime = cfg.get_screenshot_chat_config()["runtime"]
        self.assertEqual(runtime["mode"], "webview-process")
        self.assertTrue(runtime["legacy_webview_enabled"])


if __name__ == "__main__":
    unittest.main()
