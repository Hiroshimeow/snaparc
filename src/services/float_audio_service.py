import os
import threading
import time
from dataclasses import dataclass, field
from typing import Protocol

from src.utils.ConfigManager import ConfigManager


SUPPORTED_FILE_AUDIO_PROVIDER_APIS = {"openai-completions"}


@dataclass(frozen=True)
class FloatAudioModelResolution:
    success: bool
    provider_name: str
    provider_api: str
    model_name: str
    model_full_name: str
    source: str
    rejections: tuple[dict, ...] = field(default_factory=tuple)
    error_code: str | None = None
    message: str | None = None

    def to_payload(self):
        return {
            "success": self.success,
            "provider_name": self.provider_name,
            "provider_api": self.provider_api,
            "model_name": self.model_name,
            "model_full_name": self.model_full_name,
            "source": self.source,
            "rejections": [dict(item) for item in self.rejections],
            "error_code": self.error_code,
            "message": self.message,
        }


@dataclass(frozen=True)
class FloatAudioSourceSelection:
    success: bool
    source: str
    file_paths: list[str]
    error_code: str | None = None
    message: str | None = None


@dataclass(frozen=True)
class FloatAudioBatchItemResult:
    index: int
    file_path: str
    success: bool
    text: str | None = None
    error_message: str | None = None
    retry_count: int = 0


@dataclass(frozen=True)
class FloatAudioBatchSummary:
    success_count: int
    failed_count: int
    total_count: int
    processed_count: int
    skipped_count: int


class FloatAudioOutputSink:
    def __init__(self, latest_window_getter, create_window_fn, dispatch_fn=None):
        self._latest_window_getter = latest_window_getter
        self._create_window_fn = create_window_fn
        self._dispatch_fn = dispatch_fn or (lambda callback: callback())
        self._bound_window = None

    def append_text(self, text):
        def action():
            window = self._ensure_window()
            if not window:
                return False
            window.append_text(text)
            return True

        return self._dispatch_fn(action)

    def _ensure_window(self):
        if self._is_window_alive(self._bound_window):
            return self._bound_window

        candidate = self._latest_window_getter()
        if self._is_window_alive(candidate):
            self._bound_window = candidate
            return candidate

        candidate = self._create_window_fn()
        if self._is_window_alive(candidate):
            self._bound_window = candidate
            return candidate
        return None

    def _is_window_alive(self, window):
        if not window:
            return False
        win_handle = getattr(window, "win", window)
        exists_fn = getattr(win_handle, "winfo_exists", None)
        if callable(exists_fn):
            try:
                return bool(exists_fn())
            except Exception:
                return False
        return True


class FloatAudioRecorderLike(Protocol):
    is_recording: bool
    last_saved_path: str | None
    output_folder: str

    def save_snapshot(self) -> str | None: ...


