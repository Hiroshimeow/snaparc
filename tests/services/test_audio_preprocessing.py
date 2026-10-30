import wave

import pytest

from src.services.audio_preprocessing import AudioPreprocessor, _tomono_16bit


class _Config:
    def __init__(self, settings):
        self.settings = settings

    def get(self, key, default=None):
        if key == "audio.stt.preprocess":
            return dict(self.settings)
        return default


def _enabled_energy_preprocessor():
    return AudioPreprocessor(_Config({"enabled": True, "provider": "energy"}))


def _write_wav(path, samples, rate=16000):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        data = b"".join(int(max(-32767, min(32767, s))).to_bytes(2, "little", signed=True) for s in samples)
        w.writeframes(data)


def _pcm16(samples):
    return b"".join(int(sample).to_bytes(2, "little", signed=True) for sample in samples)


def _read_pcm16(raw):
    return [int.from_bytes(raw[i:i + 2], "little", signed=True) for i in range(0, len(raw), 2)]


def test_tomono_16bit_preserves_average_semantics_for_signed_extremes():
    raw = _pcm16([32767, 32767, -32768, -32768, 1, 0, -1, 0, 30000, -10000])

    mono = _tomono_16bit(raw, 2)

    assert _read_pcm16(mono) == [32767, -32768, 0, 0, 10000]


def test_tomono_16bit_outputs_one_int16_per_input_frame():
    raw = _pcm16([1000, 3000, -1000, -3000, 123, 321])

    mono = _tomono_16bit(raw, 2)

    assert len(mono) == 3 * 2


def test_tomono_16bit_keeps_mono_input_unchanged():
    raw = _pcm16([1, -2, 32767, -32768])

    assert _tomono_16bit(raw, 1) is raw


def test_tomono_16bit_rejects_odd_byte_payload():
    with pytest.raises(ValueError, match="16-bit"):
        _tomono_16bit(b"\x00\x01\x02", 2)


def test_tomono_16bit_rejects_incomplete_channel_frame():
    with pytest.raises(ValueError, match="channel"):
        _tomono_16bit(_pcm16([1, 2, 3]), 2)


def test_energy_preprocessor_stereo_writes_readable_mono_wav(tmp_path):
    path = tmp_path / "stereo.wav"
    frame_count = 3200
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(2)
        writer.setsampwidth(2)
        writer.setframerate(16000)
        writer.writeframes(_pcm16([12000, -4000] * frame_count))
    preprocessor = AudioPreprocessor(_Config({
        "enabled": True,
        "provider": "energy",
        "energy_db_threshold": -80,
        "frame_ms": 10,
        "min_speech_ms": 0,
        "speech_pad_ms": 0,
    }))

    result = preprocessor.preprocess(str(path))

    assert result.status == "ok"
    with wave.open(result.output_path, "rb") as reader:
        assert reader.getnchannels() == 1
        assert reader.getsampwidth() == 2
        assert reader.getframerate() == 16000
        output = reader.readframes(reader.getnframes())
    assert len(output) == frame_count * 2
    assert _read_pcm16(output) == [4000] * frame_count


def test_energy_preprocessor_rejects_unsupported_sample_width(tmp_path):
    path = tmp_path / "unsupported-8bit.wav"
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(1)
        writer.setframerate(16000)
        writer.writeframes(b"\x80" * 160)

    result = _enabled_energy_preprocessor().preprocess(str(path))

    assert result.status == "error"
    assert result.output_path is None
    assert result.message == "Only 16-bit PCM WAV preprocessing is supported."


def test_energy_preprocessor_skips_silence(tmp_path):
    path = tmp_path / "silence.wav"
    _write_wav(path, [0] * 16000)

    result = _enabled_energy_preprocessor().preprocess(str(path))

    assert result.status == "skipped"
    assert result.output_path is None
    assert "No speech" in result.message


def test_energy_preprocessor_writes_speech_only_wav(tmp_path):
    path = tmp_path / "speech.wav"
    samples = [0] * 4000 + [12000 if i % 2 == 0 else -12000 for i in range(16000)] + [0] * 4000
    _write_wav(path, samples)

    result = _enabled_energy_preprocessor().preprocess(str(path))

    assert result.status == "ok"
    assert result.output_path and result.output_path.endswith(".wav")
    assert result.speech_duration_seconds > 0.5
    assert result.speech_segments


def test_preprocessing_is_non_destructive_by_default(tmp_path):
    path = tmp_path / "raw.wav"
    _write_wav(path, [0] * 8000)

    result = AudioPreprocessor().preprocess(str(path))

    assert result.status == "ok"
    assert result.output_path == str(path)
    assert result.speech_segments == []


def test_silero_provider_reports_structured_error():
    cfg = type("Cfg", (), {"get": lambda self, key, default=None: {"enabled": True, "provider": "silero"}})()

    result = AudioPreprocessor(cfg).preprocess("x.wav")

    assert result.status == "error"
    assert "Silero" in result.message
