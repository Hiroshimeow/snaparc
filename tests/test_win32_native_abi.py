import ctypes
import sys
import unittest

from PIL import Image

from src.platform import win32_mouse, win32_tray


@unittest.skipUnless(sys.platform == "win32", "Windows-only ABI regression")
class TestWin32NativeAbi(unittest.TestCase):
    def test_lresult_is_pointer_sized(self):
        self.assertEqual(ctypes.sizeof(win32_mouse._LRESULT), ctypes.sizeof(ctypes.c_void_p))
        self.assertEqual(ctypes.sizeof(win32_tray._LRESULT), ctypes.sizeof(ctypes.c_void_p))

    def test_call_next_hook_ex_accepts_pointer_sized_lparam(self):
        user32 = ctypes.windll.user32
        win32_mouse._configure_hook_chain_api(user32)
        large_lparam = (1 << 48) + 0x1234
        # nCode < 0 tells Windows to pass through without dereferencing lParam.
        user32.CallNextHookEx(None, -1, 0, large_lparam)

    def test_native_tray_starts_and_stops(self):
        icon = win32_tray.Icon(
            "SnapArcAbiTest",
            Image.new("RGBA", (32, 32), (32, 160, 220, 255)),
            "SnapArc ABI Test",
            win32_tray.Menu(),
        )
        try:
            icon.run_detached()
            self.assertTrue(icon._hwnd)
            self.assertTrue(icon._thread and icon._thread.is_alive())
        finally:
            icon.stop()
        self.assertFalse(icon._thread and icon._thread.is_alive())

    def test_tray_startup_exception_is_reported(self):
        icon = win32_tray.Icon(
            "SnapArcAbiFailure",
            Image.new("RGBA", (32, 32), (32, 160, 220, 255)),
            "SnapArc ABI Failure",
            win32_tray.Menu(),
        )
        icon._run = lambda: (_ for _ in ()).throw(ValueError("synthetic tray failure"))
        with self.assertRaisesRegex(RuntimeError, "synthetic tray failure"):
            icon.run_detached()


class TestRunBatLauncher(unittest.TestCase):
    def test_run_bat_uses_supported_launcher_not_conda(self):
        from pathlib import Path

        text = Path("run.bat").read_text(encoding="utf-8").lower()
        self.assertIn("main.bat", text)
        self.assertNotIn("conda", text)


if __name__ == "__main__":
    unittest.main()
