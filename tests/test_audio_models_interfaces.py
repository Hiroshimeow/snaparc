import importlib

import pytest

from src.audio.interfaces import AudioProviderUnavailableError
from src.audio.models import AudioChunk, SpeechSegment, TranscriptResult
from src.audio.providers import UnavailableASRProvider, UnavailableTTSProvider


def test_audio_package_imports_cleanly_without_optional_runtime_imports():
    audio = importlib.import_module("src.audio")

    assert hasattr(audio, "AudioChunk")
    assert hasattr(audio, "LocalAudioPlugin")
    assert not hasattr(audio, "MockAudioPlugin")


def test_audio_models_are_lightweight_dataclasses():
    chunk = AudioChunk(data=b"abc", sample_rate_hz=16_000, channels=1)
    segment = SpeechSegment(start_ms=100, end_ms=450, confidence=0.8)
    transcript = TranscriptResult(
        text="xin chao",
        language="vi",
        confidence=0.9,
        segments=(segment,),
        audio_path="sample.wav",
        provider="test",
    )

    assert chunk.encoding == "pcm_s16le"
    assert segment.duration_ms == 350
    assert transcript.text == "xin chao"
    assert not transcript.is_empty


def test_missing_optional_asr_dependency_raises_clear_runtime_error():
    provider = UnavailableASRProvider()

    with pytest.raises(AudioProviderUnavailableError) as exc_info:
        provider.transcribe("missing.wav")

    message = str(exc_info.value)
    assert "Audio provider 'asr' requires optional dependency 'ASR runtime/model'" in message
    assert "Configure an ASR provider" in message


def test_missing_optional_tts_dependency_raises_clear_runtime_error():
    provider = UnavailableTTSProvider()

    with pytest.raises(AudioProviderUnavailableError) as exc_info:
        provider.synthesize("hello")

    message = str(exc_info.value)
    assert "Audio provider 'tts' requires optional dependency 'TTS runtime/model'" in message
    assert "Configure a TTS provider" in message
