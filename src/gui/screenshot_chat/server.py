from __future__ import annotations

import base64
import time

import hashlib
import hmac
import json
import mimetypes
import os
import secrets
import socket
import threading
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from src.gui.screenshot_chat.webview_bridge import ScreenshotChatStateAdapter

ASSET_DIR = os.path.join(os.path.dirname(__file__), "web_assets")
TOKEN_HEADER = "X-STC-Token"
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp")
_CONNECTION_ERRORS = (ConnectionAbortedError, BrokenPipeError, ConnectionResetError)


def render_index_html(*, server_url: str, poll_interval_ms: int, token: str = "") -> str:
    with open(os.path.join(ASSET_DIR, "index.html"), "r", encoding="utf-8") as handle:
        html = handle.read()
    with open(os.path.join(ASSET_DIR, "app.css"), "r", encoding="utf-8") as handle:
        css = handle.read()
    with open(os.path.join(ASSET_DIR, "app.js"), "r", encoding="utf-8") as handle:
        js = handle.read()
    config = json.dumps({"serverUrl": server_url, "pollIntervalMs": poll_interval_ms, "token": token})
    config_script = f"window.__SCREENSHOT_CHAT_CONFIG__ = {config.replace('</', '<' + chr(92) + '/')};\n"
    js = config_script + js
    return html.replace("/* __APP_CSS__ */", css).replace("/* __APP_JS__ */", js)


class MediaRegistry:
    """Maps opaque ids to image paths so the same-origin page can load local images."""

    def __init__(self, token: str = ""):
        self.token = token
        self._lock = threading.Lock()
        self._paths: dict[str, str] = {}

    def url_for(self, path: str) -> str:
        if not path:
            return ""
        absolute = os.path.abspath(path)
        media_id = hashlib.blake2s(absolute.encode("utf-8"), digest_size=8).hexdigest()
        with self._lock:
            if media_id not in self._paths and len(self._paths) >= 256:
                oldest_key = next(iter(self._paths))
                self._paths.pop(oldest_key, None)
            self._paths[media_id] = absolute
        return f"/media/{media_id}?t={self.token}"

    def resolve(self, media_id: str) -> str | None:
        with self._lock:
            path = self._paths.get(media_id)
        if not path or not path.lower().endswith(IMAGE_EXTENSIONS) or not os.path.isfile(path):
            return None
        return path


