import unittest

from src.gui.screenshot_chat.models import (
    ScreenshotChatAttachment,
    ScreenshotChatMessage,
    ScreenshotChatSession,
)


class FakeController:
    def __init__(self):
        self.sent = []
        self.stopped = 0
        self.created = 0
        self.loaded = []
        self.imported = []
        self.model = ""
        self.mode = ""
        self.deleted_messages = []
        self.system_prompt = None
        self.read_messages = []
        self.speech_stops = 0
        self.auto_read_values = []
        self.speech_state = {"state": "idle", "message_id": "", "message": "", "auto_read": False}
        self.status = "Ready"
        self.runtime_status = {
            "state": "ready",
            "scope": "llm",
            "label": "Ready",
            "message": "",
            "detail": "",
            "retryable": False,
        }
        self.model_options = [
            {"id": "provider/model", "label": "model", "runtime": "llm", "available": True, "reason": ""},
            {"id": "provider/other", "label": "other", "runtime": "llm", "available": True, "reason": ""},
            {"id": "pi://agent/pi", "label": "Pi Agent", "runtime": "pi", "available": True, "reason": "available"},
        ]
        self.pending_attachments = [
            ScreenshotChatAttachment(path="C:/tmp/a.png", label="a.png", source="file")
        ]
        self.session = ScreenshotChatSession(
            id="s1",
            title="Existing",
            model_name="provider/model",
            prompt_type="prompt_translate",
            system_prompt_label="Blank",
            messages=[
                ScreenshotChatMessage(
                    id="m1",
                    role="user",
                    author="you",
                    text="hello",
                    attachments=[],
                    status="done",
                ),
                ScreenshotChatMessage(
                    id="m2",
                    role="assistant",
                    author="main",
                    text="answer",
                    attachments=[
                        ScreenshotChatAttachment(
                            path="C:/tmp/snap.png",
                            label="snap.png",
                            source="pinned",
                        )
                    ],
                    status="done",
                    events=[{"id": "evt_1", "kind": "tool_started", "status": "running"}],
                ),
            ],
        )

    def get_active_session(self):
        return self.session

    def list_display_sessions(self):
        return [self.session]

    def available_models(self):
        return ["provider/model", "provider/other"]

    def available_model_options(self):
        return list(self.model_options)

    def model_display_name(self, model_name):
        if model_name == "pi://agent/pi":
            return "Pi Agent"
        return model_name.rsplit("/", 1)[-1] if model_name else "Model"

    def prompt_definitions(self):
        return {"prompt_translate": "Translate"}

    def system_prompt_summary(self, session=None):
        return (session or self.session).prompt_type

    def list_pinned_snaps(self):
        return [{"image_path": "C:/tmp/snap.png", "display_title": "Pinned"}]

    def is_pinned_snap_used(self, entry):
        return True

    def get_status(self):
        return self.status

    def get_runtime_status(self):
        return dict(self.runtime_status)

    def get_speech_state(self):
        return dict(self.speech_state)

    def drain_runner_events(self):
        return None

    def send_message(self, text, model_name, prompt_type):
        self.sent.append((text, model_name, prompt_type))

    def stop_active_session_jobs(self):
        self.stopped += 1

    def create_session(self):
        self.created += 1

    def load_session(self, session_id):
        self.loaded.append(session_id)

    def import_pinned_snap(self, entry):
        self.imported.append(entry)
        return True

    def import_held_snaps(self):
        return 0

    def import_clipboard_image(self, show_empty_status=False):
        return False

    def clear_pending_attachment(self, attachment_id):
        self.pending_attachments = [
            item for item in self.pending_attachments if item.id != attachment_id
        ]

    def set_session_model(self, model_name):
        self.model = model_name

    def set_session_mode(self, mode):
        self.mode = mode
        self.session.mode = mode

    def delete_message(self, message_id):
        self.deleted_messages.append(message_id)
        self.session.messages = [item for item in self.session.messages if item.id != message_id]
        return True

    def set_session_system_prompt(self, prompt_type, system_prompt, label):
        self.system_prompt = (prompt_type, system_prompt, label)

    def read_message(self, message_id):
        self.read_messages.append(message_id)
        self.speech_state.update({"state": "playing", "message_id": message_id})
        return True

    def stop_speech(self):
        self.speech_stops += 1
        self.speech_state.update({"state": "idle", "message_id": ""})
        return True

    def set_auto_read(self, enabled):
        self.auto_read_values.append(bool(enabled))
        self.speech_state["auto_read"] = bool(enabled)
        return bool(enabled)


