from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

from src.gui.screenshot_chat.server import ScreenshotChatServer

LOW_MEMORY_WEBVIEW2_ARGS = (
    "--disable-features=msWebOOUI,msPdfOOUI,Translate,"
    "BackForwardCache,SpareRendererForSitePerProcess "
    "--process-per-site --disk-cache-size=16777216 --media-cache-size=16777216 "
    "--js-flags=--max-old-space-size=192"
)


class ScreenshotChatStartupError(RuntimeError):
    pass


class ScreenshotChatProcessManager:
    def __init__(self, controller):
        self.controller = controller
        self._config = None
        self.server = None
        self.process = None
        self.visible = False
        self._hidden_since = 0.0
        self._idle_timer: threading.Timer | None = None
        self._last_start_error = ""
        self._last_startup_log_text = ""
        self._last_startup_log_file = None
        self._last_ready_file = None
        self._last_child_command = []
        self._last_child_python = ""
        self._last_child_gui = ""

    def prewarm_hidden(self):
        self.visible = False
        self._config = self.controller._config()
        self._ensure_server()
        self.server.request_hide()
        runtime = self._runtime_config()
        if bool(runtime.get("eager_webview_spawn", False)):
            if not self.is_alive():
                self._spawn_child()

    def show(self):
        self._cancel_idle_timer()
        self.visible = True
        self._hidden_since = 0.0
        self._ensure_runtime()
        self.server.request_show()

    def hide(self):
        self.visible = False
        self._hidden_since = time.monotonic()
        if self.server is not None:
            self.server.request_hide()
        self._schedule_idle_reclaim()

    def _cancel_idle_timer(self):
        timer = self._idle_timer
        self._idle_timer = None
        if timer is not None:
            try:
                timer.cancel()
            except Exception:
                pass

    def _schedule_idle_reclaim(self):
        self._cancel_idle_timer()
        runtime = self._runtime_config()
        idle_seconds = float(runtime.get("idle_shutdown_seconds", 180) or 0)
        if idle_seconds <= 0 or not self.is_alive():
            return
        timer = threading.Timer(idle_seconds, self._reclaim_idle_child)
        timer.daemon = True
        self._idle_timer = timer
        timer.start()

    def _reclaim_idle_child(self):
        self._idle_timer = None
        if self.visible or not self.is_alive():
            return
        try:
            busy_fn = getattr(self.controller, "has_active_jobs", None)
            if callable(busy_fn) and busy_fn():
                self._schedule_idle_reclaim()
                return
        except Exception:
            pass
        if self.process is not None and self.process.poll() is None:
            try:
                self.process.terminate()
                self.process.wait(timeout=2)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
        self.process = None

    def is_alive(self) -> bool:
        if self.process is not None and self.process.poll() is None:
            return True
        return self.server is not None and not self.visible

    def _child_is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def shutdown(self):
        self._cancel_idle_timer()
        if self.server is not None:
            self.server.request_hide()
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except Exception:
                self.process.kill()
        self.process = None
        if self.server is not None:
            self.server.stop()
            self.server = None

    def _ensure_runtime(self):
        self._config = self.controller._config()
        self._ensure_server()
        if self._child_is_running():
            return
        self._spawn_child()

    def _ensure_server(self):
        runtime = self._runtime_config()
        if self.server is None:
            self.server = ScreenshotChatServer(
                self.controller,
                host=str(runtime.get("host") or "127.0.0.1"),
                port=int(runtime.get("port", 0) or 0),
                poll_interval_ms=int(runtime.get("poll_interval_ms", 650) or 650),
            )
            self.server.start()

    def _runtime_config(self) -> dict:
        config = self._config or self.controller._config()
        runtime = config.get("runtime")
        return dict(runtime) if isinstance(runtime, dict) else {}

    def _spawn_child(self):
        runtime = self._runtime_config()
        gui = str(runtime.get("gui") or "edgechromium")
        ready_file = Path(tempfile.gettempdir()) / f"screenshot_chat_ready_{os.getpid()}_{int(time.time() * 1000)}.tmp"
        log_file = Path(tempfile.gettempdir()) / f"screenshot_chat_startup_{os.getpid()}_{int(time.time() * 1000)}.log"
        frozen = bool(getattr(sys, "frozen", False))
        child_python = sys.executable if frozen else self._resolve_child_python()
        command = [
            child_python,
            *(["--snaparc-webview-child"] if frozen else ["-m", "src.gui.screenshot_chat.webview_app"]),
            "--server-url",
            self.server.base_url,
            "--parent-pid",
            str(os.getpid()),
            "--poll-interval-ms",
            str(int(runtime.get("poll_interval_ms", 650) or 650)),
            "--ready-file",
            str(ready_file),
            "--gui",
            gui,
        ]
        self._last_startup_log_text = ""
        self._last_startup_log_file = str(log_file)
        self._last_ready_file = str(ready_file)
        self._last_child_command = list(command)
        self._last_child_python = child_python
        self._last_child_gui = gui
        print(f"ScreenshotChat: child python={child_python}")
        print(f"ScreenshotChat: child cwd={self.controller.project_root}")
        print(f"ScreenshotChat: child gui={gui}")
        print(f"ScreenshotChat: child ready_file={ready_file}")
        print(f"ScreenshotChat: child startup_log={log_file}")
        print(f"ScreenshotChat: child command={subprocess.list2cmdline(self._mask_token(command))}")
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        child_env = os.environ.copy()
        # Keep the transient session token out of the OS process command line.
        child_env["SNAPARC_CHAT_SESSION_TOKEN"] = self.server.token
        child_env.setdefault("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", LOW_MEMORY_WEBVIEW2_ARGS)
        log_handle = open(log_file, "w", encoding="utf-8")
        try:
            self.process = subprocess.Popen(
                command,
                cwd=self.controller.project_root,
                stdout=subprocess.DEVNULL,
                stderr=log_handle,
                creationflags=creationflags,
                env=child_env,
            )
        finally:
            log_handle.close()
        timeout_ms = int(runtime.get("startup_timeout_ms", 6000) or 6000)
        deadline = time.time() + (timeout_ms / 1000.0)
        while time.time() < deadline:
            if self.process.poll() is not None:
                exit_code = self.process.poll()
                print(f"ScreenshotChat: child exit_code={exit_code}")
                details = self._read_startup_log(log_file, preserve=True)
                try:
                    ready_file.unlink()
                except OSError:
                    pass
                raise ScreenshotChatStartupError(
                    self._format_startup_error("exited during startup", details, log_file, exit_code)
                )
            if ready_file.exists():
                print("ScreenshotChat: child exit_code=None")
                self._last_startup_log_text = self._read_startup_log(log_file, preserve=True)
                try:
                    ready_file.unlink()
                except OSError:
                    pass
                try:
                    log_file.unlink()
                except OSError:
                    pass
                return
            time.sleep(0.1)
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=1)
            except Exception:
                self.process.kill()
        try:
            ready_file.unlink()
        except OSError:
            pass
        exit_code = self.process.poll() if self.process is not None else None
        print(f"ScreenshotChat: child exit_code={exit_code}")
        details = self._read_startup_log(log_file, preserve=True)
        raise ScreenshotChatStartupError(
            self._format_startup_error("startup timed out", details, log_file, exit_code)
        )

    @staticmethod
    def _mask_token(command: list) -> list:
        return ["--token=***" if str(part).startswith("--token=") else part for part in command]

    def _resolve_child_python(self) -> str:
        win_rel = ".venv\\Scripts\\python.exe"
        win_candidate = Path(f"{self.controller.project_root}\\{win_rel}")
        posix_win_candidate = Path(self.controller.project_root) / ".venv" / "Scripts" / "python.exe"
        posix_candidate = Path(self.controller.project_root) / ".venv" / "bin" / "python"

        for candidate, display in (
            (win_candidate, str(win_candidate)),
            (posix_win_candidate, str(posix_win_candidate)),
            (posix_candidate, str(posix_candidate)),
            (Path(sys.executable), sys.executable),
        ):
            if candidate.exists() and self._can_import_webview(display):
                return display
        return sys.executable

    def _can_import_webview(self, python_path: str) -> bool:
        try:
            result = subprocess.run(
                [python_path, "-c", "import webview"],
                cwd=self.controller.project_root,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _read_startup_log(self, log_file: Path, preserve: bool = False) -> str:
        try:
            text = log_file.read_text(encoding="utf-8").strip()
        except OSError:
            text = ""
        if not preserve:
            try:
                log_file.unlink()
            except OSError:
                pass
        self._last_start_error = text
        self._last_startup_log_text = text
        return text

    def _format_startup_error(self, reason: str, details: str, log_file: Path, exit_code=None) -> str:
        suffix = f" exit_code={exit_code}; startup_log={log_file}"
        if not details:
            return f"Screenshot Chat process {reason}.{suffix}"
        line = details.splitlines()[-1].strip()
        return f"Screenshot Chat process {reason}: {line};{suffix}"
