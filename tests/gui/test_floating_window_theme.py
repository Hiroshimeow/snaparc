import types
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

import tempfile

from src.gui import FloatingWindow as floating_module
from src.gui.FloatingWindow import ConfigManager, FloatingWindow


class FloatingWindowThemeConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.config_path = str(Path(self.temp_dir.name) / "float_config.xml")
        patcher = mock.patch.object(floating_module, "CONFIG_FILE", self.config_path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _stored_theme(self):
        return ET.parse(self.config_path).getroot().findtext("theme")

    def test_missing_config_defaults_to_dark_and_writes_file_without_recursion(self):
        config = ConfigManager.load_config()

        self.assertEqual(config["theme"], "dark")
        self.assertTrue(Path(self.config_path).exists())
        self.assertEqual(self._stored_theme(), "dark")

    def test_bogus_theme_in_file_falls_back_to_dark(self):
        Path(self.config_path).write_text(
            "<config><x>1</x><y>2</y><width>300</width><height>200</height>"
            "<hotkey>Ctrl+Shift+Z</hotkey><theme>bogus</theme></config>",
            encoding="utf-8",
        )

        self.assertEqual(ConfigManager.load_config()["theme"], "dark")

    def test_save_config_without_theme_preserves_stored_light_theme(self):
        ConfigManager.save_config(1, 2, 300, 200, hotkey="Ctrl+Shift+Z", theme="light")

        ConfigManager.save_config(10, 20, 320, 220)

        self.assertEqual(self._stored_theme(), "light")
        self.assertEqual(ConfigManager.load_config()["theme"], "light")

    def test_save_config_rejects_unknown_theme(self):
        ConfigManager.save_config(1, 2, 300, 200, hotkey="Ctrl+Shift+Z", theme="neon")

        self.assertEqual(self._stored_theme(), "dark")

    def test_theme_menu_label_names_the_other_mode(self):
        window = FloatingWindow.__new__(FloatingWindow)

        window.theme_name = "dark"
        self.assertEqual(window._theme_menu_label(), "Switch to Light Mode")

        window.theme_name = "light"
        self.assertEqual(window._theme_menu_label(), "Switch to Dark Mode")


class _FakeButton:
    def __init__(self):
        self.values = {}

    def config(self, **kwargs):
        self.values.update(kwargs)


class FloatingWindowThemeFollowUpTests(unittest.TestCase):
    def _fake_window(self, theme_name="dark", exists=True):
        window = FloatingWindow.__new__(FloatingWindow)
        window.theme_name = theme_name
        window.config = {"theme": theme_name, "hotkey": "Ctrl+Shift+Z"}
        window.win = mock.MagicMock()
        window.win.winfo_exists.return_value = exists
        window._apply_theme = mock.MagicMock()
        return window

    def test_toggle_theme_applies_to_other_live_windows(self):
        first = self._fake_window("dark")
        second = self._fake_window("dark")
        closed = self._fake_window("dark", exists=False)
        broken = self._fake_window("dark")
        broken._apply_theme.side_effect = RuntimeError("tk gone")

        with mock.patch.object(floating_module, "FLOAT_WINDOW_LIST", [first, closed, broken, second]), \
             mock.patch.object(ConfigManager, "save_config") as save, \
             mock.patch("builtins.print"):
            first.toggle_theme()

        self.assertEqual(first.theme_name, "light")
        first._apply_theme.assert_called_once_with()
        for other in (second, broken):
            self.assertEqual(other.theme_name, "light")
            self.assertEqual(other.config["theme"], "light")
            other._apply_theme.assert_called_once_with()
        closed._apply_theme.assert_not_called()
        self.assertEqual(closed.theme_name, "dark")
        self.assertEqual(save.call_args.kwargs["theme"], "light")

    def test_apply_theme_refreshes_stt_button_colour(self):
        window = FloatingWindow.__new__(FloatingWindow)
        window.theme_name = "dark"
        window.refresh_audio_stt_state = mock.MagicMock()
        for name in (
            "win", "header", "body", "textbox", "scrollbar", "info_label", "file_label", "timer_label",
            "follow_status_label", "nav_counter_label", "resizer_left", "resizer_right", "close_btn",
            "record_btn", "pause_btn", "transcribe_btn", "follow_btn", "pin_btn", "nav_back_btn",
            "nav_next_btn", "context_menu",
        ):
            setattr(window, name, mock.MagicMock())

        window._apply_theme()

        window.refresh_audio_stt_state.assert_called_once_with()

    def test_refresh_audio_stt_state_idle_colour_follows_theme(self):
        window = FloatingWindow.__new__(FloatingWindow)
        window.transcribe_btn = _FakeButton()
        window.packet = types.SimpleNamespace(get_audio_stt_state_callback=lambda: {"running": False})

        window.theme = floating_module.FLOAT_UI_THEMES["dark"]
        window.refresh_audio_stt_state()
        self.assertEqual(window.transcribe_btn.values["fg"], "#64B5F6")

        window.theme = floating_module.FLOAT_UI_THEMES["light"]
        window.refresh_audio_stt_state()
        self.assertEqual(window.transcribe_btn.values["fg"], "#1565C0")

    def test_refresh_audio_stt_state_running_colour_is_theme_independent(self):
        window = FloatingWindow.__new__(FloatingWindow)
        window.transcribe_btn = _FakeButton()
        window.theme = floating_module.FLOAT_UI_THEMES["dark"]
        window.packet = types.SimpleNamespace(get_audio_stt_state_callback=lambda: {"running": True})

        window.refresh_audio_stt_state()

        self.assertEqual(window.transcribe_btn.values["fg"], "#C62828")


if __name__ == "__main__":
    unittest.main()
