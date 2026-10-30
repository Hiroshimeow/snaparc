import types
import unittest
from unittest.mock import patch

from src.utils import HotkeyManager


class _FakeUser32:
    def __init__(self, sequences):
        self._sequences = iter(sequences)

    def GetClipboardSequenceNumber(self):
        return next(self._sequences)

    def SendInput(self, *_args):
        return 1


class HotkeyManagerClipboardTests(unittest.TestCase):
    def test_simulate_ctrl_c_returns_true_when_clipboard_sequence_changes(self):
        fake_user32 = _FakeUser32([10, 11])
        with patch.object(HotkeyManager.ctypes, "windll", types.SimpleNamespace(user32=fake_user32)), patch.object(
            HotkeyManager.time, "sleep", return_value=None
        ), patch.object(HotkeyManager.time, "monotonic", side_effect=[0.0, 0.1]):
            self.assertTrue(HotkeyManager.simulate_ctrl_c())

    def test_simulate_ctrl_c_returns_false_when_clipboard_sequence_never_changes(self):
        fake_user32 = _FakeUser32([10, 10])
        with patch.object(HotkeyManager.ctypes, "windll", types.SimpleNamespace(user32=fake_user32)), patch.object(
            HotkeyManager.time, "sleep", return_value=None
        ), patch.object(HotkeyManager.time, "monotonic", side_effect=[0.0, 0.1, 0.6]):
            self.assertFalse(HotkeyManager.simulate_ctrl_c())


if __name__ == "__main__":
    unittest.main()
