import unittest

from src.gui.chat_text_sanitizer import (
    history_text_for_author,
    is_error_only_assistant_text,
    sanitize_assistant_text,
    strip_repeated_author_prefixes,
)


class ChatTextSanitizerTests(unittest.TestCase):
    def test_strip_repeated_leading_main_prefixes_only(self):
        self.assertEqual(strip_repeated_author_prefixes("main: main: hello"), "hello")
        self.assertEqual(strip_repeated_author_prefixes("hello main: still prose"), "hello main: still prose")

    def test_history_text_for_author_main_and_non_main(self):
        self.assertEqual(history_text_for_author("main: answer", "main"), "answer")
        self.assertEqual(history_text_for_author("critic: critic: answer", "critic"), "critic: answer")

    def test_error_only_assistant_text_is_detected_and_skipped_from_history(self):
        self.assertEqual(sanitize_assistant_text("main: main: hello"), "hello")
        self.assertTrue(is_error_only_assistant_text("main: [Error: No response received]"))
        self.assertEqual(history_text_for_author("[Error: Response has no content]", "main"), "")


if __name__ == "__main__":
    unittest.main()
