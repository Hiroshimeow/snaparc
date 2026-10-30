import sys
import threading
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

from src.audio.recording import SoundcardCaptureProvider


class BlockingRecorder:
    def __init__(self):
        self.reading = threading.Event()
        self.release = threading.Event()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def record(self, **_kwargs):
        self.reading.set()
        assert self.release.wait(2), "test did not release the fake audio read"
        return np.full((4, 1), 0.25, dtype=np.float32)


def start_blocked_recording(tmp_path, monkeypatch):
    device = BlockingRecorder()
    microphone = SimpleNamespace(recorder=lambda **_kwargs: device)
    monkeypatch.setitem(sys.modules, "soundcard", SimpleNamespace(default_microphone=lambda: microphone))
    provider = SoundcardCaptureProvider(output_dir=tmp_path)
    handle = provider.start_recording()
    assert device.reading.wait(2)
    return provider, handle, device


def test_start_is_rejected_until_stop_finishes_writing(tmp_path, monkeypatch):
    provider, handle, device = start_blocked_recording(tmp_path, monkeypatch)
    writing = threading.Event()
    finish_write = threading.Event()
    original_write = provider._write_wav
    outcomes = []

    def write(path):
        writing.set()
        assert finish_write.wait(2)
        original_write(path)

    def stop():
        try:
            outcomes.append(provider.stop_recording())
        except Exception as exc:
            outcomes.append(exc)

    monkeypatch.setattr(provider, "_write_wav", write)
    stopper = threading.Thread(target=stop)
    stopper.start()
    device.release.set()
    try:
        assert writing.wait(2)
        with pytest.raises(RuntimeError, match="already active|stopping"):
            provider.start_recording()
    finally:
        finish_write.set()
        stopper.join(2)
        if provider._stop_event is not None:
            provider._stop_event.set()
        if provider._thread is not None:
            provider._thread.join(2)
    assert outcomes == [handle.output_path]
    samples, _ = sf.read(handle.output_path)
    np.testing.assert_allclose(samples, 0.25)
    assert provider.active_recording is None


def test_timed_out_stop_keeps_session_reserved_until_worker_exits(tmp_path, monkeypatch):
    provider, handle, device = start_blocked_recording(tmp_path, monkeypatch)
    worker = provider._thread
    try:
        with monkeypatch.context() as timeout_patch:
            timeout_patch.setattr(worker, "join", lambda timeout=None: None)
            with pytest.raises(RuntimeError, match="did not stop"):
                provider.stop_recording()
        assert provider.active_recording == handle
        with pytest.raises(RuntimeError, match="already active|stopping"):
            provider.start_recording()
    finally:
        device.release.set()
        worker.join(2)
    assert provider.stop_recording() == handle.output_path
    assert provider.active_recording is None


def test_failed_save_can_retry_without_losing_captured_samples(tmp_path, monkeypatch):
    provider, handle, device = start_blocked_recording(tmp_path, monkeypatch)
    device.release.set()
    with monkeypatch.context() as write_patch:
        write_patch.setattr(provider, "_write_wav", lambda _path: (_ for _ in ()).throw(OSError("disk full")))
        with pytest.raises(OSError, match="disk full"):
            provider.stop_recording()
    assert provider.active_recording == handle
    assert provider.stop_recording() == handle.output_path
    assert provider.active_recording is None
