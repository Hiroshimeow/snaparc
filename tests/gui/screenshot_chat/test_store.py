import json
import os
import shutil
import time
import unittest

from src.gui.screenshot_chat.models import (
    ScreenshotChatAttachment,
    ScreenshotChatMessage,
)
from src.gui.screenshot_chat.store import ScreenshotChatStore


TEST_ROOT = os.path.join(
    os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ),
    "tests_runtime",
    "ScreenshotChatStoreTests",
)


class ScreenshotChatStoreTests(unittest.TestCase):
    def setUp(self):
        unique_name = f"{self._testMethodName}_{time.time_ns()}"
        self.temp_dir = os.path.join(TEST_ROOT, unique_name)
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        os.makedirs(self.temp_dir, exist_ok=True)
        self.save_path = os.path.join(self.temp_dir, "screenshot_chat_sessions.json")
        self.store = ScreenshotChatStore(self.save_path)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_create_reload_and_rename_session(self):
        created = self.store.create_session(
            model_name="provider/model",
            prompt_type="prompt_translate",
        )
        renamed = self.store.rename_session(created.id, "Pinned Work")
        reloaded = ScreenshotChatStore(self.save_path).get_session(created.id)

        self.assertEqual(renamed.title, "Pinned Work")
        self.assertEqual(reloaded.model_name, "provider/model")
        self.assertEqual(reloaded.prompt_type, "prompt_translate")

    def test_corrupt_json_starts_empty_without_crashing(self):
        with open(self.save_path, "w", encoding="utf-8") as handle:
            handle.write("{not json")

        store = ScreenshotChatStore(self.save_path)

        self.assertEqual(store.list_sessions(), [])

    def test_append_and_replace_message_by_job(self):
        session = self.store.create_session()
        placeholder = ScreenshotChatMessage(
            role="assistant",
            author="critic",
            text="Thinking",
            job_id="job_1",
            status="thinking",
        )
        self.store.append_message(session.id, placeholder)
        self.store.set_pending_job(session.id, "job_1", True)
        replacement = ScreenshotChatMessage(
            role="assistant",
            author="critic",
            text="Answer",
            job_id="job_1",
            status="done",
        )

        updated = self.store.replace_message_by_job(session.id, "job_1", replacement)

        self.assertEqual(updated.messages[-1].text, "Answer")
        self.assertEqual(updated.messages[-1].status, "done")
        self.assertNotIn("job_1", updated.pending_job_ids)


    def test_load_time_sanitizes_polluted_assistant_messages(self):
        payload = {
            "sessions": [
                {
                    "id": "s_polluted",
                    "title": "",
                    "messages": [
                        {"id": "m1", "role": "assistant", "author": "main", "text": "main: main: clean me", "status": "done"},
                        {"id": "m2", "role": "assistant", "author": "main", "text": "main: [Error: No response received]", "status": "done"},
                    ],
                }
            ]
        }
        with open(self.save_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)

        loaded = ScreenshotChatStore(self.save_path).get_session("s_polluted")

        self.assertEqual(loaded.title, "clean me")
        self.assertEqual(loaded.messages[0].text, "clean me")
        self.assertTrue(loaded.messages[0].metadata["sanitized_at_load"])
        self.assertEqual(loaded.messages[1].text, "[Error: No response received]")
        self.assertEqual(loaded.messages[1].status, "error")
        self.assertTrue(loaded.messages[1].metadata["error_only"])

    def test_append_and_replace_sanitize_assistant_messages(self):
        session = self.store.create_session()
        message = ScreenshotChatMessage(
            role="assistant",
            author="main",
            text="main: main: appended",
            job_id="job_sanitize",
            status="thinking",
        )
        self.store.append_message(session.id, message)
        self.store.set_pending_job(session.id, "job_sanitize", True)
        updated = self.store.replace_message_by_job(
            session.id,
            "job_sanitize",
            ScreenshotChatMessage(
                role="assistant",
                author="main",
                text="main: replaced",
                job_id="job_sanitize",
                status="done",
            ),
        )

        self.assertEqual(updated.messages[-1].text, "replaced")

    def test_pinned_sessions_sort_first(self):
        older = self.store.create_session(title="Older")
        newer = self.store.create_session(title="Newer")

        self.store.set_pinned(older.id, True)
        ordered = self.store.list_sessions()

        self.assertEqual(ordered[0].id, older.id)
        self.assertEqual(ordered[1].id, newer.id)

    def test_atomic_save_uses_separate_history_file(self):
        attachment = ScreenshotChatAttachment(path="snap.png", source="held")
        created = self.store.create_session(attachments=[attachment])

        self.assertTrue(os.path.exists(self.save_path))
        self.assertFalse(os.path.exists(os.path.join(self.temp_dir, "chat_sessions.json")))
        with open(self.save_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        self.assertEqual(payload["sessions"][0]["id"], created.id)
        self.assertEqual(payload["sessions"][0]["attachments"][0]["path"], "snap.png")

    def test_session_mode_persists_and_defaults_to_chat(self):
        created = self.store.create_session(mode="note")
        reloaded = ScreenshotChatStore(self.save_path).get_session(created.id)

        self.assertEqual(reloaded.mode, "note")

    def test_message_events_metadata_and_delta_round_trip(self):
        session = self.store.create_session()
        message = ScreenshotChatMessage(
            role="assistant",
            author="pi",
            text="Thinking",
            job_id="job_pi",
            status="thinking",
            metadata={"runtime": "pi"},
        )
        self.store.append_message(session.id, message)

        self.store.append_message_delta(session.id, "job_pi", "hello")
        self.store.append_message_event(
            session.id,
            "job_pi",
            {"id": "evt_1", "kind": "tool_started", "status": "running"},
        )
        reloaded = ScreenshotChatStore(self.save_path).get_session(session.id)

        self.assertEqual(reloaded.messages[-1].text, "hello")
        self.assertEqual(reloaded.messages[-1].events[0]["kind"], "tool_started")
        self.assertEqual(reloaded.messages[-1].metadata["runtime"], "pi")

    def test_delta_mutation_can_skip_expensive_session_clone(self):
        session = self.store.create_session()
        self.store.append_message(
            session.id,
            ScreenshotChatMessage(text="Thinking", job_id="job_fast", status="thinking"),
        )

        result = self.store.append_message_delta(
            session.id,
            "job_fast",
            "streamed",
            save=False,
            return_session=False,
        )

        self.assertIsNone(result)
        self.assertEqual(self.store.get_session(session.id).messages[-1].text, "streamed")

    def test_delete_message_removes_message_from_persisted_session(self):
        session = self.store.create_session()
        message = ScreenshotChatMessage(id="msg_delete", role="user", text="delete me")
        self.store.append_message(session.id, message)

        updated = self.store.delete_message(session.id, "msg_delete")
        reloaded = ScreenshotChatStore(self.save_path).get_session(session.id)

        self.assertIsNotNone(updated)
        self.assertEqual(reloaded.messages, [])


if __name__ == "__main__":
    unittest.main()