class ScreenshotChatServer:
    def __init__(
        self,
        controller,
        host: str = "127.0.0.1",
        port: int = 0,
        token: str | None = None,
        poll_interval_ms: int = 900,
        enforce_token: bool = True,
    ):
        self.controller = controller
        if host not in {"127.0.0.1", "localhost"}:
            raise ValueError("ScreenshotChat server must bind to loopback")
        self.host = host
        self.port = int(port or 0)
        self.token = token or secrets.token_urlsafe(32)
        self.poll_interval_ms = int(poll_interval_ms or 900)
        self.enforce_token = bool(enforce_token)
        self.media = MediaRegistry(self.token)
        self.adapter = ScreenshotChatStateAdapter(controller, media_registry=self.media)
        self.page_request_count = 0
        self._thread = None
        self._httpd = None
        self._lock = threading.RLock()
        self._controller_lock = threading.RLock()
        self._ui_seq = 0
        self._ui_action = "hide"

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self):
        with self._lock:
            if self._httpd is not None:
                return
            self._httpd = ThreadingHTTPServer((self.host, self.port), self._build_handler())
            self._httpd.daemon_threads = True
            self.port = int(self._httpd.server_address[1])
            self._thread = threading.Thread(target=self._httpd.serve_forever, name="ScreenshotChatServer", daemon=True)
            self. _thread.start()

    def stop(self):
        with self._lock:
            httpd = self._httpd
            thread = self._thread
            self._httpd = None
            self._thread = None
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
        if thread is not None and thread.is_alive():
            thread.join(timeout=1)

    def request_show(self):
        self._set_ui_action("show")

    def request_hide(self):
        self._set_ui_action("hide")

    def ui_state(self) -> dict:
        with self._lock:
            return {"seq": self._ui_seq, "action": self._ui_action}

    def snapshot(self) -> dict:
        with self._controller_lock:
            payload = self.adapter.get_state()
        payload["ui"] = self.ui_state()
        return payload

    def drain_events(self, cursor: str = "", limit: int = 6) -> dict:
        with self._controller_lock:
            payload = self.adapter.drain_events(cursor=cursor, limit=limit)
        payload["ui"] = self.ui_state()
        return payload

    def _count_page_request(self):
        with self._lock:
            self.page_request_count += 1

    def render_page(self) -> str:
        return render_index_html(
            server_url=self.base_url,
            poll_interval_ms=self.poll_interval_ms,
            token=self.token,
        )

    def _set_ui_action(self, action: str):
        with self._lock:
            self._ui_seq += 1
            self._ui_action = action

    def _token_ok(self, candidate: str) -> bool:
        return hmac.compare_digest(str(candidate or "").encode("utf-8"), self.token.encode("utf-8"))

    def _host_ok(self, host_header: str) -> bool:
        allowed = {f"127.0.0.1:{self.port}", f"localhost:{self.port}", f"{self.host}:{self.port}"}
        return str(host_header or "").strip().lower() in allowed

    def _authorized(self, headers, query: dict | None = None, allow_query: bool = False) -> bool:
        if not self.enforce_token:
            return True
        if self._token_ok(headers.get(TOKEN_HEADER, "")):
            return True
        if allow_query:
            return self._token_ok(((query or {}).get("t") or [""])[0])
        return False

    def _build_handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self._guarded(self._handle_get)

            def do_POST(self):
                self._guarded(self._handle_post)

            def _guarded(self, handler):
                try:
                    handler()
                except _CONNECTION_ERRORS:
                    return
                except Exception as exc:
                    traceback.print_exc()
                    detail = f"{type(exc).__name__}: {exc}"[:300]
                    self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "internal_error", "detail": detail})

            def _check(self, query=None, allow_query=False) -> bool:
                host = self.headers.get("Host", "")
                origin = self.headers.get("Origin")
                # A cross-origin request is never trusted, even with a leaked token.
                origin_ok = not origin or origin.lower() == f"http://{host}".lower()
                if server._host_ok(host) and origin_ok and server._authorized(
                    self.headers, query, allow_query=allow_query
                ):
                    return True
                self._write_json(HTTPStatus.FORBIDDEN, {"error": "forbidden"})
                return False

            def _handle_get(self):
                parsed = urlparse(self.path)
                query = parse_qs(parsed.query)
                if parsed.path == "/health":
                    if server._host_ok(self.headers.get("Host", "")):
                        self._write_json(HTTPStatus.OK, {"ok": True})
                    else:
                        self._write_json(HTTPStatus.FORBIDDEN, {"error": "forbidden"})
                    return
                if parsed.path == "/":
                    if self._check(query, allow_query=True):
                        self._write_bytes(HTTPStatus.OK, server.render_page().encode("utf-8"), "text/html; charset=utf-8")
                    return
                if parsed.path == "/export":
                    if self._check(query, allow_query=True):
                        export_fn = getattr(server.controller, "export_session_transcript", None)
                        res = export_fn() if callable(export_fn) else {"html": "<html></html>"}
                        self._write_bytes(HTTPStatus.OK, str(res.get("html") or "").encode("utf-8"), "text/html; charset=utf-8")
                    return
                if parsed.path.startswith("/media/"):
                    if self._check(query, allow_query=True):
                        path = server.media.resolve(parsed.path[len("/media/"):])
                        if path is None:
                            self._write_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                            return
                        with open(path, "rb") as handle:
                            data = handle.read()
                        self._write_bytes(HTTPStatus.OK, data, mimetypes.guess_type(path)[0] or "application/octet-stream")
                    return
                if parsed.path == "/ui":
                    if self._check(query):
                        self._write_json(HTTPStatus.OK, server.ui_state())
                    return
                if parsed.path == "/state":
                    if self._check(query):
                        payload = server.snapshot()
                        server._count_page_request()
                        self._write_json(HTTPStatus.OK, payload)
                    return
                if parsed.path == "/events":
                    if self._check(query):
                        cursor = (query.get("cursor") or [""])[0]
                        payload = server.drain_events(cursor=cursor)
                        server._count_page_request()
                        self._write_json(HTTPStatus.OK, payload)
                    return
                self._write_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

            def _handle_post(self):
                parsed = urlparse(self.path)
                if not self._check():
                    return
                payload = self._read_json()
                routes = {
                    "/send_message": lambda body: server._act(
                        lambda: server.controller.send_message(
                            str(body.get("text") or ""),
                            str(body.get("model_name") or ""),
                            str(body.get("prompt_type") or ""),
                        )
                    ),
                    "/stop_jobs": lambda body: server._act(server.controller.stop_active_session_jobs),
                    "/create_session": lambda body: server._act(server.controller.create_session),
                    "/load_session": lambda body: server._act(
                        lambda: server.controller.load_session(str(body.get("session_id") or ""))
                    ),
                    "/delete_session": lambda body: server._act(
                        lambda: server.controller.delete_session(str(body.get("session_id") or ""))
                    ),
                    "/set_model": lambda body: server._act(
                        lambda: server.controller.set_session_model(str(body.get("model_name") or ""))
                    ),
                    "/toggle_favorite_model": lambda body: server._act(
                        lambda: server.controller.toggle_favorite_model(str(body.get("model_name") or ""))
                        if hasattr(server.controller, "toggle_favorite_model")
                        else None
                    ),
                    "/slash_command": lambda body: server._act(
                        lambda: server.controller.execute_slash_command(str(body.get("command") or ""))
                        if hasattr(server.controller, "execute_slash_command")
                        else None
                    ),
                    "/fork_session": lambda body: server._act(
                        lambda: server.controller.fork_session(str(body.get("title") or ""))
                        if hasattr(server.controller, "fork_session")
                        else None
                    ),
                    "/compact_session": lambda body: server._act(
                        lambda: server.controller.compact_session(str(body.get("focus") or ""))
                        if hasattr(server.controller, "compact_session")
                        else None
                    ),
                    "/export_session": lambda body: server._act(
                        lambda: server.controller.export_session_transcript()
                        if hasattr(server.controller, "export_session_transcript")
                        else None
                    ),
                    "/steer_run": lambda body: server._act(
                        lambda: server.controller.steer_active_run(
                            str(body.get("instruction") or ""),
                            mode=str(body.get("mode") or "steer"),
                        )
                        if hasattr(server.controller, "steer_active_run")
                        else None
                    ),
                    "/set_mode": lambda body: server._act(
                        lambda: server.controller.set_session_mode(str(body.get("mode") or "chat"))
                    ),
                    "/delete_message": lambda body: server._act(
                        lambda: server.controller.delete_message(str(body.get("message_id") or ""))
                    ),
                    "/set_system_prompt": lambda body: server._act(
                        lambda: server.controller.set_session_system_prompt(
                            str(body.get("prompt_type") or ""),
                            str(body.get("system_prompt") or ""),
                            str(body.get("label") or "Blank"),
                        )
                    ),
                    "/read_message": lambda body: server._act(
                        lambda: server.controller.read_message(str(body.get("message_id") or ""))
                    ),
                    "/stop_speech": lambda body: server._act(server.controller.stop_speech),
                    "/set_auto_read": lambda body: server._act(
                        lambda: server.controller.set_auto_read(bool(body.get("enabled", False)))
                    ),
                    "/import_held_snaps": lambda body: server._act(server.controller.import_held_snaps),
                    "/import_clipboard_image": lambda body: server._act(
                        lambda: server.controller.import_clipboard_image(show_empty_status=True)
                    ),
                    "/import_file": lambda body: server._act(
                        lambda: server.controller.import_paths(
                            [str(item) for item in (body.get("paths") or []) if isinstance(item, str)],
                            source="file",
                        )
                    ),
                    "/upload_files": lambda body: server._act(
                        lambda: server._handle_upload_files(body)
                    ),
                    "/import_pinned_snap": lambda body: server._act(
                        lambda: server.controller.import_pinned_snap(body.get("entry"))
                        if isinstance(body.get("entry"), dict)
                        else None
                    ),
                    "/clear_pending_attachment": lambda body: server._act(
                        lambda: server.controller.clear_pending_attachment(str(body.get("attachment_id") or ""))
                    ),
                    "/hide": lambda body: server._hide(),
                }
                route = routes.get(parsed.path)
                if route is None:
                    self._write_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                    return
                self._write_json(HTTPStatus.OK, route(payload))

            def _read_json(self) -> dict:
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    length = 0
                max_len = 25_000_000 if self.path.startswith("/upload_files") else 1_000_000
                if length <= 0 or length > max_len:
                    return {}
                raw = self.rfile.read(length).decode("utf-8", errors="replace")
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    return {}
                return data if isinstance(data, dict) else {}

            def _write_json(self, status: HTTPStatus, payload: dict):
                self._write_bytes(status, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")

            def _write_bytes(self, status: HTTPStatus, body: bytes, content_type: str):
                self.send_response(int(status))
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                return

        return Handler

    def _act(self, action) -> dict:
        with self._controller_lock:
            action()
            payload = self.adapter.get_state()
        payload["ui"] = self.ui_state()
        return payload

    def _hide(self) -> dict:
        self.request_hide()
        with self._controller_lock:
            self.controller.hide_window()
        return {"ok": True, "ui": self.ui_state()}

    def _handle_upload_files(self, body: dict) -> None:
        raw_files = body.get("files")
        if not isinstance(raw_files, list) or not raw_files:
            return
        proj_root = getattr(self.controller, "project_root", os.getcwd())
        upload_dir = os.path.join(proj_root, "data", "chat_uploads")
        os.makedirs(upload_dir, exist_ok=True)
        saved_paths = []
        ts = int(time.time() * 1000)
        for idx, item in enumerate(raw_files[:12]):
            if not isinstance(item, dict):
                continue
            raw_b64 = str(item.get("dataBase64") or "").strip()
            if not raw_b64:
                continue
            if raw_b64.startswith("data:") and "," in raw_b64:
                raw_b64 = raw_b64.split(",", 1)[1]
            try:
                binary = base64.b64decode(raw_b64, validate=False)
            except Exception:
                continue
            raw_name = os.path.basename(str(item.get("name") or f"upload_{idx + 1}.png"))
            safe_name = "".join(c if (c.isalnum() or c in "._-") else "_" for c in raw_name) or f"upload_{idx + 1}.bin"
            dest = os.path.join(upload_dir, f"{ts}_{idx + 1}_{safe_name}")
            with open(dest, "wb") as f:
                f.write(binary)
            saved_paths.append(dest)
        if saved_paths:
            self.controller.import_paths(saved_paths, source="file")
