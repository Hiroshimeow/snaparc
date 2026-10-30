
import types
import unittest
from unittest.mock import MagicMock

from src.gui.FloatingWindow import FloatingWindow


class _FakeButton:
    def __init__(self):
        self.values = {}

    def config(self, **kwargs):
        self.values.update(kwargs)


class FloatingWindowSttControlTests(unittest.TestCase):
    def test_trigger_stt_latest_passes_window_instance(self):
        window = FloatingWindow.__new__(FloatingWindow)
        calls = []
        window.packet = types.SimpleNamespace(
            transcribe_callback=lambda instance: calls.append(instance)
        )

        window.trigger_stt_latest()

        self.assertEqual(calls, [window])

    def test_trigger_stt_files_passes_window_as_parent(self):
        window = FloatingWindow.__new__(FloatingWindow)
        fake_win = object()
        calls = []
        window.win = fake_win
        window.packet = types.SimpleNamespace(
            select_audio_files_callback=lambda parent=None: calls.append(parent)
        )

        window.trigger_stt_files()

        self.assertEqual(calls, [fake_win])

    def test_refresh_audio_stt_state_sets_running_color(self):
        window = FloatingWindow.__new__(FloatingWindow)
        window.transcribe_btn = _FakeButton()
        window.packet = types.SimpleNamespace(
            get_audio_stt_state_callback=lambda: {"running": True, "job_count": 2}
        )

        window.refresh_audio_stt_state()

        self.assertEqual(window.transcribe_btn.values["fg"], "#C62828")
        self.assertEqual(window.transcribe_btn.values["activeforeground"], "#8E0000")

    def test_refresh_audio_stt_state_falls_back_to_idle_on_invalid_state(self):
        window = FloatingWindow.__new__(FloatingWindow)
        window.transcribe_btn = _FakeButton()
        window.packet = types.SimpleNamespace(get_audio_stt_state_callback=lambda: None)

        window.refresh_audio_stt_state()

        self.assertEqual(window.transcribe_btn.values["fg"], "#1565C0")
        self.assertEqual(window.transcribe_btn.values["activeforeground"], "#0D47A1")

    def test_entry_to_packet_preserves_stt_callbacks_from_current_packet(self):
        def transcribe_callback(*_args, **_kwargs):
            return None

        def select_audio_files_callback(*_args, **_kwargs):
            return None

        def get_audio_stt_state_callback():
            return {"running": False, "job_count": 0}

        window = FloatingWindow.__new__(FloatingWindow)
        window.packet = types.SimpleNamespace(
            rerun_prompt_callback=None,
            record_audio_callback=None,
            transcribe_callback=transcribe_callback,
            select_audio_files_callback=select_audio_files_callback,
            get_audio_duration_callback=None,
            pause_audio_callback=None,
            toggle_follow_callback=None,
            get_follow_state_callback=None,
            get_audio_stt_state_callback=get_audio_stt_state_callback,
        )

        packet = window._entry_to_packet(
            {
                "ans": "hello",
                "image_path": "img.png",
                "timestamp": "ts",
                "model_name": "model",
                "prompt_name": "prompt",
            }
        )

        self.assertIs(packet.transcribe_callback, transcribe_callback)
        self.assertIs(packet.select_audio_files_callback, select_audio_files_callback)
        self.assertIs(packet.get_audio_stt_state_callback, get_audio_stt_state_callback)

    def test_set_current_model_preserves_result_model_and_updates_label(self):
        window = FloatingWindow.__new__(FloatingWindow)
        window.win = types.SimpleNamespace(winfo_exists=lambda: True)
        window.packet = types.SimpleNamespace(api_key_index="?")
        window.model_name = "provider/model-a"
        window.current_model_name = "provider/model-a"
        window.prompt_name = "Expert"
        window.info_label = _FakeButton()

        window.set_current_model("provider/model-b")

        self.assertEqual(window.model_name, "provider/model-a")
        self.assertEqual(window.current_model_name, "provider/model-b")
        info_text = window.info_label.values["text"]
        self.assertIn("result: model-a", info_text)
        self.assertIn("current: model-b", info_text)

    def test_ctrl_n_saves_exact_nonblank_fixprompt_text(self):
        window = FloatingWindow.__new__(FloatingWindow)
        window.win = types.SimpleNamespace(
            winfo_exists=lambda: True,
            after=lambda *_args: None,
        )
        window.textbox = MagicMock()
        window.textbox.get.return_value = "  arbitrary\nprompt  "
        window.textbox.cget.return_value = "white"
        window._save_prompt_to_toml = MagicMock(return_value=True)

        result = window.handle_ctrl_n()

        self.assertEqual(result, "break")
        window.textbox.get.assert_called_once_with("1.0", "end-1c")
        window._save_prompt_to_toml.assert_called_once_with(
            key="prompt_fix_grammar", new_value="  arbitrary\nprompt  "
        )


if __name__ == "__main__":
    unittest.main()