import os
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from unittest import mock
from PIL import Image, ImageDraw

from src.agents.pi_contract import PiRunRequest, PiRuntimeConfig
from src.agents.pi_sdk_runtime import PiSdkRuntime
from src.agents.pi_sdk_transport import PiBridgeExited
from src.core.action_context import ActionContextBuilder, ForegroundWindowInfo
from src.core.dpi_capture import (
    DpiCoordinateEngine,
    MonitorDescriptor,
    VirtualScreenTopology,
    WindowMoveSafeCapturer,
)
from src.core.ocr_pipeline import HybridOcrEngine, RegionAnnotator, ScreenshotComparator
from src.core.privacy_gate import ConsentScope, PrivacyConsentGate, PrivacyPermissionError, TokenBudgetController
from src.core.secret_store import SecretRedactor, SecretStore
from src.core.singleton_guard import CrashRecoveryJournal, SingleInstanceGuard
from src.core.skills_mcp import MCPClientAdapter, SkillRegistry
from src.core.sqlite_history import LegacyHistoryMigrator, SQLiteHistoryStore
from src.gui.screenshot_chat.controller import ScreenshotChatController
from src.gui.screenshot_chat.models import ScreenshotChatAttachment
from src.gui.screenshot_chat.runner import RunnerEvent
from src.gui.screenshot_chat.store import ScreenshotChatStore
from src.llm.service import LLMService
from src.services.snap_modifier_actions import (
    ACTION_TEXT_FILE_PROMPT,
    ACTION_CUSTOM_PROMPT,
    ACTION_CUSTOM_PROMPT_NEW_SESSION,
    ACTION_DEFAULT,
    SnapActionRouter,
)


