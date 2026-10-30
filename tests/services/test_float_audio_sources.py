import sys
import os
import shutil
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from src.services.float_audio_service import FloatAudioSourceResolver

sys.modules.setdefault("soundcard", types.SimpleNamespace())
sys.modules.setdefault(
    "soundfile",
    types.SimpleNamespace(
        write=lambda filepath, _audio, _sr, **_kwargs: open(filepath, "wb").write(b"audio")
    ),
)
from src.utils.AudioRecorder import AudioRecorder, RecordingGeneration  # noqa: E402


class FloatAudioSourceResolverTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="float_audio_sources_")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _touch(self, name):
        path = os.path.join(self.temp_dir, name)
        with open(path, "wb") as handle:
            handle.write(b"audio")
        return path

    def test_latest_while_recording_uses_snapshot(self):
        snapshot_path = self._touch("snapshot_001.mp3")
        recorder = types.SimpleNamespace(
            is_recording=True,
            last_saved_path=None,
            output_folder=self.temp_dir,
            save_snapshot=lambda: snapshot_path,
        )

        selection = FloatAudioSourceResolver(recorder).resolve_latest()

        self.assertTrue(selection.success)
        self.assertEqual(selection.source, "latest_snapshot")
        self.assertEqual(selection.file_paths, [os.path.abspath(snapshot_path)])

    def test_latest_after_stop_uses_last_saved_path(self):
        saved_path = self._touch("rec_20260409_010101.mp3")
        recorder = types.SimpleNamespace(
            is_recording=False,
            last_saved_path=saved_path,
            output_folder=self.temp_dir,
        )

        selection = FloatAudioSourceResolver(recorder).resolve_latest()

        self.assertTrue(selection.success)
        self.assertEqual(selection.source, "latest_saved")
        self.assertEqual(selection.file_paths, [os.path.abspath(saved_path)])

    def test_selected_files_are_sorted_by_filename(self):
        later = self._touch("rec_20260409_010103.mp3")
        first = self._touch("rec_20260409_010101.mp3")
        middle = self._touch("rec_20260409_010102.mp3")
        recorder = types.SimpleNamespace(output_folder=self.temp_dir)
        dialog = types.SimpleNamespace(
            askopenfilenames=lambda **_kwargs: (later, first, middle)
        )
        resolver = FloatAudioSourceResolver(recorder, file_dialog_module=dialog)

        selection = resolver.select_files(parent=None)

        self.assertTrue(selection.success)
        self.assertEqual(selection.source, "selected_files")
        self.assertEqual(
            selection.file_paths,
            [
                os.path.abspath(first),
                os.path.abspath(middle),
                os.path.abspath(later),
            ],
        )

    def test_empty_live_buffer_after_auto_split_falls_back_to_last_saved_segment(self):
        saved_path = self._touch("rec_20260409_010055.mp3")
        recorder = types.SimpleNamespace(
            is_recording=True,
            last_saved_path=saved_path,
            output_folder=self.temp_dir,
            save_snapshot=lambda: None,
        )

        selection = FloatAudioSourceResolver(recorder).resolve_latest()

        self.assertTrue(selection.success)
        self.assertEqual(selection.source, "latest_saved")
        self.assertEqual(selection.file_paths, [os.path.abspath(saved_path)])

    def test_resolve_latest_returns_no_audio_when_nothing_is_available(self):
        recorder = types.SimpleNamespace(
            is_recording=False,
            last_saved_path=None,
            output_folder=self.temp_dir,
        )

        selection = FloatAudioSourceResolver(recorder).resolve_latest()

        self.assertFalse(selection.success)
        self.assertEqual(selection.source, "no_audio")
        self.assertEqual(selection.error_code, "no_audio")
        self.assertEqual(selection.file_paths, [])

    def test_select_files_returns_cancelled_when_selection_is_empty_or_invalid(self):
        recorder = types.SimpleNamespace(output_folder=self.temp_dir)
        dialog = types.SimpleNamespace(askopenfilenames=lambda **_kwargs: ("missing.mp3",))
        resolver = FloatAudioSourceResolver(recorder, file_dialog_module=dialog)

        selection = resolver.select_files(parent=None)

        self.assertFalse(selection.success)
        self.assertEqual(selection.source, "cancelled")
        self.assertEqual(selection.error_code, "cancelled")
        self.assertEqual(selection.file_paths, [])


class FakeRecordStream:
    def __init__(
        self,
        recorder=None,
        amplitude=0.1,
        fail_at=None,
        error_message="device disconnected",
        stop_after=None,
    ):
        self.recorder = recorder
        self.amplitude = amplitude
        self.fail_at = fail_at
        self.error_message = error_message
        self.stop_after = stop_after
        self.calls = 0

    def record(self, numframes):
        if self.fail_at is not None and self.calls == self.fail_at:
            self.calls += 1
            raise RuntimeError(self.error_message)
        self.calls += 1
        if self.stop_after is not None and self.calls >= self.stop_after and self.recorder is not None:
            self.recorder._stop_event.set()
        return np.full((numframes, 2), self.amplitude, dtype=np.float32)


