"""Lightweight audio/voice plugin boundary for Screen Chat Workspace.

This package intentionally imports only standard-library modules at package import
so optional capture/ASR/TTS providers can be installed later without slowing app
startup or breaking clean imports.
"""

from src.audio.models import (
    AudioChunk,
    AudioOutput,
    CorrectionResult,
    RecordingHandle,
    SpeechSegment,
    TranscriptResult,
    VoiceBotTurn,
)
from src.audio.plugin import AudioPlugin, LocalAudioPlugin

__all__ = [
    "AudioChunk",
    "AudioOutput",
    "CorrectionResult",
    "RecordingHandle",
    "SpeechSegment",
    "TranscriptResult",
    "VoiceBotTurn",
    "AudioPlugin",
    "LocalAudioPlugin",
]
