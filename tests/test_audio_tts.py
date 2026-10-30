import json
from pathlib import Path
from unittest import mock

import pytest

from src.audio.models import AudioOutput
from src.audio.providers import KorvaTTSProvider, WindowsWavePlaybackProvider


class FakeResponse:
    def __init__(self, body=b"RIFFfakewav"):
        self.body = body
        self.headers = {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self.body


def test_korva_tts_posts_expected_request_and_writes_temporary_wav(tmp_path):
    seen = {}

    def fake_urlopen(request, timeout):
        seen["request"] = request
        seen["timeout"] = timeout
        return FakeResponse()

    provider = KorvaTTSProvider(
        base_url="https://tts.example.test/",
        api_key="test-token",
        output_dir=tmp_path,
        timeout_seconds=12,
    )

    with mock.patch("src.audio.providers.urllib.request.urlopen", side_effect=fake_urlopen):
        output = provider.synthesize("Xin chao")

    request = seen["request"]
    payload = json.loads(request.data.decode("utf-8"))
    assert request.full_url == "https://tts.example.test/v1/audio/speech"
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == "Bearer test-token"
    assert payload == {"input": "Xin chao", "voice": "bao_kim", "language": "vi"}
    assert seen["timeout"] == 12
    assert Path(output.audio_path).read_bytes() == b"RIFFfakewav"
    assert output.provider == "korva-tts"
    assert output.metadata["temporary"] is True


def test_korva_tts_rejects_missing_credentials_and_oversized_input(tmp_path):
    with pytest.raises(RuntimeError, match="API key"):
        KorvaTTSProvider(base_url="https://tts.example.test", api_key="", output_dir=tmp_path).synthesize("hi")

    provider = KorvaTTSProvider(base_url="https://tts.example.test", api_key="token", output_dir=tmp_path)
    with pytest.raises(ValueError, match="20000"):
        provider.synthesize("x" * 20001)


def test_windows_wave_playback_replaces_previous_and_cleans_temporary_files(tmp_path):
    first = tmp_path / "first.wav"
    second = tmp_path / "second.wav"
    first.write_bytes(b"a")
    second.write_bytes(b"b")
    playback = WindowsWavePlaybackProvider()

    with mock.patch("src.audio.providers.winsound.PlaySound") as play_sound:
        playback.play(AudioOutput(str(first), "first", metadata={"temporary": True}))
        playback.play(AudioOutput(str(second), "second", metadata={"temporary": True}))

        assert not first.exists()
        assert second.exists()
        playback.stop()

    assert not second.exists()
    assert play_sound.call_args_list[0].args[0] is None
    assert play_sound.call_args_list[1].args[0] == str(first)
    assert play_sound.call_args_list[2].args[0] is None
    assert play_sound.call_args_list[3].args[0] == str(second)
    assert play_sound.call_args_list[4].args[0] is None
