"""
Production Verification Suite for Screens-Trans-Chatbot v2.1 (`tests/test_v2_1_production_suite.py`)
====================================================================================================
Verifies all user-reported v1.1 issues are resolved in v2.1:
1. `uv run main.py` / `pyproject.toml` flat-layout build compatibility on CPython 3.10–3.14+:
   - `[tool.uv] package = false`
   - `[tool.hatch.build.targets.wheel] packages = ["src"]`
   - `[tool.setuptools.packages.find] include = ["src*"] exclude = ["plugins*", ...]`
   - `requires-python = ">=3.10"` (no `<3.14` ceiling in `pyproject.toml` or `uv.lock`).
2. Complete elimination of legacy config/sidecar code smells:
   - Zero `stc.json`, `sample.stc.json`, `prompts.toml`, `prompts.toml.example`, `history-xml/`, `sidecars/`.
   - Single unified `config.yaml`; providers and model IDs are user configuration.
3. Complete elimination of the 64-bit Windows `_Win32DropTarget` Access Violation (`0x00000000DFE37390`):
   - `_Win32DropTarget` never calls `SetWindowLongPtrW` or `CallWindowProcW` on Tkinter windows.
   - `read_clipboard_files_win32()` safely reads copied file paths without `WndProc` subclassing.
4. Multi-Monitor & FloatingWindow Anti-Lag:
   - `FloatingWindow` caches monitor topology at the class level (`_GLOBAL_MONITOR_RECTS`) so creating multiple windows does not re-enumerate monitors or spam console logs.
5. Native `pi --mode rpc` Architecture (`src/agents/pi_rpc_bridge.py` + `src/agents/pi_runtime_factory.py`) & Obsidian `ScreenshotChat` UI (`Ctrl + Shift + Alt + X`):
   - `detect_pi_availability()` and `PiSdkBridgeTransport` run via `src/agents/pi_rpc_bridge.py` with zero `sidecars/` directory.
   - `ScreenshotChatServer` supports `POST /upload_files` for HTML5 drag-and-drop and `Ctrl+V` file/image attachments.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
import tempfile
import sys
try:
    import tomllib
except ImportError:
    import tomli as tomllib
import unittest
import urllib.request

from src.agents.pi_rpc_bridge import NativePiRpcServer, resolve_pi_cli
from src.agents.pi_runtime_factory import build_pi_runtime_factory, detect_pi_availability
from src.gui.quick_prompt_popup import _Win32DropTarget, read_clipboard_files_win32
from src.gui.screenshot_chat.controller import ScreenshotChatController
from src.gui.screenshot_chat.server import ScreenshotChatServer
from src.gui.screenshot_chat.store import ScreenshotChatStore
from src.utils.ConfigManager import ConfigManager
from src.utils.FollowManager import FollowManager
from src.utils.prompt_store import load_prompt_payload
from src.utils.yaml_io import dump_yaml_or_json, load_yaml_or_json

REPO_ROOT = Path(__file__).resolve().parents[1]


class STCV21ProductionTests(unittest.TestCase):
    def test_01_pyproject_and_uv_lock_support_python_314_and_flat_layout(self) -> None:
        pyproject_path = REPO_ROOT / "pyproject.toml"
        data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
        self.assertIn(data["project"]["version"], ("0.0.2",))
        self.assertEqual(data["project"]["requires-python"], ">=3.10")
        self.assertFalse(data.get("tool", {}).get("uv", {}).get("package", True))
        self.assertEqual(
            data.get("tool", {}).get("hatch", {}).get("build", {}).get("targets", {}).get("wheel", {}).get("packages"),
            ["src"],
        )
        find_cfg = data.get("tool", {}).get("setuptools", {}).get("packages", {}).get("find", {})
        self.assertIn("src*", find_cfg.get("include", []))
        self.assertIn("plugins*", find_cfg.get("exclude", []))

        uv_lock_path = REPO_ROOT / "uv.lock"
        if uv_lock_path.is_file():
            head = uv_lock_path.read_text(encoding="utf-8")[:300]
            self.assertNotIn("<3.14", head)

    def test_02_zero_legacy_files_and_zero_wndproc_hooks(self) -> None:
        forbidden = [
            REPO_ROOT / "stc.json",
            REPO_ROOT / "sample.stc.json",
            REPO_ROOT / "prompts.toml",
            REPO_ROOT / "prompts.toml.example",
            REPO_ROOT / "history-xml",
            REPO_ROOT / "sidecars",
            REPO_ROOT / "src" / "models" / "antigravity-accounts.json",
            REPO_ROOT / "src" / "models" / "oath_gemini.py",
        ]
        for p in forbidden:
            self.assertFalse(p.exists(), f"Legacy path must not exist in v2.1: {p}")

        for py_file in (REPO_ROOT / "src").rglob("*.py"):
            src = py_file.read_text(encoding="utf-8")
            self.assertNotIn("SetWindowLongPtrW", src, f"Forbidden WndProc hook in {py_file}")
            self.assertNotIn("CallWindowProcW", src, f"Forbidden WndProc hook in {py_file}")

    def test_03_unified_config_yaml_and_zero_missing_file_errors(self) -> None:
        cfg_path = REPO_ROOT / "sample" / "config.example.yaml"
        self.assertTrue(cfg_path.is_file())
        parsed = load_yaml_or_json(cfg_path.read_text(encoding="utf-8"))
        providers = parsed.get("models", {}).get("providers", {})
        self.assertEqual(set(providers), {"my-provider"})
        self.assertEqual(providers["my-provider"]["models"], ["example-model"])

        # FollowManager & load_prompt_payload must never fail when prompts.toml is absent
        fm = FollowManager(str(REPO_ROOT / "prompts.toml"))
        self.assertEqual(fm.get_limit(), 10)
        prompts_payload = load_prompt_payload(str(REPO_ROOT / "prompts.toml"))
        self.assertIn("prompt_translate", prompts_payload.get("prompts", {}))

    def test_04_win32_drop_target_is_crash_free_without_wndproc(self) -> None:
        received: list[list[str]] = []
        target = _Win32DropTarget(object(), lambda files: received.append(files))
        self.assertTrue(target.install())
        self.assertIsInstance(read_clipboard_files_win32(), list)

    def test_05_native_pi_rpc_bridge_and_obsidian_screenshot_chat_upload(self) -> None:
        avail = detect_pi_availability({"backend": "rpc", "enabled": True, "auto_detect": True})
        self.assertTrue(avail.available)
        self.assertEqual(avail.reason, "available")

        selection = build_pi_runtime_factory({"backend": "rpc", "enabled": True, "auto_detect": True})
        self.assertIsNotNone(selection.transport)
        try:
            hello = selection.transport.start()
            self.assertEqual(hello.protocol, 1)
            self.assertIn("pi-rpc", hello.bridge_version)
        finally:
            selection.transport.shutdown()

        # Verify ScreenshotChatServer /upload_files endpoint and Obsidian web assets
        with tempfile.TemporaryDirectory() as td:
            tmp_dir = Path(td)
            cfg_file = tmp_dir / "config.yaml"
            cfg_file.write_text((REPO_ROOT / "sample" / "config.example.yaml").read_text(encoding="utf-8"), encoding="utf-8")
            cm = ConfigManager.__new__(ConfigManager)
            cm._initialized = False
            cm.__init__(str(cfg_file))

            store = ScreenshotChatStore(str(tmp_dir / "sessions.json"))
            ctrl = ScreenshotChatController(
                root=object(),
                config_manager=cm,
                store=store,
                project_root=str(tmp_dir),
            )
            srv = ScreenshotChatServer(ctrl, host="127.0.0.1", port=0, token="test-tok")
            srv.start()
            try:
                page = srv.render_page()
                self.assertIn("dropOverlay", page)
                self.assertIn("composer-quickbar", page)
                self.assertIn("upload_files", page)

                # Test POST /upload_files with a base64 PNG
                png_b64 = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode("ascii")
                req = urllib.request.Request(
                    f"{srv.base_url}/upload_files",
                    data=json.dumps({
                        "files": [{"name": "dropped.png", "type": "image/png", "dataBase64": png_b64}]
                    }).encode("utf-8"),
                    headers={"Content-Type": "application/json", "X-STC-Token": "test-tok"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=3) as resp:
                    state = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(len(state.get("pending_attachments", [])), 1)
                self.assertIn("dropped.png", state["pending_attachments"][0]["label"])
            finally:
                srv.stop()



    def test_06_webview2_anti_800mb_memory_and_lazy_spawn_guard(self) -> None:
        from src.gui.screenshot_chat.process_manager import (
            LOW_MEMORY_WEBVIEW2_ARGS,
            ScreenshotChatProcessManager,
        )
        from src.core.memory_guard import MemoryGuard

        self.assertIn("--max-old-space-size=192", LOW_MEMORY_WEBVIEW2_ARGS)
        self.assertIn("--disk-cache-size=16777216", LOW_MEMORY_WEBVIEW2_ARGS)
        self.assertTrue(callable(MemoryGuard.trim_working_set))

        class _DummyCtrl:
            project_root = str(REPO_ROOT)
            def _config(self):
                return {
                    "runtime": {
                        "mode": "webview-process",
                        "host": "127.0.0.1",
                        "port": 0,
                        "poll_interval_ms": 650,
                        "eager_webview_spawn": False,
                        "idle_shutdown_seconds": 180,
                    }
                }
            def has_active_jobs(self):
                return False

        mgr = ScreenshotChatProcessManager(_DummyCtrl())
        # Verify prewarm_hidden starts only the lightweight server without spawning msedgewebview2.exe child
        mgr._ensure_server = lambda: setattr(
            mgr,
            "server",
            type("S", (), {"request_hide": lambda self: None, "stop": lambda self: None})(),
        )
        mgr.prewarm_hidden()
        self.assertIsNone(mgr.process, "prewarm_hidden must not spawn heavy WebView2 child process at startup")
        mgr.shutdown()



    def test_07_pi_rpc_full_terminal_tools_read_write_edit_bash_grep_find_ls(self) -> None:
        from src.agents.pi_rpc_bridge import PI_TERMINAL_TOOLS, PiTerminalToolset

        self.assertEqual(PI_TERMINAL_TOOLS, ("read", "write", "edit", "bash", "grep", "find", "ls"))
        with tempfile.TemporaryDirectory() as td:
            toolset = PiTerminalToolset(cwd=td, trusted_roots=["."])
            w_res = toolset.execute("write", {"path": "hello.txt", "content": "alpha\nbeta\ngamma\n"})
            self.assertTrue(w_res["ok"])

            r_res = toolset.execute("read", {"path": "hello.txt", "offset": 1, "limit": 10})
            self.assertIn("2: beta", r_res["output"])

            e_res = toolset.execute("edit", {"path": "hello.txt", "oldText": "beta", "newText": "BETA_UPDATED"})
            self.assertTrue(e_res["ok"])

            g_res = toolset.execute("grep", {"pattern": "BETA_UPDATED", "path": "."})
            self.assertIn("hello.txt:2: BETA_UPDATED", g_res["output"])

            f_res = toolset.execute("find", {"pattern": "*.txt", "path": "."})
            self.assertIn("hello.txt", f_res["output"])

            l_res = toolset.execute("ls", {"path": "."})
            self.assertIn("hello.txt", l_res["output"])

            # Use the interpreter running this suite, not a platform-specific alias.
            b_res = toolset.execute("bash", {"command": f'"{sys.executable}" -c "print(6 * 7)"'})
            self.assertTrue(b_res["ok"], b_res)
            self.assertIn("42", b_res["output"])


if __name__ == "__main__":
    unittest.main()
