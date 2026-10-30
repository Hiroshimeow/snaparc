"""Double-Run Singleton Guard & Crash/Power-Restart Recovery Journal.

Ensures:
1. Only one instance of the assistant runs per user desktop session via Win32 Named Mutex
   (`Local\\ScreensTransChatbot_Singleton_v2`) or POSIX lockfile.
2. Clean crash/power-restart recovery of any in-flight tasks without replaying ambiguous
   side-effecting tool calls.
"""

from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import sys
import threading
import time
from typing import Any

_PROCESS_LOCKS_GUARD = threading.Lock()
_ACTIVE_PROCESS_LOCKS: set[str] = set()


class SingleInstanceGuard:
    """Prevents accidental double-launch hotkey/tray collisions."""

    def __init__(self, lock_path: str | Path, mutex_name: str = "Local\\ScreensTransChatbot_Singleton_v2"):
        self.lock_path = Path(lock_path).resolve()
        self.mutex_name = mutex_name
        self._acquired = False
        self._mutex_handle = None

    def acquire(self) -> bool:
        if self._acquired:
            return True

        lock_key = str(self.lock_path)
        with _PROCESS_LOCKS_GUARD:
            if lock_key in _ACTIVE_PROCESS_LOCKS:
                return False

            if sys.platform == "win32" and hasattr(ctypes, "windll"):
                try:
                    kernel32 = ctypes.windll.kernel32
                    ERROR_ALREADY_EXISTS = 183
                    handle = kernel32.CreateMutexW(None, False, self.mutex_name)
                    if handle:
                        last_err = kernel32.GetLastError()
                        if last_err == ERROR_ALREADY_EXISTS:
                            kernel32.CloseHandle(handle)
                            return False
                        self._mutex_handle = handle
                except Exception:
                    pass

            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
            if self.lock_path.is_file():
                try:
                    data = json.loads(self.lock_path.read_text(encoding="utf-8"))
                    pid = int(data.get("pid") or 0)
                    if pid and self._pid_alive(pid):
                        return False
                except Exception:
                    pass

            try:
                payload = {"pid": os.getpid(), "started_at": time.time(), "mutex": self.mutex_name}
                self.lock_path.write_text(json.dumps(payload), encoding="utf-8")
                _ACTIVE_PROCESS_LOCKS.add(lock_key)
                self._acquired = True
                return True
            except OSError:
                return False

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False

    def release(self) -> None:
        if not self._acquired:
            return
        self._acquired = False
        lock_key = str(self.lock_path)
        with _PROCESS_LOCKS_GUARD:
            _ACTIVE_PROCESS_LOCKS.discard(lock_key)
            try:
                if self.lock_path.is_file():
                    self.lock_path.unlink(missing_ok=True)
            except OSError:
                pass
            if self._mutex_handle is not None and sys.platform == "win32" and hasattr(ctypes, "windll"):
                try:
                    ctypes.windll.kernel32.CloseHandle(self._mutex_handle)
                except Exception:
                    pass
                self._mutex_handle = None


class CrashRecoveryJournal:
    """Persists active run state so power loss or process crashes never replay side-effecting jobs."""

    def __init__(self, journal_path: str | Path):
        self.journal_path = Path(journal_path)
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)

    def mark_run_started(self, run_id: str, session_id: str, side_effect_possible: bool = False) -> None:
        entries = self._read_all()
        entries[run_id] = {
            "run_id": run_id,
            "session_id": session_id,
            "state": "running",
            "side_effect_started": False,
            "side_effect_possible": bool(side_effect_possible),
            "started_at": time.time(),
        }
        self._write_all(entries)

    def mark_side_effect_started(self, run_id: str, tool_name: str = "") -> None:
        entries = self._read_all()
        if run_id in entries:
            entries[run_id]["side_effect_started"] = True
            entries[run_id]["tool_name"] = str(tool_name or "")
            self._write_all(entries)

    def mark_run_completed(self, run_id: str) -> None:
        entries = self._read_all()
        if run_id in entries:
            del entries[run_id]
            self._write_all(entries)

    def recover_after_crash(self) -> list[dict[str, Any]]:
        """Returns interrupted runs from a prior crash and clears the active journal without replaying."""
        entries = self._read_all()
        recovered = []
        for _run_id, info in entries.items():
            item = dict(info)
            item["recovered_outcome"] = (
                "ambiguous_no_replay" if item.get("side_effect_started") else "interrupted_safe_no_replay"
            )
            recovered.append(item)
        if entries:
            self._write_all({})
        return recovered

    def _read_all(self) -> dict[str, dict[str, Any]]:
        if not self.journal_path.is_file():
            return {}
        try:
            data = json.loads(self.journal_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _write_all(self, data: dict[str, dict[str, Any]]) -> None:
        tmp = self.journal_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, self.journal_path)
