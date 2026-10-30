"""Anti-Lag Memory & Resource Lifecycle Guard (v1.1)."""

from __future__ import annotations

import gc
import os
from pathlib import Path
import threading
from typing import Any


class MemoryGuard:
    MAX_FLOATING_WINDOWS = 6
    MAX_SCREENSHOT_FILES = 120

    def __init__(
        self,
        screenshots_dir: str | Path | None = None,
        max_float_windows: int = 6,
        max_screenshot_files: int = 120,
    ) -> None:
        self.screenshots_dir = Path(screenshots_dir) if screenshots_dir else None
        self.max_float_windows = max(1, int(max_float_windows or self.MAX_FLOATING_WINDOWS))
        self.max_screenshot_files = max(1, int(max_screenshot_files or self.MAX_SCREENSHOT_FILES))
        self._lock = threading.Lock()
        self._ops_counter = 0

    @staticmethod
    def close_image_safely(img: Any) -> None:
        if img is None:
            return
        close_fn = getattr(img, "close", None)
        if callable(close_fn):
            try:
                close_fn()
            except Exception:
                pass

    @staticmethod
    def close_images_safely(images: list[Any] | tuple[Any, ...]) -> None:
        for img in images or ():
            MemoryGuard.close_image_safely(img)

    def enforce_floating_window_cap(self, window_list: list[Any], max_windows: int | None = None) -> int:
        limit = max(1, int(max_windows or self.max_float_windows))
        closed_count = 0
        alive = []
        for fw in list(window_list):
            try:
                if fw is not None and hasattr(fw, "win") and fw.win.winfo_exists():
                    alive.append(fw)
                else:
                    window_list.remove(fw)
            except Exception:
                try:
                    window_list.remove(fw)
                except ValueError:
                    pass

        while len(alive) > limit:
            oldest = alive.pop(0)
            try:
                if oldest in window_list:
                    window_list.remove(oldest)
                oldest.close()
                closed_count += 1
            except Exception:
                pass
        return closed_count

    def enforce_window_cap(self, window_list: list[Any], max_windows: int | None = None) -> dict[str, int]:
        closed = self.enforce_floating_window_cap(window_list, max_windows=max_windows)
        return {"active_windows": len(window_list), "closed_windows": closed}

    def prune_screenshots_dir(self, screenshots_dir: str | Path | None = None, protected_paths: set[str] | None = None) -> int:
        target_dir = Path(screenshots_dir) if screenshots_dir else self.screenshots_dir
        if target_dir is None or not target_dir.is_dir():
            return 0
        protected = {os.path.abspath(p) for p in (protected_paths or set()) if p}
        try:
            files = [
                p
                for p in target_dir.iterdir()
                if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
            ]
            if len(files) <= self.max_screenshot_files:
                return 0
            files.sort(key=lambda p: p.stat().st_mtime)
            removed = 0
            for candidate in files[: len(files) - self.max_screenshot_files]:
                if os.path.abspath(str(candidate)) in protected:
                    continue
                try:
                    candidate.unlink(missing_ok=True)
                    removed += 1
                except OSError:
                    pass
            return removed
        except Exception:
            return 0

    def prune_old_screenshots(self, protected_paths: set[str] | None = None) -> int:
        return self.prune_screenshots_dir(self.screenshots_dir, protected_paths=protected_paths)

    def on_capture_completed(self, window_list: list[Any], screenshots_dir: str | Path | None = None) -> None:
        self.enforce_floating_window_cap(window_list)
        if self.record_heavy_op_and_maybe_gc():
            self.prune_screenshots_dir(screenshots_dir)

    def record_heavy_op_and_maybe_gc(self) -> bool:
        with self._lock:
            self._ops_counter += 1
            should_collect = (self._ops_counter % 15) == 0
        if should_collect:
            gc.collect(generation=1)
            self.trim_working_set()
            return True
        return False

    @staticmethod
    def trim_working_set() -> bool:
        """Reclaim unused physical RAM pages on Windows (SetProcessWorkingSetSize / EmptyWorkingSet)."""
        gc.collect()
        if os.name != "nt":
            return False
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetCurrentProcess()
            kernel32.SetProcessWorkingSetSize(handle, ctypes.c_size_t(-1), ctypes.c_size_t(-1))
            try:
                ctypes.windll.psapi.EmptyWorkingSet(handle)
            except Exception:
                pass
            return True
        except Exception:
            return False

