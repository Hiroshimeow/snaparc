from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.gui.screenshot_chat.models import ScreenshotChatSession  # noqa: E402
from src.gui.screenshot_chat.process_manager import (  # noqa: E402
    ScreenshotChatProcessManager,
    ScreenshotChatStartupError,
)


class SmokeController:
    def __init__(self, project_root: Path, gui: str, timeout_ms: int):
        self.project_root = str(project_root)
        self.pending_attachments = []
        self._session = ScreenshotChatSession(id="smoke", title="Smoke Test")
        self._config_payload = {
            "runtime": {
                "mode": "webview-process",
                "gui": gui,
                "host": "127.0.0.1",
                "port": 0,
                "startup_timeout_ms": timeout_ms,
                "poll_interval_ms": 900,
            }
        }

    def _config(self):
        return self._config_payload

    def get_active_session(self):
        return self._session

    def list_display_sessions(self):
        return [self._session]

    def list_pinned_snaps(self):
        return []

    def is_pinned_snap_used(self, entry):
        return False

    def get_status(self):
        return ""

    def available_models(self):
        return []

    def prompt_definitions(self):
        return {}

    def system_prompt_summary(self, session=None):
        return "Blank"

    def drain_runner_events(self, limit=6):
        return []


def parse_args():
    parser = argparse.ArgumentParser(description="Smoke test Screenshot Chat WebView child-process startup.")
    parser.add_argument("--gui", default="edgechromium")
    parser.add_argument("--timeout-ms", type=int, default=8000)
    parser.add_argument("--hold-seconds", type=float, default=3.0)
    return parser.parse_args()


def wait_for_page_request(server, timeout_seconds: float) -> bool:
    """Wait until the WebView page itself has fetched /state or /events from the server."""
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    while True:
        if getattr(server, "page_request_count", 0) > 0:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)


def main() -> int:
    args = parse_args()
    controller = SmokeController(PROJECT_ROOT, args.gui or "edgechromium", args.timeout_ms)
    manager = ScreenshotChatProcessManager(controller)
    ready = False
    alive = False
    state_ok = False
    page_ok = False
    state_error = ""
    runtime_error = False
    try:
        # Lazy prewarm intentionally starts only the server; this diagnostic
        # must explicitly launch the child and wait for its ready handshake.
        manager.show()
        ready = True
        page_ok = wait_for_page_request(manager.server, args.hold_seconds)
        alive = manager.process is not None and manager.process.poll() is None
        try:
            state = manager.server.snapshot()
            state_ok = state.get("active_session_id") == "smoke"
        except Exception as exc:
            state_error = f"{type(exc).__name__}: {exc}"
        runtime_error = "python.runtime.dll" in manager._last_startup_log_text.lower()
        print(f"ready={ready}")
        print(f"alive={alive}")
        print(f"state_ok={state_ok}")
        print(f"page_ok={page_ok}")
        if state_error:
            print(f"state_error={state_error}")
        print(f"gui={manager._last_child_gui}")
        print(f"python={manager._last_child_python}")
        print(f"cwd={controller.project_root}")
        print(f"ready_file={manager._last_ready_file}")
        print(f"startup_log={manager._last_startup_log_file}")
        print(f"runtime_dll_error={runtime_error}")
        return 0 if ready and alive and state_ok and page_ok and not runtime_error else 1
    except ScreenshotChatStartupError as exc:
        runtime_error = "python.runtime.dll" in manager._last_startup_log_text.lower()
        print(f"ready={ready}")
        print(f"alive={alive}")
        print(f"startup_error={exc}")
        print(f"startup_log={manager._last_startup_log_file}")
        print(f"runtime_dll_error={runtime_error}")
        return 1
    finally:
        manager.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
