from __future__ import annotations

from types import SimpleNamespace

from scripts.diagnostics import screenshot_chat_webview_smoke as smoke
from src.gui.screenshot_chat.webview_bridge import ScreenshotChatStateAdapter


def test_smoke_controller_can_build_real_state_snapshot(tmp_path):
    controller = smoke.SmokeController(tmp_path, "edgechromium", 1000)

    state = ScreenshotChatStateAdapter(controller).get_state()

    assert state["active_session_id"] == "smoke"
    assert state["session"]["system_prompt_label"] == "Blank"


def test_smoke_main_fails_if_server_state_snapshot_is_broken(monkeypatch, tmp_path):
    class FakeProcess:
        def poll(self):
            return None

    class BrokenServer:
        page_request_count = 1

        def snapshot(self):
            raise RuntimeError("state endpoint broken")

    class FakeManager:
        def __init__(self, controller):
            self.process = FakeProcess()
            self.server = BrokenServer()
            self._last_startup_log_text = ""
            self._last_child_gui = "edgechromium"
            self._last_child_python = "python.exe"
            self._last_ready_file = "ready.tmp"
            self._last_startup_log_file = "startup.log"

        def show(self):
            return None

        def shutdown(self):
            return None

    monkeypatch.setattr(
        smoke,
        "parse_args",
        lambda: SimpleNamespace(gui="edgechromium", timeout_ms=1000, hold_seconds=0),
    )
    monkeypatch.setattr(smoke, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(smoke, "ScreenshotChatProcessManager", FakeManager)

    assert smoke.main() == 1


def _run_smoke_with_server(monkeypatch, tmp_path, server, capsys):
    class FakeProcess:
        def poll(self):
            return None

    class FakeManager:
        def __init__(self, controller):
            self.process = None
            self.server = server
            self._last_startup_log_text = ""
            self._last_child_gui = "edgechromium"
            self._last_child_python = "python.exe"
            self._last_ready_file = "ready.tmp"
            self._last_startup_log_file = "startup.log"

        def prewarm_hidden(self):
            # Normal lazy startup has a server but no WebView child.
            return None

        def show(self):
            self.process = FakeProcess()

        def shutdown(self):
            return None

    monkeypatch.setattr(
        smoke,
        "parse_args",
        lambda: SimpleNamespace(gui="edgechromium", timeout_ms=1000, hold_seconds=0.2),
    )
    monkeypatch.setattr(smoke, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(smoke, "ScreenshotChatProcessManager", FakeManager)
    code = smoke.main()
    return code, capsys.readouterr().out


class _HealthyServer:
    def __init__(self, page_request_count):
        self.page_request_count = page_request_count

    def snapshot(self):
        return {"active_session_id": "smoke"}


def test_smoke_passes_when_page_fetched_state(monkeypatch, tmp_path, capsys):
    code, out = _run_smoke_with_server(monkeypatch, tmp_path, _HealthyServer(2), capsys)

    assert code == 0
    assert "page_ok=True" in out


def test_smoke_fails_when_page_never_reaches_server(monkeypatch, tmp_path, capsys):
    code, out = _run_smoke_with_server(monkeypatch, tmp_path, _HealthyServer(0), capsys)

    assert code == 1
    assert "page_ok=False" in out
    assert "state_ok=True" in out
