import math
import os
import queue
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from src.utils import soundcard_patch as _soundcard_patch  # noqa: F401

import soundcard as sc
import soundfile as sf


@dataclass
class StartReservation:
    token: str
    lifecycle_epoch: int
    cancelled: threading.Event = field(default_factory=threading.Event)


@dataclass
class RecordingGeneration:
    token: str
    base_filename: str
    stop_event: threading.Event = field(default_factory=threading.Event)
    pause_event: threading.Event = field(default_factory=threading.Event)
    finished_event: threading.Event = field(default_factory=threading.Event)
    accept_audio: bool = True
    buffer: list = field(default_factory=list)
    pending_silence: list = field(default_factory=list)
    pre_roll: deque = field(default_factory=deque)
    has_sound: bool = False
    worker: threading.Thread | None = None
    is_recording: bool = True
    is_paused: bool = False
    last_error: str | None = None
    last_saved_path: str | None = None
    last_loopback_error: str | None = None
    stop_reason: str | None = None
    selected_mic_name: str | None = None
    selected_mic_id: str | None = None
    selected_loopback_name: str | None = None
    selected_loopback_id: str | None = None
    loopback_state: str = "inactive"
    split_index: int = 0
    start_time: float | None = None
    pause_start_time: float | None = None
    total_paused_seconds: float = 0.0
    last_split_time: float | None = None
    limiter_gain: float = 1.0
    resume_epoch: int = 0

    def __post_init__(self):
        self.pause_event.set()


