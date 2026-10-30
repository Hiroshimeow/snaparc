from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


AudioEncoding = Literal["pcm_s16le", "pcm_f32le", "wav", "mp3", "ogg", "flac", "unknown"]
VoiceRole = Literal["user", "assistant", "system"]


@dataclass(frozen=True)
class AudioChunk:
    """Small unit of captured audio data passed between audio providers."""

    data: bytes
    sample_rate_hz: int
    channels: int = 1
    encoding: AudioEncoding = "pcm_s16le"
    timestamp_ms: int | None = None
    duration_ms: int | None = None
    source: str = "microphone"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SpeechSegment:
    """A voice activity interval within an audio stream or file."""

    start_ms: int
    end_ms: int
    confidence: float | None = None
    audio_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)


@dataclass(frozen=True)
class TranscriptResult:
    """Speech-to-text result returned by an ASR provider or audio plugin."""

    text: str
    language: str | None = None
    confidence: float | None = None
    segments: tuple[SpeechSegment, ...] = ()
    audio_path: str | None = None
    provider: str = "unknown"
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()


@dataclass(frozen=True)
class CorrectionResult:
    """Optional post-ASR transcript cleanup/correction result."""

    original_text: str
    corrected_text: str
    language: str | None = None
    provider: str = "unknown"
    confidence: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AudioOutput:
    """Text-to-speech synthesis output; playback is explicit and separate."""

    audio_path: str
    text: str
    voice: str | None = None
    language: str | None = None
    provider: str = "unknown"
    metadata: dict[str, Any] = field(default_factory=dict)

    def path(self) -> Path:
        return Path(self.audio_path)


@dataclass(frozen=True)
class RecordingHandle:
    """Opaque handle for a single short recording session."""

    recording_id: str
    started_at: float
    output_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VoiceBotTurn:
    """Bridge-friendly representation of one voice bot conversation turn."""

    role: VoiceRole
    text: str
    audio_path: str | None = None
    transcript: TranscriptResult | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