class FloatAudioModelResolver:
    def __init__(self, config_manager=None):
        self.config_manager = config_manager or ConfigManager()

    def resolve(self, selected_model=None):
        configured_selected = selected_model
        if not isinstance(configured_selected, str) or not configured_selected.strip():
            configured_selected = self.config_manager.get("agents.defaults.model.primary")
        fallback_model = self.config_manager.get("audio.stt.fallback_model")

        candidates = [
            ("selected_model", configured_selected),
            ("preferred_model", self.config_manager.get("audio.stt.preferred_model")),
            ("fallback_model", fallback_model),
        ]
        audio_capable_models = self.config_manager.get("audio.stt.audio_capable_models", [])
        audio_capable_models = set(audio_capable_models or [])
        if isinstance(fallback_model, str) and fallback_model.strip():
            audio_capable_models.add(fallback_model.strip())
        rejections = []

        for source, model_full_name in candidates:
            if not isinstance(model_full_name, str) or not model_full_name.strip():
                continue

            model_full_name = model_full_name.strip()
            provider_name, separator, model_name = model_full_name.partition("/")
            if not separator or not model_name:
                rejections.append(
                    {
                        "source": source,
                        "model_full_name": model_full_name,
                        "reason_code": "invalid_model_name",
                        "provider_api": "",
                    }
                )
                continue

            provider_config = self.config_manager.get_provider_config(provider_name)
            provider_api = provider_config.get("api", "")
            provider_models = provider_config.get("models", [])
            if model_name not in provider_models:
                rejections.append(
                    {
                        "source": source,
                        "model_full_name": model_full_name,
                        "reason_code": "model_not_declared_for_provider",
                        "provider_api": provider_api,
                    }
                )
                continue

            if model_full_name not in audio_capable_models:
                rejections.append(
                    {
                        "source": source,
                        "model_full_name": model_full_name,
                        "reason_code": "not_audio_capable",
                        "provider_api": provider_api,
                    }
                )
                continue

            if provider_api not in SUPPORTED_FILE_AUDIO_PROVIDER_APIS:
                rejections.append(
                    {
                        "source": source,
                        "model_full_name": model_full_name,
                        "reason_code": "unsupported_provider_api",
                        "provider_api": provider_api,
                    }
                )
                continue

            return FloatAudioModelResolution(
                success=True,
                provider_name=provider_name,
                provider_api=provider_api,
                model_name=model_name,
                model_full_name=model_full_name,
                source=source,
                rejections=tuple(dict(item) for item in rejections),
            )

        return FloatAudioModelResolution(
            success=False,
            provider_name="",
            provider_api="",
            model_name="",
            model_full_name="",
            source="unresolved",
            rejections=tuple(dict(item) for item in rejections),
            error_code="audio_model_resolution_failed",
            message="No supported audio model is available for file-audio transcription.",
        )


class FloatAudioSourceResolver:
    def __init__(self, recorder: FloatAudioRecorderLike, file_dialog_module=None):
        self.recorder = recorder
        self.file_dialog_module = file_dialog_module

    def resolve_latest(self):
        if getattr(self.recorder, "is_recording", False):
            snapshot_path = self._normalize_existing_path(
                self.recorder.save_snapshot()
            )
            if snapshot_path:
                return FloatAudioSourceSelection(
                    success=True,
                    source="latest_snapshot",
                    file_paths=[snapshot_path],
                )

        last_saved_path = self._normalize_existing_path(
            getattr(self.recorder, "last_saved_path", None)
        )
        if last_saved_path:
            return FloatAudioSourceSelection(
                success=True,
                source="latest_saved",
                file_paths=[last_saved_path],
            )

        return FloatAudioSourceSelection(
            success=False,
            source="no_audio",
            file_paths=[],
            error_code="no_audio",
            message="No audio file is available for STT.",
        )

    def select_files(self, parent=None):
        dialog_module = self.file_dialog_module
        if dialog_module is None:
            from tkinter import filedialog

            dialog_module = filedialog

        selected = dialog_module.askopenfilenames(
            parent=parent,
            title="Choose audio files",
            initialdir=getattr(self.recorder, "output_folder", None) or "src/rec_list",
            filetypes=[
                ("Audio", "*.mp3;*.wav;*.ogg;*.flac"),
                ("All files", "*.*"),
            ],
        )
        normalized = [
            path for path in (self._normalize_existing_path(item) for item in selected) if path
        ]
        normalized.sort(key=lambda path: os.path.basename(path).lower())

        if normalized:
            return FloatAudioSourceSelection(
                success=True,
                source="selected_files",
                file_paths=normalized,
            )

        return FloatAudioSourceSelection(
            success=False,
            source="cancelled",
            file_paths=[],
            error_code="cancelled",
            message="No audio files selected.",
        )

    def _normalize_existing_path(self, path):
        if not isinstance(path, str) or not path.strip():
            return None
        abs_path = os.path.abspath(path)
        if not os.path.exists(abs_path):
            return None
        return abs_path


