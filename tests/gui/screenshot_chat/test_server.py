import http.client
import json
import os
import queue
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

from src.gui.screenshot_chat.controller import ScreenshotChatController
from src.gui.screenshot_chat.models import ScreenshotChatAttachment
from src.gui.screenshot_chat.server import ScreenshotChatServer
from src.gui.screenshot_chat.store import ScreenshotChatStore


class FakeConfig:
    def reload_if_changed(self):
        return None

    def get_screenshot_chat_config(self):
        return {
            "enabled": True,
            "default_model": "provider/main",
            "default_prompt_type": "prompt_translate",
            "temperature": 0.7,
            "agents": {},
            "runtime": {
                "mode": "webview-process",
                "host": "127.0.0.1",
                "port": 0,
                "startup_timeout_ms": 3000,
                "poll_interval_ms": 900,
                "fallback_tk_debug": False,
            },
        }

    def get_all_models(self):
        return {"provider/main": {}}


class FakeRunner:
    def __init__(self):
        self.event_queue = queue.Queue()
        self.started = []

    def prepare_response_jobs(self, session, user_message, responders):
        from src.gui.screenshot_chat.models import ScreenshotChatJob

        return [ScreenshotChatJob(id="job_main", session_id=session.id, message_id=user_message.id, responder_name="main")]

    def start_prepared_jobs(self, session, user_message, prompt_text, responders, jobs, temperature=0.7):
        self.started.append((session.id, prompt_text))

    def cancel_jobs(self, job_ids):
        return None


class ScreenshotChatServerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="screenshot_chat_server_")
        self.controller = ScreenshotChatController(
            root=None,
            config_manager=FakeConfig(),
            project_root=self.temp_dir,
            held_images_getter=lambda: [],
            held_images_consumer=lambda paths: None,
            snap_history_getter=lambda: None,
            runner=FakeRunner(),
            store=ScreenshotChatStore(os.path.join(self.temp_dir, "sessions.json")),
        )
        self.server = ScreenshotChatServer(self.controller, host="127.0.0.1", port=0)
        self.assertTrue(self.server.enforce_token)
        self.server.start()

    def tearDown(self):
        self.server.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _get_json(self, path):
        request = urllib.request.Request(
            f"{self.server.base_url}{path}",
            headers={"X-STC-Token": self.server.token},
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            return json.loads(response.read().decode("utf-8"))

    def _post_json(self, path, payload):
        request = urllib.request.Request(
            f"{self.server.base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-STC-Token": self.server.token},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_state_endpoint_returns_current_session_payload(self):
        payload = self._get_json("/state")

        self.assertEqual(payload["session"]["model_name"], "provider/main")
        self.assertIn("ui", payload)
        self.assertEqual(payload["ui"]["action"], "hide")

    def test_send_message_and_events_round_trip_through_http(self):
        initial = self._get_json("/state")
        self._post_json(
            "/send_message",
            {"text": "hello", "model_name": "provider/main", "prompt_type": "prompt_translate"},
        )

        changed = self._get_json(f"/events?cursor={initial['version']}")

        self.assertFalse(changed.get("unchanged", False))
        self.assertEqual(changed["messages"][0]["text"], "hello")

    def test_events_returns_unchanged_marker_for_same_cursor(self):
        current = self._get_json("/state")

        result = self._get_json(f"/events?cursor={current['version']}")

        self.assertTrue(result["unchanged"])
        self.assertEqual(result["version"], current["version"])

    def test_hide_endpoint_updates_ui_command(self):
        self._post_json("/hide", {})

        payload = self._get_json("/state")
        self.assertEqual(payload["ui"]["action"], "hide")

    def test_hide_endpoint_syncs_parent_visibility_before_next_toggle(self):
        class FakeWindow:
            def __init__(self):
                self.visible = True
                self.show_count = 0
                self.hide_count = 0

            def winfo_exists(self):
                return True

            def is_visible(self):
                return self.visible

            def show(self):
                self.visible = True
                self.show_count += 1

            def hide(self):
                self.visible = False
                self.hide_count += 1

        window = FakeWindow()
        self.controller.window = window
        self.server.request_show()

        self._post_json("/hide", {})

        self.assertFalse(window.visible)
        self.controller.toggle_window()
        self.assertTrue(window.visible)
        self.assertEqual(window.hide_count, 1)
        self.assertEqual(window.show_count, 1)

    def test_delete_session_endpoint_removes_chat_history(self):
        first = self._get_json("/state")["session"]["id"]
        self._post_json(
            "/send_message",
            {"text": "hello", "model_name": "provider/main", "prompt_type": "prompt_translate"},
        )

        payload = self._post_json("/delete_session", {"session_id": first})

        self.assertNotEqual(payload["session"]["id"], first)
        self.assertNotIn(first, [session["id"] for session in payload["sessions"]])

    def test_set_mode_endpoint_switches_to_note_mode(self):
        payload = self._post_json("/set_mode", {"mode": "note"})

        self.assertEqual(payload["session"]["mode"], "note")

    def test_speech_endpoints_delegate_without_blocking_http_contract(self):
        with mock.patch.object(self.controller, "read_message", return_value=True) as read, \
             mock.patch.object(self.controller, "stop_speech", return_value=True) as stop, \
             mock.patch.object(self.controller, "set_auto_read", return_value=True) as auto:
            self._post_json("/read_message", {"message_id": "m2"})
            self._post_json("/stop_speech", {})
            self._post_json("/set_auto_read", {"enabled": True})

        read.assert_called_once_with("m2")
        stop.assert_called_once_with()
        auto.assert_called_once_with(True)

    def _raw_get(self, path, host=None, headers=None, token=True):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=2)
        try:
            connection.putrequest("GET", path, skip_host=host is not None)
            if host is not None:
                connection.putheader("Host", host)
            sent = {"X-STC-Token": self.server.token} if token and headers is None else {}
            sent.update(headers or {})
            for name, value in sent.items():
                connection.putheader(name, value)
            connection.endheaders()
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_api_requests_without_valid_token_are_rejected(self):
        for path in ("/state", "/events", "/ui"):
            self.assertEqual(self._raw_get(path, token=False)[0], 403, path)
            self.assertEqual(self._raw_get(path, headers={"X-STC-Token": "wrong"})[0], 403, path)
            # The query token is only accepted for the page and media routes.
            self.assertEqual(self._raw_get(f"{path}?t={self.server.token}", token=False)[0], 403, path)
        request = urllib.request.Request(
            f"{self.server.base_url}/send_message",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as context:
            urllib.request.urlopen(request, timeout=2)
        self.assertEqual(context.exception.code, 403)
        self.assertEqual(self._raw_get("/health")[0], 200)

    def test_index_page_and_media_require_query_token(self):
        self.assertEqual(self._raw_get("/", token=False)[0], 403)
        self.assertEqual(self._raw_get("/?t=wrong", token=False)[0], 403)
        self.assertEqual(self._raw_get("/media/deadbeefdeadbeef", token=False)[0], 403)
        self.assertEqual(self._raw_get(f"/?t={self.server.token}", token=False)[0], 200)
        self.assertEqual(self._raw_get("/", headers={"X-STC-Token": self.server.token})[0], 200)

    def test_index_page_is_served_with_token_config(self):
        status, headers, body = self._raw_get(f"/?t={self.server.token}", token=False)

        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["Referrer-Policy"], "no-referrer")
        text = body.decode("utf-8")
        self.assertIn("__SCREENSHOT_CHAT_CONFIG__", text)
        self.assertIn(self.server.token, text)
        self.assertIn(self.server.base_url, text)

    def test_ui_endpoint_returns_only_ui_command(self):
        payload = self._get_json("/ui")

        self.assertEqual(set(payload), {"seq", "action"})
        self.assertEqual(payload["action"], "hide")

    def test_bad_host_header_is_rejected(self):
        status, _, body = self._raw_get("/state", host="evil.example:80")
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body)["error"], "forbidden")

        status, _, _ = self._raw_get("/health", host="evil.example:80")
        self.assertEqual(status, 403)

        status, _, _ = self._raw_get("/state", host=f"localhost:{self.server.port}")
        self.assertEqual(status, 200)

    def test_handler_exception_returns_json_500_and_server_keeps_serving(self):
        with mock.patch.object(self.server.adapter, "get_state", side_effect=RuntimeError("boom")), \
             mock.patch("traceback.print_exc"):
            status, _, body = self._raw_get("/state")

        self.assertEqual(status, 500)
        payload = json.loads(body)
        self.assertEqual(payload["error"], "internal_error")
        self.assertIn("RuntimeError: boom", payload["detail"])
        self.assertEqual(self._get_json("/state")["session"]["model_name"], "provider/main")

    def test_failed_state_and_events_do_not_count_as_page_requests(self):
        with mock.patch.object(self.server.adapter, "get_state", side_effect=RuntimeError("boom")), \
             mock.patch("traceback.print_exc"):
            self.assertEqual(self._raw_get("/state")[0], 500)
        with mock.patch.object(self.server.adapter, "drain_events", side_effect=RuntimeError("boom")), \
             mock.patch("traceback.print_exc"):
            self.assertEqual(self._raw_get("/events")[0], 500)
        self.assertEqual(self.server.page_request_count, 0)

        self.assertEqual(self._raw_get("/state")[0], 200)
        self.assertEqual(self.server.page_request_count, 1)

    def test_media_serves_registered_image_and_rejects_others(self):
        image_path = os.path.join(self.temp_dir, "snap.png")
        with open(image_path, "wb") as handle:
            handle.write(b"\x89PNG\r\n\x1a\nfake")
        text_path = os.path.join(self.temp_dir, "notes.txt")
        with open(text_path, "w", encoding="utf-8") as handle:
            handle.write("secret")

        image_url = self.server.media.url_for(image_path)
        text_url = self.server.media.url_for(text_path)

        status, headers, body = self._raw_get(image_url, token=False)
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertTrue(body.startswith(b"\x89PNG"))
        self.assertEqual(self._raw_get(text_url, token=False)[0], 404)
        self.assertEqual(self._raw_get(f"/media/deadbeefdeadbeef?t={self.server.token}", token=False)[0], 404)

    def test_media_missing_file_returns_404(self):
        url = self.server.media.url_for(os.path.join(self.temp_dir, "gone.png"))

        self.assertEqual(self._raw_get(url, token=False)[0], 404)

    def test_state_urls_point_at_media_endpoint(self):
        self.controller.pending_attachments = [
            ScreenshotChatAttachment(path=os.path.join(self.temp_dir, "a.png"), label="a.png", source="file")
        ]

        payload = self._get_json("/state")

        self.assertTrue(payload["pending_attachments"][0]["url"].startswith("/media/"))

    def test_concurrent_events_and_send_message_complete_under_controller_lock(self):
        errors = []

        def poll():
            try:
                for _ in range(10):
                    self._get_json("/events")
            except Exception as exc:  # pragma: no cover - failure path
                errors.append(exc)

        def send(index):
            try:
                self._post_json(
                    "/send_message",
                    {"text": f"hello {index}", "model_name": "provider/main", "prompt_type": "prompt_translate"},
                )
            except Exception as exc:  # pragma: no cover - failure path
                errors.append(exc)

        threads = [threading.Thread(target=poll) for _ in range(3)]
        threads += [threading.Thread(target=send, args=(index,)) for index in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])

    def test_page_request_count_only_counts_state_and_events(self):
        self._get_json("/health")
        self._get_json("/ui")
        self.assertEqual(self.server.page_request_count, 0)

        self._get_json("/state")
        self._get_json("/events")

        self.assertEqual(self.server.page_request_count, 2)

    def test_delete_message_endpoint_removes_message(self):
        sent = self._post_json(
            "/send_message",
            {"text": "hello", "model_name": "provider/main", "prompt_type": "prompt_translate"},
        )
        message_id = sent["messages"][0]["id"]

        payload = self._post_json("/delete_message", {"message_id": message_id})

        self.assertNotIn(message_id, [message["id"] for message in payload["messages"]])
