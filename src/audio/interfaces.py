from __future__ import annotations

from pathlib import Path
from typing import Iterable, Protocol, runtime_checkable

from src.audio.models import (
    AudioChunk,
    AudioOutput,
    CorrectionResult,
    RecordingHandle,
    SpeechSegment,
    TranscriptResult,
)


class AudioProviderUnavailableError(RuntimeError):
    """Raised when an optional provider is requested without its dependency."""

    def __init__(self, provider_name: str, dependency: str, install_hint: str | None = None):
        self.provider_name = provider_name
        self.dependency = dependency
        self.install_hint = install_hint
        message = f"Audio provider '{provider_name}' requires optional dependency '{dependency}'."
        if install_hint:
            message = f"{message} {install_hint}"
        super().__init__(message)


@runtime_checkable
class CaptureProvider(Protocol):
    """Captures short user-triggered audio clips; never starts at app import/startup."""

    def start_recording(self) -> RecordingHandle: ...

    def stop_recording(self) -> str: ...


@runtime_checkable
class VADProvider(Protocol):
    """Detects speech segments in chunks or a saved audio file."""

    def detect_speech(self, source: str | Path | Iterable[AudioChunk]) -> list[SpeechSegment]: ...


@runtime_checkable
class ASRProvider(Protocol):
    """Speech-to-text provider for short recordings or speech segments."""

    def transcribe(self, audio_path: str | Path, *, language: str | None = None) -> TranscriptResult: ...


@runtime_checkable
class TranscriptCorrectionProvider(Protocol):
    """Optional transcript normalization/correction stage."""

    def correct(self, transcript: TranscriptResult) -> CorrectionResult: ...


@runtime_checkable
class TTSProvider(Protocol):
    """Text-to-speech provider. Synthesis must not auto-play."""

    def synthesize(
        self,
        text: str,
        *,
        voice: str | None = None,
        language: str | None = None,
    ) -> AudioOutput: ...


@runtime_checkable
class PlaybackProvider(Protocol):
    """Explicit playback provider; not invoked automatically by TTS."""

    def play(self, audio: AudioOutput | str | Path) -> None: ...

    def stop(self) -> None: ...
