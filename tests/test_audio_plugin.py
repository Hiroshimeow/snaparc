from pathlib import Path

import pytest

from src.audio.interfaces import AudioProviderUnavailableError
from src.audio.plugin import LocalAudioPlugin
from src.audio.providers import MockASRProvider, MockTTSProvider
from src.audio.recording import StubCaptureProvider


def test_injected_record_to_transcript_flow_uses_temp_file(tmp_path):
    plugin = LocalAudioPlugin(
        capture_provider=StubCaptureProvider(output_dir=tmp_path),
        asr_provider=MockASRProvider(text="toi muon hoi bang giong noi"),
    )

    handle = plugin.start_recording()
    audio_path = plugin.stop_recording()
    transcript = plugin.transcribe_recording(language="vi")

    assert handle.output_path == audio_path
    assert Path(audio_path).exists()
    assert Path(audio_path).read_bytes() == b"STUB-AUDIO\n"
    assert transcript.text == "toi muon hoi bang giong noi"
    assert transcript.language == "vi"
    assert transcript.audio_path == audio_path
    assert transcript.metadata["mock"] is True

    plugin.close()


def test_transcribe_recording_async_runs_off_caller_thread(tmp_path):
    plugin = LocalAudioPlugin(
        capture_provider=StubCaptureProvider(output_dir=tmp_path),
        asr_provider=MockASRProvider(text="async transcript"),
    )

    plugin.start_recording()
    audio_path = plugin.stop_recording()
    future = plugin.transcribe_recording_async(audio_path)

    assert future.result(timeout=2).text == "async transcript"
    plugin.close()


def test_tts_synthesizes_without_autoplay_when_provider_is_injected():
    plugin = LocalAudioPlugin(tts_provider=MockTTSProvider(output_path="dummy.wav"))

    output = plugin.synthesize_speech("hello", voice="test", language="en")

    assert output.audio_path == "dummy.wav"
    assert output.text == "hello"
    assert output.metadata["auto_play"] is False

    plugin.close()


def test_default_asr_and_tts_fail_explicitly_without_provider(tmp_path):
    plugin = LocalAudioPlugin(capture_provider=StubCaptureProvider(output_dir=tmp_path))
    plugin.start_recording()
    audio_path = plugin.stop_recording()

    with pytest.raises(AudioProviderUnavailableError):
        plugin.transcribe_recording(audio_path)
    with pytest.raises(AudioProviderUnavailableError):
        plugin.synthesize_speech("hello")

    plugin.close()


def test_close_stops_and_saves_active_recording(tmp_path):
    capture = StubCaptureProvider(output_dir=tmp_path)
    plugin = LocalAudioPlugin(capture_provider=capture)
    handle = plugin.start_recording()

    plugin.close()

    assert capture._active is None
    assert Path(handle.output_path).read_bytes() == b"STUB-AUDIO\n"
    assert plugin.last_audio_path == handle.output_path
    plugin.close()
    with pytest.raises(RuntimeError, match="closed"):
        plugin.start_recording()


def test_close_can_retry_a_failed_recording_save(tmp_path, monkeypatch):
    capture = StubCaptureProvider(output_dir=tmp_path)
    plugin = LocalAudioPlugin(capture_provider=capture)
    handle = plugin.start_recording()

    def fail_stop():
        raise OSError("disk full")

    with monkeypatch.context() as stop_patch:
        stop_patch.setattr(capture, "stop_recording", fail_stop)
        with pytest.raises(OSError, match="disk full"):
            plugin.close()

    plugin.close()
    assert capture._active is None
    assert Path(handle.output_path).exists()


def test_close_without_recording_does_not_start_audio_hardware(tmp_path):
    capture = StubCaptureProvider(output_dir=tmp_path)
    plugin = LocalAudioPlugin(capture_provider=capture)

    plugin.close()

    assert list(tmp_path.iterdir()) == []
