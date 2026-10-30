import os
import queue
import shutil
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

if "pystray" not in sys.modules:
    sys.modules["pystray"] = types.SimpleNamespace(
        Icon=object,
        Menu=lambda *args: args,
        MenuItem=lambda *args, **kwargs: (args, kwargs),
    )
if "pynput" not in sys.modules:
    mouse_stub = types.SimpleNamespace(Controller=object, Listener=object, Button=types.SimpleNamespace(left="left", right="right"))
    sys.modules["pynput"] = types.SimpleNamespace(mouse=mouse_stub)
    sys.modules["pynput.mouse"] = mouse_stub
if "keyboard" not in sys.modules:
    sys.modules["keyboard"] = types.SimpleNamespace()
if "soundcard" not in sys.modules:
    sys.modules["soundcard"] = types.SimpleNamespace()
if "soundfile" not in sys.modules:
    try:
        import soundfile  # noqa: F401
    except ImportError:
        sys.modules["soundfile"] = types.SimpleNamespace(write=lambda filepath, _audio, _sr, **_kwargs: open(filepath, "wb").write(b"audio"))

import main

from PIL import Image

from src.agents.pi_contract import PiAvailability
from src.gui.screenshot_chat.controller import (
    PI_MODEL_ID_PREFIX,
    ScreenshotChatController,
    sanitize_speech_text,
)
from src.gui.screenshot_chat.models import ScreenshotChatMessage
from src.gui.screenshot_chat.runner import RunnerEvent
from src.gui.screenshot_chat.store import ScreenshotChatStore


class FakeConfig:
    def __init__(self):
        self.config = {
            "enabled": True,
            "history_path": "history-xml/screenshot_chat_sessions.json",
            "default_model": "provider/main",
            "default_prompt_type": "prompt_translate",
            "temperature": 0.7,
            "runtime": {
                "mode": "tk",
                "legacy_webview_enabled": False,
                "host": "127.0.0.1",
                "port": 0,
                "startup_timeout_ms": 3000,
                "poll_interval_ms": 900,
                "fallback_tk_debug": False,
            },
            "pi": {"enabled": False, "show_unavailable_in_selector": True},
            "agents": {
                "critic": {
                    "enabled": True,
                    "model": "provider/critic",
                    "system_prompt": "Review.",
                }
            },
        }

    def reload_if_changed(self):
        return self.config

    def get_screenshot_chat_config(self):
        return dict(self.config)

    def get_all_models(self):
        return {"provider/main": {}, "provider/critic": {}}

    def get(self, key, default=None):
        if key == "agents.defaults.model.primary":
            return ""
        return default


class FakeRunner:
    def __init__(self):
        self.event_queue = queue.Queue()
        self.started = []
        self.started_responders = []
        self.canceled = []
        self.closed_pi_sessions = []

    def prepare_response_jobs(self, session, user_message, responders):
        from src.gui.screenshot_chat.models import ScreenshotChatJob

        return [
            ScreenshotChatJob(
                id=f"job_{responder.name}",
                session_id=session.id,
                message_id=user_message.id,
                responder_name=responder.name,
            )
            for responder in responders
        ]

    def start_prepared_jobs(self, session, user_message, prompt_text, responders, jobs, temperature=0.7):
        self.started.append(
            (session.id, prompt_text, [item.name for item in responders], [item.id for item in jobs])
        )
        self.started_responders.append(list(responders))

    def cancel_jobs(self, job_ids):
        self.canceled.extend(job_ids)

    def close_pi_session(self, session_id, responder_names, timeout_ms=3000):
        self.closed_pi_sessions.append((session_id, tuple(responder_names), timeout_ms))


class FakeWindow:
    def __init__(self):
        self.visible = False
        self.refreshed = 0
        self.message_refreshed = 0
        self.status = ""
        self.show_count = 0

    def winfo_exists(self):
        return True

    def is_visible(self):
        return self.visible

    def hide(self):
        self.visible = False

    def show(self):
        self.show_count += 1
        self.visible = True

    def refresh_all(self):
        self.refreshed += 1

    def refresh_messages(self):
        self.message_refreshed += 1

    def prewarm_hidden(self):
        self.visible = False

    def update_status(self, status):
        self.status = status



class FailingPrewarmWindow(FakeWindow):
    def prewarm_hidden(self):
        raise RuntimeError("pythonnet ABI unsupported")

    def shutdown(self):
        self.visible = False

class ScreenshotChatControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.store = ScreenshotChatStore(os.path.join(self.temp_dir, "sessions.json"))
        self.held = []
        self.consumed = []
        self.runner = FakeRunner()
        self.controller = ScreenshotChatController(
            root=None,
            config_manager=FakeConfig(),
            project_root=self.temp_dir,
            held_images_getter=lambda: list(self.held),
            held_images_consumer=lambda paths: self.consumed.extend(paths),
            snap_history_getter=lambda: None,
            runner=self.runner,
            store=self.store,
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_delete_session_releases_pi_runtime_state_for_known_responder_keys(self):
        self.controller.config_manager.config["pi"]["agent_names"] = ["pi", "reviewer"]
        session = self.controller.get_active_session()
        session.model_name = "pi://agent/pi"
        self.store.save_session(session)

        self.assertTrue(self.controller.delete_session(session.id))

        self.assertEqual(len(self.runner.closed_pi_sessions), 1)
        closed_id, responder_names, timeout_ms = self.runner.closed_pi_sessions[0]
        self.assertEqual(closed_id, session.id)
        self.assertIn("Pi Agent", responder_names)
        self.assertIn("pi", responder_names)
        self.assertIn("reviewer", responder_names)
        self.assertEqual(timeout_ms, 3000)

    def test_store_eviction_releases_pi_runtime_state(self):
        self.controller.config_manager.config["pi"]["agent_names"] = ["pi"]
        self.store.MAX_SESSIONS = 2
        oldest = self.controller.get_active_session()
        oldest.model_name = "pi://agent/pi"
        oldest.updated_at = 1
        self.store.save_session(oldest)
        self.store.create_session(model_name="provider/main", title="two")
        self.store.create_session(model_name="provider/main", title="three")

        self.assertTrue(any(item[0] == oldest.id for item in self.runner.closed_pi_sessions))

    def test_restart_recovers_interrupted_jobs_without_replaying_queued_messages(self):
        self.controller.send_message("first", "provider/main", "prompt_translate")
        original = self.controller.get_active_session()
        self.store.append_message_delta(original.id, "job_main", "Partial answer")
        self.controller.send_message("queued before restart", "provider/main", "prompt_translate")

        runner = FakeRunner()
        restored = ScreenshotChatController(
            root=None,
            config_manager=FakeConfig(),
            project_root=self.temp_dir,
            runner=runner,
            store=ScreenshotChatStore(self.store.save_path),
        )
        recovered = restored.get_active_session()

        self.assertEqual(recovered.id, original.id)
        self.assertEqual(recovered.pending_job_ids, [])
        self.assertEqual([item.status for item in recovered.messages], ["done", "canceled", "canceled"])
        self.assertEqual(recovered.messages[1].text, "Partial answer")
        self.assertEqual(recovered.messages[2].text, "queued before restart")
        self.assertEqual(runner.started, [])
        restored.send_message("after restart", "provider/main", "prompt_translate")
        self.assertEqual(len(runner.started), 1)
        self.assertEqual(runner.started[0][1], "after restart")

    def test_restart_recovers_inactive_placeholders_and_orphaned_pending_ids(self):
        self.controller.send_message("first", "provider/main", "prompt_translate")
        inactive = self.controller.get_active_session()
        self.controller.create_session()
        active = self.controller.get_active_session()
        self.store.set_pending_job(active.id, "missing_placeholder", True)

        restored = ScreenshotChatController(
            root=None,
            config_manager=FakeConfig(),
            project_root=self.temp_dir,
            runner=FakeRunner(),
            store=ScreenshotChatStore(self.store.save_path),
        )

        self.assertEqual(restored.get_active_session().id, active.id)
        self.assertTrue(all(not session.pending_job_ids for session in restored.list_sessions()))
        recovered = restored.store.get_session(inactive.id)
        self.assertEqual(recovered.messages[-1].status, "canceled")
        self.assertIn("Interrupted", recovered.messages[-1].text)
        self.assertEqual(recovered.messages[-1].id, inactive.messages[-1].id)
        persisted = ScreenshotChatStore(self.store.save_path).get_session(inactive.id)
        self.assertEqual(persisted.to_dict(), recovered.to_dict())

    def test_send_message_adds_placeholders_before_starting_jobs(self):
        self.controller.send_message("@main @critic explain", "provider/main", "prompt_translate")
        session = self.controller.get_active_session()

        self.assertEqual(self.runner.started[0][1], "explain")
        self.assertEqual(self.runner.started[0][2], ["main", "critic"])
        self.assertEqual([message.status for message in session.messages], ["done", "thinking", "thinking"])
        self.assertEqual(session.pending_job_ids, ["job_main", "job_critic"])

    def test_rejected_pi_send_preserves_pending_attachments(self):
        from src.gui.screenshot_chat.models import ScreenshotChatAttachment

        self.controller.config_manager.config["pi"]["auto_detect"] = False
        attachment = ScreenshotChatAttachment(path="screen.png", label="screen.png")
        self.controller.pending_attachments = [attachment]

        accepted = self.controller.send_message("inspect", "pi://agent/pi", "prompt_translate")

        self.assertIs(accepted, False)
        self.assertEqual(self.controller.pending_attachments, [attachment])
        self.assertEqual(self.controller.get_active_session().messages, [])
        self.assertEqual(self.runner.started, [])

    def test_accepted_sends_report_success_and_consume_attachments(self):
        from src.gui.screenshot_chat.models import ScreenshotChatAttachment

        for mode, expected_status in (("chat", "done"), ("note", "note")):
            with self.subTest(mode=mode):
                self.controller.set_session_mode(mode)
                attachment = ScreenshotChatAttachment(path=f"{mode}.png")
                self.controller.pending_attachments = [attachment]

                accepted = self.controller.send_message("inspect", "provider/main", "prompt_translate")

                self.assertIs(accepted, True)
                self.assertEqual(self.controller.pending_attachments, [])
                user_message = next(
                    message for message in reversed(self.controller.get_active_session().messages)
                    if message.role == "user"
                )
                self.assertEqual(user_message.status, expected_status)
                self.assertEqual(user_message.attachments[0].id, attachment.id)

    def test_queued_send_reports_success(self):
        self.controller.send_message("first", "provider/main", "prompt_translate")

        accepted = self.controller.send_message("second", "provider/main", "prompt_translate")

        self.assertIs(accepted, True)
        self.assertEqual(self.controller.get_active_session().messages[-1].status, "queued")

    def test_empty_send_reports_rejection(self):
        self.assertIs(self.controller.send_message(" ", "provider/main", "prompt_translate"), False)

    def test_zero_temperature_is_passed_to_runner(self):
        self.controller.config_manager.config["temperature"] = 0.0
        with mock.patch.object(self.runner, "start_prepared_jobs", wraps=self.runner.start_prepared_jobs) as start:
            self.controller.send_message("deterministic", "provider/main", "prompt_translate")

        self.assertEqual(start.call_args.kwargs["temperature"], 0.0)

    def test_runner_event_updates_original_session_when_inactive(self):
        self.controller.send_message("first", "provider/main", "prompt_translate")
        first_session = self.controller.get_active_session()
        self.controller.create_session()

        self.controller.event_queue.put(
            RunnerEvent(
                type="message_final",
                session_id=first_session.id,
                job_id="job_main",
                responder_name="main",
                message_id="m1",
                text="answer",
            )
        )
        self.controller.event_queue.put(
            RunnerEvent(
                type="job_finished",
                session_id=first_session.id,
                job_id="job_main",
                responder_name="main",
            )
        )
        self.controller.drain_runner_events()

        reloaded = self.store.get_session(first_session.id)
        self.assertEqual(reloaded.messages[-1].text, "answer")
        self.assertEqual(reloaded.pending_job_ids, [])
        self.assertNotEqual(self.controller.get_active_session().id, first_session.id)

    def test_inactive_session_queue_continues_after_switching_sessions(self):
        self.controller.send_message("first", "provider/main", "prompt_translate")
        first_session = self.controller.get_active_session()
        self.controller.send_message("second", "provider/main", "prompt_translate")
        self.controller.create_session()
        second_session = self.controller.get_active_session()

        self.controller.event_queue.put(
            RunnerEvent(
                type="message_final",
                session_id=first_session.id,
                job_id="job_main",
                responder_name="main",
                message_id="m1",
                text="answer",
            )
        )
        self.controller.event_queue.put(
            RunnerEvent(
                type="job_finished",
                session_id=first_session.id,
                job_id="job_main",
                responder_name="main",
            )
        )
        self.controller.drain_runner_events()

        reloaded = self.store.get_session(first_session.id)
        self.assertEqual(self.runner.started[-1][0], first_session.id)
        self.assertEqual(self.runner.started[-1][1], "second")
        self.assertEqual(reloaded.messages[-1].status, "thinking")
        self.assertEqual(self.controller.get_active_session().id, second_session.id)

    def test_import_held_snaps_is_manual_and_consumes_only_existing_paths(self):
        missing = os.path.join(self.temp_dir, "missing.png")
        existing = os.path.join(self.temp_dir, "snap.png")
        with open(existing, "wb") as handle:
            handle.write(b"png")
        self.held.extend([missing, existing])

        imported = self.controller.import_held_snaps()

        self.assertEqual(imported, 1)
        self.assertEqual(self.consumed, [existing])
        self.assertEqual(self.controller.pending_attachments[0].path, existing)

    def test_stop_active_session_jobs_marks_placeholders_canceled(self):
        self.controller.send_message("first", "provider/main", "prompt_translate")

        self.controller.stop_active_session_jobs()
        session = self.controller.get_active_session()

        self.assertEqual(self.runner.canceled, ["job_main"])
        self.assertEqual(session.messages[-1].status, "canceled")

    def test_send_message_queues_while_session_has_pending_job(self):
        self.controller.send_message("first", "provider/main", "prompt_translate")

        self.controller.send_message("second", "provider/main", "prompt_translate")
        session = self.controller.get_active_session()

        self.assertEqual(len(self.runner.started), 1)
        self.assertEqual(session.messages[-1].text, "second")
        self.assertEqual(session.messages[-1].status, "queued")

    def test_stop_active_session_jobs_starts_next_queued_message(self):
        self.controller.send_message("first", "provider/main", "prompt_translate")
        self.controller.send_message("second", "provider/main", "prompt_translate")

        self.controller.stop_active_session_jobs()
        session = self.controller.get_active_session()

        self.assertEqual(self.runner.canceled, ["job_main"])
        self.assertEqual(len(self.runner.started), 2)
        self.assertEqual(self.runner.started[-1][1], "second")
        self.assertEqual([message.status for message in session.messages], ["canceled", "canceled", "done", "thinking"])

    def test_cancel_queued_message_marks_message_canceled(self):
        self.controller.send_message("first", "provider/main", "prompt_translate")
        self.controller.send_message("second", "provider/main", "prompt_translate")
        session = self.controller.get_active_session()
        queued_id = session.messages[-1].id

        self.controller.cancel_queued_message(queued_id)
        session = self.controller.get_active_session()

        self.assertEqual(session.messages[-1].status, "canceled")
        self.assertEqual(len(self.runner.started), 1)

    def test_new_session_uses_default_model_when_configured(self):
        session = self.controller.get_active_session()

        self.assertEqual(session.model_name, "provider/main")

    def test_existing_empty_model_session_is_backfilled(self):
        session = self.controller.get_active_session()
        session.model_name = ""
        self.store.save_session(session)

        reloaded = self.controller.get_active_session()

        self.assertEqual(reloaded.model_name, "provider/main")

    def test_available_model_options_include_pi_when_enabled_and_available(self):
        self.controller.config_manager.config["pi"] = {"enabled": True, "agent_names": ["pi"]}
        availability = PiAvailability(available=True, reason="available", executable="pi", package_available=True)
        with mock.patch("src.gui.screenshot_chat.controller.detect_pi_availability", return_value=availability):
            options = self.controller.available_model_options()

        self.assertEqual([option["id"] for option in options], ["provider/main", "provider/critic", "pi://agent/pi"])
        self.assertEqual(options[-1]["label"], "Pi Agent")
        self.assertEqual(options[-1]["runtime"], "pi")

    def test_available_model_options_show_pi_disabled_when_unavailable(self):
        self.controller.config_manager.config["pi"] = {"enabled": True, "agent_names": ["pi"], "show_unavailable_in_selector": True}
        availability = PiAvailability(available=False, reason="missing_executable", executable="", package_available=False)
        with mock.patch("src.gui.screenshot_chat.controller.detect_pi_availability", return_value=availability):
            options = self.controller.available_model_options()

        self.assertEqual([option["id"] for option in options], ["provider/main", "provider/critic", "pi://agent/pi"])
        self.assertFalse(options[-1]["available"])
        self.assertIn("executable", options[-1]["reason"])

    def test_disabled_pi_selection_is_blocked_with_status_reason(self):
        self.controller.config_manager.config["pi"] = {"enabled": False, "auto_detect": False, "agent_names": ["pi"], "show_unavailable_in_selector": True}
        before = self.controller.get_active_session().model_name

        self.controller.set_session_model("pi://agent/pi")

        self.assertEqual(self.controller.get_active_session().model_name, before)
        self.assertIn("Pi Agent unavailable", self.controller.get_status())

    def test_pi_approval_dialog_uses_summary_and_correlated_ids(self):
        session = self.controller.get_active_session()
        event = RunnerEvent(
            type="approval_request",
            session_id=session.id,
            job_id="job-main",
            responder_name="Pi Agent",
            metadata={
                "kind": "approval_request",
                "approval_id": "appr-1",
                "tool_name": "write",
                "summary": "Write safe.txt",
                "expires_at": "2026-09-06T00:00:00.000Z",
                "ui_type": "tool",
                "payload": {},
            },
        )
        with mock.patch("src.gui.screenshot_chat.controller.messagebox.askyesno", return_value=True) as dialog:
            with mock.patch.object(self.runner, "resolve_approval", return_value=True, create=True) as resolve:
                self.controller._show_pi_approval_dialog(event)

        title, detail = dialog.call_args.args[:2]
        self.assertIn("write", title.lower())
        self.assertIn("Write safe.txt", detail)
        self.assertIn("2026-09-06T00:00:00.000Z", detail)
        resolve.assert_called_once_with("job-main", "appr-1", True, "approved_by_user", {})

    def test_pi_selector_status_reports_disabled_reason(self):
        self.controller.config_manager.config["pi"] = {"enabled": False, "auto_detect": False, "executable": ""}

        status = self.controller.pi_selector_status()

        self.assertFalse(status["enabled"])
        self.assertFalse(status["available"])
        self.assertEqual(status["reason"], "disabled_by_user")

    def test_create_session_from_pi_active_uses_normal_default_model(self):
        session = self.controller.get_active_session()
        session.model_name = f"{PI_MODEL_ID_PREFIX}pi"
        self.store.save_session(session)
        self.store.append_message(session.id, ScreenshotChatMessage(role="user", author="you", text="first"))

        self.controller.create_session()
        created = self.controller.get_active_session()

        self.assertEqual(created.model_name, "provider/main")

    def test_selected_pi_model_routes_main_responder_to_pi_with_normal_fallback(self):
        self.controller.config_manager.config["pi"] = {"enabled": True, "agent_names": ["pi"]}

        self.controller.send_message("inspect", "pi://agent/pi", "prompt_translate")

        responder = self.runner.started_responders[0][0]
        self.assertEqual(responder.name, "Pi Agent")
        self.assertEqual(responder.runtime, "pi")
        self.assertEqual(responder.model_name, "provider/main")
        self.assertEqual(responder.metadata["provider"], "pi")
        self.assertEqual(responder.metadata["agent_name"], "pi")
        self.assertEqual(responder.metadata["pi_model_id"], "pi://agent/pi")
        self.assertEqual(responder.metadata["fallback_model_name"], "provider/main")

    def test_note_mode_saves_pi_selection_without_starting_runner_job(self):
        self.controller.config_manager.config["pi"] = {"enabled": True, "agent_names": ["pi"], "show_unavailable_in_selector": True}
        availability = PiAvailability(available=True, reason="available", executable="pi", package_available=True)
        with mock.patch("src.gui.screenshot_chat.controller.detect_pi_availability", return_value=availability):
            self.controller.set_session_model("pi://agent/pi")
            self.controller.set_session_mode("note")

            self.controller.send_message("remember", "pi://agent/pi", "prompt_translate")
        session = self.controller.get_active_session()

        self.assertEqual(len(self.runner.started), 0)
        self.assertEqual(session.model_name, "pi://agent/pi")
        self.assertEqual(session.messages[-1].status, "note")

    def test_pi_mention_routes_to_synthetic_pi_agent_when_enabled(self):
        self.controller.config_manager.config["pi"] = {
            "enabled": True,
            "agent_names": ["pi"],
            "fallback_to_llm": True,
        }

        self.controller.send_message("@pi inspect", "provider/main", "prompt_translate")

        self.assertEqual(self.runner.started[0][1], "inspect")
        self.assertEqual(self.runner.started[0][2], ["pi"])
        responder = self.runner.started_responders[0][0]
        self.assertEqual(responder.runtime, "pi")
        self.assertTrue(responder.metadata["synthetic_pi_agent"])
        self.assertTrue(responder.metadata["pi_config"]["enabled"])

    def test_pi_mention_is_note_text_in_note_mode(self):
        self.controller.config_manager.config["pi"] = {"enabled": True, "agent_names": ["pi"]}
        self.controller.set_session_mode("note")

        self.controller.send_message("@pi remember", "provider/main", "prompt_translate")
        session = self.controller.get_active_session()

        self.assertEqual(len(self.runner.started), 0)
        self.assertEqual(session.messages[-1].text, "@pi remember")
        self.assertEqual(session.messages[-1].status, "note")

    def test_runner_delta_batch_is_coalesced_and_uses_message_only_refresh(self):
        self.controller.send_message("first", "provider/main", "prompt_translate")
        session = self.controller.get_active_session()
        job_id = session.pending_job_ids[0]
        window = FakeWindow()
        self.controller.window = window

        for text in ("a", "b", "c"):
            self.controller.event_queue.put(
                RunnerEvent(
                    type="message_delta",
                    session_id=session.id,
                    job_id=job_id,
                    responder_name="main",
                    text=text,
                )
            )

        with mock.patch.object(self.store, "append_message_delta", wraps=self.store.append_message_delta) as append_delta:
            has_backlog = self.controller.drain_runner_events()

        reloaded = self.controller.get_active_session()
        self.assertFalse(has_backlog)
        self.assertEqual(reloaded.messages[-1].text, "abc")
        append_delta.assert_called_once_with(
            session.id,
            job_id,
            "abc",
            save=False,
            return_session=False,
        )
        self.assertEqual(window.message_refreshed, 1)
        self.assertEqual(window.refreshed, 0)

    def test_runner_pi_events_append_timeline_and_delta(self):
        self.controller.send_message("first", "provider/main", "prompt_translate")
        session = self.controller.get_active_session()
        job_id = session.pending_job_ids[0]

        self.controller.event_queue.put(
            RunnerEvent(
                type="message_delta",
                session_id=session.id,
                job_id=job_id,
                responder_name="main",
                text="partial",
            )
        )
        self.controller.event_queue.put(
            RunnerEvent(
                type="tool_event",
                session_id=session.id,
                job_id=job_id,
                responder_name="main",
                metadata={"kind": "tool_started", "tool_name": "bash", "status": "running"},
            )
        )
        self.controller.drain_runner_events()
        reloaded = self.controller.get_active_session()

        self.assertEqual(reloaded.messages[-1].text, "partial")
        self.assertEqual(reloaded.messages[-1].events[0]["kind"], "tool_started")

    def test_note_mode_saves_without_starting_runner_job(self):
        self.controller.set_session_mode("note")

        self.controller.send_message("remember this", "provider/critic", "prompt_translate")
        session = self.controller.get_active_session()

        self.assertEqual(len(self.runner.started), 0)
        self.assertEqual(session.messages[-1].author, "note")
        self.assertEqual(session.messages[-1].status, "note")
        self.assertEqual(session.model_name, "provider/main")

    def test_delete_message_removes_completed_message(self):
        self.store.append_message(
            self.controller.get_active_session().id,
            ScreenshotChatMessage(id="m_delete", role="user", author="you", text="remove me"),
        )

        self.assertTrue(self.controller.delete_message("m_delete"))
        session = self.controller.get_active_session()

        self.assertNotIn("m_delete", [message.id for message in session.messages])

    def test_delete_running_user_message_cancels_job_and_placeholder(self):
        self.controller.send_message("first", "provider/main", "prompt_translate")
        session = self.controller.get_active_session()
        user_id = session.messages[0].id
        placeholder_id = session.messages[1].id

        self.assertTrue(self.controller.delete_message(user_id))
        session = self.controller.get_active_session()

        self.assertEqual(self.runner.canceled, ["job_main"])
        self.assertNotIn(user_id, [message.id for message in session.messages])
        self.assertNotIn(placeholder_id, [message.id for message in session.messages])
        self.assertEqual(session.pending_job_ids, [])

    def test_runner_final_sanitizes_repeated_main_prefix_before_storage(self):
        self.controller.send_message("first", "provider/main", "prompt_translate")
        session = self.controller.get_active_session()
        job_id = session.pending_job_ids[0]

        self.controller.event_queue.put(
            RunnerEvent(
                type="message_final",
                session_id=session.id,
                job_id=job_id,
                responder_name="main",
                text="main: main: stored answer",
            )
        )
        self.controller.drain_runner_events()

        reloaded = self.controller.get_active_session()
        self.assertEqual(reloaded.messages[-1].text, "stored answer")

    def test_runner_error_sanitizes_repeated_main_prefix_before_storage(self):
        self.controller.send_message("first", "provider/main", "prompt_translate")
        session = self.controller.get_active_session()
        job_id = session.pending_job_ids[0]

        self.controller.event_queue.put(
            RunnerEvent(
                type="job_error",
                session_id=session.id,
                job_id=job_id,
                responder_name="main",
                error="main: [Error: No response received]",
            )
        )
        self.controller.drain_runner_events()

        reloaded = self.controller.get_active_session()
        self.assertEqual(reloaded.messages[-1].text, "[Error: No response received]")
        self.assertEqual(reloaded.messages[-1].status, "error")
        self.assertEqual(self.controller.get_runtime_status()["state"], "error")

    def test_pi_startup_error_keeps_raw_detail_out_of_primary_message(self):
        self.controller.send_message("first", "provider/main", "prompt_translate")
        session = self.controller.get_active_session()
        job_id = session.pending_job_ids[0]
        raw_error = "Pi protocol error: bridge request hello timed out"

        self.controller.event_queue.put(
            RunnerEvent(
                type="job_error",
                session_id=session.id,
                job_id=job_id,
                responder_name="pi",
                error=raw_error,
            )
        )
        self.controller.drain_runner_events()

        reloaded = self.controller.get_active_session()
        failed = reloaded.messages[-1]
        runtime = self.controller.get_runtime_status()
        self.assertEqual(failed.text, "Pi did not start correctly.")
        self.assertEqual(failed.metadata["detail"], raw_error)
        self.assertEqual(runtime["state"], "error")
        self.assertEqual(runtime["label"], "Action needed")
        self.assertEqual(runtime["detail"], raw_error)

    def test_runtime_status_tracks_streaming_canceling_and_approval(self):
        self.controller.send_message("first", "provider/main", "prompt_translate")
        session = self.controller.get_active_session()
        job_id = session.pending_job_ids[0]
        self.assertEqual(self.controller.get_runtime_status()["state"], "streaming")

        self.controller._handle_runner_event(
            RunnerEvent(
                type="approval_request",
                session_id=session.id,
                job_id=job_id,
                responder_name="pi",
                metadata={"approval_id": "approval-1", "payload": {"title": "Allow write"}},
            )
        )
        self.assertEqual(self.controller.get_runtime_status()["state"], "waiting_approval")

        self.controller.stop_active_session_jobs()
        self.assertEqual(self.controller.get_runtime_status()["state"], "canceling")

    def test_hotkey_entrypoint_toggles_screenshot_chat_module(self):
        app = main.ScreenshotApp.__new__(main.ScreenshotApp)
        module = mock.MagicMock()
        app._ensure_screenshot_chat_module = mock.MagicMock(return_value=module)

        app._open_screenshot_chat_on_main_thread()

        module.run_hotkey_action.assert_called_once_with("toggle")

    def test_open_or_focus_reuses_existing_window_without_duplicate(self):
        window = FakeWindow()
        window.visible = True
        self.controller.window = window
        with mock.patch.object(self.controller, "_create_tk_window") as tk_cls:
            with mock.patch.object(self.controller, "_create_webview_window") as web_cls:

                self.controller.open_or_focus()

        self.assertTrue(window.visible)
        self.assertEqual(window.show_count, 1)
        tk_cls.assert_not_called()
        web_cls.assert_not_called()

    def test_toggle_window_hides_visible_window(self):
        window = FakeWindow()
        window.visible = True
        self.controller.window = window

        self.controller.toggle_window()

        self.assertFalse(window.visible)

    def test_toggle_window_shows_then_hides_same_window(self):
        window = FakeWindow()
        self.controller.window = window
        with mock.patch.object(self.controller, "_create_tk_window") as tk_cls:
            with mock.patch.object(self.controller, "_create_webview_window") as web_cls:

                self.controller.toggle_window()
                self.controller.toggle_window()

        self.assertFalse(window.visible)
        self.assertEqual(window.show_count, 1)
        tk_cls.assert_not_called()
        web_cls.assert_not_called()

    def test_ensure_window_uses_tk_by_default(self):
        with mock.patch.object(self.controller, "_create_tk_window", return_value=FakeWindow()) as tk_cls:
            with mock.patch.object(self.controller, "_create_webview_window") as web_cls:

                opened = self.controller._ensure_window()

        self.assertTrue(opened)
        self.assertIsInstance(self.controller.window, FakeWindow)
        tk_cls.assert_called_once_with()
        web_cls.assert_not_called()

    def test_ensure_window_uses_webview_process_without_legacy_opt_in(self):
        self.controller.config_manager.config["runtime"]["mode"] = "webview-process"
        self.controller.config_manager.config["runtime"]["legacy_webview_enabled"] = False
        with mock.patch.object(self.controller, "_create_webview_window", return_value=FakeWindow()) as web_cls:
            with mock.patch.object(self.controller, "_create_tk_window") as tk_cls:

                opened = self.controller._ensure_window()

        self.assertTrue(opened)
        web_cls.assert_called_once_with()
        tk_cls.assert_not_called()

    def test_webview_startup_projects_connecting_then_ready(self):
        self.controller.config_manager.config["runtime"]["mode"] = "webview-process"
        observed_states = []

        def create_webview():
            observed_states.append(self.controller.get_runtime_status()["state"])
            return FakeWindow()

        with mock.patch.object(self.controller, "_create_webview_window", side_effect=create_webview):
            self.controller._ensure_window()

        self.assertEqual(observed_states, ["connecting"])
        self.assertEqual(self.controller.get_runtime_status()["state"], "ready")

    def test_ensure_window_uses_webview_with_legacy_opt_in(self):
        self.controller.config_manager.config["runtime"]["mode"] = "webview-process"
        self.controller.config_manager.config["runtime"]["legacy_webview_enabled"] = True
        with mock.patch.object(self.controller, "_create_webview_window", return_value=FakeWindow()) as web_cls:
            with mock.patch.object(self.controller, "_create_tk_window") as tk_cls:

                opened = self.controller._ensure_window()

        self.assertTrue(opened)
        self.assertIsInstance(self.controller.window, FakeWindow)
        web_cls.assert_called_once_with()
        tk_cls.assert_not_called()

    def test_ensure_window_falls_back_to_tk_when_legacy_webview_construction_fails(self):
        self.controller.config_manager.config["runtime"]["mode"] = "webview-process"
        self.controller.config_manager.config["runtime"]["legacy_webview_enabled"] = True
        with mock.patch.object(self.controller, "_create_webview_window", side_effect=RuntimeError("webview failed")):
            with mock.patch.object(self.controller, "_create_tk_window", return_value=FakeWindow()) as tk_cls:

                opened = self.controller._ensure_window()

        self.assertTrue(opened)
        self.assertIsInstance(self.controller.window, FakeWindow)
        self.assertIn("WebView construction failed", self.controller.get_status())
        tk_cls.assert_called_once_with()

    def test_ensure_window_falls_back_to_tk_when_legacy_webview_startup_fails(self):
        self.controller.config_manager.config["runtime"]["mode"] = "webview-process"
        self.controller.config_manager.config["runtime"]["legacy_webview_enabled"] = True
        with mock.patch.object(self.controller, "_create_webview_window", return_value=FailingPrewarmWindow()):
            with mock.patch.object(self.controller, "_create_tk_window", return_value=FakeWindow()) as tk_cls:

                opened = self.controller._ensure_window()

        self.assertTrue(opened)
        self.assertIsInstance(self.controller.window, FakeWindow)
        self.assertIn("WebView startup failed", self.controller.get_status())
        self.assertIn("pythonnet ABI unsupported", self.controller.get_status())
        tk_cls.assert_called_once_with()

    def test_display_sessions_filters_empty_new_chats(self):
        self.controller.create_session()
        active = self.controller.get_active_session()
        self.store.append_message(active.id, ScreenshotChatMessage(role="user", author="you", text="hi"))

        sessions = self.controller.list_display_sessions()

        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].id, active.id)

    def test_import_pinned_snap_inserts_main_message(self):
        image_path = os.path.join(self.temp_dir, "pinned.png")
        with open(image_path, "wb") as handle:
            handle.write(b"png")

        inserted = self.controller.import_pinned_snap(
            {
                "image_path": image_path,
                "ans": "Pinned answer",
                "display_title": "Pinned title",
            }
        )
        session = self.controller.get_active_session()

        self.assertTrue(inserted)
        self.assertEqual(session.messages[-1].role, "assistant")
        self.assertEqual(session.messages[-1].author, "main")
        self.assertEqual(session.messages[-1].text, "Pinned answer")
        self.assertEqual(session.messages[-1].attachments[0].path, image_path)

    def test_import_pinned_snap_blocks_duplicate_path(self):
        image_path = os.path.join(self.temp_dir, "pinned.png")
        with open(image_path, "wb") as handle:
            handle.write(b"png")
        entry = {"image_path": image_path, "ans": "Pinned answer"}

        self.assertTrue(self.controller.import_pinned_snap(entry))
        self.assertFalse(self.controller.import_pinned_snap(entry))
        session = self.controller.get_active_session()

        self.assertEqual(len(session.messages), 1)
        self.assertEqual(self.controller.get_status(), "Pinned snap is already in this chat.")

    def _write_prompt_definitions(self):
        prompt_path = os.path.join(self.temp_dir, "prompts.toml")
        with open(prompt_path, "w", encoding="utf-8") as handle:
            handle.write(
                '[prompts]\nprompt_translate = "Translate carefully."\n'
                'prompt_summarize = "Summarize exactly."\n'
            )

    def test_empty_history_startup_projects_configured_effective_prompt(self):
        self._write_prompt_definitions()
        session = self.controller.get_active_session()

        self.assertEqual(session.prompt_type, "prompt_translate")
        self.assertEqual(self.controller._resolve_session_system_prompt(session), "Translate carefully.")
        self.assertEqual(self.controller.system_prompt_summary(), "prompt_translate")
        self.assertFalse(self.controller.is_system_prompt_selected("", "", "Blank"))
        self.assertTrue(
            self.controller.is_system_prompt_selected(
                "prompt_translate",
                "Translate carefully.",
                "prompt_translate",
            )
        )

    def test_create_session_after_named_prompt_is_effectively_blank(self):
        self._write_prompt_definitions()
        session = self.controller.get_active_session()
        session.prompt_type = "prompt_summarize"
        session.system_prompt = ""
        session.system_prompt_label = "Blank"
        self.store.save_session(session)
        self.store.append_message(
            session.id,
            ScreenshotChatMessage(role="user", author="you", text="first"),
        )

        self.controller.create_session()
        created = self.controller.get_active_session()

        self.assertEqual(created.prompt_type, "custom")
        self.assertEqual(created.system_prompt, "")
        self.assertEqual(self.controller._resolve_session_system_prompt(created), "")
        self.assertEqual(self.controller.system_prompt_summary(), "Blank")
        self.assertTrue(self.controller.is_system_prompt_selected("", "", "Blank"))

    def test_legacy_blank_label_projects_effective_named_prompt_without_rewrite(self):
        self._write_prompt_definitions()
        session = self.controller.get_active_session()
        session.prompt_type = "prompt_summarize"
        session.system_prompt = ""
        session.system_prompt_label = "Blank"
        self.store.save_session(session)
        with open(self.store.save_path, "rb") as handle:
            stored_before = handle.read()

        restored = ScreenshotChatController(
            root=None,
            config_manager=FakeConfig(),
            project_root=self.temp_dir,
            runner=FakeRunner(),
            store=self.store,
        )
        restored_session = restored.get_active_session()

        self.assertEqual(restored_session.id, session.id)
        self.assertEqual(restored._resolve_session_system_prompt(restored_session), "Summarize exactly.")
        self.assertEqual(restored.system_prompt_summary(), "prompt_summarize")
        self.assertFalse(restored.is_system_prompt_selected("", "", "Blank"))
        self.assertTrue(
            restored.is_system_prompt_selected(
                "prompt_summarize",
                "Summarize exactly.",
                "prompt_summarize",
            )
        )
        with open(self.store.save_path, "rb") as handle:
            self.assertEqual(handle.read(), stored_before)

    def test_nonempty_custom_prompt_never_projects_blank(self):
        self.controller.set_session_system_prompt("custom", "Custom rules", "Blank")

        self.assertEqual(self.controller.system_prompt_summary(), "Custom")
        self.assertFalse(self.controller.is_system_prompt_selected("", "", "Blank"))

    def test_legacy_named_type_with_different_explicit_prompt_projects_custom_without_rewrite(self):
        self._write_prompt_definitions()
        session = self.controller.get_active_session()
        session.prompt_type = "prompt_summarize"
        session.system_prompt = "Different explicit prompt"
        session.system_prompt_label = "Blank"
        self.store.save_session(session)
        with open(self.store.save_path, "rb") as handle:
            stored_before = handle.read()

        self.assertEqual(self.controller._resolve_session_system_prompt(session), "Different explicit prompt")
        self.assertEqual(self.controller.system_prompt_summary(), "Custom")
        self.assertFalse(self.controller.is_system_prompt_selected("", "", "Blank"))
        self.assertFalse(
            self.controller.is_system_prompt_selected(
                "prompt_summarize",
                "Summarize exactly.",
                "prompt_summarize",
            )
        )
        with open(self.store.save_path, "rb") as handle:
            self.assertEqual(handle.read(), stored_before)

    def test_dispatch_freezes_prompt_identity_before_session_mutation(self):
        self._write_prompt_definitions()
        self.controller.set_session_system_prompt("prompt_summarize", "", "Blank")
        session_id = self.controller.get_active_session().id

        self.controller.send_message("first", "provider/main", "prompt_summarize")
        responder = self.runner.started_responders[0][0]
        dispatched = (
            self.runner.started[0][0],
            responder.model_name,
            responder.prompt_type,
            responder.system_prompt,
        )

        self.controller.set_session_model("provider/critic")
        self.controller.set_session_system_prompt("custom", "Changed later", "Custom")

        self.assertEqual(
            dispatched,
            (session_id, "provider/main", "prompt_summarize", "Summarize exactly."),
        )
        self.assertEqual(
            (
                self.runner.started[0][0],
                responder.model_name,
                responder.prompt_type,
                responder.system_prompt,
            ),
            dispatched,
        )

    def test_missing_prompts_toml_is_quiet_and_uses_default_prompt_list(self):
        with mock.patch("builtins.print") as printed:
            self.assertEqual(self.controller.prompt_definitions(), {})
            self.assertEqual(self.controller.available_prompts(), ["prompt_translate"])
            self.assertEqual(self.controller._resolve_session_system_prompt(self.controller.get_active_session()), "")
            self.assertEqual(self.controller.prompt_definitions(), {})

        printed.assert_not_called()

    def test_prompt_definitions_load_from_local_prompts_toml_when_present(self):
        prompt_path = os.path.join(self.temp_dir, "prompts.toml")
        with open(prompt_path, "w", encoding="utf-8") as handle:
            handle.write('[prompts]\nprompt_translate = "Translate carefully."\nprompt_summarize = "Summarize."\n')

        self.assertEqual(
            self.controller.prompt_definitions(),
            {"prompt_translate": "Translate carefully.", "prompt_summarize": "Summarize."},
        )
        self.assertEqual(self.controller.available_prompts(), ["prompt_translate", "prompt_summarize"])

    def test_invalid_prompts_toml_logs_once_and_falls_back(self):
        prompt_path = os.path.join(self.temp_dir, "prompts.toml")
        with open(prompt_path, "w", encoding="utf-8") as handle:
            handle.write("[prompts\n")

        with mock.patch("builtins.print") as printed:
            self.assertEqual(self.controller.prompt_definitions(), {})
            self.assertEqual(self.controller.prompt_definitions(), {})

        printed.assert_called_once()

    def test_delete_active_session_selects_clean_replacement(self):
        self.controller.send_message("first", "provider/main", "prompt_translate")
        deleted_id = self.controller.get_active_session().id

        self.assertTrue(self.controller.delete_session(deleted_id))
        session = self.controller.get_active_session()

        self.assertNotEqual(session.id, deleted_id)
        self.assertEqual(session.messages, [])

    def test_delete_active_session_cancels_pending_jobs(self):
        self.controller.send_message("first", "provider/main", "prompt_translate")
        deleted_id = self.controller.get_active_session().id

        self.assertTrue(self.controller.delete_session(deleted_id))
        session = self.controller.get_active_session()

        self.assertEqual(self.runner.canceled, ["job_main"])
        self.assertNotEqual(session.id, deleted_id)

    def test_import_clipboard_image_supports_image_file_list(self):
        image_path = os.path.join(self.temp_dir, "clip.png")
        with open(image_path, "wb") as handle:
            handle.write(b"png")

        with mock.patch(
            "src.gui.screenshot_chat.controller.ImageGrab.grabclipboard",
            return_value=[image_path],
        ):
            imported = self.controller.import_clipboard_image()

        self.assertTrue(imported)
        self.assertEqual(self.controller.pending_attachments[0].path, image_path)
        self.assertEqual(self.controller.pending_attachments[0].source, "clipboard")
        self.assertEqual(self.controller.get_status(), "")

    def test_import_clipboard_image_supports_bitmap(self):
        image = Image.new("RGB", (3, 3), "red")
        with mock.patch(
            "src.gui.screenshot_chat.controller.ImageGrab.grabclipboard",
            return_value=image,
        ):
            imported = self.controller.import_clipboard_image()

        self.assertTrue(imported)
        self.assertTrue(os.path.exists(self.controller.pending_attachments[0].path))

    def test_clipboard_import_returns_false_when_no_image(self):
        with mock.patch("src.gui.screenshot_chat.controller.ImageGrab.grabclipboard", return_value=None):
            imported = self.controller.import_clipboard_image()

        self.assertFalse(imported)

    def _wait_for_speech_state(self, expected):
        for _ in range(200):
            state = self.controller.get_speech_state()
            if state["state"] == expected:
                return state
            time.sleep(0.01)
        self.fail(f"speech state did not reach {expected}: {self.controller.get_speech_state()}")

    def test_sanitize_speech_text_keeps_prose_and_skips_code_and_raw_metadata(self):
        text = """# Answer\n**Useful** [link](https://example.test).\n\n```python\nprint('skip me')\n```\nmermaid: graph TD; A-->B\ntool: raw metadata\n- Final point"""

        self.assertEqual(sanitize_speech_text(text), "Answer Useful link. Final point")

    def test_read_message_runs_synthesis_and_playback_off_caller_thread(self):
        from src.audio.models import AudioOutput

        class FakeAudioPlugin:
            def __init__(self):
                self.calls = []
                self.thread_name = ""

            def synthesize_speech(self, text, *, voice=None, language=None):
                self.thread_name = threading.current_thread().name
                self.calls.append((text, voice, language))
                return AudioOutput("fake.wav", text, voice=voice, language=language, provider="fake")

            def close(self):
                return None

        class FakePlayback:
            def __init__(self):
                self.played = []
                self.stopped = 0
                self.thread_name = ""

            def play(self, audio):
                self.thread_name = threading.current_thread().name
                self.played.append(audio)

            def stop(self):
                self.thread_name = threading.current_thread().name
                self.stopped += 1

        audio = FakeAudioPlugin()
        playback = FakePlayback()
        self.controller._audio_plugin = audio
        self.controller._playback_provider = playback
        session = self.controller.get_active_session()
        message = ScreenshotChatMessage(
            id="speech_message",
            role="assistant",
            author="main",
            text="**Hello** world",
            status="done",
        )
        self.store.append_message(session.id, message)

        accepted = self.controller.read_message(message.id)
        state = self._wait_for_speech_state("playing")

        self.assertTrue(accepted)
        self.assertEqual(audio.calls, [("Hello world", "bao_kim", "vi")])
        self.assertEqual(len(playback.played), 1)
        self.assertNotEqual(audio.thread_name, threading.current_thread().name)
        self.assertNotEqual(playback.thread_name, threading.current_thread().name)
        self.assertEqual(state["message_id"], message.id)

        self.controller.stop_speech()
        stopped = self._wait_for_speech_state("idle")
        self.assertGreaterEqual(playback.stopped, 2)
        self.assertEqual(stopped["message_id"], "")

    def test_auto_read_triggers_only_for_successful_final_event_for_main_and_pi(self):
        self.controller._speech_auto_read = True
        self.controller.read_message = mock.MagicMock(return_value=True)
        for responder_name in ("main", "pi"):
            self.controller.send_message(f"ask {responder_name}", "provider/main", "prompt_translate")
            session = self.controller.get_active_session()
            job_id = session.pending_job_ids[0]

            self.controller._handle_runner_event(
                RunnerEvent(
                    type="message_delta",
                    session_id=session.id,
                    job_id=job_id,
                    responder_name=responder_name,
                    text="partial",
                )
            )
            self.assertEqual(self.controller.read_message.call_count, 0 if responder_name == "main" else 1)

            self.controller._handle_runner_event(
                RunnerEvent(
                    type="message_final",
                    session_id=session.id,
                    job_id=job_id,
                    responder_name=responder_name,
                    text=f"final {responder_name}",
                )
            )
            self.assertEqual(self.controller.read_message.call_count, 1 if responder_name == "main" else 2)
            self.controller._handle_runner_event(
                RunnerEvent(
                    type="job_finished",
                    session_id=session.id,
                    job_id=job_id,
                    responder_name=responder_name,
                )
            )

    def test_auto_read_skips_errors_and_empty_final_text(self):
        self.controller._speech_auto_read = True
        self.controller.read_message = mock.MagicMock(return_value=True)

        self.controller.send_message("error case", "provider/main", "prompt_translate")
        session = self.controller.get_active_session()
        job_id = session.pending_job_ids[0]
        self.controller._handle_runner_event(
            RunnerEvent(
                type="job_error",
                session_id=session.id,
                job_id=job_id,
                responder_name="main",
                error="offline",
            )
        )
        self.assertEqual(self.controller.read_message.call_count, 0)
        self.controller._handle_runner_event(
            RunnerEvent(type="job_finished", session_id=session.id, job_id=job_id, responder_name="main")
        )

        self.controller.send_message("empty case", "provider/main", "prompt_translate")
        session = self.controller.get_active_session()
        job_id = session.pending_job_ids[0]
        self.controller._handle_runner_event(
            RunnerEvent(
                type="message_final",
                session_id=session.id,
                job_id=job_id,
                responder_name="main",
                text="   ",
            )
        )
        self.assertEqual(self.controller.read_message.call_count, 0)

    def test_tts_failure_is_fail_soft_and_preserves_answer(self):
        class FailingAudioPlugin:
            def synthesize_speech(self, text, *, voice=None, language=None):
                raise TimeoutError("infrastructure detail")

            def close(self):
                return None

        class FakePlayback:
            def play(self, audio):
                raise AssertionError("play should not be called")

            def stop(self):
                return None

        self.controller._audio_plugin = FailingAudioPlugin()
        self.controller._playback_provider = FakePlayback()
        session = self.controller.get_active_session()
        message = ScreenshotChatMessage(
            id="speech_failure",
            role="assistant",
            author="main",
            text="Answer remains usable",
            status="done",
        )
        self.store.append_message(session.id, message)

        self.assertTrue(self.controller.read_message(message.id))
        state = self._wait_for_speech_state("error")

        reloaded = self.store.get_session(session.id)
        preserved = next(item for item in reloaded.messages if item.id == message.id)
        self.assertEqual(preserved.text, "Answer remains usable")
        self.assertEqual(state["message"], "TTS unavailable.")
        self.assertNotIn("infrastructure", state["message"])


if __name__ == "__main__":
    unittest.main()