class NextGenMultimodalE2ETests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="stc_nextgen_test_"))

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_cross_monitor_high_dpi_negative_coords_crop_accuracy(self):
        topology = VirtualScreenTopology.detect(
            custom_monitors=[
                MonitorDescriptor("MON_LEFT_1080P", -1920, 0, 0, 1080, dpi_x=96, dpi_y=96, is_primary=False),
                MonitorDescriptor("MON_MAIN_4K_150", 0, 0, 3840, 2160, dpi_x=144, dpi_y=144, is_primary=True),
            ]
        )
        self.assertEqual(topology.virtual_left, -1920)
        self.assertEqual(topology.virtual_width, 5760)
        self.assertEqual(topology.virtual_height, 2160)

        engine = DpiCoordinateEngine(topology)
        # Region straddling negative left monitor (-500, 100) to primary monitor (300, 600)
        crop_box = engine.virtual_to_image_crop(-500, 100, 300, 600, (5760, 2160))
        self.assertEqual(crop_box, (1420, 100, 2220, 600))

        dom = topology.dominant_monitor_for_rect(-500, 100, 300, 600)
        self.assertEqual(dom.monitor_id, "MON_LEFT_1080P")

    def test_window_move_while_capture_pending_preserves_action_time_pixels(self):
        desktop_t0 = Image.new("RGB", (1920, 1080), color=(10, 20, 30))
        draw = ImageDraw.Draw(desktop_t0)
        draw.rectangle((100, 100, 400, 300), fill=(255, 0, 0))  # Red window at t0

        topology = VirtualScreenTopology.detect(custom_monitors=[
            MonitorDescriptor("TEST_DISPLAY", 0, 0, 1920, 1080, is_primary=True),
        ])
        capturer = WindowMoveSafeCapturer(
            str(self.temp_dir / "snaps"), topology_provider=lambda: topology
        )
        frozen_frame = capturer.capture_region_at_action_time(
            100, 100, 400, 300, timestamp_str="t0_snap", full_screen_image=desktop_t0
        )
        ctx = ActionContextBuilder.capture(
            trigger_source="mouse_snap",
            session_id="sess_alpha",
            model_name="provider/vision-pro",
            prompt_type="prompt_translate",
            virtual_bbox=frozen_frame.virtual_bbox,
            image_paths=[frozen_frame.saved_path],
            foreground_window=ForegroundWindowInfo(hwnd=101, title="CodeEditor - main.py", process_id=4200, rect=(100, 100, 400, 300)),
        )

        # Simulate window moving away and turning blue while user types custom prompt
        draw.rectangle((100, 100, 400, 300), fill=(0, 0, 255))
        desktop_t0.close()

        # Verify frozen frame still has the exact red pixel (255, 0, 0) captured at action time
        captured_pil = frozen_frame.to_pil()
        self.assertEqual(captured_pil.size, (300, 200))
        self.assertEqual(captured_pil.getpixel((50, 50)), (255, 0, 0))
        captured_pil.close()
        self.assertEqual(ctx.foreground_window.title, "CodeEditor - main.py")
        self.assertEqual(ctx.request_model_name, "provider/vision-pro")

    def test_snap_modifier_router_supports_alt_compare_ctrl_and_ctrl_shift(self):
        router = SnapActionRouter()
        self.assertEqual(router.resolve({}).name, ACTION_DEFAULT)
        self.assertEqual(router.resolve({"shift": True}).name, ACTION_DEFAULT)
        self.assertEqual(router.resolve({"ctrl": True}).name, ACTION_CUSTOM_PROMPT)
        self.assertEqual(router.resolve({"ctrl": True, "shift": True}).name, ACTION_CUSTOM_PROMPT_NEW_SESSION)
        self.assertEqual(router.resolve({"alt": True}).name, ACTION_TEXT_FILE_PROMPT)

    def test_simultaneous_tasks_in_two_sessions_have_zero_crosstalk(self):
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

        # Session 1 starts a job
        s1 = controller.get_active_session()
        controller.send_message("Question in Session 1", "provider/main", "prompt_translate")
        job_s1 = s1_reloaded_job = controller.get_active_session().pending_job_ids[0]

        # Switch to Session 2 and start a concurrent job with a different model
        controller.create_session()
        s2 = controller.get_active_session()
        self.assertNotEqual(s1.id, s2.id)
        controller.send_message("Question in Session 2", "provider/critic", "prompt_summarize")
        job_s2 = controller.get_active_session().pending_job_ids[0]

        # Interleave streaming deltas for both sessions
        controller.event_queue.put(RunnerEvent("message_delta", s1.id, job_s1, "main", text="S1-PartA "))
        controller.event_queue.put(RunnerEvent("message_delta", s2.id, job_s2, "main", text="S2-PartA "))
        controller.event_queue.put(RunnerEvent("message_delta", s1.id, job_s1, "main", text="S1-PartB"))
        controller.event_queue.put(RunnerEvent("message_final", s1.id, job_s1, "main", text="S1-PartA S1-PartB"))
        controller.event_queue.put(RunnerEvent("job_finished", s1.id, job_s1, "main"))
        controller.event_queue.put(RunnerEvent("message_final", s2.id, job_s2, "main", text="S2-PartA Final"))
        controller.event_queue.put(RunnerEvent("job_finished", s2.id, job_s2, "main"))
        controller.drain_runner_events()

        final_s1 = store.get_session(s1.id)
        final_s2 = store.get_session(s2.id)
        self.assertEqual(final_s1.messages[-1].text, "S1-PartA S1-PartB")
        self.assertEqual(final_s2.messages[-1].text, "S2-PartA Final")
        self.assertEqual(final_s1.model_name, "provider/main")
        self.assertEqual(final_s2.model_name, "provider/critic")

    def test_pi_bridge_crash_before_vs_after_side_effect_never_replays_mutations(self):
        events = []

        class CrashBeforeSideEffectTransport:
            max_image_bytes = 20 * 1024 * 1024
            max_run_frame_bytes = 28 * 1024 * 1024

            def request(self, command_type, payload, **_kwargs):
                raise PiBridgeExited("bridge exited unexpectedly (code 1)")

        cfg = PiRuntimeConfig(fallback_to_llm=True, crash_fallback_to_llm=True, tool_policy="approve_mutations")
        rt_before = PiSdkRuntime(cfg, (lambda *a, **kw: events.append((a, kw))), CrashBeforeSideEffectTransport())
        req = PiRunRequest(job_id="job_pre", session_id="s1", responder_name="pi", message_id="m1", prompt_text="read file")
        res_before = rt_before.run(req, threading.Event())
        self.assertFalse(res_before.side_effect_started)
        self.assertTrue(res_before.fallback_to_llm)

        class CrashAfterSideEffectTransport:
            max_image_bytes = 20 * 1024 * 1024
            max_run_frame_bytes = 28 * 1024 * 1024

            def request(self, command_type, payload, *, on_event=None, **_kwargs):
                if on_event:
                    on_event({"kind": "runtime_notice", "name": "side_effect_started", "sideEffectStarted": True, "toolName": "write"})
                raise PiBridgeExited("bridge crashed mid-write")

        rt_after = PiSdkRuntime(cfg, (lambda *a, **kw: events.append((a, kw))), CrashAfterSideEffectTransport())
        req_after = PiRunRequest(job_id="job_post", session_id="s1", responder_name="pi", message_id="m2", prompt_text="write file")
        res_after = rt_after.run(req_after, threading.Event())
        self.assertTrue(res_after.side_effect_started)
        self.assertFalse(res_after.fallback_to_llm)
        self.assertIn("ambiguous", res_after.error.lower())

    def test_zero_token_readiness_and_model_failover_preserves_attribution(self):
        providers = {
            "primary_prov": {
                "api": "openai-completions",
                "baseUrl": "https://primary.example.test/v1",
                "apiKey": "sk-" + "primary-valid-key-123456",
                "models": ["model-primary"],
            },
            "backup_prov": {
                "api": "openai-completions",
                "baseUrl": "https://backup.example.test/v1",
                "apiKey": "sk-" + "backup-valid-key-123456",
                "models": ["model-backup"],
            },
        }
        cfg = mock.MagicMock()
        cfg.get_provider_config.side_effect = lambda name: providers.get(name, {})
        cfg.get.side_effect = lambda key, default=None: {
            "agents.defaults.model.primary": "primary_prov/model-primary",
            "agents.defaults.model.fallbacks": ["backup_prov/model-backup"],
            "privacy.allow_cloud_upload": True,
        }.get(key, default)

        class FailThenSucceedProvider:
            def __init__(self, **kwargs):
                self.provider_name = kwargs.get("provider_name")

            def send_text_message(self, prompt, *, callback=None, model_name=None, **_kwargs):
                if self.provider_name == "primary_prov":
                    callback({"text": "Error: 503 Service Unavailable"})
                else:
                    callback({"text": f"Answered by {self.provider_name}/{model_name}"})

            def supports(self, _f):
                return True

        with mock.patch("src.llm.service.OpenAICompatibleProvider", FailThenSucceedProvider):
            service = LLMService(config_manager=cfg)
            readiness = service.check_readiness("primary_prov/model-primary")
            self.assertTrue(readiness["ready"])
            self.assertEqual(readiness["tokens_consumed"], 0)
            self.assertEqual(service.budget_controller.summary()["total_tokens"], 0)

            results = []
            service.send_text_message(
                "Explain DPI scaling",
                callback=results.append,
                model_name="primary_prov/model-primary",
                enable_failover=True,
            )
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["text"], "Answered by backup_prov/model-backup")
            self.assertEqual(results[0]["model_used"], "backup_prov/model-backup")
            self.assertEqual(results[0]["fallback_from"], "primary_prov/model-primary")
            self.assertGreater(service.budget_controller.summary()["total_tokens"], 0)

    def test_secret_store_and_redactor_scrub_all_credential_patterns(self):
        store = SecretStore(self.temp_dir / "secrets.dpapi.json")
        store.set_secret("ROUTE9_API_KEY", "sk-" + "live-secret-token-9988776655", persist_dpapi=False)
        resolved = store.resolve_config_secret("<ROUTE9_API_KEY>", provider_name="route9")
        self.assertEqual(resolved, "sk-" + "live-secret-token-9988776655")

        dirty_log = (
            "Authorization: Bearer " + "sk-" + "abcdef1234567890XYZ "
            "key=" + "AIza" + "SyD-9876543210abcdefGHIJKL "
            "client_" + "secret=" + "GOCSPX-" + "1234567890abcdef "
            "refresh" + "Token=" + "1//0e4PAOkaKgQqyCgYIARAAGA4SNwF-L9IrRJg"
        )
        clean_log = SecretRedactor.redact(dirty_log)
        self.assertNotIn("sk-abcdef", clean_log)
        self.assertNotIn("AIzaSyD", clean_log)
        self.assertNotIn("GOCSPX-", clean_log)
        self.assertNotIn("1//0e4PAO", clean_log)

    def test_privacy_consent_gate_blocks_cloud_upload_in_offline_mode(self):
        gate = PrivacyConsentGate(offline_mode=True)
        self.assertTrue(gate.check_permission(ConsentScope.SCREEN_CAPTURE))
        self.assertFalse(gate.check_permission(ConsentScope.CLOUD_UPLOAD, provider="openai"))
        with self.assertRaises(PrivacyPermissionError):
            gate.check_permission(ConsentScope.CLOUD_UPLOAD, provider="openai", raise_on_deny=True)

    def test_sqlite_fts5_over_1000_events_and_legacy_migration_rollback(self):
        history_dir = self.temp_dir / "history-xml"
        history_dir.mkdir(parents=True, exist_ok=True)
        pi_sess_dir = history_dir / "pi-sdk-sessions"
        pi_sess_dir.mkdir(parents=True, exist_ok=True)
        (pi_sess_dir / "bindings-v1.json").write_text('{"version":1,"bindings":{}}', encoding="utf-8")

        # Seed legacy snap_history.json
        (history_dir / "snap_history.json").write_text(
            '{"history":[{"id":"legacy_1","ans":"Legacy OCR invoice 2026","timestamp":"20261021_120000","model_name":"gemini","prompt_name":"Trans","image_path":""}],"pinned":[]}',
            encoding="utf-8",
        )

        sqlite_store = SQLiteHistoryStore(history_dir / "stc_history_v2.sqlite3")
        migrator = LegacyHistoryMigrator(history_dir, sqlite_store=sqlite_store)
        mig_status = migrator.migrate()
        self.assertEqual(mig_status["migrated_snaps"], 1)

        # Insert 1,050 additional events and verify FTS5 search speed & accuracy
        batch = [
            {
                "entry_id": f"e_{i:04d}",
                "session_id": "sess_scale",
                "timestamp_str": f"20261021_{i:06d}",
                "prompt_name": "Trans",
                "model_name": "aistudio_api/gemini-2.5-flash",
                "answer_text": f"Entry {i}: QuantumInvoiceToken_{i} processed cleanly",
            }
            for i in range(1050)
        ]
        sqlite_store.record_batch(batch)
        self.assertGreaterEqual(sqlite_store.count(), 1051)

        hits = sqlite_store.search("QuantumInvoiceToken_999")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["entry_id"], "e_0999")

        # Verify rollback restores legacy files and preserves pi-sdk-sessions
        rb_status = migrator.rollback()
        self.assertEqual(rb_status["status"], "rolled_back")
        self.assertTrue(rb_status["pi_sessions_preserved"])
        self.assertTrue((pi_sess_dir / "bindings-v1.json").is_file())
        sqlite_store.close()

    def test_complete_e2e_screenshot_prompt_ocr_stream_floating_ui_history_and_recall(self):
        # Step 1: Capture screenshot region at user action time
        desktop = Image.new("RGB", (1280, 720), color=(250, 250, 250))
        draw = ImageDraw.Draw(desktop)
        draw.rectangle((60, 60, 560, 260), outline=(20, 20, 20), width=3)
        draw.line((80, 100, 480, 100), fill=(10, 10, 10), width=5)
        draw.line((80, 140, 420, 140), fill=(10, 10, 10), width=5)

        topology = VirtualScreenTopology.detect(custom_monitors=[
            MonitorDescriptor("TEST_DISPLAY", 0, 0, 1280, 720, is_primary=True),
        ])
        capturer = WindowMoveSafeCapturer(
            str(self.temp_dir / "screenshots"), topology_provider=lambda: topology
        )
        frame_a = capturer.capture_region_at_action_time(
            50, 50, 600, 300, timestamp_str="20261021_230001", full_screen_image=desktop
        )
        desktop.close()
        self.assertTrue(os.path.isfile(frame_a.saved_path))
        with frame_a.to_pil() as captured:
            self.assertEqual(captured.size, (550, 250))
            self.assertEqual(captured.getpixel((30, 50)), (10, 10, 10))

        # Step 2: Run Hybrid Offline OCR & Local Skill Registry
        skills = SkillRegistry()
        ocr_out = skills.execute_local("ocr_extract", {"images": [frame_a.saved_path], "mode": "offline"})
        self.assertTrue(ocr_out["ok"])
        self.assertIn("Offline Structural OCR", ocr_out["result"]["text"])

        # Step 3: Route captured screenshot into Chatbot Session -> Stream AI response -> Persist -> Recall
        from tests.gui.screenshot_chat.test_controller import FakeConfig, FakeRunner

        store = ScreenshotChatStore(str(self.temp_dir / "chat_sessions.json"))
        runner = FakeRunner()
        controller = ScreenshotChatController(
            root=None,
            config_manager=FakeConfig(),
            project_root=str(self.temp_dir),
            held_images_getter=lambda: [frame_a.saved_path],
            runner=runner,
            store=store,
        )
        imported = controller.import_held_snaps()
        self.assertEqual(imported, 1)
        accepted = controller.send_message("Dịch và giải thích ảnh chụp này", "provider/main", "prompt_translate")
        self.assertTrue(accepted)

        session = controller.get_active_session()
        job_id = session.pending_job_ids[0]
        controller.event_queue.put(RunnerEvent("message_delta", session.id, job_id, "main", text="Bản dịch: "))
        controller.event_queue.put(RunnerEvent("message_delta", session.id, job_id, "main", text="Báo cáo doanh thu Q4."))
        controller.event_queue.put(
            RunnerEvent("message_final", session.id, job_id, "main", text="Bản dịch: Báo cáo doanh thu Q4.")
        )
        controller.event_queue.put(RunnerEvent("job_finished", session.id, job_id, "main"))
        controller.drain_runner_events()

        # Step 4: Index in SQLite FTS5 & Recall Later
        sql_store = SQLiteHistoryStore(self.temp_dir / "stc_history_v2.sqlite3")
        entry_id = sql_store.record_event(
            session_id=session.id,
            timestamp_str="20261021_230001",
            prompt_name="Trans",
            model_name="provider/main",
            image_path=frame_a.saved_path,
            prompt_text="Dịch và giải thích ảnh chụp này",
            answer_text="Bản dịch: Báo cáo doanh thu Q4.",
            is_pinned=True,
            pin_title="Doanh thu Q4",
        )
        thumb = sql_store.get_or_create_thumbnail(entry_id)
        self.assertTrue(os.path.isfile(thumb))

        recalled = sql_store.search("doanh thu Q4")
        self.assertEqual(len(recalled), 1)
        self.assertEqual(recalled[0]["answer_text"], "Bản dịch: Báo cáo doanh thu Q4.")
        sql_store.close()


if __name__ == "__main__":
    unittest.main()
