import unittest
from types import SimpleNamespace
from unittest import mock

from src.gui.screenshot_chat.composer import ScreenshotChatComposer


class DraftEntry:
    def __init__(self, text):
        self.text = text

    def get(self, start, end):
        return self.text

    def delete(self, start, end):
        self.text = ""


class ScreenshotChatComposerTests(unittest.TestCase):
    def make_composer(self, accepted):
        session = SimpleNamespace(id="session", model_name="provider/main", prompt_type="custom")
        return SimpleNamespace(
            controller=SimpleNamespace(
                get_active_session=lambda: session,
                send_message=mock.Mock(return_value=accepted),
            ),
            entry=DraftEntry("Keep this draft"),
            _resize_entry_to_content=mock.Mock(),
            window=SimpleNamespace(schedule_scroll_to_bottom=mock.Mock()),
        )

    def test_rejected_send_preserves_composer_text(self):
        composer = self.make_composer(False)

        ScreenshotChatComposer._send_current_message(composer)

        self.assertEqual(composer.entry.text, "Keep this draft")
        composer.window.schedule_scroll_to_bottom.assert_not_called()

    def test_accepted_send_clears_composer_text(self):
        composer = self.make_composer(True)

        ScreenshotChatComposer._send_current_message(composer)

        self.assertEqual(composer.entry.text, "")
        composer.controller.send_message.assert_called_once_with("Keep this draft", "provider/main", "custom")
        composer.window.schedule_scroll_to_bottom.assert_called_once_with("session", reason="send")


if __name__ == "__main__":
    unittest.main()
