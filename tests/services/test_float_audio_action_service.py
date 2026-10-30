import time
import types
import unittest

from src.services.float_audio_actions import FloatAudioActionService
from src.services.float_audio_service import FloatAudioModelResolution


class _InlineThread:
    def __init__(self, target=None, daemon=None):
        self._target = target

    def start(self):
        if self._target:
            self._target()


class _CollectingSink:
    def __init__(self):
        self.items = []

    def append_text(self, text):
        self.items.append(text)
        return True


class FloatAudioActionServiceTests(unittest.TestCase):
    def _resolution(self, provider_api="openai-completions"):
        return FloatAudioModelResolution(
            success=True,
            provider_name="cliproxy",
            provider_api=provider_api,
            model_name="gpt-5.4" if provider_api == "openai-completions" else "gemini-3-flash",
            model_full_name=(
                "cliproxy/gpt-5.4"
                if provider_api == "openai-completions"
                else "google-gemini-cli/gemini-3-flash"
            ),
            source="selected_model",
        )

    def test_transcribe_latest_uses_resolved_model_and_appends_result(self):
        sink = _CollectingSink()
        calls = []
        service = FloatAudioActionService(
            source_resolver=types.SimpleNamespace(
                resolve_latest=lambda: types.SimpleNamespace(
                    success=True,
                    file_paths=["a.wav"],
                )
            ),
            output_sink=sink,
            model_resolver=types.SimpleNamespace(
                resolve=lambda selected_model=None: self._resolution()
            ),
            transcribe_audio_fn=lambda file_path, callback=None, resolution=None: (
                calls.append((file_path, resolution.model_full_name)),
                callback("hello"),
            ),
            thread_factory=_InlineThread,
        )

        started = service.transcribe_latest(selected_model="cliproxy/gpt-5.4")

        self.assertTrue(started)
        self.assertEqual(calls, [("a.wav", "cliproxy/gpt-5.4")])
        self.assertEqual(sink.items, ["hello"])
        self.assertEqual(service.get_stt_state()["job_count"], 0)

    def test_transcribe_latest_marks_running_until_callback_finishes(self):
        sink = _CollectingSink()
        callbacks = []
        service = FloatAudioActionService(
            source_resolver=types.SimpleNamespace(
                resolve_latest=lambda: types.SimpleNamespace(
                    success=True,
                    file_paths=["a.wav"],
                )
            ),
            output_sink=sink,
            model_resolver=types.SimpleNamespace(
                resolve=lambda selected_model=None: self._resolution()
            ),
            transcribe_audio_fn=lambda file_path, callback=None, resolution=None: callbacks.append(callback),
            thread_factory=_InlineThread,
        )

        started = service.transcribe_latest(selected_model="cliproxy/gpt-5.4")

        self.assertTrue(started)
        self.assertEqual(service.get_stt_state()["job_count"], 1)
        callbacks[0]("later")
        self.assertEqual(service.get_stt_state()["job_count"], 0)
        self.assertEqual(sink.items, ["later"])

    def test_transcribe_latest_writes_no_audio_to_origin_window(self):
        sink = _CollectingSink()
        window = types.SimpleNamespace(messages=[], append_text=lambda text: window.messages.append(text))
        service = FloatAudioActionService(
            source_resolver=types.SimpleNamespace(
                resolve_latest=lambda: types.SimpleNamespace(
                    success=False,
                    file_paths=[],
                )
            ),
            output_sink=sink,
            model_resolver=types.SimpleNamespace(resolve=lambda selected_model=None: self._resolution()),
            transcribe_audio_fn=lambda *args, **kwargs: None,
            thread_factory=_InlineThread,
        )

        started = service.transcribe_latest(window_instance=window)

        self.assertFalse(started)
        self.assertEqual(window.messages, ["[No Audio Found]"])
        self.assertEqual(sink.items, [])

    def test_transcribe_selected_files_appends_running_header_then_ordered_results(self):
        sink = _CollectingSink()

        def transcribe_audio(file_path, callback=None, resolution=None):
            delays = {"b.wav": 0.01, "a.wav": 0.03, "c.wav": 0.005}
            time.sleep(delays[file_path])
            if file_path == "b.wav":
                callback(None)
                return None
            callback(f"text:{file_path}")
            return None

        service = FloatAudioActionService(
            source_resolver=types.SimpleNamespace(
                select_files=lambda parent=None: types.SimpleNamespace(
                    success=True,
                    file_paths=["a.wav", "b.wav", "c.wav"],
                )
            ),
            output_sink=sink,
            model_resolver=types.SimpleNamespace(
                resolve=lambda selected_model=None: self._resolution()
            ),
            transcribe_audio_fn=transcribe_audio,
            thread_factory=_InlineThread,
            sleep_fn=lambda _seconds: None,
        )

        started = service.transcribe_selected_files(selected_model="cliproxy/gpt-5.4")

        self.assertTrue(started)
        self.assertEqual(
            sink.items,
            [
                "Files a.wav, b.wav, c.wav running:",
                "text:a.wav",
                "File b.wav error: Empty transcript.",
                "text:c.wav",
            ],
        )
        self.assertEqual(service.get_stt_state()["job_count"], 0)

    def test_transcribe_selected_files_returns_false_on_cancel(self):
        sink = _CollectingSink()
        service = FloatAudioActionService(
            source_resolver=types.SimpleNamespace(
                select_files=lambda parent=None: types.SimpleNamespace(
                    success=False,
                    file_paths=[],
                )
            ),
            output_sink=sink,
            model_resolver=types.SimpleNamespace(
                resolve=lambda selected_model=None: self._resolution()
            ),
            transcribe_audio_fn=lambda *args, **kwargs: None,
            thread_factory=_InlineThread,
        )

        started = service.transcribe_selected_files()

        self.assertFalse(started)
        self.assertEqual(sink.items, [])


if __name__ == "__main__":
    unittest.main()
