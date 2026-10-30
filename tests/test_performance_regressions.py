import json
import os
import shutil
import time
import unittest
from unittest import mock

from src.core.memory_guard import MemoryGuard
from src.plugins.snap_history import SnapHistoryManager
from src.utils.ConfigManager import ConfigManager
from src.utils.DataPacket import DataPacket


TEST_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "tests_runtime",
)


class ConfigManagerPerfTests(unittest.TestCase):
    def setUp(self):
        unique_name = f"{self._testMethodName}_{time.time_ns()}"
        self.temp_dir = os.path.join(TEST_ROOT, self.__class__.__name__, unique_name)
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        os.makedirs(self.temp_dir, exist_ok=True)
        self.config_path = os.path.join(self.temp_dir, "stc.json")
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump({"hotkeys": [], "models": {"providers": {}}}, f)
        ConfigManager._instance = None

    def tearDown(self):
        ConfigManager._instance = None
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_load_config_skips_disk_read_when_file_is_unchanged(self):
        cfg = ConfigManager(self.config_path)

        with mock.patch("src.utils.ConfigManager.json.load", side_effect=AssertionError):
            cached = cfg.load_config()

        self.assertEqual(cached["hotkeys"], [])

    def test_reload_if_changed_detects_external_updates(self):
        cfg = ConfigManager(self.config_path)

        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump({"hotkeys": [{"id": "updated"}], "models": {"providers": {}}}, f)
        future_time = time.time() + 2
        os.utime(self.config_path, (future_time, future_time))

        cfg.reload_if_changed()

        self.assertEqual(cfg.get("hotkeys"), [{"id": "updated"}])


class MemoryGuardPerfTests(unittest.TestCase):
    def setUp(self):
        unique_name = f"{self._testMethodName}_{time.time_ns()}"
        self.temp_dir = os.path.join(TEST_ROOT, self.__class__.__name__, unique_name)
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        os.makedirs(self.temp_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_memory_guard_caps_floating_windows_and_prunes_screenshots(self):
        guard = MemoryGuard(max_float_windows=3, max_screenshot_files=5)
        closed = []

        class FakeWin:
            def __init__(self, idx):
                self.idx = idx
                self.win = mock.MagicMock()
                self.win.winfo_exists.return_value = True

            def close(self):
                closed.append(self.idx)

        windows = [FakeWin(i) for i in range(6)]
        stats = guard.enforce_window_cap(windows)
        self.assertEqual(stats["active_windows"], 3)
        self.assertEqual(closed, [0, 1, 2])

        for i in range(8):
            p = os.path.join(self.temp_dir, f"screenshot_{i:03d}.png")
            with open(p, "wb") as fh:
                fh.write(b"png")
            os.utime(p, (1000 + i, 1000 + i))

        deleted = guard.prune_screenshots_dir(self.temp_dir)
        self.assertEqual(deleted, 3)


class SnapHistoryPerfTests(unittest.TestCase):
    def setUp(self):
        unique_name = f"{self._testMethodName}_{time.time_ns()}"
        self.temp_dir = os.path.join(TEST_ROOT, self.__class__.__name__, unique_name)
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        os.makedirs(self.temp_dir, exist_ok=True)
        self.save_path = os.path.join(self.temp_dir, "snap_history.json")
        SnapHistoryManager._instance = None

    def tearDown(self):
        try:
            manager = SnapHistoryManager.get_instance(self.save_path)
            manager.flush()
        except Exception:
            pass
        SnapHistoryManager._instance = None
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_debounced_save_persists_latest_snapshot(self):
        manager = SnapHistoryManager.get_instance(self.save_path)

        for i in range(5):
            manager.push(
                DataPacket(
                    ans=f"answer {i}",
                    image_path=f"img_{i}.png",
                    original_timestamp_str=f"ts_{i}",
                    model_name="model",
                    prompt_name="prompt",
                )
            )

        time.sleep(0.35)
        manager.flush()

        with open(self.save_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertEqual(len(data["history"]), 5)
        self.assertEqual(data["history"][-1]["ans"], "answer 4")

    def test_pin_lifecycle_persists_custom_title(self):
        manager = SnapHistoryManager.get_instance(self.save_path)
        packet = DataPacket(
            ans="First line title\nSecond line",
            image_path="img.png",
            original_timestamp_str="ts_1",
            model_name="model",
            prompt_name="prompt",
        )

        pinned_entry = manager.pin(packet)
        self.assertTrue(manager.is_pinned(packet))

        pins = manager.get_pins()
        self.assertEqual(len(pins), 1)
        self.assertEqual(pins[0]["display_title"], "First line title")

        manager.rename_pin(pinned_entry["id"], "Custom title")
        manager.flush()

        SnapHistoryManager._instance = None
        manager = SnapHistoryManager.get_instance(self.save_path)
        pins = manager.get_pins()
        self.assertEqual(pins[0]["display_title"], "Custom title")

        manager.unpin(pinned_entry["id"])
        manager.flush()
        self.assertFalse(manager.get_pins())


if __name__ == "__main__":
    unittest.main()