class BlockingRecordStream:
    def __init__(self, release_event, amplitude=0.1):
        self.release_event = release_event
        self.amplitude = amplitude
        self.calls = 0

    def record(self, numframes):
        self.calls += 1
        self.release_event.wait()
        return np.full((numframes, 2), self.amplitude, dtype=np.float32)


class BlockingFailRecordStream:
    def __init__(self, release_event, error_message):
        self.release_event = release_event
        self.error_message = error_message
        self.calls = 0

    def record(self, numframes):
        self.calls += 1
        self.release_event.wait()
        raise RuntimeError(self.error_message)


class StaleThenPacedRecordStream:
    def __init__(
        self,
        read_entered,
        release_read,
        error_message,
        delay_seconds=0.005,
        amplitude=0.1,
    ):
        self.read_entered = read_entered
        self.release_read = release_read
        self.error_message = error_message
        self.delay_seconds = delay_seconds
        self.amplitude = amplitude
        self.calls = 0

    def record(self, numframes):
        self.calls += 1
        if self.calls == 1:
            self.read_entered.set()
            self.release_read.wait(timeout=3.0)
            raise RuntimeError(self.error_message)
        time.sleep(self.delay_seconds)
        return np.full((numframes, 2), self.amplitude, dtype=np.float32)


class TrackingLock:
    def __init__(self):
        self._lock = threading.Lock()
        self._state_lock = threading.Lock()
        self.acquire_attempts = 0
        self.second_attempted = threading.Event()

    def __enter__(self):
        with self._state_lock:
            self.acquire_attempts += 1
            if self.acquire_attempts >= 2:
                self.second_attempted.set()
        self._lock.acquire()
        return self

    def __exit__(self, exc_type, exc, tb):
        self._lock.release()
        return False


class PacedRecordStream:
    def __init__(self, delay_seconds=0.001, amplitude=0.1):
        self.delay_seconds = delay_seconds
        self.amplitude = amplitude
        self.calls = 0

    def record(self, numframes):
        self.calls += 1
        time.sleep(self.delay_seconds)
        return np.full((numframes, 2), self.amplitude, dtype=np.float32)


class FakeRecorderContext:
    def __init__(self, stream):
        self.stream = stream
        self.closed = False

    def __enter__(self):
        return self.stream

    def __exit__(self, exc_type, exc, tb):
        self.closed = True
        return False


class FakeAudioDevice:
    def __init__(self, name, device_id, stream):
        self.name = name
        self.id = device_id
        self.stream = stream
        self.context = None

    def recorder(self, **_kwargs):
        self.context = FakeRecorderContext(self.stream)
        return self.context


class DelayedRecorderContext(FakeRecorderContext):
    def __init__(self, stream, open_delay_seconds):
        super().__init__(stream)
        self.open_delay_seconds = open_delay_seconds

    def __enter__(self):
        time.sleep(self.open_delay_seconds)
        return super().__enter__()


class DelayedOpenAudioDevice(FakeAudioDevice):
    def __init__(self, name, device_id, stream, open_delay_seconds):
        super().__init__(name, device_id, stream)
        self.open_delay_seconds = open_delay_seconds

    def recorder(self, **_kwargs):
        self.context = DelayedRecorderContext(self.stream, self.open_delay_seconds)
        return self.context


class AudioRecorderLoopbackDegradeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="audio_recorder_loopback_")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _recorder(self):
        return AudioRecorder(
            output_folder=self.temp_dir,
            silence_threshold=0.01,
        )

    def test_loopback_unavailable_at_start_records_mic_only_without_fatal_error(self):
        recorder = self._recorder()
        mic_stream = FakeRecordStream(recorder=recorder, stop_after=2)
        mic = FakeAudioDevice("primary mic", "mic-1", mic_stream)

        with patch.object(recorder, "_get_input_mic", return_value=mic), patch.object(
            recorder, "_get_loopback_mic", return_value=None
        ):
            self.assertTrue(recorder.start_recording())
            recorder._thread.join(timeout=1.0)
            saved_path = recorder.stop_recording()

        self.assertIsNone(recorder.last_error)
        self.assertEqual(recorder.loopback_state, "unavailable")
        self.assertEqual(recorder.selected_mic_name, "primary mic")
        self.assertIsNotNone(saved_path)
        self.assertTrue(os.path.exists(saved_path))

    def test_loopback_read_failure_degrades_to_mic_only_and_keeps_recording(self):
        recorder = self._recorder()
        mic_stream = FakeRecordStream(recorder=recorder, stop_after=3)
        loop_stream = FakeRecordStream(fail_at=0, error_message="hdmi unplugged")
        mic = FakeAudioDevice("primary mic", "mic-1", mic_stream)
        loop = FakeAudioDevice("hdmi loopback", "loop-1", loop_stream)

        with patch.object(recorder, "_get_input_mic", return_value=mic), patch.object(
            recorder, "_get_loopback_mic", return_value=loop
        ):
            self.assertTrue(recorder.start_recording())
            recorder._thread.join(timeout=1.0)
            saved_path = recorder.stop_recording()

        self.assertIsNone(recorder.last_error)
        self.assertEqual(recorder.loopback_state, "failed")
        self.assertIn("hdmi unplugged", recorder.last_loopback_error)
        self.assertGreaterEqual(mic_stream.calls, 3)
        self.assertEqual(loop_stream.calls, 1)
        self.assertTrue(loop.context.closed)
        self.assertIsNotNone(saved_path)
        self.assertTrue(os.path.exists(saved_path))

    def test_stop_waits_until_coordinator_cannot_append_after_loopback_stall(self):
        recorder = self._recorder()
        loop_release = threading.Event()
        mic_stream = PacedRecordStream(delay_seconds=0.001)
        loop_stream = BlockingRecordStream(loop_release)
        mic = FakeAudioDevice("primary mic", "mic-1", mic_stream)
        loop = FakeAudioDevice("stalled loopback", "loop-1", loop_stream)

        try:
            with patch.object(recorder, "_get_input_mic", return_value=mic), patch.object(
                recorder, "_get_loopback_mic", return_value=loop
            ):
                self.assertTrue(recorder.start_recording())
                time.sleep(0.08)
                stop_started = time.perf_counter()
                saved_path = recorder.stop_recording()
                stop_seconds = time.perf_counter() - stop_started

            self.assertIsNotNone(saved_path)
            self.assertLess(stop_seconds, 1.0)
            self.assertFalse(recorder._thread.is_alive())
            with recorder._lock:
                frames_on_return = sum(len(block) for block in recorder._current_chunk_buffer)
            self.assertEqual(frames_on_return, 0)

            loop_release.set()
            time.sleep(0.1)
            with recorder._lock:
                frames_later = sum(len(block) for block in recorder._current_chunk_buffer)
            self.assertEqual(frames_later, 0)
        finally:
            loop_release.set()
            recorder._stop_event.set()
            recorder._pause_event.set()
            if recorder._thread is not None:
                recorder._thread.join(timeout=2.0)

    def test_stalled_loopback_degrades_to_mic_only_without_blocking_capture(self):
        recorder = self._recorder()
        loop_release = threading.Event()
        mic_stream = PacedRecordStream(delay_seconds=0.005)
        loop_stream = BlockingRecordStream(loop_release)
        mic = FakeAudioDevice("primary mic", "mic-1", mic_stream)
        loop = FakeAudioDevice("stalled loopback", "loop-1", loop_stream)

        try:
            with patch.object(recorder, "_get_input_mic", return_value=mic), patch.object(
                recorder, "_get_loopback_mic", return_value=loop
            ):
                self.assertTrue(recorder.start_recording())
                time.sleep(0.4)

            self.assertTrue(recorder.is_recording)
            self.assertEqual(recorder.loopback_state, "failed")
            self.assertIn("stalled", recorder.last_loopback_error.lower())
            with recorder._lock:
                captured_frames = sum(len(block) for block in recorder._current_chunk_buffer)
            self.assertGreater(captured_frames, 0)
            self.assertGreater(mic_stream.calls, 10)

            loop_release.set()
            time.sleep(0.05)
            self.assertEqual(loop_stream.calls, 1)
        finally:
            recorder.stop_recording()
            loop_release.set()
            recorder._stop_event.set()
            recorder._pause_event.set()
            if recorder._thread is not None:
                recorder._thread.join(timeout=2.0)

    def test_loopback_data_marks_active_when_ready_event_was_evicted(self):
        recorder = self._recorder()
        mic_stream = PacedRecordStream(delay_seconds=0.005, amplitude=0.1)
        loop_stream = PacedRecordStream(delay_seconds=0.001, amplitude=0.2)
        mic = DelayedOpenAudioDevice(
            "slow mic",
            "mic-slow",
            mic_stream,
            open_delay_seconds=0.15,
        )
        loop = FakeAudioDevice("fast loopback", "loop-fast", loop_stream)

        try:
            with patch.object(recorder, "_get_input_mic", return_value=mic), patch.object(
                recorder, "_get_loopback_mic", return_value=loop
            ):
                self.assertTrue(recorder.start_recording())
                time.sleep(0.35)

            self.assertTrue(recorder.is_recording)
            self.assertEqual(recorder.loopback_state, "active")
            self.assertIsNone(recorder.last_loopback_error)
            self.assertGreater(loop_stream.calls, 0)
            with recorder._lock:
                blocks = list(recorder._current_chunk_buffer)
            self.assertTrue(blocks)
            self.assertTrue(
                any(float(np.mean(np.abs(block))) > 0.26 for block in blocks)
            )
        finally:
            recorder.stop_recording()

    def test_stale_loopback_read_error_from_before_resume_is_ignored(self):
        recorder = self._recorder()
        read_entered = threading.Event()
        release_read = threading.Event()
        mic_stream = PacedRecordStream(delay_seconds=0.005, amplitude=0.1)
        loop_stream = StaleThenPacedRecordStream(
            read_entered,
            release_read,
            "stale loop read failed",
            delay_seconds=0.005,
            amplitude=0.2,
        )
        mic = FakeAudioDevice("primary mic", "mic-1", mic_stream)
        loop = FakeAudioDevice("loopback", "loop-1", loop_stream)

        try:
            with patch.object(recorder, "_get_input_mic", return_value=mic), patch.object(
                recorder, "_get_loopback_mic", return_value=loop
            ):
                self.assertTrue(recorder.start_recording())
                self.assertTrue(read_entered.wait(timeout=1.0))
                self.assertTrue(recorder.pause_recording())
                self.assertFalse(recorder.resume_recording())
                release_read.set()
                time.sleep(0.18)

            self.assertTrue(recorder.is_recording)
            self.assertEqual(recorder.loopback_state, "active")
            self.assertIsNone(recorder.last_loopback_error)
            self.assertTrue(recorder._thread.is_alive())
            self.assertGreater(loop_stream.calls, 1)
            with recorder._lock:
                blocks = list(recorder._current_generation.buffer)
            self.assertTrue(blocks)
            self.assertTrue(
                any(float(np.mean(np.abs(block))) > 0.26 for block in blocks)
            )
        finally:
            release_read.set()
            recorder.stop_recording()

    def test_delayed_loopback_open_cannot_revert_stalled_source_to_active(self):
        recorder = self._recorder()
        mic_stream = PacedRecordStream(delay_seconds=0.005)
        loop_stream = PacedRecordStream(delay_seconds=0.005)
        mic = FakeAudioDevice("primary mic", "mic-1", mic_stream)
        loop = DelayedOpenAudioDevice(
            "slow loopback",
            "loop-slow",
            loop_stream,
            open_delay_seconds=0.35,
        )

        try:
            with patch.object(recorder, "_get_input_mic", return_value=mic), patch.object(
                recorder, "_get_loopback_mic", return_value=loop
            ):
                self.assertTrue(recorder.start_recording())
                time.sleep(0.5)

            self.assertTrue(recorder.is_recording)
            self.assertEqual(recorder.loopback_state, "failed")
            self.assertIn("stalled", recorder.last_loopback_error.lower())
            self.assertEqual(loop_stream.calls, 0)
        finally:
            recorder.stop_recording()

    def test_stale_capture_thread_does_not_restart_with_next_recording(self):
        recorder = self._recorder()
        old_loop_release = threading.Event()
        first_mic = FakeAudioDevice("first mic", "mic-1", PacedRecordStream(0.005))
        old_loop_stream = BlockingRecordStream(old_loop_release)
        first_loop = FakeAudioDevice("old loopback", "loop-1", old_loop_stream)

        with patch.object(recorder, "_get_input_mic", return_value=first_mic), patch.object(
            recorder, "_get_loopback_mic", return_value=first_loop
        ):
            self.assertTrue(recorder.start_recording())
            time.sleep(0.05)
            recorder.stop_recording()

        second_mic = FakeAudioDevice("second mic", "mic-2", PacedRecordStream(0.005))
        try:
            with patch.object(recorder, "_get_input_mic", return_value=second_mic), patch.object(
                recorder, "_get_loopback_mic", return_value=None
            ):
                self.assertTrue(recorder.start_recording())
                old_loop_release.set()
                time.sleep(0.1)

            self.assertEqual(old_loop_stream.calls, 1)
            self.assertTrue(recorder.is_recording)
        finally:
            recorder.stop_recording()
            old_loop_release.set()
            recorder._stop_event.set()
            recorder._pause_event.set()
            if recorder._thread is not None:
                recorder._thread.join(timeout=2.0)

    def test_stale_mic_only_backend_cannot_mutate_next_recording(self):
        recorder = self._recorder()
        old_mic_release = threading.Event()
        old_mic_stream = BlockingFailRecordStream(
            old_mic_release,
            "old mic backend failed after release",
        )
        first_mic = FakeAudioDevice("blocked mic", "mic-old", old_mic_stream)

        with patch.object(recorder, "_get_input_mic", return_value=first_mic), patch.object(
            recorder, "_get_loopback_mic", return_value=None
        ):
            self.assertTrue(recorder.start_recording())
            time.sleep(0.05)
            stop_started = time.perf_counter()
            first_saved = recorder.stop_recording()
            first_stop_seconds = time.perf_counter() - stop_started

        self.assertLess(first_stop_seconds, 1.0)
        self.assertIsNone(first_saved)
        self.assertFalse(recorder._thread.is_alive())

        second_mic = FakeAudioDevice("second mic", "mic-new", PacedRecordStream(0.005))
        try:
            with patch.object(recorder, "_get_input_mic", return_value=second_mic), patch.object(
                recorder, "_get_loopback_mic", return_value=None
            ):
                self.assertTrue(recorder.start_recording())
                time.sleep(0.05)
                old_mic_release.set()
                time.sleep(0.1)

            self.assertTrue(recorder.is_recording)
            self.assertTrue(recorder._accept_audio)
            self.assertIsNone(recorder.last_error)
            self.assertTrue(recorder._thread.is_alive())
            with recorder._lock:
                captured_frames = sum(len(block) for block in recorder._current_chunk_buffer)
            self.assertGreater(captured_frames, 0)
        finally:
            recorder.stop_recording()
            old_mic_release.set()
            recorder._stop_event.set()
            recorder._pause_event.set()
            if recorder._thread is not None:
                recorder._thread.join(timeout=2.0)

    def test_mic_read_failure_remains_fatal(self):
        recorder = self._recorder()
        mic_stream = FakeRecordStream(fail_at=0, error_message="mic disconnected")
        mic = FakeAudioDevice("primary mic", "mic-1", mic_stream)

        with patch.object(recorder, "_get_input_mic", return_value=mic), patch.object(
            recorder, "_get_loopback_mic", return_value=None
        ):
            self.assertTrue(recorder.start_recording())
            recorder._thread.join(timeout=1.0)

        self.assertFalse(recorder.is_recording)
        self.assertEqual(recorder.last_error, "mic disconnected")


class AudioRecorderPauseLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="audio_recorder_pause_")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_long_pause_does_not_count_as_loopback_stall_after_resume(self):
        recorder = AudioRecorder(
            output_folder=self.temp_dir,
        )
        mic_stream = PacedRecordStream(delay_seconds=0.001, amplitude=0.1)
        loop_stream = PacedRecordStream(delay_seconds=0.05, amplitude=0.2)
        mic = FakeAudioDevice("fast mic", "mic-fast", mic_stream)
        loop = FakeAudioDevice("slower loopback", "loop-slow", loop_stream)

        try:
            with patch.object(recorder, "_get_input_mic", return_value=mic), patch.object(
                recorder, "_get_loopback_mic", return_value=loop
            ):
                self.assertTrue(recorder.start_recording())
                deadline = time.monotonic() + 1.0
                while recorder.loopback_state != "active" and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertEqual(recorder.loopback_state, "active")
                time.sleep(0.08)

                self.assertTrue(recorder.pause_recording())
                generation = recorder._current_generation
                with recorder._lock:
                    frames_at_pause = sum(len(block) for block in generation.buffer)
                loop_calls_at_pause = loop_stream.calls
                time.sleep(0.45)
                with recorder._lock:
                    frames_during_pause = sum(len(block) for block in generation.buffer)
                self.assertEqual(frames_during_pause, frames_at_pause)

                self.assertFalse(recorder.resume_recording())
                time.sleep(0.18)
                with recorder._lock:
                    blocks = list(generation.buffer)

            self.assertTrue(recorder.is_recording)
            self.assertEqual(recorder.loopback_state, "active")
            self.assertIsNone(recorder.last_loopback_error)
            self.assertGreater(loop_stream.calls, loop_calls_at_pause)
            self.assertGreater(sum(len(block) for block in blocks), frames_during_pause)
            self.assertTrue(
                any(float(np.mean(np.abs(block))) > 0.26 for block in blocks)
            )
        finally:
            recorder.stop_recording()

    def test_stale_mic_read_error_from_before_resume_is_ignored(self):
        recorder = AudioRecorder(
            output_folder=self.temp_dir,
        )
        read_entered = threading.Event()
        release_read = threading.Event()
        mic_stream = StaleThenPacedRecordStream(
            read_entered,
            release_read,
            "stale mic read failed",
        )
        mic = FakeAudioDevice("primary mic", "mic-1", mic_stream)

        try:
            with patch.object(recorder, "_get_input_mic", return_value=mic), patch.object(
                recorder, "_get_loopback_mic", return_value=None
            ):
                self.assertTrue(recorder.start_recording())
                self.assertTrue(read_entered.wait(timeout=1.0))
                self.assertTrue(recorder.pause_recording())
                self.assertFalse(recorder.resume_recording())
                release_read.set()
                time.sleep(0.15)

            self.assertTrue(recorder.is_recording)
            self.assertIsNone(recorder.last_error)
            self.assertTrue(recorder._thread.is_alive())
            self.assertGreater(mic_stream.calls, 1)
            with recorder._lock:
                captured_frames = sum(
                    len(block) for block in recorder._current_generation.buffer
                )
            self.assertGreater(captured_frames, 0)
        finally:
            release_read.set()
            recorder.stop_recording()

    def test_pause_discards_queued_audio_resume_restarts_and_stop_is_prompt(self):
        recorder = AudioRecorder(
            output_folder=self.temp_dir,
        )
        mic_stream = PacedRecordStream(delay_seconds=0.005)
        loop_stream = PacedRecordStream(delay_seconds=0.005)
        mic = FakeAudioDevice("primary mic", "mic-1", mic_stream)
        loop = FakeAudioDevice("loopback", "loop-1", loop_stream)

        with patch.object(recorder, "_get_input_mic", return_value=mic), patch.object(
            recorder, "_get_loopback_mic", return_value=loop
        ):
            self.assertTrue(recorder.start_recording())
            time.sleep(0.08)
            self.assertTrue(recorder.pause_recording())
            generation = recorder._current_generation
            with recorder._lock:
                frames_at_pause = sum(len(block) for block in generation.buffer)
            mic_calls_at_pause = mic_stream.calls
            loop_calls_at_pause = loop_stream.calls

            time.sleep(0.15)
            with recorder._lock:
                frames_while_paused = sum(len(block) for block in generation.buffer)
            mic_calls_while_paused = mic_stream.calls
            loop_calls_while_paused = loop_stream.calls

            self.assertTrue(recorder.is_paused)
            self.assertFalse(generation.pause_event.is_set())
            self.assertEqual(frames_while_paused, frames_at_pause)
            self.assertLessEqual(mic_calls_while_paused, mic_calls_at_pause + 1)
            self.assertLessEqual(loop_calls_while_paused, loop_calls_at_pause + 1)

            self.assertFalse(recorder.resume_recording())
            time.sleep(0.08)
            with recorder._lock:
                frames_after_resume = sum(len(block) for block in generation.buffer)
            self.assertGreater(frames_after_resume, frames_while_paused)
            self.assertGreater(mic_stream.calls, mic_calls_while_paused)
            self.assertGreater(loop_stream.calls, loop_calls_while_paused)

            self.assertTrue(recorder.pause_recording())
            stop_started = time.perf_counter()
            saved_path = recorder.stop_recording()
            stop_seconds = time.perf_counter() - stop_started

        self.assertLess(stop_seconds, 1.0)
        self.assertFalse(generation.worker.is_alive())
        self.assertIsNotNone(saved_path)
        self.assertTrue(os.path.exists(saved_path))


class AudioRecorderGenerationIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="audio_recorder_generation_")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_stop_during_device_discovery_cancels_pending_start(self):
        recorder = AudioRecorder(
            output_folder=self.temp_dir,
        )
        discovery_entered = threading.Event()
        release_discovery = threading.Event()
        blocked_stream = PacedRecordStream(delay_seconds=0.005)
        blocked_mic = FakeAudioDevice("blocked mic", "mic-blocked", blocked_stream)
        start_results = []

        def blocked_discovery():
            discovery_entered.set()
            release_discovery.wait(timeout=3.0)
            return blocked_mic

        def start_caller():
            start_results.append(recorder.start_recording())

        start_thread = threading.Thread(target=start_caller)
        with patch.object(recorder, "_get_input_mic", side_effect=blocked_discovery), patch.object(
            recorder, "_get_loopback_mic", return_value=None
        ):
            start_thread.start()
            self.assertTrue(discovery_entered.wait(timeout=1.0))
            stop_started = time.perf_counter()
            stopped_path = recorder.stop_recording()
            stop_seconds = time.perf_counter() - stop_started
            release_discovery.set()
            start_thread.join(timeout=2.0)

        self.assertLess(stop_seconds, 0.25)
        self.assertIsNone(stopped_path)
        self.assertEqual(start_results, [False])
        self.assertFalse(start_thread.is_alive())
        self.assertFalse(recorder.is_recording)
        self.assertIsNone(recorder._current_generation)
        self.assertEqual(blocked_stream.calls, 0)
        self.assertFalse(
            any(
                thread.name.startswith("audio-recorder-")
                or thread.name.startswith("audio-capture-")
                for thread in threading.enumerate()
            )
        )

        next_stream = PacedRecordStream(delay_seconds=0.005)
        next_mic = FakeAudioDevice("next mic", "mic-next", next_stream)
        try:
            with patch.object(recorder, "_get_input_mic", return_value=next_mic), patch.object(
                recorder, "_get_loopback_mic", return_value=None
            ):
                self.assertTrue(recorder.start_recording())
                time.sleep(0.08)
            self.assertTrue(recorder.is_recording)
            self.assertGreater(next_stream.calls, 0)
        finally:
            recorder.stop_recording()

    def test_stop_cancels_all_start_calls_queued_before_it(self):
        recorder = AudioRecorder(
            output_folder=self.temp_dir,
        )
        tracking_lock = TrackingLock()
        recorder._start_lock = tracking_lock
        first_discovery_entered = threading.Event()
        release_first_discovery = threading.Event()
        discovery_calls = 0
        discovery_lock = threading.Lock()
        streams = []
        results = []

        def discover_mic():
            nonlocal discovery_calls
            with discovery_lock:
                discovery_calls += 1
                index = discovery_calls
            stream = PacedRecordStream(delay_seconds=0.005)
            streams.append(stream)
            if index == 1:
                first_discovery_entered.set()
                release_first_discovery.wait(timeout=3.0)
            return FakeAudioDevice(f"mic-{index}", f"mic-{index}", stream)

        start_barrier = threading.Barrier(3)

        def start_caller():
            start_barrier.wait()
            results.append(recorder.start_recording())

        callers = [threading.Thread(target=start_caller) for _ in range(2)]
        try:
            with patch.object(recorder, "_get_input_mic", side_effect=discover_mic), patch.object(
                recorder, "_get_loopback_mic", return_value=None
            ):
                for caller in callers:
                    caller.start()
                start_barrier.wait()
                self.assertTrue(first_discovery_entered.wait(timeout=1.0))
                self.assertTrue(tracking_lock.second_attempted.wait(timeout=1.0))

                stop_started = time.perf_counter()
                self.assertIsNone(recorder.stop_recording())
                stop_seconds = time.perf_counter() - stop_started
                release_first_discovery.set()
                for caller in callers:
                    caller.join(timeout=2.0)

            self.assertLess(stop_seconds, 0.25)
            self.assertCountEqual(results, [False, False])
            self.assertEqual(discovery_calls, 1)
            self.assertFalse(recorder.is_recording)
            self.assertIsNone(recorder._current_generation)
            self.assertTrue(all(stream.calls == 0 for stream in streams))

            next_stream = PacedRecordStream(delay_seconds=0.005)
            next_mic = FakeAudioDevice("next mic", "mic-next", next_stream)
            with patch.object(recorder, "_get_input_mic", return_value=next_mic), patch.object(
                recorder, "_get_loopback_mic", return_value=None
            ):
                self.assertTrue(recorder.start_recording())
                time.sleep(0.08)
            self.assertTrue(recorder.is_recording)
            self.assertGreater(next_stream.calls, 0)
        finally:
            release_first_discovery.set()
            recorder.stop_recording()
            for caller in callers:
                caller.join(timeout=1.0)

    def test_concurrent_start_calls_create_only_one_generation(self):
        recorder = AudioRecorder(
            output_folder=self.temp_dir,
        )
        caller_barrier = threading.Barrier(3)
        discovery_calls = 0
        discovery_lock = threading.Lock()
        streams = []
        generations = []
        generation_lock = threading.Lock()
        original_record_worker = recorder._record_worker

        def discover_mic():
            nonlocal discovery_calls
            with discovery_lock:
                discovery_calls += 1
                index = discovery_calls
            time.sleep(0.1)
            stream = PacedRecordStream(delay_seconds=0.005)
            streams.append(stream)
            return FakeAudioDevice(f"mic-{index}", f"mic-{index}", stream)

        def tracked_record_worker(mic, generation):
            with generation_lock:
                generations.append(generation)
            return original_record_worker(mic, generation)

        results = []

        def start_caller():
            caller_barrier.wait()
            results.append(recorder.start_recording())

        callers = [threading.Thread(target=start_caller) for _ in range(2)]
        try:
            with patch.object(recorder, "_get_input_mic", side_effect=discover_mic), patch.object(
                recorder, "_get_loopback_mic", return_value=None
            ), patch.object(
                recorder,
                "_record_worker",
                side_effect=tracked_record_worker,
            ):
                for caller in callers:
                    caller.start()
                caller_barrier.wait()
                for caller in callers:
                    caller.join(timeout=2.0)

                self.assertEqual(results, [True, True])
                self.assertEqual(discovery_calls, 1)
                self.assertEqual(len(generations), 1)
                generation = generations[0]
                time.sleep(0.08)
                calls_before_stop = streams[0].calls
                saved_path = recorder.stop_recording()
                calls_on_return = streams[0].calls
                time.sleep(0.08)

            self.assertIsNotNone(saved_path)
            self.assertFalse(generation.worker.is_alive())
            self.assertEqual(streams[0].calls, calls_on_return)
            self.assertGreaterEqual(calls_on_return, calls_before_stop)
            live_names = {
                thread.name
                for thread in threading.enumerate()
                if generation.token[:8] in thread.name
            }
            self.assertEqual(live_names, set())
        finally:
            recorder.stop_recording()
            for caller in callers:
                caller.join(timeout=1.0)


class AudioRecorderSilenceGateTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="audio_recorder_silence_gate_")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _generation(self, recorder):
        generation = RecordingGeneration(
            token="gate",
            base_filename="gate",
            start_time=100.0,
            last_split_time=100.0,
        )
        with recorder._lock:
            recorder._current_generation = generation
            recorder._sync_public_from_generation_locked(generation)
        return generation

    def test_silence_is_trimmed_without_stopping_recording(self):
        recorder = AudioRecorder(output_folder=self.temp_dir)
        generation = self._generation(recorder)
        silence = np.zeros((recorder.BLOCK_SIZE, recorder.CHANNELS), dtype=np.float32)

        for _ in range(200):
            recorder._process_and_append(silence, None, generation)

        self.assertEqual(recorder.CONTEXT_SECONDS, 1.2)
        self.assertEqual(generation.buffer, [])
        self.assertTrue(generation.is_recording)
        self.assertFalse(generation.stop_event.is_set())

    def test_gate_keeps_context_before_and_after_audio_and_drops_middle_silence(self):
        block_seconds = AudioRecorder.BLOCK_SIZE / float(AudioRecorder.SR)
        recorder = AudioRecorder(
            output_folder=self.temp_dir,
            silence_threshold=0.05,
            context_seconds=block_seconds * 2,
        )
        generation = self._generation(recorder)
        silence = np.zeros((recorder.BLOCK_SIZE, recorder.CHANNELS), dtype=np.float32)
        sound = np.full(
            (recorder.BLOCK_SIZE, recorder.CHANNELS),
            0.2,
            dtype=np.float32,
        )

        for _ in range(4):
            recorder._process_and_append(silence, None, generation)
        recorder._process_and_append(sound, None, generation)
        recorder._process_and_append(silence, None, generation)
        recorder._process_and_append(sound, None, generation)
        for _ in range(6):
            recorder._process_and_append(silence, None, generation)
        recorder._process_and_append(sound, None, generation)

        self.assertEqual(len(generation.buffer), 10)
        self.assertEqual(sum(len(block) for block in generation.buffer), 10 * recorder.BLOCK_SIZE)
        self.assertTrue(np.allclose(generation.buffer[0], 0.0))
        self.assertTrue(np.allclose(generation.buffer[1], 0.0))
        self.assertGreater(float(np.max(np.abs(generation.buffer[2]))), 0.0)
        self.assertTrue(np.allclose(generation.buffer[-3], 0.0))
        self.assertTrue(np.allclose(generation.buffer[-2], 0.0))
        self.assertGreater(float(np.max(np.abs(generation.buffer[-1]))), 0.0)
        self.assertTrue(generation.is_recording)
        self.assertFalse(generation.stop_event.is_set())


class AudioRecorderSnapshotNamingTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="audio_recorder_snapshot_")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_recording_names_do_not_collide_when_clock_and_uuid_repeat(self):
        recorder = AudioRecorder(output_folder=self.temp_dir)
        fixed_now = types.SimpleNamespace(
            strftime=lambda _fmt: "20260409_010101_000000"
        )
        fixed_uuid = types.SimpleNamespace(hex="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
        paths = []

        with patch("src.utils.AudioRecorder.datetime") as mock_datetime, patch(
            "src.utils.AudioRecorder.uuid.uuid4",
            return_value=fixed_uuid,
        ):
            mock_datetime.now.return_value = fixed_now
            for index in range(2):
                mic_stream = FakeRecordStream(recorder=recorder, stop_after=2)
                mic = FakeAudioDevice(f"mic-{index}", f"mic-{index}", mic_stream)
                with patch.object(
                    recorder, "_get_input_mic", return_value=mic
                ), patch.object(recorder, "_get_loopback_mic", return_value=None):
                    self.assertTrue(recorder.start_recording())
                    recorder._thread.join(timeout=1.0)
                    paths.append(recorder.stop_recording())

        self.assertEqual(len(set(paths)), 2)
        self.assertTrue(all(path and os.path.exists(path) for path in paths))
        self.assertEqual(len(list(Path(self.temp_dir).glob("*.wav"))), 2)

    def test_snapshot_names_are_unique_for_rapid_reentry(self):
        recorder = AudioRecorder(output_folder=self.temp_dir)
        recorder._current_chunk_buffer = [
            np.zeros((16, recorder.CHANNELS), dtype=np.float32)
        ]
        fixed_now = types.SimpleNamespace(
            strftime=lambda _fmt: "20260409_010101_000000"
        )

        with patch("src.utils.AudioRecorder.datetime") as mock_datetime, patch(
            "src.utils.AudioRecorder.uuid.uuid4",
            side_effect=[
                types.SimpleNamespace(hex="aaaaaaaaaaaaaaaa"),
                types.SimpleNamespace(hex="bbbbbbbbbbbbbbbb"),
            ],
        ):
            mock_datetime.now.return_value = fixed_now
            first = recorder.save_snapshot()
            second = recorder.save_snapshot()

        self.assertNotEqual(first, second)
        self.assertTrue(os.path.exists(first))
        self.assertTrue(os.path.exists(second))

    def test_peak_limiter_preserves_waveform_shape_without_hard_clipping(self):
        recorder = AudioRecorder(output_folder=self.temp_dir)
        values = np.array([1.0, 0.5, 0.25, -1.0], dtype=np.float32)
        mic = np.repeat(values[:, None], recorder.CHANNELS, axis=1)
        loop = np.zeros_like(mic)

        recorder._process_and_append(mic, loop)
        mixed = recorder._current_chunk_buffer[-1][:, 0]

        self.assertLessEqual(float(np.max(np.abs(mixed))), recorder.LIMITER_PEAK)
        self.assertAlmostEqual(float(mixed[0] / mixed[1]), 2.0, places=4)
        self.assertAlmostEqual(float(mixed[1] / mixed[2]), 2.0, places=4)
        self.assertAlmostEqual(float(mixed[0]), -float(mixed[3]), places=4)

    def test_default_auto_split_duration_is_ten_minutes(self):
        recorder = AudioRecorder(output_folder=self.temp_dir)

        self.assertEqual(recorder.SPLIT_DURATION, 600)


if __name__ == "__main__":
    unittest.main()
