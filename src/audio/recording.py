from __future__ import annotations

import tempfile
import threading
import time
import uuid
from pathlib import Path

from src.audio.models import RecordingHandle


class SoundcardCaptureProvider:
    """User-triggered microphone capture backed by soundcard/soundfile.

    No microphone is opened in __init__. Capture starts only from start_recording()
    and runs until stop_recording() writes the recorded WAV file.
    """

    def __init__(self, output_dir: str | Path | None = None, sample_rate: int = 16000, channels: int = 1, block_seconds: float = 0.25):
        self.output_dir = Path(output_dir) if output_dir else Path(tempfile.gettempdir()) / "screens_trans_chatbot" / "audio"
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self.block_seconds = float(block_seconds)
        self._lock = threading.Lock()
        self._active: RecordingHandle | None = None
        self._stop_event: threading.Event | None = None
        self._thread: threading.Thread | None = None
        self._chunks: list[object] = []
        self._error: Exception | None = None
        self._stopping = False

    @property
    def active_recording(self) -> RecordingHandle | None:
        with self._lock:
            return self._active

    def start_recording(self) -> RecordingHandle:
        with self._lock:
            if self._active is not None or self._stopping:
                raise RuntimeError("A voice recording is already active.")
            self.output_dir.mkdir(parents=True, exist_ok=True)
            recording_id = uuid.uuid4().hex
            handle = RecordingHandle(
                recording_id=recording_id,
                started_at=time.time(),
                output_path=str(self.output_dir / f"voice_question_{recording_id}.wav"),
                metadata={"provider": "soundcard"},
            )
            self._active = handle
            self._stop_event = threading.Event()
            self._chunks = []
            self._error = None
            self._thread = threading.Thread(target=self._record_loop, args=(self._stop_event,), daemon=True)
            self._thread.start()
            return handle

    def stop_recording(self) -> str:
        with self._lock:
            if self._active is None or self._stop_event is None:
                raise RuntimeError("No active voice recording to stop.")
            if self._stopping:
                raise RuntimeError("Voice recording is already stopping.")
            handle = self._active
            stop_event = self._stop_event
            thread = self._thread
            self._stopping = True
        stop_event.set()
        try:
            if thread is not None:
                thread.join(timeout=5.0)
                if thread.is_alive():
                    raise RuntimeError("Audio recording worker did not stop in time.")
            if self._error is not None:
                error = self._error
                with self._lock:
                    self._release_recording_locked()
                raise RuntimeError(f"Audio recording failed: {error}") from error
            if not self._chunks:
                with self._lock:
                    self._release_recording_locked()
                raise RuntimeError("No audio samples were captured.")
            output_path = Path(handle.output_path or self.output_dir / f"voice_question_{handle.recording_id}.wav")
            self._write_wav(output_path)
            with self._lock:
                self._release_recording_locked()
            return str(output_path)
        finally:
            with self._lock:
                self._stopping = False

    def _release_recording_locked(self) -> None:
        self._active = None
        self._stop_event = None
        self._thread = None
        self._chunks = []
        self._error = None

    def _record_loop(self, stop_event: threading.Event) -> None:
        try:
            import soundcard as sc

            block_frames = max(1, int(self.sample_rate * self.block_seconds))
            mic = sc.default_microphone()
            with mic.recorder(samplerate=self.sample_rate, channels=self.channels) as recorder:
                while not stop_event.is_set():
                    data = recorder.record(numframes=block_frames)
                    if data is not None and len(data):
                        with self._lock:
                            self._chunks.append(data.copy())
        except Exception as exc:
            self._error = exc

    def _write_wav(self, output_path: Path) -> None:
        import numpy as np
        import soundfile as sf

        output_path.parent.mkdir(parents=True, exist_ok=True)
        data = np.concatenate(self._chunks, axis=0)
        sf.write(str(output_path), data, self.sample_rate, subtype="PCM_16")


class StubCaptureProvider(SoundcardCaptureProvider):
    """Test-only capture provider that writes a tiny marker file and never opens a microphone."""

    def __init__(self, output_dir: str | Path | None = None, suffix: str = ".wav"):
        self.output_dir = Path(output_dir) if output_dir else Path(tempfile.gettempdir()) / "screens_trans_chatbot" / "audio"
        self.suffix = suffix
        self._lock = threading.Lock()
        self._active: RecordingHandle | None = None

    def start_recording(self) -> RecordingHandle:
        with self._lock:
            if self._active is not None:
                raise RuntimeError("A voice recording is already active.")
            self.output_dir.mkdir(parents=True, exist_ok=True)
            recording_id = uuid.uuid4().hex
            handle = RecordingHandle(
                recording_id=recording_id,
                started_at=time.time(),
                output_path=str(self.output_dir / f"voice_question_{recording_id}{self.suffix}"),
                metadata={"stub": True},
            )
            self._active = handle
            return handle

    def stop_recording(self) -> str:
        with self._lock:
            if self._active is None:
                raise RuntimeError("No active voice recording to stop.")
            handle = self._active
            self._active = None
        output_path = Path(handle.output_path or self.output_dir / f"voice_question_{handle.recording_id}{self.suffix}")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"STUB-AUDIO\n")
        return str(output_path)
