from __future__ import annotations

import math
import struct
import os
import tempfile
import wave
from dataclasses import dataclass
from typing import Literal

import numpy as np


@dataclass(frozen=True)
class AudioPreprocessResult:
    status: Literal["ok", "skipped", "error"]
    input_path: str
    output_path: str | None
    speech_segments: list[dict]
    duration_seconds: float
    speech_duration_seconds: float
    message: str | None = None


class AudioPreprocessor:
    def __init__(self, config_manager=None):
        self.config_manager = config_manager

    def preprocess(self, input_path: str) -> AudioPreprocessResult:
        settings = self._settings()
        if not settings.get("enabled", False):
            duration = self._duration_seconds(input_path)
            return AudioPreprocessResult("ok", input_path, input_path, [], duration, duration)
        provider = str(settings.get("provider", "energy") or "energy").lower()
        if provider == "silero":
            return AudioPreprocessResult("error", input_path, None, [], 0.0, 0.0, "Silero VAD is not installed/configured.")
        if provider != "energy":
            return AudioPreprocessResult("error", input_path, None, [], 0.0, 0.0, f"Unsupported audio preprocessing provider: {provider}")
        try:
            return self._preprocess_energy(input_path, settings)
        except Exception as exc:
            return AudioPreprocessResult("error", input_path, None, [], 0.0, 0.0, str(exc))

    def _settings(self) -> dict:
        default = {
            "enabled": False,
            "provider": "energy",
            "target_sample_rate": 16000,
            "frame_ms": 30,
            "min_speech_ms": 350,
            "min_silence_ms": 500,
            "speech_pad_ms": 180,
            "merge_gap_ms": 250,
            "energy_db_threshold": None,
        }
        if self.config_manager is not None:
            configured = self.config_manager.get("audio.stt.preprocess", {})
            if isinstance(configured, dict):
                default.update(configured)
        return default

    def _preprocess_energy(self, input_path: str, settings: dict) -> AudioPreprocessResult:
        if not os.path.exists(input_path):
            return AudioPreprocessResult("error", input_path, None, [], 0.0, 0.0, "Audio file does not exist.")
        with wave.open(input_path, "rb") as reader:
            channels = reader.getnchannels()
            sample_width = reader.getsampwidth()
            sample_rate = reader.getframerate()
            frame_count = reader.getnframes()
            raw = reader.readframes(frame_count)
        duration = frame_count / float(sample_rate or 1)
        if sample_width != 2:
            return AudioPreprocessResult("error", input_path, None, [], duration, 0.0, "Only 16-bit PCM WAV preprocessing is supported.")
        if channels > 1:
            raw = _tomono_16bit(raw, channels)
            channels = 1
        frame_ms = max(10, int(settings.get("frame_ms", 30)))
        frame_bytes = max(1, int(sample_rate * frame_ms / 1000)) * sample_width * channels
        frames = []
        for start in range(0, len(raw), frame_bytes):
            chunk = raw[start:start + frame_bytes]
            if not chunk:
                continue
            rms = _rms_16bit(chunk)
            db = 20 * math.log10(max(rms, 1) / 32768.0)
            frames.append((start, min(start + len(chunk), len(raw)), db))
        if not frames:
            return AudioPreprocessResult("skipped", input_path, None, [], duration, 0.0, "No audio frames found.")
        threshold = settings.get("energy_db_threshold")
        if threshold is None:
            sorted_db = sorted(frame[2] for frame in frames)
            floor = sorted_db[max(0, int(len(sorted_db) * 0.2) - 1)]
            threshold = min(-26.0, max(-55.0, floor + 10.0))
        else:
            threshold = float(threshold)
        raw_segments = self._speech_segments(frames, threshold, sample_rate, sample_width, settings)
        if not raw_segments:
            return AudioPreprocessResult("skipped", input_path, None, [], duration, 0.0, "No speech detected; skipped STT.")
        speech_audio = b"".join(raw[start:end] for start, end in raw_segments)
        speech_duration = len(speech_audio) / float(sample_rate * sample_width)
        out = tempfile.NamedTemporaryFile(prefix="stc_speech_", suffix=".wav", delete=False)
        out.close()
        with wave.open(out.name, "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(sample_width)
            writer.setframerate(sample_rate)
            writer.writeframes(speech_audio)
        segments = [
            {"start_ms": round(start / (sample_rate * sample_width) * 1000), "end_ms": round(end / (sample_rate * sample_width) * 1000)}
            for start, end in raw_segments
        ]
        return AudioPreprocessResult("ok", input_path, out.name, segments, duration, speech_duration)

    def _speech_segments(self, frames, threshold, sample_rate, sample_width, settings):
        min_speech = int(settings.get("min_speech_ms", 350))
        min_silence = int(settings.get("min_silence_ms", 500))
        pad = int(settings.get("speech_pad_ms", 180))
        merge_gap = int(settings.get("merge_gap_ms", 250))
        units_per_ms = sample_rate * sample_width / 1000.0
        segments = []
        start = None
        last_voice_end = None
        for byte_start, byte_end, db in frames:
            is_voice = db >= threshold
            if is_voice:
                if start is None:
                    start = byte_start
                last_voice_end = byte_end
            elif start is not None and last_voice_end is not None:
                silence_ms = (byte_end - last_voice_end) / units_per_ms
                if silence_ms >= min_silence:
                    if (last_voice_end - start) / units_per_ms >= min_speech:
                        segments.append((start, last_voice_end))
                    start = None
                    last_voice_end = None
        if start is not None and last_voice_end is not None and (last_voice_end - start) / units_per_ms >= min_speech:
            segments.append((start, last_voice_end))
        padded = []
        pad_bytes = int(pad * units_per_ms)
        for start, end in segments:
            padded.append((max(0, start - pad_bytes), min(frames[-1][1], end + pad_bytes)))
        if not padded:
            return []
        merged = [padded[0]]
        merge_bytes = int(merge_gap * units_per_ms)
        for start, end in padded[1:]:
            prev_start, prev_end = merged[-1]
            if start - prev_end <= merge_bytes:
                merged[-1] = (prev_start, max(prev_end, end))
            else:
                merged.append((start, end))
        return merged

    def _duration_seconds(self, input_path: str) -> float:
        try:
            with wave.open(input_path, "rb") as reader:
                return reader.getnframes() / float(reader.getframerate() or 1)
        except Exception:
            return 0.0


def _rms_16bit(raw: bytes) -> int:
    if not raw:
        return 0
    count = len(raw) // 2
    if count <= 0:
        return 0
    total = 0
    for (sample,) in struct.iter_unpack("<h", raw[: count * 2]):
        total += sample * sample
    return int(math.sqrt(total / count))


def _tomono_16bit(raw: bytes, channels: int) -> bytes:
    if channels <= 1:
        return raw
    if len(raw) % 2:
        raise ValueError("16-bit PCM payload must contain complete samples.")
    sample_count = len(raw) // 2
    if sample_count % channels:
        raise ValueError("PCM sample count must align to complete channel frames.")
    samples = np.frombuffer(raw, dtype="<i2").reshape(-1, channels)
    return samples.mean(axis=1).astype("<i2").tobytes()
