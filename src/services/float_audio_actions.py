import os
import threading

from src.services.audio_preprocessing import AudioPreprocessor
from src.services.float_audio_service import FloatAudioBatchCoordinator


class FloatAudioActionService:
    def __init__(
        self,
        source_resolver,
        output_sink,
        model_resolver,
        transcribe_audio_fn,
        config_manager=None,
        coordinator_factory=None,
        audio_preprocessor=None,
        postprocess_text_fn=None,
        thread_factory=threading.Thread,
        sleep_fn=None,
        callback_timeout_seconds=600.0,
    ):
        self.source_resolver = source_resolver
        self.output_sink = output_sink
        self.model_resolver = model_resolver
        self.transcribe_audio_fn = transcribe_audio_fn
        self.config_manager = config_manager or _NullConfig()
        self.audio_preprocessor = audio_preprocessor
        self.postprocess_text_fn = postprocess_text_fn
        self.thread_factory = thread_factory
        self.sleep_fn = sleep_fn
        self.callback_timeout_seconds = callback_timeout_seconds
        self.coordinator_factory = coordinator_factory or self._build_coordinator
        self._active_job_count = 0
        self._state_lock = threading.Lock()

    def get_stt_state(self):
        with self._state_lock:
            job_count = self._active_job_count
        return {
            "running": job_count > 0,
            "job_count": job_count,
        }

    def transcribe_latest(self, selected_model=None, window_instance=None):
        selection = self.source_resolver.resolve_latest()
        if not selection.success:
            if window_instance:
                window_instance.append_text("[No Audio Found]")
            return False

        resolution = self.model_resolver.resolve(selected_model=selected_model)
        if not resolution.success:
            self.output_sink.append_text("[STT Error] No supported audio model is available.")
            return False

        file_path = selection.file_paths[0]
        preprocess_result = self._preprocess_audio(file_path)
        if preprocess_result is not None and preprocess_result.status == "skipped":
            self.output_sink.append_text(f"[STT Skipped] {preprocess_result.message or 'No speech detected.'}")
            return False
        if preprocess_result is not None and preprocess_result.status == "error":
            self.output_sink.append_text(f"[STT Error] {preprocess_result.message or 'Audio preprocessing failed.'}")
            return False
        audio_path = (preprocess_result.output_path if preprocess_result else None) or file_path
        self._job_started()

        def on_result(result):
            try:
                text = self._normalize_result(result)
                if text:
                    self.output_sink.append_text(self._postprocess_transcript_if_enabled(text))
            finally:
                self._job_finished()

        try:
            self._call_transcribe_audio(audio_path, on_result, resolution)
        except Exception:
            self._job_finished()
            raise
        return True

    def transcribe_selected_files(self, parent=None, selected_model=None):
        selection = self.source_resolver.select_files(parent=parent)
        if not selection.success:
            return False

        resolution = self.model_resolver.resolve(selected_model=selected_model)
        if not resolution.success:
            self.output_sink.append_text("[STT Error] No supported audio model is available.")
            return False

        file_paths = list(selection.file_paths)
        file_names = ", ".join(os.path.basename(path) for path in file_paths)
        self.output_sink.append_text(f"Files {file_names} running:")
        self._job_started()

        def run_batch():
            try:
                coordinator = self.coordinator_factory(resolution.provider_api)
                coordinator.run(
                    file_paths,
                    transcribe_file=lambda path: self._transcribe_file_blocking(path, resolution),
                    on_item=self._append_batch_item,
                )
            finally:
                self._job_finished()

        self.thread_factory(target=run_batch, daemon=True).start()
        return True

    def _append_batch_item(self, item):
        if item.success and item.text:
            self.output_sink.append_text(item.text)
            return
        file_name = os.path.basename(item.file_path)
        message = item.error_message or "Unknown error."
        self.output_sink.append_text(f"File {file_name} error: {message}")

    def _transcribe_file_blocking(self, file_path, resolution):
        done = threading.Event()
        result = {"value": None}

        def on_result(value):
            result["value"] = self._normalize_result(value)
            done.set()

        preprocess_result = self._preprocess_audio(file_path)
        if preprocess_result is not None and preprocess_result.status == "skipped":
            raise RuntimeError(preprocess_result.message or "No speech detected; skipped STT.")
        if preprocess_result is not None and preprocess_result.status == "error":
            raise RuntimeError(preprocess_result.message or "Audio preprocessing failed.")
        self._call_transcribe_audio((preprocess_result.output_path if preprocess_result else None) or file_path, on_result, resolution)
        if not done.wait(timeout=self.callback_timeout_seconds):
            raise TimeoutError(f"Timed out waiting for transcript: {os.path.basename(file_path)}")

        if not result["value"]:
            raise RuntimeError("Empty transcript.")
        return self._postprocess_transcript_if_enabled(result["value"])

    def _build_coordinator(self, provider_api):
        parallel_safe_provider_apis = self.config_manager.get(
            "audio.stt.parallel_safe_provider_apis",
            ["openai-completions"],
        )
        max_retry = self.config_manager.get("audio.stt.max_retry", 2)
        continue_on_error = self.config_manager.get("audio.stt.continue_on_error", True)
        max_workers = self.config_manager.get("audio.stt.max_workers", 4)
        kwargs = {
            "provider_api": provider_api,
            "max_retry": max_retry,
            "continue_on_error": continue_on_error,
            "max_workers": max_workers,
            "parallel_safe_provider_apis": tuple(parallel_safe_provider_apis or ()),
        }
        if self.sleep_fn is not None:
            kwargs["sleep_fn"] = self.sleep_fn
        return FloatAudioBatchCoordinator(**kwargs)

    def _call_transcribe_audio(self, file_path, callback, resolution):
        try:
            return self.transcribe_audio_fn(
                file_path,
                callback=callback,
                resolution=resolution,
                prompt=self._stt_prompt(),
            )
        except TypeError as exc:
            if "prompt" not in str(exc):
                raise
            return self.transcribe_audio_fn(
                file_path,
                callback=callback,
                resolution=resolution,
            )

    def _preprocess_audio(self, file_path):
        if self.audio_preprocessor is not None:
            return self.audio_preprocessor.preprocess(file_path)
        enabled = self.config_manager.get("audio.stt.preprocess.enabled", None) is True
        if not enabled:
            return None
        return AudioPreprocessor(self.config_manager).preprocess(file_path)

    def _stt_prompt(self):
        return self.config_manager.get(
            "audio.stt.prompt",
            "Transcribe this audio in Vietnamese-first mode. Return only the transcript.",
        )

    def _postprocess_transcript_if_enabled(self, transcript):
        mode = self.config_manager.get("audio.stt.postprocess.mode", "correct_vi")
        if mode == "none" or not self.postprocess_text_fn:
            return transcript
        prompt = self._build_postprocess_prompt(transcript, mode)
        done = threading.Event()
        result = {"value": None}

        def on_result(value):
            result["value"] = self._normalize_result(value)
            done.set()

        model_name = self.config_manager.get("audio.stt.postprocess.model", "") or None
        self.postprocess_text_fn(prompt, callback=on_result, model_name=model_name)
        timeout = float(self.config_manager.get("audio.stt.postprocess.timeout_seconds", 120) or 120)
        if not done.wait(timeout=timeout):
            raise TimeoutError("Timed out waiting for transcript postprocess result.")
        if not result["value"]:
            raise RuntimeError("Empty transcript postprocess result.")
        return result["value"]

    def _build_postprocess_prompt(self, transcript, mode):
        if mode == "translate_vi":
            instruction = "Dịch transcript sau sang tiếng Việt tự nhiên. Chỉ trả về bản dịch."
        elif mode == "correct_vi":
            instruction = "Bạn là bộ sửa transcript tiếng Việt. Chỉ sửa lỗi nhận dạng giọng nói, dấu câu, viết hoa, thuật ngữ kỹ thuật, tên riêng nếu rõ ràng. Không thêm ý mới. Chỉ trả về transcript đã sửa."
        else:
            raise ValueError(f"Unsupported audio STT postprocess mode: {mode}")
        return f"{instruction}\n\nTranscript:\n{transcript}"

    def _normalize_result(self, result):
        if isinstance(result, str):
            return result
        if isinstance(result, dict):
            text = result.get("text")
            if isinstance(text, str):
                return text
        return None

    def _job_started(self):
        with self._state_lock:
            self._active_job_count += 1

    def _job_finished(self):
        with self._state_lock:
            self._active_job_count = max(0, self._active_job_count - 1)


class _NullConfig:
    def get(self, _key, default=None):
        return default
