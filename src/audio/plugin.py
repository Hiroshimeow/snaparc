from __future__ import annotations

import concurrent.futures
import threading
from pathlib import Path
from typing import Protocol

from src.audio.interfaces import ASRProvider, CaptureProvider, TTSProvider
from src.audio.models import AudioOutput, RecordingHandle, TranscriptResult
from src.audio.providers import UnavailableASRProvider, UnavailableTTSProvider
from src.audio.recording import SoundcardCaptureProvider


class AudioPlugin(Protocol):
    """Workspace-facing audio plugin contract.

    Implementations should keep heavy providers lazy and run long operations away
    from GUI code. This interface is intentionally small for the first phase.
    """

    def start_recording(self) -> RecordingHandle: ...

    def stop_recording(self) -> str: ...

    def transcribe_recording(
        self,
        audio_path: str | Path | None = None,
        *,
        language: str | None = None,
    ) -> TranscriptResult: ...

    def transcribe_recording_async(
        self,
        audio_path: str | Path | None = None,
        *,
        language: str | None = None,
    ) -> concurrent.futures.Future[TranscriptResult]: ...

    def synthesize_speech(
        self,
        text: str,
        *,
        voice: str | None = None,
        language: str | None = None,
    ) -> AudioOutput: ...


class LocalAudioPlugin:
    """Local audio plugin for explicit record/transcribe actions.

    Providers remain lazy: the default microphone provider opens audio hardware
    only during start_recording(). Production transcription is expected to be
    injected via the Workspace live transcript controller or a configured ASR
    provider; synthetic providers are never the default runtime path.
    """

    def __init__(
        self,
        capture_provider: CaptureProvider | None = None,
        asr_provider: ASRProvider | None = None,
        tts_provider: TTSProvider | None = None,
        max_workers: int = 1,
    ):
        self.capture_provider = capture_provider or SoundcardCaptureProvider()
        self.asr_provider = asr_provider or UnavailableASRProvider()
        self.tts_provider = tts_provider or UnavailableTTSProvider()
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=max(1, int(max_workers)),
            thread_name_prefix="workspace-audio",
        )
        self._lock = threading.Lock()
        self._capture_lock = threading.RLock()
        self._recording = False
        self._last_audio_path: str | None = None
        self._closed = False

    @property
    def last_audio_path(self) -> str | None:
        with self._lock:
            return self._last_audio_path

    def start_recording(self) -> RecordingHandle:
        with self._capture_lock:
            self._ensure_open()
            handle = self.capture_provider.start_recording()
            self._recording = True
            return handle

    def stop_recording(self) -> str:
        with self._capture_lock:
            self._ensure_open()
            audio_path = self.capture_provider.stop_recording()
            self._recording = False
            with self._lock:
                self._last_audio_path = audio_path
            return audio_path

    def transcribe_recording(
        self,
        audio_path: str | Path | None = None,
        *,
        language: str | None = None,
    ) -> TranscriptResult:
        self._ensure_open()
        resolved_path = str(audio_path or self.last_audio_path or "")
        if not resolved_path:
            raise RuntimeError("No recording is available to transcribe.")
        return self.asr_provider.transcribe(resolved_path, language=language)

    def transcribe_recording_async(
        self,
        audio_path: str | Path | None = None,
        *,
        language: str | None = None,
    ) -> concurrent.futures.Future[TranscriptResult]:
        self._ensure_open()
        return self._executor.submit(self.transcribe_recording, audio_path, language=language)

    def synthesize_speech(
        self,
        text: str,
        *,
        voice: str | None = None,
        language: str | None = None,
    ) -> AudioOutput:
        self._ensure_open()
        return self.tts_provider.synthesize(text, voice=voice, language=language)

    def close(self) -> None:
        with self._capture_lock:
            with self._lock:
                if self._closed:
                    return
            if self._recording:
                self.stop_recording()
            with self._lock:
                self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _ensure_open(self) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("Audio plugin is closed.")

    def __enter__(self) -> "LocalAudioPlugin":
        self._ensure_open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


