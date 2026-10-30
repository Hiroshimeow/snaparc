from __future__ import annotations

import json
import tempfile
import threading
import urllib.request
from pathlib import Path

try:
    import winsound
except ImportError:  # pragma: no cover - Windows product, import-safe elsewhere
    winsound = None

from src.audio.interfaces import AudioProviderUnavailableError
from src.audio.models import AudioOutput, TranscriptResult


class MissingDependencyProvider:
    """Base helper for optional providers that should fail lazily and clearly."""

    provider_name = "audio-provider"
    dependency = "optional dependency"
    install_hint: str | None = None

    def _raise_unavailable(self) -> None:
        raise AudioProviderUnavailableError(
            self.provider_name,
            self.dependency,
            install_hint=self.install_hint,
        )



class UnavailableASRProvider(MissingDependencyProvider):
    provider_name = "asr"
    dependency = "ASR runtime/model"
    install_hint = "Configure an ASR provider before transcription."

    def transcribe(self, audio_path: str | Path, *, language: str | None = None) -> TranscriptResult:
        self._raise_unavailable()


class UnavailableTTSProvider(MissingDependencyProvider):
    provider_name = "tts"
    dependency = "TTS runtime/model"
    install_hint = "Configure a TTS provider before speech synthesis."

    def synthesize(
        self,
        text: str,
        *,
        voice: str | None = None,
        language: str | None = None,
    ) -> AudioOutput:
        self._raise_unavailable()


class KorvaTTSProvider:
    """KorvaTTS HTTP provider. Synthesis only; playback stays explicit."""

    def __init__(
        self,
        *,
        base_url: str = "https://tts-r28-edge.hcu-lab.me",
        api_key: str = "",
        output_dir: str | Path | None = None,
        timeout_seconds: float = 60,
    ):
        self.base_url = str(base_url or "").rstrip("/")
        self.api_key = str(api_key or "")
        self.output_dir = Path(output_dir) if output_dir else None
        self.timeout_seconds = float(timeout_seconds or 60)

    def synthesize(
        self,
        text: str,
        *,
        voice: str | None = None,
        language: str | None = None,
    ) -> AudioOutput:
        if not self.api_key:
            raise RuntimeError("KorvaTTS API key is not configured.")
        if not self.base_url:
            raise RuntimeError("KorvaTTS base URL is not configured.")
        if len(text) > 20000:
            raise ValueError("KorvaTTS input exceeds the 20000 character limit.")
        if not text.strip():
            raise ValueError("KorvaTTS input is empty.")

        payload = json.dumps(
            {"input": text, "voice": voice or "bao_kim", "language": language or "vi"},
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/v1/audio/speech",
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": "Screens-Trans-Chatbot/1.0",
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            audio = response.read()
        if not audio:
            raise RuntimeError("KorvaTTS returned empty audio.")

        if self.output_dir:
            self.output_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="wb",
            suffix=".wav",
            prefix="stc_tts_",
            dir=str(self.output_dir) if self.output_dir else None,
            delete=False,
        ) as handle:
            handle.write(audio)
            output_path = handle.name
        return AudioOutput(
            audio_path=output_path,
            text=text,
            voice=voice or "bao_kim",
            language=language or "vi",
            provider="korva-tts",
            metadata={"temporary": True},
        )


class WindowsWavePlaybackProvider:
    """Minimal native Windows WAV playback with replace semantics."""

    def __init__(self):
        if winsound is None:
            raise RuntimeError("Windows WAV playback is unavailable on this platform.")
        self._lock = threading.RLock()
        self._current_path: Path | None = None
        self._current_temporary = False

    def play(self, audio: AudioOutput | str | Path) -> None:
        with self._lock:
            self.stop()
            if isinstance(audio, AudioOutput):
                path = audio.path()
                temporary = bool(audio.metadata.get("temporary"))
            else:
                path = Path(audio)
                temporary = False
            winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
            self._current_path = path
            self._current_temporary = temporary

    def stop(self) -> None:
        with self._lock:
            winsound.PlaySound(None, 0)
            path = self._current_path
            temporary = self._current_temporary
            self._current_path = None
            self._current_temporary = False
            if temporary and path is not None:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass


class MockASRProvider:

    def __init__(self, text: str = "Mock transcript from voice question.", provider_name: str = "mock-asr"):
        self.text = text
        self.provider_name = provider_name

    def transcribe(self, audio_path: str | Path, *, language: str | None = None) -> TranscriptResult:
        return TranscriptResult(
            text=self.text,
            language=language,
            audio_path=str(audio_path),
            provider=self.provider_name,
            metadata={"mock": True},
        )


class MockTTSProvider:
    """Mock TTS provider that returns a dummy path and never auto-plays."""

    def __init__(self, output_path: str | Path = "mock_tts_output.wav"):
        self.output_path = str(output_path)

    def synthesize(
        self,
        text: str,
        *,
        voice: str | None = None,
        language: str | None = None,
    ) -> AudioOutput:
        return AudioOutput(
            audio_path=self.output_path,
            text=text,
            voice=voice,
            language=language,
            provider="mock-tts",
            metadata={"mock": True, "auto_play": False},
        )
