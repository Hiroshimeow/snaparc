import json
import os
import shutil
import time
import unittest

from src.gui.chat_session_store import ChatSessionStore


TEST_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "tests_runtime",
    "ChatSessionStoreTests",
)


class ChatSessionStoreTests(unittest.TestCase):
    def setUp(self):
        unique_name = f"{self._testMethodName}_{time.time_ns()}"
        self.temp_dir = os.path.join(TEST_ROOT, unique_name)
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        os.makedirs(self.temp_dir, exist_ok=True)
        self.save_path = os.path.join(self.temp_dir, "chat_sessions.json")
        self.store = ChatSessionStore(self.save_path)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_create_session_generates_default_title_from_first_message(self):
        session = self.store.create_session(
            model_name="provider/model",
            prompt_name="prompt_translate",
            messages=[{"role": "user", "text": "First line\nSecond line"}],
        )

        self.assertEqual(session["title"], "First line")
        self.assertEqual(session["model_name"], "provider/model")

    def test_rename_and_reload_session(self):
        created = self.store.create_session(messages=[{"role": "assistant", "text": "Answer"}])
        renamed = self.store.rename_session(created["id"], "Custom Title")
        reloaded = ChatSessionStore(self.save_path).get_session(created["id"])

        self.assertEqual(renamed["title"], "Custom Title")
        self.assertEqual(reloaded["title"], "Custom Title")

    def test_duplicate_session_creates_new_id(self):
        created = self.store.create_session(messages=[{"role": "assistant", "text": "Answer"}])
        duplicated = self.store.duplicate_session(created["id"])

        self.assertNotEqual(created["id"], duplicated["id"])
        self.assertEqual(len(self.store.list_sessions()), 2)

    def test_find_by_source_id_returns_matching_session(self):
        created = self.store.create_session(
            source_id="packet_123",
            messages=[{"role": "assistant", "text": "Answer"}],
        )

        found = self.store.find_by_source_id("packet_123")

        self.assertIsNotNone(found)
        self.assertEqual(found["id"], created["id"])

    def test_pinned_sessions_sort_before_unpinned(self):
        older = self.store.create_session(title="Older", messages=[{"role": "assistant", "text": "A"}])
        newer = self.store.create_session(title="Newer", messages=[{"role": "assistant", "text": "B"}])

        self.store.set_pinned(older["id"], True)
        ordered = self.store.list_sessions()

        self.assertEqual(ordered[0]["id"], older["id"])
        self.assertTrue(ordered[0]["is_pinned"])
        self.assertEqual(ordered[1]["id"], newer["id"])

    def test_load_time_sanitizes_polluted_assistant_message(self):
        with open(self.save_path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "sessions": [
                        {
                            "id": "s1",
                            "messages": [
                                {"id": "m1", "role": "assistant", "text": "main: main: clean"}
                            ],
                        }
                    ]
                },
                handle,
            )

        loaded = ChatSessionStore(self.save_path).get_session("s1")

        self.assertEqual(loaded["title"], "clean")
        self.assertEqual(loaded["messages"][0]["text"], "clean")


if __name__ == "__main__":
    unittest.main()
