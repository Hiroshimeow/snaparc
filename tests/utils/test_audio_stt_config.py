import json
import os
import shutil
import tempfile
import unittest

from src.utils.ConfigManager import ConfigManager


class ConfigManagerAudioSTTConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="audio_stt_cfg_")
        self.config_path = os.path.join(self.temp_dir, "config.yaml")
        ConfigManager._instance = None

    def tearDown(self):
        ConfigManager._instance = None
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write(self, payload):
        with open(self.config_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)

    def test_missing_audio_config_gets_neutral_defaults(self):
        self._write({})
        cfg = ConfigManager(self.config_path)
        self.assertEqual(cfg.get("audio.stt.preferred_model"), "")
        self.assertEqual(cfg.get("audio.stt.audio_capable_models"), [])
        self.assertIsNone(cfg.get("audio.stt.fallback_model"))
        self.assertEqual(cfg.get("audio.stt.max_retry"), 2)
        self.assertTrue(cfg.get("audio.stt.continue_on_error"))
        self.assertEqual(cfg.get("audio.recording.max_minutes"), 10)

    def test_explicit_audio_stt_values_are_preserved(self):
        self._write(
            {
                "audio": {
                    "recording": {"max_minutes": 25},
                    "stt": {
                        "preferred_model": "provider/audio-model",
                        "audio_capable_models": ["provider/audio-model"],
                        "max_retry": 1,
                        "continue_on_error": False,
                        "max_workers": 2,
                        "parallel_safe_provider_apis": [],
                        "prompt": "Transcribe exactly.",
                        "postprocess": {"mode": "none", "model": "", "timeout_seconds": 30},
                    },
                }
            }
        )
        cfg = ConfigManager(self.config_path)
        self.assertEqual(cfg.get("audio.stt.preferred_model"), "provider/audio-model")
        self.assertEqual(cfg.get("audio.stt.audio_capable_models"), ["provider/audio-model"])
        self.assertIsNone(cfg.get("audio.stt.fallback_model"))
        self.assertEqual(cfg.get("audio.stt.max_retry"), 1)
        self.assertFalse(cfg.get("audio.stt.continue_on_error"))
        self.assertEqual(cfg.get("audio.recording.max_minutes"), 25)
        self.assertEqual(cfg.get("audio.stt.postprocess.mode"), "none")

    def test_partial_audio_config_merges_only_current_defaults(self):
        self._write(
            {
                "audio": {
                    "stt": {
                        "preferred_model": "provider/audio-model",
                        "audio_capable_models": ["provider/audio-model"],
                    }
                }
            }
        )
        cfg = ConfigManager(self.config_path)
        self.assertEqual(cfg.get("audio.stt.preferred_model"), "provider/audio-model")
        self.assertEqual(cfg.get("audio.stt.audio_capable_models"), ["provider/audio-model"])
        self.assertIsNone(cfg.get("audio.stt.fallback_model"))
        self.assertEqual(cfg.get("audio.stt.max_workers"), 4)
        self.assertEqual(cfg.get("audio.stt.preprocess.provider"), "energy")


if __name__ == "__main__":
    unittest.main()
