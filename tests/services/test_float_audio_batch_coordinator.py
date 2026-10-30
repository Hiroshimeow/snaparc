import threading
import time
import unittest
from unittest.mock import patch

from src.services.float_audio_service import FloatAudioBatchCoordinator


class FloatAudioBatchCoordinatorTests(unittest.TestCase):
    def test_ready_results_cannot_overtake_a_waiting_emitter(self):
        original_lock = threading.Lock
        first_emitter_waiting = threading.Event()
        later_emitter_finished = threading.Event()

        class DelayedFirstEmissionLock:
            def __init__(self):
                self.lock = original_lock()
                self.count_lock = original_lock()
                self.attempts = 0

            def __enter__(self):
                with self.count_lock:
                    self.attempts += 1
                    attempt = self.attempts
                if attempt == 1:
                    first_emitter_waiting.set()
                    if not later_emitter_finished.wait(2):
                        raise TimeoutError("Later emitter did not finish")
                self.lock.acquire()
                return self

            def __exit__(self, *_args):
                self.lock.release()
                later_emitter_finished.set()

        locks_created = 0

        def lock_factory():
            nonlocal locks_created
            locks_created += 1
            return DelayedFirstEmissionLock() if locks_created == 2 else original_lock()

        def transcribe(path):
            if path == "b.mp3":
                self.assertTrue(first_emitter_waiting.wait(2))
            return path

        rendered = []
        coordinator = FloatAudioBatchCoordinator(
            "parallel-safe", max_retry=0, max_workers=2,
            parallel_safe_provider_apis=("parallel-safe",),
        )
        with patch("src.services.float_audio_service.threading.Lock", side_effect=lock_factory):
            summary = coordinator.run(
                ["a.mp3", "b.mp3"], transcribe,
                on_item=lambda item: rendered.append(item.index),
            )
        self.assertEqual(rendered, [0, 1])
        self.assertEqual(summary.success_count, 2)

    def test_parallel_batch_creates_at_most_max_workers_threads(self):
        original_thread = threading.Thread
        created_threads = []

        class CountedThread(original_thread):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                created_threads.append(self)

        coordinator = FloatAudioBatchCoordinator(
            "parallel-safe", max_retry=0, max_workers=2,
            parallel_safe_provider_apis=("parallel-safe",),
        )
        with patch("src.services.float_audio_service.threading.Thread", CountedThread):
            summary = coordinator.run([f"{i}.mp3" for i in range(100)], lambda path: path)
        self.assertEqual(summary.success_count, 100)
        self.assertLessEqual(len(created_threads), 2)

    def test_on_item_can_run_another_batch_on_same_coordinator(self):
        coordinator = FloatAudioBatchCoordinator("serial-provider", max_retry=0)
        rendered = []

        def on_item(item):
            rendered.append(item.file_path)
            coordinator.run(["nested.mp3"], lambda path: path,
                            on_item=lambda nested: rendered.append(nested.file_path))

        summary = coordinator.run(["outer.mp3"], lambda path: path, on_item=on_item)
        self.assertEqual(rendered, ["outer.mp3", "nested.mp3"])
        self.assertEqual(summary.success_count, 1)

    def test_parallel_completions_render_in_input_order(self):
        rendered = []
        coordinator = FloatAudioBatchCoordinator(
            provider_api="parallel-safe",
            max_retry=0,
            backoff_schedule=(),
            parallel_safe_provider_apis=("parallel-safe",),
        )

        def transcribe(file_path):
            delays = {
                "a.mp3": 0.05,
                "b.mp3": 0.01,
                "c.mp3": 0.02,
            }
            time.sleep(delays[file_path])
            return f"text:{file_path}"

        summary = coordinator.run(
            ["a.mp3", "b.mp3", "c.mp3"],
            transcribe,
            on_item=lambda item: rendered.append((item.file_path, item.success)),
        )

        self.assertEqual(
            rendered,
            [("a.mp3", True), ("b.mp3", True), ("c.mp3", True)],
        )
        self.assertEqual(summary.success_count, 3)
        self.assertEqual(summary.failed_count, 0)

    def test_failed_item_renders_error_after_retries_without_blocking_later_items(self):
        rendered = []
        attempts = {"b.mp3": 0}
        coordinator = FloatAudioBatchCoordinator(
            provider_api="parallel-safe",
            max_retry=2,
            backoff_schedule=(0, 0),
            sleep_fn=lambda _seconds: None,
            parallel_safe_provider_apis=("parallel-safe",),
        )

        def transcribe(file_path):
            if file_path == "a.mp3":
                time.sleep(0.05)
                return "text:a"
            if file_path == "b.mp3":
                attempts["b.mp3"] += 1
                raise RuntimeError("boom")
            return "text:c"

        summary = coordinator.run(
            ["a.mp3", "b.mp3", "c.mp3"],
            transcribe,
            on_item=lambda item: rendered.append(
                (item.file_path, item.success, item.retry_count)
            ),
        )

        self.assertEqual(
            rendered,
            [
                ("a.mp3", True, 0),
                ("b.mp3", False, 2),
                ("c.mp3", True, 0),
            ],
        )
        self.assertEqual(attempts["b.mp3"], 3)
        self.assertEqual(summary.success_count, 2)
        self.assertEqual(summary.failed_count, 1)

    def test_batch_summary_reports_success_and_failure_counts(self):
        coordinator = FloatAudioBatchCoordinator(
            provider_api="parallel-safe",
            max_retry=1,
            backoff_schedule=(0,),
            sleep_fn=lambda _seconds: None,
            parallel_safe_provider_apis=("parallel-safe",),
        )

        def transcribe(file_path):
            if file_path == "bad.mp3":
                raise RuntimeError("bad")
            return f"text:{file_path}"

        summary = coordinator.run(["ok.mp3", "bad.mp3"], transcribe)

        self.assertEqual(summary.success_count, 1)
        self.assertEqual(summary.failed_count, 1)
        self.assertEqual(summary.total_count, 2)
        self.assertEqual(summary.processed_count, 2)
        self.assertEqual(summary.skipped_count, 0)

    def test_gemini_native_jobs_are_serialized(self):
        active = 0
        max_active = 0
        lock = threading.Lock()
        coordinator = FloatAudioBatchCoordinator(
            provider_api="openai-completions",
            max_retry=0,
        )

        def transcribe(file_path):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.02)
            with lock:
                active -= 1
            return f"text:{file_path}"

        summary = coordinator.run(["a.mp3", "b.mp3", "c.mp3"], transcribe)

        self.assertEqual(max_active, 1)
        self.assertEqual(summary.success_count, 3)

    def test_continue_on_error_false_stops_batch_after_first_failure(self):
        rendered = []
        attempts = []
        coordinator = FloatAudioBatchCoordinator(
            provider_api="parallel-safe",
            max_retry=0,
            continue_on_error=False,
        )

        def transcribe(file_path):
            attempts.append(file_path)
            if file_path == "b.mp3":
                raise RuntimeError("boom")
            return f"text:{file_path}"

        summary = coordinator.run(
            ["a.mp3", "b.mp3", "c.mp3"],
            transcribe,
            on_item=lambda item: rendered.append((item.file_path, item.success)),
        )

        self.assertEqual(attempts, ["a.mp3", "b.mp3"])
        self.assertEqual(rendered, [("a.mp3", True), ("b.mp3", False)])
        self.assertEqual(summary.success_count, 1)
        self.assertEqual(summary.failed_count, 1)
        self.assertEqual(summary.total_count, 3)
        self.assertEqual(summary.processed_count, 2)
        self.assertEqual(summary.skipped_count, 1)

    def test_parallel_on_item_callbacks_do_not_overlap(self):
        callback_active = 0
        max_callback_active = 0
        callback_lock = threading.Lock()
        coordinator = FloatAudioBatchCoordinator(
            provider_api="parallel-safe",
            max_retry=0,
            parallel_safe_provider_apis=("parallel-safe",),
        )

        def transcribe(file_path):
            delays = {"a.mp3": 0.05, "b.mp3": 0.01, "c.mp3": 0.02}
            time.sleep(delays[file_path])
            return f"text:{file_path}"

        def on_item(_item):
            nonlocal callback_active, max_callback_active
            with callback_lock:
                callback_active += 1
                max_callback_active = max(max_callback_active, callback_active)
            time.sleep(0.02)
            with callback_lock:
                callback_active -= 1

        summary = coordinator.run(["a.mp3", "b.mp3", "c.mp3"], transcribe, on_item=on_item)

        self.assertEqual(max_callback_active, 1)
        self.assertEqual(summary.success_count, 3)

    def test_unknown_provider_api_is_serial_by_default(self):
        active = 0
        max_active = 0
        lock = threading.Lock()
        coordinator = FloatAudioBatchCoordinator(
            provider_api="unknown-provider",
            max_retry=0,
        )

        def transcribe(file_path):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.02)
            with lock:
                active -= 1
            return f"text:{file_path}"

        summary = coordinator.run(["a.mp3", "b.mp3"], transcribe)

        self.assertEqual(max_active, 1)
        self.assertEqual(summary.success_count, 2)

    def test_on_item_exceptions_raise_from_run_in_parallel_mode(self):
        coordinator = FloatAudioBatchCoordinator(
            provider_api="parallel-safe",
            max_retry=0,
            parallel_safe_provider_apis=("parallel-safe",),
        )
        callback_calls = []

        def transcribe(file_path):
            return f"text:{file_path}"

        with self.assertRaisesRegex(RuntimeError, "callback boom"):
            coordinator.run(
                ["a.mp3", "b.mp3"],
                transcribe,
                on_item=lambda item: (
                    callback_calls.append(item.file_path),
                    (_ for _ in ()).throw(RuntimeError("callback boom")),
                )[1],
            )

        self.assertEqual(callback_calls, ["a.mp3"])


if __name__ == "__main__":
    unittest.main()
