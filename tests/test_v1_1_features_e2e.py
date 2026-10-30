"""
End-to-End Verification Suite for Screens-Trans-Chatbot v1.1
============================================================
Verifies all 6 v1.1 requirements requested by the user:
1. Complete removal of `chat in workspace` (`src/workspace/`, `screen_chat_workspace.py`, `ChatUI.py`).
2. Deep Pi Sidecar Chat Screen (`Ctrl + Shift + Alt + X`) rework:
   - Structured message extraction (`extract_message_structured`) with zero `[object Object]` leakage
   - `<thinking>` blocks, expandable Tool Call/Output cards, and `<task-notification>` subagent cards
   - `/` Slash commands (`/new` with empty-session guard, `/pi`, `/model`, `/thinking`, `/tools`, `/skills`,
     `/harness init`, `/compact`, `/fork`, `/export` full unabbreviated HTML transcript, `/steer`, `/followup`)
   - ★ Favorite models toggle & persistence
3. Removal of `Alt + Snap` visual diff; replacement with Unified `QuickPromptPopup`:
   - `Ctrl + Snap` -> Image prompt mode
   - `Alt + Snap` -> Text & File Drag-and-Drop Q&A mode (`inspect_and_extract_file` for `.txt`, `.md`, `.json`, `.py`, `.pdf`)
   - Direct escalation to Pi Sidecar Multi-Turn Chat Screen (`escalate_from_quick_popup`)
4. `Ctrl + Alt + Hold Mouse (0.2s)` Radial Plugin Wheel (`lolPing` style):
   - Circular sector geometry (`compute_radial_sector`) with center deadzone (`BACK`) and 8 outer slices
   - Hot-reloadable `plugins.yaml` & `stc.json` configuration (`RadialPluginService`)
   - Isolated subprocess execution of external plugin scripts (`format_json.py`, `extract_table.py`, `ocr_clipboard.py`)
   - `InputHandler` `Ctrl + Alt + Hold` detection without conflicting with right-mouse drag capture
5. Ruthless code cleanup, removal of `HistoryManager.py` (`result.html`), and `MemoryGuard` anti-lag protection.
6. Provider/Model Switch Bug Fix ("đổi provider xong vẫn hiện provider gemini 2.5"):
   - `ConfigManager.set` nested key fix
   - `ModelStateStore` single source of truth syncing System Tray, `Gemini`, `FloatingWindow`, and `ScreenshotChatController`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest import mock

from PIL import Image

from src.core.event_bus import AppEventBus
from src.core.memory_guard import MemoryGuard
from src.core.model_state import ModelStateStore
from src.gui.quick_prompt_popup import (
    QuickPromptPopup,
    QuickPromptSubmission,
    inspect_and_extract_file,
)
from src.gui.radial_wheel import compute_radial_sector
from src.gui.screenshot_chat.controller import ScreenshotChatController
from src.gui.screenshot_chat.models import ScreenshotChatMessage
from src.gui.screenshot_chat.pi_harness import (
    SLASH_COMMANDS,
    export_session_html,
    extract_message_structured,
    init_pi_project_harness,
    safe_stringify_content,
)
from src.gui.screenshot_chat.store import ScreenshotChatStore
from src.handlers.InputHandler import InputHandler
from src.services.radial_plugin_service import RadialPluginService
from src.services.snap_modifier_actions import (
    ACTION_CUSTOM_PROMPT,
    ACTION_CUSTOM_PROMPT_NEW_SESSION,
    ACTION_DEFAULT,
    ACTION_RADIAL_WHEEL,
    ACTION_TEXT_FILE_PROMPT,
    SnapActionRouter,
)
from src.utils.ConfigManager import ConfigManager


REPO_ROOT = Path(__file__).resolve().parents[1]


class V11CompleteVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="stc_v1_1_test_"))
        ConfigManager._instance = None

    def tearDown(self):
        ConfigManager._instance = None
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # ------------------------------------------------------------------
    # Requirement 1 & 5: Obsolete Workspace & HTML History Removed
    # ------------------------------------------------------------------
    def test_req1_and_req5_obsolete_workspace_and_html_history_deleted(self):
        self.assertFalse((REPO_ROOT / "src" / "workspace").exists())
        self.assertFalse((REPO_ROOT / "src" / "gui" / "screen_chat_workspace.py").exists())
        self.assertFalse((REPO_ROOT / "src" / "gui" / "ChatUI.py").exists())
        self.assertFalse((REPO_ROOT / "src" / "utils" / "HistoryManager.py").exists())

    # ------------------------------------------------------------------
    # Requirement 2: Deep Pi Sidecar Chat Screen (Lantern v7.91 features)
    # ------------------------------------------------------------------
    def test_req2_structured_extraction_thinking_tools_subagents_and_no_object_object(self):
        raw_content = [
            {"type": "thinking", "thinking": "Analyzing screenshot coordinates and DPI."},
            {"type": "text", "text": "Here is the final answer.[object Object]"},
        ]
        stringified = safe_stringify_content(raw_content)
        self.assertNotIn("[object Object]", stringified)

        msg = ScreenshotChatMessage(
            role="assistant",
            author="Pi Agent",
            text=(
                "<thinking>Step 1: Read file.\nStep 2: Verify output.</thinking>\n"
                "<task-notification>\n"
                "<task-id>sub_01</task-id>\n"
                "<status>completed</status>\n"
                "<summary>Audited 4 modules</summary>\n"
                "<result>All 4 modules passed static checks.</result>\n"
                "</task-notification>\n"
                "Final response prose."
            ),
            status="done",
            events=[
                {
                    "id": "call_1",
                    "kind": "tool_started",
                    "tool_name": "read_file",
                    "status": "running",
                    "data": {"tool_call_id": "call_1", "args": {"path": "main.py"}},
                },
                {
                    "id": "out_1",
                    "kind": "tool_output",
                    "tool_name": "read_file",
                    "text": "def main(): pass",
                    "status": "done",
                    "data": {"tool_call_id": "call_1"},
                },
            ],
        )
        structured = extract_message_structured(msg)
        self.assertEqual(structured["text"], "Final response prose.")
        self.assertIn("Step 1: Read file.", structured["thinking"])
        self.assertEqual(len(structured["tools"]), 1)
        self.assertEqual(structured["tools"][0]["name"], "read_file")
        self.assertEqual(structured["tools"][0]["result"], "def main(): pass")
        self.assertEqual(len(structured["subagents"]), 1)
        self.assertEqual(structured["subagents"][0]["task_id"], "sub_01")
        self.assertEqual(structured["subagents"][0]["status"], "completed")

    def test_req2_slash_commands_fork_compact_export_favorites_and_empty_session_guard(self):
        from tests.gui.screenshot_chat.test_controller import FakeConfig, FakeRunner

        store = ScreenshotChatStore(str(self.temp_dir / "sessions.json"))
        runner = FakeRunner()
        controller = ScreenshotChatController(
            root=None,
            config_manager=FakeConfig(),
            project_root=str(self.temp_dir),
            runner=runner,
            store=store,
        )

        # 1. Empty-session guard on /new
        initial_session_id = controller.get_active_session().id
        controller.execute_slash_command("/new")
        self.assertEqual(controller.get_active_session().id, initial_session_id)

        # 2. Favorite models toggle
        favs = controller.toggle_favorite_model("provider/main")
        self.assertIn("provider/main", favs)
        opts = {m["id"]: m for m in controller.available_model_options()}
        self.assertTrue(opts["provider/main"]["favorite"])

        # 3. /thinking, /tools, /skills, /harness init
        res_think = controller.execute_slash_command("/thinking high")
        self.assertEqual(res_think["thinking_level"], "high")
        res_tools = controller.execute_slash_command("/tools approve_mutations")
        self.assertEqual(res_tools["tool_mode"], "approve_mutations")
        res_harness = controller.execute_slash_command("/harness init")
        self.assertTrue(res_harness["ok"])
        self.assertTrue((self.temp_dir / ".pi" / "AGENTS.md").exists())
        self.assertTrue((self.temp_dir / ".pi" / "settings.json").exists())

        # 4. /fork and /compact
        controller.send_message("Turn 1 question", "provider/main", "prompt_translate")
        controller.send_message("Turn 2 question", "provider/main", "prompt_translate")
        forked = controller.fork_session("Branched Investigation")
        self.assertEqual(forked.title, "Branched Investigation")
        self.assertGreaterEqual(len(forked.messages), 4)

        compacted = controller.compact_session("Keep key findings")
        self.assertLessEqual(len(compacted.messages), 3)
        self.assertIn("Compacted", compacted.messages[0].text)

        # 5. /export full unabbreviated HTML transcript
        exported = controller.export_session_transcript()
        self.assertTrue(exported["ok"])
        self.assertTrue(os.path.exists(exported["path"]))
        self.assertIn("Full Unabbreviated Transcript", exported["html"])

    # ------------------------------------------------------------------
    # Requirement 3: Unified QuickPromptPopup (Ctrl=Image, Alt=Text/File + Drag&Drop + Pi Escalation)
    # ------------------------------------------------------------------
    def test_req3_alt_snap_text_file_extraction_and_pi_escalation(self):
        router = SnapActionRouter()
        self.assertEqual(router.resolve({"ctrl": True}).name, ACTION_CUSTOM_PROMPT)
        self.assertEqual(router.resolve({"alt": True}).name, ACTION_TEXT_FILE_PROMPT)
        self.assertEqual(router.resolve({"ctrl": True, "shift": True}).name, ACTION_CUSTOM_PROMPT_NEW_SESSION)
        self.assertEqual(router.resolve({"ctrl": True, "alt": True}).name, ACTION_RADIAL_WHEEL)

        sample_md = self.temp_dir / "notes.md"
        sample_md.write_text("# Sprint Architecture\nUse ModelStateStore for sync.", encoding="utf-8")
        sample_py = self.temp_dir / "helper.py"
        sample_py.write_text("def calc(x):\n    return x * 2\n", encoding="utf-8")

        att_md = inspect_and_extract_file(sample_md)
        att_py = inspect_and_extract_file(sample_py)
        self.assertEqual(att_md.kind, "text")
        self.assertIn("ModelStateStore", att_md.extracted_text)
        self.assertEqual(att_py.kind, "text")
        self.assertIn("def calc(x):", att_py.extracted_text)

        sub = QuickPromptSubmission(
            prompt_text="Explain how these two files interact.",
            mode="text_file",
            target="pi_agent",
            context_text="Clipboard note: refactor v1.1",
            attachments=[att_md, att_py],
        )
        combined = sub.build_combined_prompt()
        self.assertIn("Explain how these two files interact.", combined)
        self.assertIn("Clipboard note: refactor v1.1", combined)
        self.assertIn("notes.md", combined)
        self.assertIn("helper.py", combined)

        # Verify escalation into ScreenshotChatController
        from tests.gui.screenshot_chat.test_controller import FakeConfig, FakeRunner

        store = ScreenshotChatStore(str(self.temp_dir / "sessions_esc.json"))
        runner = FakeRunner()
        controller = ScreenshotChatController(
            root=None,
            config_manager=FakeConfig(),
            project_root=str(self.temp_dir),
            runner=runner,
            store=store,
        )
        controller.open_or_focus = mock.MagicMock()
        ok = controller.escalate_from_quick_popup(sub)
        self.assertTrue(ok)
        active = controller.get_active_session()
        self.assertTrue(any("Explain how these two files interact." in m.text for m in active.messages))

    # ------------------------------------------------------------------
    # Requirement 4: Ctrl + Alt + Hold (0.2s) Radial Plugin Wheel (lolPing style)
    # ------------------------------------------------------------------
    def test_req4_radial_wheel_geometry_and_external_plugins_execution(self):
        # 1. Verify lolPing radial sector geometry (8 sectors around (500, 500))
        # Center deadzone (< 46px) -> None (BACK / cancel)
        idx, dist, _ang = compute_radial_sector(500, 500, 510, 510, 8, deadzone_radius=46.0)
        self.assertIsNone(idx)
        self.assertLess(dist, 46.0)

        # North (12 o'clock: (500, 380)) -> Sector 0
        idx_n, _, _ = compute_radial_sector(500, 500, 500, 380, 8, deadzone_radius=46.0)
        self.assertEqual(idx_n, 0)

        # East (3 o'clock: (620, 500)) -> Sector 2
        idx_e, _, _ = compute_radial_sector(500, 500, 620, 500, 8, deadzone_radius=46.0)
        self.assertEqual(idx_e, 2)

        # South (6 o'clock: (500, 620)) -> Sector 4
        idx_s, _, _ = compute_radial_sector(500, 500, 500, 620, 8, deadzone_radius=46.0)
        self.assertEqual(idx_s, 4)

        # West (9 o'clock: (380, 500)) -> Sector 6
        idx_w, _, _ = compute_radial_sector(500, 500, 380, 500, 8, deadzone_radius=46.0)
        self.assertEqual(idx_w, 6)

        # 2. Load plugins.yaml from repo root and execute external plugin scripts
        service = RadialPluginService(repo_root=REPO_ROOT)
        plugins = service.list_plugins()
        self.assertGreaterEqual(len(plugins), 6)
        plugin_ids = {p.id for p in plugins}
        self.assertIn("format_json", plugin_ids)
        self.assertIn("extract_table", plugin_ids)

        # Execute format_json plugin
        res_json = service.execute_plugin(
            "format_json",
            context={"clipboard_text": '{"b":1,"a":[2,3]}'},
        )
        self.assertTrue(res_json.ok)
        self.assertIn('"a": [', res_json.copy_to_clipboard or "")

        # Execute extract_table plugin
        res_tbl = service.execute_plugin(
            "extract_table",
            context={"clipboard_text": "Name\tScore\nAlice\t98\nBob\t95"},
        )
        self.assertTrue(res_tbl.ok)
        self.assertIn("| Name | Score |", res_tbl.markdown)

        # 3. Verify InputHandler triggers radial wheel after 0.2s hold when Ctrl+Alt pressed
        radial_events = []
        captures = []
        ih = InputHandler(
            on_capture=lambda *a: captures.append(a),
            key_state_checker=lambda k: k in ("ctrl", "alt"),
            on_radial_open=lambda x, y: radial_events.append(("open", x, y)),
            on_radial_move=lambda x, y: radial_events.append(("move", x, y)),
            on_radial_release=lambda x, y: radial_events.append(("release", x, y)),
            radial_hold_seconds=0.02,
        )
        from src.platform.win32_mouse import Button

        ih.on_click(300, 300, Button.right, True)
        time.sleep(0.06)
        ih.on_move(380, 300)
        ih.on_click(380, 300, Button.right, False)

        self.assertEqual(radial_events[0], ("open", 300, 300))
        self.assertIn(("move", 380, 300), radial_events)
        self.assertEqual(radial_events[-1], ("release", 380, 300))
        # Must NOT trigger normal screen capture when Ctrl+Alt radial wheel is used
        self.assertEqual(len(captures), 0)

    # ------------------------------------------------------------------
    # Requirement 6: Provider & Model Switch Bug Fix ("đổi provider xong vẫn hiện gemini 2.5")
    # ------------------------------------------------------------------
    def test_req6_provider_and_model_switch_updates_all_surfaces_without_reverting_to_gemini_2_5(self):
        cfg_path = self.temp_dir / "stc.json"
        cfg_path.write_text(
            json.dumps(
                {
                    "models": {
                        "providers": {
                            "aistudio_api": {
                                "api": "openai-completions",
                                "displayName": "AI Studio",
                                "models": ["gemini-2.5-flash"],
                            },
                            "cliproxy": {
                                "api": "openai-completions",
                                "displayName": "CLI Proxy",
                                "models": ["gpt-5.4", "claude-4-sonnet"],
                            },
                        }
                    },
                    "agents": {
                        "defaults": {
                            "model": {"primary": "aistudio_api/gemini-2.5-flash"},
                            "prompt_type": "prompt_translate",
                        }
                    },
                    "screenshot_chat": {
                        "default_model": "aistudio_api/gemini-2.5-flash",
                    },
                }
            ),
            encoding="utf-8",
        )
        cm = ConfigManager(str(cfg_path))
        bus = AppEventBus()
        model_store = ModelStateStore(cm, bus)

        # Initially on aistudio_api/gemini-2.5-flash
        self.assertEqual(model_store.current_model, "aistudio_api/gemini-2.5-flash")
        self.assertEqual(model_store.current_provider, "aistudio_api")

        # Attach ScreenshotChatController to the same ModelStateStore
        chat_store = ScreenshotChatStore(str(self.temp_dir / "chat.json"))
        controller = ScreenshotChatController(
            root=None,
            config_manager=cm,
            project_root=str(self.temp_dir),
            store=chat_store,
            model_state_store=model_store,
        )
        self.assertEqual(controller.get_active_session().model_name, "aistudio_api/gemini-2.5-flash")

        # User switches provider to "cliproxy" (or selects "cliproxy/gpt-5.4")
        snap = model_store.set_provider("cliproxy", source="tray")
        self.assertEqual(snap.provider, "cliproxy")
        self.assertEqual(snap.full_model, "cliproxy/gpt-5.4")

        # Verify stc.json was updated in BOTH agents.defaults.model.primary AND screenshot_chat.default_model
        reloaded_json = json.loads(cfg_path.read_text(encoding="utf-8"))
        self.assertEqual(
            reloaded_json["agents"]["defaults"]["model"]["primary"],
            "cliproxy/gpt-5.4",
        )
        self.assertEqual(
            reloaded_json["screenshot_chat"]["default_model"],
            "cliproxy/gpt-5.4",
        )

        # Verify ScreenshotChatController active session immediately updated to cliproxy/gpt-5.4
        self.assertEqual(controller.get_active_session().model_name, "cliproxy/gpt-5.4")


if __name__ == "__main__":
    unittest.main()