class AudioRecorder:
    """Crash-safe recorder that trims long silence without stopping the session."""

    SR = 48000
    CHANNELS = 2
    BLOCK_SIZE = 1024
    CONTEXT_SECONDS = 1.2

    def __init__(
        self,
        output_folder="src/rec_list",
        split_duration=600,
        silence_threshold=0.01,
        context_seconds=CONTEXT_SECONDS,
    ):
        self.output_folder = output_folder
        self.temp_folder = os.path.join(output_folder, "temp")
        os.makedirs(self.output_folder, exist_ok=True)
        os.makedirs(self.temp_folder, exist_ok=True)

        self.SPLIT_DURATION = split_duration
        self.silence_threshold = float(silence_threshold)
        self.CONTEXT_SECONDS = max(0.0, float(context_seconds))

        self.MIC_GAIN = 3.0
        self.LOOP_GAIN = 0.35
        self.MASTER_GAIN = 0.8
        self.LIMITER_PEAK = 0.95

        self._lock = threading.RLock()
        self._start_lock = threading.Lock()
        self._lifecycle_epoch = 0
        self._pending_start: StartReservation | None = None
        self._current_generation: RecordingGeneration | None = None
        self._reserved_basenames: set[str] = set()
        self._reserved_output_paths: set[str] = set()
        self._capture_queue_blocks = 8
        self._loopback_stall_seconds = 0.25
        self._capture_thread_join_seconds = 0.25

        # Public compatibility state. Runtime workers mutate generation state and
        # publish here only when their generation is still current.
        self.is_recording = False
        self.is_paused = False
        self.last_saved_path = None
        self.last_error = None
        self.last_loopback_error = None
        self.last_stop_reason = None
        self.current_base_filename = None
        self.selected_mic_name = None
        self.selected_mic_id = None
        self.selected_loopback_name = None
        self.selected_loopback_id = None
        self.loopback_state = "inactive"
        self.split_index = 0
        self.start_time = None
        self.pause_start_time = None
        self.total_paused_seconds = 0.0
        self.last_split_time = None
        self._limiter_gain = 1.0
        self._current_chunk_buffer = []
        self._pending_silence_buffer = []
        self._pre_roll_buffer = deque(maxlen=self._context_block_count())
        self._has_sound = False
        self._accept_audio = True
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._worker_finished = threading.Event()
        self._thread = None

    # ---------------- PUBLIC API ----------------

    def start_recording(self):
        with self._lock:
            request_epoch = self._lifecycle_epoch

        with self._start_lock:
            with self._lock:
                if request_epoch != self._lifecycle_epoch:
                    return False
                current = self._current_generation
                if current is not None and current.is_recording:
                    return True
                reservation = StartReservation(
                    token=uuid.uuid4().hex,
                    lifecycle_epoch=request_epoch,
                )
                self._pending_start = reservation

            print(f"--- Starting Recording (Auto-Split every {self.SPLIT_DURATION}s) ---")
            try:
                mic = self._get_input_mic()
            except Exception as exc:
                with self._lock:
                    if self._pending_start is reservation:
                        self._pending_start = None
                    if reservation.cancelled.is_set():
                        return False
                    self.last_error = str(exc)
                    self.is_recording = False
                    self.is_paused = False
                print(f"Recording Error: {self.last_error}")
                return False

            now = time.time()
            with self._lock:
                if (
                    reservation.cancelled.is_set()
                    or reservation.lifecycle_epoch != self._lifecycle_epoch
                    or self._pending_start is not reservation
                ):
                    if self._pending_start is reservation:
                        self._pending_start = None
                    return False

                generation = RecordingGeneration(
                    token=reservation.token,
                    base_filename=self._allocate_recording_base_filename(
                        reservation.token
                    ),
                    pre_roll=deque(maxlen=self._context_block_count()),
                    start_time=now,
                    last_split_time=now,
                )
                worker = threading.Thread(
                    target=self._record_worker,
                    args=(mic, generation),
                    name=f"audio-recorder-{reservation.token[:8]}",
                    daemon=True,
                )
                generation.worker = worker
                self._pending_start = None
                self._current_generation = generation
                self._sync_public_from_generation_locked(generation)
                worker.start()
            return True

    def stop_recording(self):
        with self._lock:
            self._lifecycle_epoch += 1
            pending_start = self._pending_start
            if pending_start is not None:
                pending_start.cancelled.set()
            generation = self._current_generation
            if generation is None:
                self.is_recording = False
                self.is_paused = False
                return None
            worker = generation.worker
            has_buffered_audio = bool(generation.buffer or generation.pending_silence)
            if (
                not generation.is_recording
                and not (worker and worker.is_alive())
                and not has_buffered_audio
            ):
                return None

            print("--- Stopping Recording ---")
            generation.stop_reason = "manual"
            generation.is_recording = False
            generation.is_paused = False
            generation.stop_event.set()
            generation.pause_event.set()
            self._sync_public_from_generation_locked(generation)

        if worker and worker.is_alive():
            generation.finished_event.wait(timeout=3.0)
            worker.join(timeout=0)

        with self._lock:
            generation.accept_audio = False
            if worker and worker.is_alive() and generation.last_error is None:
                generation.last_error = "Recorder worker did not stop cleanly."
            self._sync_public_from_generation_locked(generation)

        return self._save_current_split(final=True, generation=generation)

    def pause_recording(self):
        with self._lock:
            generation = self._current_generation
            if generation is None or not generation.is_recording or generation.is_paused:
                return False
            generation.is_paused = True
            generation.pause_event.clear()
            generation.pause_start_time = time.time()
            self._sync_public_from_generation_locked(generation)
        print("Recording Paused.")
        return True

    def resume_recording(self):
        with self._lock:
            generation = self._current_generation
            if generation is None or not generation.is_recording or not generation.is_paused:
                return True
            generation.is_paused = False
            generation.resume_epoch += 1
            generation.pause_event.set()
            if generation.pause_start_time:
                paused_duration = time.time() - generation.pause_start_time
                generation.total_paused_seconds += paused_duration
                if generation.last_split_time is not None:
                    generation.last_split_time += paused_duration
                generation.pause_start_time = None
            self._sync_public_from_generation_locked(generation)
        print("Recording Resumed.")
        return False

    def get_duration(self):
        with self._lock:
            generation = self._current_generation
            if generation is None or not generation.is_recording or not generation.start_time:
                return 0.0
            elapsed = time.time() - generation.start_time
            current_pause = 0.0
            if generation.is_paused and generation.pause_start_time:
                current_pause = time.time() - generation.pause_start_time
            return max(
                0.0,
                elapsed - generation.total_paused_seconds - current_pause,
            )

    def save_snapshot(self):
        with self._lock:
            generation = self._current_generation
            if generation is None:
                blocks = list(self._current_chunk_buffer) + list(
                    self._pending_silence_buffer
                )
            else:
                blocks = list(generation.buffer) + list(generation.pending_silence)
            if not blocks:
                return None
            full_audio = np.concatenate(blocks)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        filename = f"snapshot_{timestamp}_{uuid.uuid4().hex[:8]}.wav"
        filepath = os.path.join(self.temp_folder, filename)
        try:
            sf.write(filepath, full_audio, self.SR, format="WAV", subtype="PCM_16")
            print(f"Saved snapshot: {filename}")
            return filepath
        except Exception as exc:
            print(f"Snapshot Save Error: {exc}")
            return None

    # ---------------- GENERATION STATE ----------------

    def _allocate_recording_base_filename(self, token):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        root = f"rec_{timestamp}_{token[:8]}"
        with self._lock:
            candidate = root
            suffix = 1
            while (
                candidate in self._reserved_basenames
                or os.path.exists(os.path.join(self.output_folder, f"{candidate}.wav"))
            ):
                candidate = f"{root}_{suffix}"
                suffix += 1
            self._reserved_basenames.add(candidate)
            return candidate

    def _allocate_output_path(self, generation):
        if generation.split_index == 0:
            stem = generation.base_filename
        else:
            stem = f"{generation.base_filename}_{generation.split_index}"

        with self._lock:
            candidate = os.path.join(self.output_folder, f"{stem}.wav")
            suffix = 1
            while candidate in self._reserved_output_paths or os.path.exists(candidate):
                candidate = os.path.join(
                    self.output_folder,
                    f"{stem}_collision_{suffix}.wav",
                )
                suffix += 1
            self._reserved_output_paths.add(candidate)
            return candidate

    def _sync_public_from_generation_locked(self, generation):
        if self._current_generation is not generation:
            return
        self.is_recording = generation.is_recording
        self.is_paused = generation.is_paused
        self.last_saved_path = generation.last_saved_path
        self.last_error = generation.last_error
        self.last_loopback_error = generation.last_loopback_error
        self.last_stop_reason = generation.stop_reason
        self.current_base_filename = generation.base_filename
        self.selected_mic_name = generation.selected_mic_name
        self.selected_mic_id = generation.selected_mic_id
        self.selected_loopback_name = generation.selected_loopback_name
        self.selected_loopback_id = generation.selected_loopback_id
        self.loopback_state = generation.loopback_state
        self.split_index = generation.split_index
        self.start_time = generation.start_time
        self.pause_start_time = generation.pause_start_time
        self.total_paused_seconds = generation.total_paused_seconds
        self.last_split_time = generation.last_split_time
        self._limiter_gain = generation.limiter_gain
        self._current_chunk_buffer = generation.buffer
        self._pending_silence_buffer = generation.pending_silence
        self._pre_roll_buffer = generation.pre_roll
        self._has_sound = generation.has_sound
        self._accept_audio = generation.accept_audio
        self._stop_event = generation.stop_event
        self._pause_event = generation.pause_event
        self._worker_finished = generation.finished_event
        self._thread = generation.worker

    def _publish_generation(self, generation):
        with self._lock:
            self._sync_public_from_generation_locked(generation)

    # ---------------- DEVICE DISCOVERY ----------------

    def _get_loopback_mic(self):
        try:
            speaker = sc.default_speaker()
            return sc.get_microphone(id=str(speaker.id), include_loopback=True)
        except Exception:
            try:
                for mic in sc.all_microphones(include_loopback=True):
                    if getattr(mic, "isloopback", False):
                        return mic
            except Exception:
                pass
        return None

    def _get_input_mic(self):
        errors = []
        try:
            return sc.default_microphone()
        except Exception as exc:
            errors.append(f"default_microphone failed: {exc}")

        try:
            microphones = sc.all_microphones(include_loopback=False)
        except TypeError:
            microphones = sc.all_microphones()
        except Exception as exc:
            errors.append(f"all_microphones failed: {exc}")
            microphones = []

        for mic in microphones:
            if not getattr(mic, "isloopback", False):
                return mic

        detail = " | ".join(errors) if errors else "No input microphone found."
        raise RuntimeError(
            "Khong tim thay microphone kha dung. "
            "May cong ty co the dang chan quyen microphone hoac chua co default input device. "
            f"Chi tiet: {detail}"
        )

    # ---------------- RECORDING WORKERS ----------------

    def _record_worker(self, mic, generation):
        try:
            if generation.stop_event.is_set():
                return
            self._remember_mic_device(mic, generation)
            loop = self._get_loopback_mic()
            if generation.stop_event.is_set():
                return
            if loop is None:
                self._mark_loopback_unavailable(
                    "No loopback device found.",
                    generation,
                )
            else:
                self._remember_loopback_device(loop, generation)

            print(f"Mic: {mic.name} | Loop: {loop.name if loop else 'None'}")
            self._loop_processing_concurrent(mic, loop, generation)
        except Exception as exc:
            generation.last_error = str(exc)
            generation.is_recording = False
            generation.is_paused = False
            generation.stop_event.set()
            generation.pause_event.set()
            self._publish_generation(generation)
            print(f"Recording Error: {generation.last_error}")
        finally:
            generation.is_recording = False
            generation.is_paused = False
            with self._lock:
                generation.accept_audio = False
                generation.finished_event.set()
                self._sync_public_from_generation_locked(generation)

    def _remember_mic_device(self, mic, generation):
        generation.selected_mic_name = getattr(mic, "name", None)
        generation.selected_mic_id = getattr(mic, "id", None)
        self._publish_generation(generation)

    def _remember_loopback_device(self, loop, generation):
        generation.selected_loopback_name = getattr(loop, "name", None)
        generation.selected_loopback_id = getattr(loop, "id", None)
        self._publish_generation(generation)

    def _mark_loopback_unavailable(self, reason, generation):
        generation.loopback_state = "unavailable"
        generation.last_loopback_error = reason
        self._publish_generation(generation)
        print(f"Loopback unavailable: {reason} Continuing microphone recording.")

    def _mark_loopback_active(self, generation):
        if generation.loopback_state == "failed":
            return False
        if generation.loopback_state != "active":
            generation.loopback_state = "active"
            generation.last_loopback_error = None
            self._publish_generation(generation)
        return True

    def _mark_loopback_failed(self, error, generation):
        generation.loopback_state = "failed"
        generation.last_loopback_error = str(error)
        self._publish_generation(generation)
        print(
            f"Loopback Error: {generation.last_loopback_error}. "
            "Continuing microphone recording."
        )

    def _put_capture_event(
        self,
        output_queue,
        event,
        *,
        latest_only,
        source_stop_event,
        generation_stop_event,
    ):
        if source_stop_event.is_set() or generation_stop_event.is_set():
            return
        if latest_only:
            while not source_stop_event.is_set() and not generation_stop_event.is_set():
                try:
                    output_queue.put_nowait(event)
                    return
                except queue.Full:
                    try:
                        output_queue.get_nowait()
                    except queue.Empty:
                        pass
            return

        while not source_stop_event.is_set() and not generation_stop_event.is_set():
            try:
                output_queue.put(event, timeout=0.05)
                return
            except queue.Full:
                continue

    def _capture_device_worker(
        self,
        device,
        source_name,
        output_queue,
        source_stop_event,
        generation,
    ):
        latest_only = source_name == "loopback"
        try:
            context = device.recorder(
                samplerate=self.SR,
                channels=self.CHANNELS,
                blocksize=self.BLOCK_SIZE,
            )
            with context as recorder:
                if source_stop_event.is_set() or generation.stop_event.is_set():
                    return
                if source_name == "loopback":
                    self._put_capture_event(
                        output_queue,
                        ("ready", None, generation.resume_epoch),
                        latest_only=True,
                        source_stop_event=source_stop_event,
                        generation_stop_event=generation.stop_event,
                    )
                while not source_stop_event.is_set() and not generation.stop_event.is_set():
                    generation.pause_event.wait()
                    if source_stop_event.is_set() or generation.stop_event.is_set():
                        break
                    capture_epoch = generation.resume_epoch
                    try:
                        data = recorder.record(numframes=self.BLOCK_SIZE)
                    except Exception as exc:
                        if source_stop_event.is_set() or generation.stop_event.is_set():
                            break
                        if (
                            not generation.pause_event.is_set()
                            or generation.resume_epoch != capture_epoch
                        ):
                            continue
                        self._put_capture_event(
                            output_queue,
                            ("error", exc, capture_epoch),
                            latest_only=latest_only,
                            source_stop_event=source_stop_event,
                            generation_stop_event=generation.stop_event,
                        )
                        return
                    if source_stop_event.is_set() or generation.stop_event.is_set():
                        break
                    if (
                        not generation.pause_event.is_set()
                        or generation.resume_epoch != capture_epoch
                    ):
                        continue
                    self._put_capture_event(
                        output_queue,
                        ("data", data, capture_epoch),
                        latest_only=latest_only,
                        source_stop_event=source_stop_event,
                        generation_stop_event=generation.stop_event,
                    )
        except Exception as exc:
            self._put_capture_event(
                output_queue,
                ("error", exc, generation.resume_epoch),
                latest_only=latest_only,
                source_stop_event=source_stop_event,
                generation_stop_event=generation.stop_event,
            )
        finally:
            self._put_capture_event(
                output_queue,
                ("eof", None, generation.resume_epoch),
                latest_only=latest_only,
                source_stop_event=source_stop_event,
                generation_stop_event=generation.stop_event,
            )

    def _loop_processing_concurrent(self, mic, loop, generation):
        mic_stop_event = threading.Event()
        loop_stop_event = threading.Event()
        mic_queue = queue.Queue(maxsize=self._capture_queue_blocks)
        loop_queue = queue.Queue(maxsize=2)
        mic_thread = threading.Thread(
            target=self._capture_device_worker,
            args=(
                mic,
                "microphone",
                mic_queue,
                mic_stop_event,
                generation,
            ),
            name=f"audio-capture-microphone-{generation.token[:8]}",
            daemon=True,
        )
        loop_thread = None
        if loop is not None:
            loop_thread = threading.Thread(
                target=self._capture_device_worker,
                args=(
                    loop,
                    "loopback",
                    loop_queue,
                    loop_stop_event,
                    generation,
                ),
                name=f"audio-capture-loopback-{generation.token[:8]}",
                daemon=True,
            )
            loop_thread.start()
        mic_thread.start()

        loop_enabled = loop_thread is not None
        mic_ended = False
        last_loopback_data_at = time.monotonic()
        observed_resume_epoch = generation.resume_epoch
        try:
            while True:
                try:
                    mic_kind, mic_payload, mic_epoch = mic_queue.get(timeout=0.05)
                except queue.Empty:
                    if not mic_thread.is_alive() and mic_queue.empty():
                        mic_ended = True
                        break
                    if generation.stop_event.is_set() and mic_queue.empty():
                        break
                    continue

                if mic_epoch != generation.resume_epoch:
                    continue
                if mic_kind == "error":
                    raise mic_payload
                if mic_kind == "eof":
                    mic_ended = True
                    break
                if generation.is_paused or not generation.pause_event.is_set():
                    continue
                if generation.resume_epoch != observed_resume_epoch:
                    observed_resume_epoch = generation.resume_epoch
                    last_loopback_data_at = time.monotonic()

                loop_data = None
                if loop_enabled:
                    while True:
                        try:
                            loop_kind, loop_payload, loop_epoch = loop_queue.get_nowait()
                        except queue.Empty:
                            break
                        if loop_epoch != generation.resume_epoch:
                            continue
                        if loop_kind == "ready":
                            self._mark_loopback_active(generation)
                            last_loopback_data_at = time.monotonic()
                        elif loop_kind == "data":
                            if self._mark_loopback_active(generation):
                                loop_data = loop_payload
                                last_loopback_data_at = time.monotonic()
                        elif loop_kind == "error":
                            self._mark_loopback_failed(loop_payload, generation)
                            loop_stop_event.set()
                            loop_enabled = False
                            break
                        elif loop_kind == "eof":
                            loop_stop_event.set()
                            loop_enabled = False
                            break

                    if (
                        loop_enabled
                        and not generation.stop_event.is_set()
                        and time.monotonic() - last_loopback_data_at
                        >= self._loopback_stall_seconds
                    ):
                        self._mark_loopback_failed(
                            TimeoutError(
                                "Loopback capture stalled for "
                                f"{self._loopback_stall_seconds:.2f}s"
                            ),
                            generation,
                        )
                        loop_stop_event.set()
                        loop_enabled = False

                self._process_and_append(mic_payload, loop_data, generation)
                self._after_capture_block(generation)

            if mic_ended and not generation.stop_event.is_set():
                raise RuntimeError("Microphone capture ended unexpectedly.")
        finally:
            mic_stop_event.set()
            loop_stop_event.set()
            generation.stop_event.set()
            generation.pause_event.set()
            mic_thread.join(timeout=self._capture_thread_join_seconds)
            if loop_thread is not None:
                loop_thread.join(timeout=self._capture_thread_join_seconds)

    # ---------------- AUDIO PROCESSING ----------------

    def _after_capture_block(self, generation):
        if not generation.is_paused:
            current_time = time.time()
            segment_duration = current_time - (generation.last_split_time or current_time)
            if segment_duration >= self.SPLIT_DURATION:
                print(f"--- Auto-splitting file after {segment_duration:.1f}s ---")
                self._save_current_split(final=False, generation=generation)
                generation.last_split_time = current_time
                self._publish_generation(generation)

    def _process_and_append(self, mic_data, loop_data, generation=None):
        sound_active = self._is_audio_active(mic_data, self.MIC_GAIN) or (
            self._is_audio_active(loop_data, self.LOOP_GAIN)
            if loop_data is not None
            else False
        )

        if loop_data is not None:
            frame_count = min(len(mic_data), len(loop_data))
            mixed = (
                mic_data[:frame_count] * self.MIC_GAIN
                + loop_data[:frame_count] * self.LOOP_GAIN
            ) * self.MASTER_GAIN
        else:
            mixed = mic_data * self.MIC_GAIN * self.MASTER_GAIN

        mixed = np.nan_to_num(mixed, nan=0.0, posinf=0.0, neginf=0.0)
        peak = float(np.max(np.abs(mixed))) if mixed.size else 0.0

        with self._lock:
            if generation is None:
                limiter_gain = self._limiter_gain
                accept_audio = self._accept_audio
            else:
                limiter_gain = generation.limiter_gain
                accept_audio = generation.accept_audio
                if generation.is_paused or not generation.pause_event.is_set():
                    return
            if not accept_audio:
                return

            target_gain = min(1.0, self.LIMITER_PEAK / peak) if peak > 0.0 else 1.0
            limiter_gain = (
                target_gain
                if target_gain < limiter_gain
                else min(1.0, limiter_gain + 0.02)
            )
            mixed = np.clip(
                mixed * limiter_gain,
                -self.LIMITER_PEAK,
                self.LIMITER_PEAK,
            ).astype(np.float32)

            if generation is None:
                self._limiter_gain = limiter_gain
                if self._pre_roll_buffer.maxlen != self._context_block_count():
                    self._pre_roll_buffer = deque(
                        self._pre_roll_buffer,
                        maxlen=self._context_block_count(),
                    )
                self._has_sound = self._append_gated_locked(
                    self._current_chunk_buffer,
                    self._pending_silence_buffer,
                    self._pre_roll_buffer,
                    self._has_sound,
                    mixed,
                    sound_active,
                )
                return

            generation.limiter_gain = limiter_gain
            if generation.pre_roll.maxlen != self._context_block_count():
                generation.pre_roll = deque(
                    generation.pre_roll,
                    maxlen=self._context_block_count(),
                )
            generation.has_sound = self._append_gated_locked(
                generation.buffer,
                generation.pending_silence,
                generation.pre_roll,
                generation.has_sound,
                mixed,
                sound_active,
            )
            self._sync_public_from_generation_locked(generation)

    def _append_gated_locked(
        self,
        buffer,
        pending_silence,
        pre_roll,
        has_sound,
        mixed,
        sound_active,
    ):
        if sound_active:
            if not has_sound:
                buffer.extend(pre_roll)
                pre_roll.clear()
                has_sound = True
            if pending_silence:
                buffer.extend(pending_silence)
                pending_silence.clear()
            buffer.append(mixed)
            return has_sound

        if has_sound:
            pending_silence.append(mixed)
            if self._buffer_duration_seconds(pending_silence) >= self.CONTEXT_SECONDS:
                buffer.extend(pending_silence)
                pending_silence.clear()
                has_sound = False
            return has_sound

        pre_roll.append(mixed)
        return has_sound

    def _is_audio_active(self, audio_data, gain=1.0):
        if audio_data is None or len(audio_data) == 0:
            return False
        weighted = np.asarray(audio_data, dtype=np.float32) * float(gain)
        rms = float(np.sqrt(np.mean(np.square(weighted))))
        return rms >= self.silence_threshold

    def _context_block_count(self):
        if self.CONTEXT_SECONDS <= 0:
            return 0
        return max(1, int(math.ceil(self.CONTEXT_SECONDS * self.SR / self.BLOCK_SIZE)))

    def _buffer_duration_seconds(self, blocks):
        return sum(len(block) for block in blocks) / float(self.SR) if blocks else 0.0

    def _flush_pending_silence_locked(self, buffer, pending_silence):
        if pending_silence:
            buffer.extend(pending_silence)
            pending_silence.clear()

    def _reset_gate_locked(self, generation=None):
        if generation is None:
            self._current_chunk_buffer = []
            self._pending_silence_buffer = []
            self._pre_roll_buffer = deque(maxlen=self._context_block_count())
            self._has_sound = False
            return
        generation.buffer = []
        generation.pending_silence = []
        generation.pre_roll = deque(maxlen=self._context_block_count())
        generation.has_sound = False
        self._sync_public_from_generation_locked(generation)

    def _save_current_split(self, final=False, generation=None):
        if generation is None:
            with self._lock:
                self._flush_pending_silence_locked(
                    self._current_chunk_buffer,
                    self._pending_silence_buffer,
                )
                if not self._current_chunk_buffer:
                    return None
                blocks = list(self._current_chunk_buffer)
                full_audio = np.concatenate(blocks)
                self._reset_gate_locked()
                base_filename = self.current_base_filename or self._allocate_recording_base_filename(
                    uuid.uuid4().hex
                )
                split_index = self.split_index
            stem = base_filename if split_index == 0 else f"{base_filename}_{split_index}"
            filepath = os.path.join(self.output_folder, f"{stem}.wav")
            try:
                sf.write(filepath, full_audio, self.SR, format="WAV", subtype="PCM_16")
                self.last_saved_path = filepath
                if not final:
                    self.split_index += 1
                return filepath
            except Exception as exc:
                with self._lock:
                    self._current_chunk_buffer = blocks + self._current_chunk_buffer
                print(f"Save Error: {exc}")
                return None

        with self._lock:
            self._flush_pending_silence_locked(
                generation.buffer,
                generation.pending_silence,
            )
            if not generation.buffer:
                return None
            blocks = list(generation.buffer)
            full_audio = np.concatenate(blocks)
            self._reset_gate_locked(generation)

        filepath = self._allocate_output_path(generation)
        filename = os.path.basename(filepath)
        try:
            sf.write(filepath, full_audio, self.SR, format="WAV", subtype="PCM_16")
            print(f"Saved split: {filename} ({len(full_audio) / self.SR:.2f}s)")
            generation.last_saved_path = filepath
            if not final:
                generation.split_index += 1
            self._publish_generation(generation)
            return filepath
        except Exception as exc:
            with self._lock:
                self._reserved_output_paths.discard(filepath)
                generation.buffer = blocks + generation.buffer
                self._sync_public_from_generation_locked(generation)
            print(f"Save Error: {exc}")
            return None


if __name__ == "__main__":
    recorder = AudioRecorder(split_duration=5)
    print("Start recording (Auto-split 5s)...")
    recorder.start_recording()