class FloatAudioBatchCoordinator:
    def __init__(
        self,
        provider_api: str,
        max_retry: int = 2,
        continue_on_error: bool = True,
        backoff_schedule: tuple[float, ...] = (0.8, 1.5, 3.0),
        sleep_fn=time.sleep,
        max_workers: int = 4,
        parallel_safe_provider_apis: tuple[str, ...] = (),
    ):
        self.provider_api = provider_api
        self.max_retry = max(0, int(max_retry))
        self.continue_on_error = continue_on_error
        self.backoff_schedule = backoff_schedule
        self.sleep_fn = sleep_fn
        self.max_workers = max(1, int(max_workers))
        self.parallel_safe_provider_apis = set(parallel_safe_provider_apis)

    def run(self, file_paths, transcribe_file, on_item=None):
        """Run a batch and emit ordered item results.

        `on_item` is invoked synchronously by the coordinator after ordered flushes.
        In parallel mode, the callback still runs on a coordinator worker thread, so
        UI callers must provide their own thread hop onto the main/UI thread.
        """
        indexed_paths = list(enumerate(file_paths))
        results = {}
        next_index = 0
        lock = threading.Lock()
        emit_lock = threading.Lock()
        success_count = 0
        failed_count = 0
        stop_after_failure = threading.Event()
        callback_errors = []

        def flush_ready_items():
            nonlocal next_index, success_count, failed_count
            with emit_lock:
                ready = []
                with lock:
                    while next_index in results:
                        item = results.pop(next_index)
                        if item.success:
                            success_count += 1
                        else:
                            failed_count += 1
                        ready.append(item)
                        next_index += 1
                if on_item and ready:
                    if callback_errors:
                        return
                    for item in ready:
                        try:
                            on_item(item)
                        except Exception as exc:
                            callback_errors.append(exc)
                            stop_after_failure.set()
                            break

        def store_result(item):
            with lock:
                results[item.index] = item
            flush_ready_items()

        def worker(index, file_path):
            if stop_after_failure.is_set():
                return
            item = self._run_with_retry(index, file_path, transcribe_file)
            store_result(item)
            if not item.success and not self.continue_on_error:
                stop_after_failure.set()

        threads = []
        if self._run_serially():
            for index, file_path in indexed_paths:
                worker(index, file_path)
                if stop_after_failure.is_set():
                    break
        else:
            work_lock = threading.Lock()
            next_work = 0

            def threaded_worker():
                nonlocal next_work
                while not stop_after_failure.is_set():
                    with work_lock:
                        if next_work >= len(indexed_paths):
                            return
                        index, file_path = indexed_paths[next_work]
                        next_work += 1
                    worker(index, file_path)

            for _ in range(min(self.max_workers, len(indexed_paths))):
                thread = threading.Thread(target=threaded_worker, daemon=True)
                threads.append(thread)
                thread.start()

            for thread in threads:
                thread.join()

        flush_ready_items()
        if callback_errors:
            raise callback_errors[0]
        processed_count = success_count + failed_count
        total_count = len(indexed_paths)
        return FloatAudioBatchSummary(
            success_count=success_count,
            failed_count=failed_count,
            total_count=total_count,
            processed_count=processed_count,
            skipped_count=max(0, total_count - processed_count),
        )

    def _run_with_retry(self, index, file_path, transcribe_file):
        attempts = 0
        while True:
            try:
                text = transcribe_file(file_path)
                return FloatAudioBatchItemResult(
                    index=index,
                    file_path=file_path,
                    success=True,
                    text=text,
                    retry_count=max(0, attempts),
                )
            except Exception as exc:
                if attempts >= self.max_retry:
                    return FloatAudioBatchItemResult(
                        index=index,
                        file_path=file_path,
                        success=False,
                        error_message=str(exc),
                        retry_count=attempts,
                    )
                backoff_index = min(attempts, max(0, len(self.backoff_schedule) - 1))
                if self.backoff_schedule:
                    self.sleep_fn(self.backoff_schedule[backoff_index])
                attempts += 1

    def _run_serially(self):
        if not self.continue_on_error:
            return True
        return self.provider_api not in self.parallel_safe_provider_apis
