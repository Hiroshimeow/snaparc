import json
import os
import shutil
import tempfile
import unittest

from src.utils.ConfigManager import ConfigManager


class ConfigManagerAudioSTTConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="config_manager_audio_stt_")
        self.config_path = os.path.join(self.temp_dir, "stc.json")
        ConfigManager._instance = None

    def tearDown(self):
        ConfigManager._instance = None
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write_config(self, data):
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def test_legacy_config_returns_default_audio_stt_values(self):
        self._write_config(
            {
                "models": {"providers": {}},
                "agents": {
                    "defaults": {
                        "model": {"primary": "google-gemini-cli/gemini-3-flash-preview", "fallbacks": []},
                        "prompt_type": "prompt_fix_grammar",
                    }
                },
                "hotkeys": [],
                "ui": {"animation_speed": 0.1, "ani_folder": "src/animate"},
            }
        )

        cfg = ConfigManager(self.config_path)

        self.assertEqual(cfg.get("audio.stt.preferred_model"), "")
        self.assertIn(cfg.get("audio.stt.fallback_model"), ("google-gemini-cli/gemini-3-flash", "cliproxy/gemini-2.5-flash"))
        self.assertEqual(cfg.get("audio.stt.audio_capable_models"), [])
        self.assertEqual(cfg.get("audio.stt.max_retry"), 2)
        self.assertTrue(cfg.get("audio.stt.continue_on_error"))

    def test_legacy_config_returns_recording_split_default(self):
        self._write_config({"models": {"providers": {}}})

        cfg = ConfigManager(self.config_path)

        self.assertEqual(cfg.get("audio.recording"), {"max_minutes": 10})

    def test_explicit_audio_stt_values_are_preserved(self):
        self._write_config(
            {
                "models": {"providers": {}},
                "audio": {
                    "stt": {
                        "preferred_model": "omni/gemini-3-flash",
                        "fallback_model": "google-gemini-cli/gemini-3-flash",
                        "audio_capable_models": ["omni/gemini-3-flash"],
                        "max_retry": 5,
                        "continue_on_error": False,
                    }
                },
            }
        )

        cfg = ConfigManager(self.config_path)

        self.assertEqual(cfg.get("audio.stt.preferred_model"), "omni/gemini-3-flash")
        self.assertIn(cfg.get("audio.stt.fallback_model"), ("google-gemini-cli/gemini-3-flash", "cliproxy/gemini-2.5-flash"))
        self.assertEqual(cfg.get("audio.stt.audio_capable_models"), ["omni/gemini-3-flash"])
        self.assertEqual(cfg.get("audio.stt.max_retry"), 5)
        self.assertFalse(cfg.get("audio.stt.continue_on_error"))

    def test_partially_populated_audio_stt_values_fall_back_for_missing_keys(self):
        self._write_config(
            {
                "models": {"providers": {}},
                "audio": {
                    "stt": {
                        "preferred_model": "omni/gemini-3-flash",
                        "audio_capable_models": ["omni/gemini-3-flash"],
                    }
                },
            }
        )

        cfg = ConfigManager(self.config_path)

        self.assertEqual(cfg.get("audio.stt.preferred_model"), "omni/gemini-3-flash")
        self.assertEqual(cfg.get("audio.stt.audio_capable_models"), ["omni/gemini-3-flash"])
        self.assertIn(cfg.get("audio.stt.fallback_model"), ("google-gemini-cli/gemini-3-flash", "cliproxy/gemini-2.5-flash"))
        self.assertEqual(cfg.get("audio.stt.max_retry"), 2)
        self.assertTrue(cfg.get("audio.stt.continue_on_error"))