class ScreenshotChatWebBridgeTests(unittest.TestCase):
    def test_get_state_serializes_ui_without_tk_widgets(self):
        from src.gui.screenshot_chat.webview_bridge import ScreenshotChatWebBridge

        bridge = ScreenshotChatWebBridge(FakeController())

        state = bridge.get_state()

        self.assertEqual(state["active_session_id"], "s1")
        self.assertEqual(state["sessions"][0]["title"], "Existing")
        self.assertEqual(state["messages"][1]["author"], "main")
        self.assertEqual(state["messages"][1]["attachments"][0]["label"], "snap.png")
        self.assertEqual(state["messages"][1]["events"][0]["kind"], "tool_started")
        self.assertEqual(state["pending_attachments"][0]["label"], "a.png")
        self.assertEqual(state["models"], ["provider/model", "provider/other"])
        self.assertEqual([option["id"] for option in state["model_options"]], ["provider/model", "provider/other", "pi://agent/pi"])
        self.assertEqual(state["model_label"], "model")
        self.assertEqual(state["session"]["model_label"], "model")
        self.assertEqual(state["session"]["mode"], "chat")
        self.assertEqual(state["session"]["system_prompt_label"], "prompt_translate")
        self.assertTrue(state["pinned_snaps"][0]["used"])
        self.assertEqual(state["runtime_status"]["state"], "ready")
        self.assertEqual(state["runtime_status"]["label"], "Ready")
        self.assertEqual(state["speech"]["state"], "idle")
        self.assertFalse(state["speech"]["auto_read"])

    def test_state_version_changes_when_current_model_changes(self):
        from src.gui.screenshot_chat.webview_bridge import ScreenshotChatWebBridge

        controller = FakeController()
        bridge = ScreenshotChatWebBridge(controller)
        first = bridge.state_version()
        controller.session.model_name = "pi://agent/pi"

        self.assertNotEqual(first, bridge.state_version())
        state = bridge.get_state()
        self.assertEqual(state["model_label"], "Pi Agent")

    def test_state_version_changes_when_runtime_status_changes(self):
        from src.gui.screenshot_chat.webview_bridge import ScreenshotChatWebBridge

        controller = FakeController()
        bridge = ScreenshotChatWebBridge(controller)
        first = bridge.state_version()
        controller.runtime_status["state"] = "streaming"
        controller.runtime_status["label"] = "Generating..."

        self.assertNotEqual(first, bridge.state_version())

    def test_state_version_changes_when_speech_state_changes(self):
        from src.gui.screenshot_chat.webview_bridge import ScreenshotChatWebBridge

        controller = FakeController()
        bridge = ScreenshotChatWebBridge(controller)
        first = bridge.state_version()
        controller.speech_state.update({"state": "playing", "message_id": "m2"})

        self.assertNotEqual(first, bridge.state_version())

    def test_state_version_changes_when_model_options_change(self):
        from src.gui.screenshot_chat.webview_bridge import ScreenshotChatWebBridge

        controller = FakeController()
        bridge = ScreenshotChatWebBridge(controller)
        first = bridge.state_version()
        controller.model_options[-1]["available"] = False
        controller.model_options[-1]["reason"] = "missing"

        self.assertNotEqual(first, bridge.state_version())

    def test_state_version_changes_when_message_events_change(self):
        from src.gui.screenshot_chat.webview_bridge import ScreenshotChatWebBridge

        controller = FakeController()
        bridge = ScreenshotChatWebBridge(controller)
        first = bridge.state_version()
        controller.session.messages[1].events.append({"id": "evt_2", "kind": "approval_resolved", "status": "approved"})

        self.assertNotEqual(first, bridge.state_version())

    def test_bridge_actions_delegate_to_controller_and_return_state(self):
        from src.gui.screenshot_chat.webview_bridge import ScreenshotChatWebBridge

        controller = FakeController()
        bridge = ScreenshotChatWebBridge(controller)

        bridge.send_message("hi", "provider/model", "prompt_translate")
        bridge.stop_jobs()
        bridge.create_session()
        bridge.load_session("s1")
        bridge.import_pinned_snap({"image_path": "C:/tmp/snap.png"})
        bridge.set_model("provider/other")
        bridge.set_mode("note")
        bridge.delete_message("m1")
        bridge.set_system_prompt("custom", "rules", "Custom")
        bridge.read_message("m2")
        bridge.stop_speech()
        bridge.set_auto_read(True)

        self.assertEqual(controller.sent, [("hi", "provider/model", "prompt_translate")])
        self.assertEqual(controller.stopped, 1)
        self.assertEqual(controller.created, 1)
        self.assertEqual(controller.loaded, ["s1"])
        self.assertEqual(controller.imported, [{"image_path": "C:/tmp/snap.png"}])
        self.assertEqual(controller.model, "provider/other")
        self.assertEqual(controller.mode, "note")
        self.assertEqual(controller.deleted_messages, ["m1"])
        self.assertEqual(controller.system_prompt, ("custom", "rules", "Custom"))
        self.assertEqual(controller.read_messages, ["m2"])
        self.assertEqual(controller.speech_stops, 1)
        self.assertEqual(controller.auto_read_values, [True])

    def test_bridge_actions_suspend_reentrant_window_refresh(self):
        from src.gui.screenshot_chat.webview_bridge import ScreenshotChatWebBridge

        class FakeWindow:
            def __init__(self):
                self.calls = []

            def begin_bridge_action(self):
                self.calls.append("begin")

            def end_bridge_action(self):
                self.calls.append("end")

        window = FakeWindow()
        bridge = ScreenshotChatWebBridge(FakeController(), window)

        bridge.send_message("hi", "provider/model", "prompt_translate")

        self.assertEqual(window.calls, ["begin", "end"])

    def test_drain_events_returns_unchanged_marker_when_state_did_not_change(self):
        from src.gui.screenshot_chat.webview_bridge import ScreenshotChatWebBridge

        bridge = ScreenshotChatWebBridge(FakeController())

        result = bridge.drain_events()

        self.assertTrue(result["unchanged"])
        self.assertIn("version", result)

    def test_file_urls_use_file_scheme_without_media_registry(self):
        from src.gui.screenshot_chat.webview_bridge import ScreenshotChatStateAdapter

        state = ScreenshotChatStateAdapter(FakeController()).get_state()

        self.assertEqual(state["pending_attachments"][0]["url"], "file:///C:/tmp/a.png")

    def test_file_urls_use_media_registry_when_present(self):
        from src.gui.screenshot_chat.server import MediaRegistry
        from src.gui.screenshot_chat.webview_bridge import ScreenshotChatStateAdapter

        registry = MediaRegistry(token="tok")
        state = ScreenshotChatStateAdapter(FakeController(), media_registry=registry).get_state()

        url = state["pending_attachments"][0]["url"]
        self.assertTrue(url.startswith("/media/"))
        self.assertTrue(url.endswith("?t=tok"))
        self.assertNotIn("file:", url)


if __name__ == "__main__":
    unittest.main()
