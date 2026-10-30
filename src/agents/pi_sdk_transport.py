from __future__ import annotations

import collections
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping


class PiBridgeError(RuntimeError):
    pass


class PiBridgeUnavailable(PiBridgeError):
    pass


class PiBridgeIncompatibleProtocol(PiBridgeError):
    pass


class PiBridgeTimeout(PiBridgeError):
    pass


class PiBridgeFirstEventTimeout(PiBridgeTimeout):
    def __init__(self, message: str, *, quiescent: bool):
        super().__init__(message)
        self.quiescent = bool(quiescent)


class PiBridgeExited(PiBridgeError):
    pass


class PiBridgeMalformedFrame(PiBridgeError):
    pass


class PiBridgeFrameTooLarge(PiBridgeError):
    pass


class PiBridgeRemoteError(PiBridgeError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.remote_message = message


@dataclass(frozen=True)
class BridgeHello:
    protocol: int
    bridge_version: str
    sdk_package: str
    sdk_version: str
    pid: int
    capabilities: dict[str, Any] = field(default_factory=dict)
    limits: dict[str, int] = field(default_factory=dict)


@dataclass
class _PendingRequest:
    request_id: str
    job_id: str | None
    command: str = ""
    event: threading.Event = field(default_factory=threading.Event)
    first_event: threading.Event = field(default_factory=threading.Event)
    result: dict[str, Any] | None = None
    error: BaseException | None = None
    on_event: Callable[[dict[str, Any]], None] | None = None
    event_count: int = 0


_AUTH_HEADER_RE = re.compile(r"(?i)(\bauthorization\s*[:=]\s*)([^\r\n]+)")
_PROMPT_RE = re.compile(
    r"(?i)([\"']?(?:prompt|systemprompt)[\"']?\s*[:=]\s*)(\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\r\n,;]+)"
)
_BASE64_DATA_RE = re.compile(
    r"(?i)([\"']?(?:data|imagedata|base64)[\"']?\s*[:=]\s*)([\"']?[A-Za-z0-9+/]{24,}={0,2}[\"']?)"
)
_SECRET_RE = re.compile(
    r"(?i)(bearer\s+|(?:api[_-]?key|token|secret|password)\s*[:=]\s*)([^\s,;]+)"
)


def redact_diagnostic(text: str) -> str:
    value = _AUTH_HEADER_RE.sub(lambda match: f"{match.group(1)}[REDACTED]", text)
    value = _PROMPT_RE.sub(lambda match: f"{match.group(1)}[REDACTED]", value)
    value = _BASE64_DATA_RE.sub(lambda match: f"{match.group(1)}[REDACTED]", value)
    return _SECRET_RE.sub(lambda match: f"{match.group(1)}[REDACTED]", value)


_redact = redact_diagnostic


_OBSERVABILITY_LOGGER = logging.getLogger("screens_trans_chatbot.pi_sdk")
if not _OBSERVABILITY_LOGGER.handlers:
    _observability_handler = logging.StreamHandler()
    _observability_handler.setFormatter(logging.Formatter("%(message)s"))
    _OBSERVABILITY_LOGGER.addHandler(_observability_handler)
_OBSERVABILITY_LOGGER.setLevel(logging.INFO)
_OBSERVABILITY_LOGGER.propagate = False
_OBSERVABILITY_FIELDS = {
    "pi_backend_selected": {
        "backend",
        "reason_code",
        "bridge_version",
        "protocol_version",
        "sdk_version",
    },
    "pi_bridge_lifecycle": {
        "state",
        "process_id",
        "restart_count",
        "detail",
    },
    "pi_run_lifecycle": {
        "job_id",
        "stage",
        "duration_ms",
        "terminal_category",
        "side_effect_started",
    },
    "pi_session_lifecycle": {
        "session_key_hash",
        "action",
    },
    "pi_resource_limit": {
        "limit_name",
        "observed",
        "allowed",
    },
}
_OBSERVABILITY_INT_FIELDS = {"process_id", "restart_count", "observed", "allowed", "protocol_version"}
_OBSERVABILITY_FLOAT_FIELDS = {"duration_ms"}
_OBSERVABILITY_BOOL_FIELDS = {"side_effect_started"}


def sanitize_observability_event(event_type: str, fields: Mapping[str, Any]) -> dict[str, Any]:
    allowed = _OBSERVABILITY_FIELDS.get(str(event_type), set())
    record: dict[str, Any] = {"event": str(event_type)}
    for key in allowed:
        if key not in fields:
            continue
        value = fields[key]
        if key in _OBSERVABILITY_INT_FIELDS:
            try:
                record[key] = int(value)
            except (TypeError, ValueError):
                continue
        elif key in _OBSERVABILITY_FLOAT_FIELDS:
            try:
                record[key] = max(0.0, float(value))
            except (TypeError, ValueError):
                continue
        elif key in _OBSERVABILITY_BOOL_FIELDS:
            record[key] = bool(value)
        else:
            limit = 512 if key == "detail" else 160
            record[key] = redact_diagnostic(str(value))[:limit]
    return record


def emit_observability(
    sink: Callable[[dict[str, Any]], None] | None,
    event_type: str,
    **fields: Any,
) -> None:
    record = sanitize_observability_event(event_type, fields)
    try:
        if sink is not None:
            sink(record)
        else:
            _OBSERVABILITY_LOGGER.info(json.dumps(record, separators=(",", ":"), ensure_ascii=False))
    except Exception:
        return


class PiSdkBridgeTransport:
    DEFAULT_MAX_FRAME_BYTES = 1024 * 1024
    DEFAULT_MAX_RUN_FRAME_BYTES = 28 * 1024 * 1024
    DEFAULT_MAX_IMAGE_BYTES = 20 * 1024 * 1024
    EXPECTED_SDK_PACKAGE = "@earendil-works/pi-coding-agent"
    EXPECTED_SDK_VERSION = "0.84.2"

    NEW = "NEW"
    STARTING = "STARTING"
    READY = "READY"
    FAILED = "FAILED"
    CLOSED = "CLOSED"

    def __init__(
        self,
        *,
        node_executable: str | None = None,
        bridge_path: str | None = None,
        startup_timeout_ms: int = 15000,
        shutdown_timeout_ms: int = 3000,
        protocol_version: int = 1,
        hello_payload: Mapping[str, Any] | None = None,
        stderr_max_lines: int = 40,
        stderr_max_bytes: int = 8192,
        max_active_jobs: int = 32,
        max_events_per_job: int = 2048,
        max_restarts_per_window: int = 1,
        restart_window_ms: int = 30000,
        restart_cooldown_ms: int = 60000,
        monotonic: Callable[[], float] = time.monotonic,
        observability_sink: Callable[[dict[str, Any]], None] | None = None,
    ):
        root = Path(__file__).resolve().parents[2]
        self.project_root = str(root)
        self.node_executable = node_executable or (sys.executable if not bridge_path else (shutil.which("node") or "node"))
        self.bridge_path = bridge_path or str(Path(__file__).with_name("pi_rpc_bridge.py"))
        self.session_dir = str(root / "data" / "pi-sdk-sessions")
        self.startup_timeout_ms = int(startup_timeout_ms)
        self.shutdown_timeout_ms = int(shutdown_timeout_ms)
        self.protocol_version = int(protocol_version)
        self.hello_payload = dict(hello_payload or {})
        self.stderr_max_lines = max(1, int(stderr_max_lines))
        self.stderr_max_bytes = max(256, int(stderr_max_bytes))
        self.max_active_jobs = max(1, int(max_active_jobs))
        self.max_events_per_job = max(1, int(max_events_per_job))
        self.max_restarts_per_window = max(0, int(max_restarts_per_window))
        self.restart_window_ms = max(1, int(restart_window_ms))
        self.restart_cooldown_ms = max(1, int(restart_cooldown_ms))
        self._monotonic = monotonic
        self.observability_sink = observability_sink
        self.max_frame_bytes = self.DEFAULT_MAX_FRAME_BYTES
        self.max_run_frame_bytes = self.DEFAULT_MAX_RUN_FRAME_BYTES
        self.max_image_bytes = self.DEFAULT_MAX_IMAGE_BYTES

        self._state = self.NEW
        self._state_lock = threading.RLock()
        self._ready_event = threading.Event()
        self._writer_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._pending: dict[str, _PendingRequest] = {}
        self._pending_by_job: dict[str, _PendingRequest] = {}
        self._active_run_jobs: set[str] = set()
        self._stderr_lock = threading.Lock()
        self._stderr_lines: collections.deque[str] = collections.deque(maxlen=self.stderr_max_lines)
        self._process: subprocess.Popen | None = None
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._hello: BridgeHello | None = None
        self._failure: BaseException | None = None
        self._counter = 0
        self._prefix = secrets.token_hex(5)
        self._spawn_count = 0
        self._restart_times: collections.deque[float] = collections.deque()
        self._circuit_open_until = 0.0

    @property
    def spawn_count(self) -> int:
        return self._spawn_count

    @property
    def pending_count(self) -> int:
        with self._pending_lock:
            return len(self._pending)

    @property
    def reader_threads_alive(self) -> bool:
        return any(
            thread is not None and thread.is_alive()
            for thread in (self._stdout_thread, self._stderr_thread)
        )

    @property
    def state(self) -> str:
        with self._state_lock:
            return self._state

    def start(self) -> BridgeHello:
        owner = False
        with self._state_lock:
            if self._state == self.READY and self._hello is not None:
                return self._hello
            if self._state == self.CLOSED:
                raise PiBridgeExited("bridge transport is closed")
            if self._state == self.FAILED:
                now = self._monotonic()
                if self._circuit_open_until:
                    if now < self._circuit_open_until:
                        raise PiBridgeUnavailable("bridge restart cooldown is active")
                    self._circuit_open_until = 0.0
                    self._restart_times.clear()
                window_seconds = self.restart_window_ms / 1000
                while self._restart_times and now - self._restart_times[0] >= window_seconds:
                    self._restart_times.popleft()
                if len(self._restart_times) >= self.max_restarts_per_window:
                    self._circuit_open_until = now + (self.restart_cooldown_ms / 1000)
                    raise PiBridgeUnavailable("bridge restart cooldown is active")
                self._restart_times.append(now)
                self._failure = None
                self._hello = None
                self._state = self.STARTING
                self._ready_event.clear()
                owner = True
            elif self._state == self.NEW:
                self._state = self.STARTING
                self._ready_event.clear()
                owner = True

        if not owner:
            if not self._ready_event.wait(self.startup_timeout_ms / 1000):
                raise PiBridgeTimeout("bridge startup timed out")
            with self._state_lock:
                if self._state == self.READY and self._hello is not None:
                    return self._hello
                raise self._as_bridge_failure(self._failure)

        try:
            self._spawn()
            data = self._request_started(
                "hello",
                self.hello_payload,
                timeout_ms=self.startup_timeout_ms,
            )
            protocols = data.get("protocols") if isinstance(data, dict) else None
            if not isinstance(protocols, list) or self.protocol_version not in protocols:
                raise PiBridgeIncompatibleProtocol(
                    f"bridge does not support protocol {self.protocol_version}"
                )
            sdk = data.get("sdk") if isinstance(data.get("sdk"), dict) else {}
            bridge = data.get("bridge") if isinstance(data.get("bridge"), dict) else {}
            if str(sdk.get("package") or "") != self.EXPECTED_SDK_PACKAGE:
                raise PiBridgeIncompatibleProtocol("bridge SDK package is incompatible")
            if str(sdk.get("version") or "") != self.EXPECTED_SDK_VERSION:
                raise PiBridgeIncompatibleProtocol("bridge SDK version is incompatible")
            raw_limits = data.get("limits") if isinstance(data.get("limits"), dict) else {}
            max_frame_bytes = self._positive_limit(raw_limits.get("maxFrameBytes"), self.DEFAULT_MAX_FRAME_BYTES)
            max_run_frame_bytes = self._positive_limit(raw_limits.get("maxRunFrameBytes"), self.DEFAULT_MAX_RUN_FRAME_BYTES)
            max_image_bytes = self._positive_limit(raw_limits.get("maxImageBytes"), self.DEFAULT_MAX_IMAGE_BYTES)
            if max_run_frame_bytes < max_frame_bytes:
                raise PiBridgeIncompatibleProtocol("bridge run frame limit is smaller than normal frame limit")
            hello = BridgeHello(
                protocol=self.protocol_version,
                bridge_version=str(bridge.get("version") or ""),
                sdk_package=str(sdk.get("package") or ""),
                sdk_version=str(sdk.get("version") or ""),
                pid=int(data.get("pid") or 0),
                capabilities=dict(data.get("capabilities") or {}),
                limits={
                    "maxFrameBytes": max_frame_bytes,
                    "maxRunFrameBytes": max_run_frame_bytes,
                    "maxImageBytes": max_image_bytes,
                },
            )
            with self._state_lock:
                self.max_frame_bytes = max_frame_bytes
                self.max_run_frame_bytes = max_run_frame_bytes
                self.max_image_bytes = max_image_bytes
                self._hello = hello
                self._state = self.READY
                self._ready_event.set()
            emit_observability(
                self.observability_sink,
                "pi_bridge_lifecycle",
                state="started",
                process_id=hello.pid,
                restart_count=max(0, self._spawn_count - 1),
            )
            return hello
        except BaseException as exc:
            failure = self._as_bridge_failure(exc)
            with self._state_lock:
                self._failure = failure
                self._state = self.FAILED
                self._ready_event.set()
            self._terminate_process(self.shutdown_timeout_ms)
            raise failure

    def probe_health(self, model_ref: str = "") -> dict[str, Any]:
        hello = self.start()
        try:
            completed = subprocess.run(
                [self.node_executable, "--version"],
                check=True,
                capture_output=True,
                text=True,
                timeout=max(1, self.startup_timeout_ms) / 1000,
                shell=False,
            )
            node_version = completed.stdout.strip() or completed.stderr.strip()
        except (OSError, subprocess.SubprocessError) as exc:
            raise PiBridgeUnavailable("failed to read Node version") from exc
        health_payload = {"model": str(model_ref)} if str(model_ref).strip() else {}
        health = self.request("health", health_payload, timeout_ms=self.startup_timeout_ms)
        return {"nodeVersion": node_version, "hello": hello, "health": health}

    def request(
        self,
        command: str,
        payload: Mapping[str, Any],
        *,
        job_id: str | None = None,
        session_key: str | None = None,
        timeout_ms: int | None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        first_event_timeout_ms: int | None = None,
        cancel_timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        self.start()
        return self._request_started(
            command,
            payload,
            job_id=job_id,
            session_key=session_key,
            timeout_ms=timeout_ms,
            on_event=on_event,
            first_event_timeout_ms=first_event_timeout_ms,
            cancel_timeout_ms=cancel_timeout_ms,
        )

    def notify(
        self,
        command: str,
        payload: Mapping[str, Any],
        *,
        job_id: str | None = None,
    ) -> str:
        self.start()
        request_id = self._next_request_id()
        frame = {
            "v": self.protocol_version,
            "type": command,
            "requestId": request_id,
            **({"jobId": job_id} if job_id else {}),
            "payload": dict(payload),
        }
        self._write_frame(frame)
        return request_id

    def shutdown(self, timeout_ms: int = 3000) -> None:
        timeout_ms = max(1, int(timeout_ms or self.shutdown_timeout_ms))
        with self._state_lock:
            if self._state == self.CLOSED:
                return
            process = self._process
            was_ready = self._state == self.READY
            self._state = self.CLOSED
            self._ready_event.set()

        if process is not None and process.poll() is None and was_ready:
            try:
                with self._writer_lock:
                    if process.stdin is not None:
                        process.stdin.write(
                            json.dumps({"v": self.protocol_version, "type": "shutdown"}, separators=(",", ":")).encode("utf-8") + b"\n"
                        )
                        process.stdin.flush()
            except Exception:
                pass

        self._terminate_process(timeout_ms)
        error = PiBridgeExited("bridge transport closed")
        self._fail_all_pending(error)
        self._join_threads(timeout_ms)
        if process is not None:
            emit_observability(
                self.observability_sink,
                "pi_bridge_lifecycle",
                state="stopped",
                process_id=int(process.pid or 0),
                restart_count=max(0, self._spawn_count - 1),
            )

    def _spawn(self) -> None:
        if self._spawn_count:
            with self._stderr_lock:
                self._stderr_lines.clear()
        node = shutil.which(self.node_executable) if not os.path.isabs(self.node_executable) else self.node_executable
        if not node or not os.path.exists(node):
            raise PiBridgeUnavailable("Node executable is unavailable")
        bridge = os.path.abspath(self.bridge_path)
        if not os.path.exists(bridge):
            raise PiBridgeUnavailable("Pi SDK bridge entry point is unavailable")
        argv = [node, bridge]
        try:
            process = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                bufsize=0,
            )
        except OSError as exc:
            raise PiBridgeUnavailable("failed to launch Pi SDK bridge") from exc
        self._process = process
        self._spawn_count += 1
        self._stdout_thread = threading.Thread(
            target=self._stdout_reader,
            name="pi-sdk-stdout",
            daemon=True,
        )
        self._stderr_thread = threading.Thread(
            target=self._stderr_reader,
            name="pi-sdk-stderr",
            daemon=True,
        )
        self._stdout_thread.start()
        self._stderr_thread.start()

    def _request_started(
        self,
        command: str,
        payload: Mapping[str, Any],
        *,
        job_id: str | None = None,
        session_key: str | None = None,
        timeout_ms: int | None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        first_event_timeout_ms: int | None = None,
        cancel_timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        request_id = self._next_request_id()
        pending = _PendingRequest(request_id=request_id, job_id=job_id, command=command, on_event=on_event)
        with self._pending_lock:
            if command == "run" and job_id:
                if job_id not in self._active_run_jobs and len(self._active_run_jobs) >= self.max_active_jobs:
                    emit_observability(
                        self.observability_sink,
                        "pi_resource_limit",
                        limit_name="active_jobs",
                        observed=len(self._active_run_jobs) + 1,
                        allowed=self.max_active_jobs,
                    )
                    raise PiBridgeRemoteError("RESOURCE_LIMIT", "too many active bridge jobs")
                self._active_run_jobs.add(job_id)
            self._pending[request_id] = pending
            if command == "run" and job_id:
                self._pending_by_job[job_id] = pending
        frame = {
            "v": self.protocol_version,
            "type": command,
            "requestId": request_id,
            **({"jobId": job_id} if job_id else {}),
            **({"sessionKey": session_key} if session_key else {}),
        }
        if command == "approval_response":
            frame.update(dict(payload))
        else:
            frame["payload"] = dict(payload)
        try:
            self._write_frame(frame)
        except BaseException as exc:
            self._remove_pending(pending)
            raise self._as_bridge_failure(exc)

        if command == "run" and job_id and first_event_timeout_ms is not None:
            deadline = self._monotonic() + (max(1, int(first_event_timeout_ms)) / 1000)
            while not pending.first_event.is_set() and not pending.event.is_set():
                remaining = deadline - self._monotonic()
                if remaining <= 0:
                    break
                pending.first_event.wait(min(remaining, 0.01))
            if not pending.first_event.is_set() and not pending.event.is_set():
                cancel_timeout = max(1, int(cancel_timeout_ms or self.shutdown_timeout_ms))
                try:
                    self._request_started(
                        "cancel",
                        {"reason": "first_event_timeout"},
                        job_id=job_id,
                        timeout_ms=cancel_timeout,
                    )
                except PiBridgeError:
                    pass
                quiescent = pending.event.wait(cancel_timeout / 1000)
                self._remove_pending(pending)
                raise PiBridgeFirstEventTimeout(
                    "bridge run produced no event before first-event deadline",
                    quiescent=quiescent,
                )

        completed = pending.event.wait() if timeout_ms is None else pending.event.wait(max(1, int(timeout_ms)) / 1000)
        if not completed:
            self._remove_pending(pending)
            message = f"bridge request {command} timed out after {timeout_ms} ms"
            tail = self._stderr_tail()
            if tail:
                message += f"; stderr: {redact_diagnostic(tail)}"
            raise PiBridgeTimeout(message)
        self._remove_pending(pending)
        if pending.error is not None:
            raise pending.error
        return dict(pending.result or {})

    @staticmethod
    def _positive_limit(value: Any, default: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed > 0 else default

    def _next_request_id(self) -> str:
        with self._state_lock:
            self._counter += 1
            return f"py-{self._prefix}-{self._counter}"

    def _write_frame(self, value: Mapping[str, Any]) -> None:
        process = self._process
        if process is None or process.poll() is not None or process.stdin is None:
            raise self._exit_error(process)
        data = json.dumps(dict(value), separators=(",", ":"), ensure_ascii=False).encode("utf-8") + b"\n"
        frame_limit = self.max_run_frame_bytes if value.get("type") == "run" else self.max_frame_bytes
        if len(data) - 1 > frame_limit:
            raise PiBridgeFrameTooLarge(f"outgoing {value.get('type') or 'unknown'} frame exceeds negotiated limit")
        try:
            with self._writer_lock:
                process.stdin.write(data)
                process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise self._exit_error(process) from exc

    def _stdout_reader(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        buffer = bytearray()
        try:
            while True:
                chunk = os.read(process.stdout.fileno(), 4096)
                if not chunk:
                    break
                buffer.extend(chunk)
                while True:
                    try:
                        index = buffer.index(0x0A)
                    except ValueError:
                        break
                    raw = bytes(buffer[:index])
                    del buffer[: index + 1]
                    if raw.endswith(b"\r"):
                        raw = raw[:-1]
                    if not raw:
                        continue
                    try:
                        frame = json.loads(raw.decode("utf-8", errors="strict"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        self._fail_all_pending(PiBridgeMalformedFrame("bridge emitted malformed JSONL"))
                        continue
                    self._dispatch_frame(frame)
        except OSError:
            pass
        finally:
            if buffer:
                self._fail_all_pending(PiBridgeMalformedFrame("bridge emitted an unterminated frame"))
            with self._state_lock:
                if self._process is not process:
                    return
                state = self._state
            if state not in {self.CLOSED, self.FAILED}:
                error = self._exit_error(process)
                with self._state_lock:
                    if self._process is not process:
                        return
                    self._failure = error
                    self._state = self.FAILED
                    self._ready_event.set()
                emit_observability(
                    self.observability_sink,
                    "pi_bridge_lifecycle",
                    state="crashed",
                    process_id=int(process.pid or 0),
                    restart_count=max(0, self._spawn_count - 1),
                    detail=str(error),
                )
                self._fail_all_pending(error)

    def _stderr_reader(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        try:
            while True:
                raw = process.stderr.readline()
                if not raw:
                    break
                if self._process is not process:
                    break
                line = _redact(raw.decode("utf-8", errors="replace").rstrip())
                with self._stderr_lock:
                    self._stderr_lines.append(line[-self.stderr_max_bytes :])
                    self._trim_stderr_bytes()
        except OSError:
            return

    def _trim_stderr_bytes(self) -> None:
        total = sum(len(line.encode("utf-8", errors="replace")) for line in self._stderr_lines)
        while self._stderr_lines and total > self.stderr_max_bytes:
            removed = self._stderr_lines.popleft()
            total -= len(removed.encode("utf-8", errors="replace"))

    def _stderr_tail(self) -> str:
        with self._stderr_lock:
            return " | ".join(self._stderr_lines)

    def _dispatch_frame(self, frame: Any) -> None:
        if not isinstance(frame, dict):
            self._fail_all_pending(PiBridgeMalformedFrame("bridge frame must be an object"))
            return
        frame_type = frame.get("type")
        if frame_type == "event":
            job_id = str(frame.get("jobId") or "")
            with self._pending_lock:
                pending = self._pending_by_job.get(job_id)
                if pending is None or pending.event.is_set():
                    return
                if pending.event_count >= self.max_events_per_job:
                    emit_observability(
                        self.observability_sink,
                        "pi_resource_limit",
                        limit_name="events_per_job",
                        observed=pending.event_count + 1,
                        allowed=self.max_events_per_job,
                    )
                    pending.error = PiBridgeRemoteError("RESOURCE_LIMIT", "bridge job exceeded event limit")
                    pending.event.set()
                    return
                pending.event_count += 1
                pending.first_event.set()
                callback = pending.on_event
            if callback is not None:
                try:
                    callback(dict(frame.get("event") or {}))
                except Exception:
                    pass
            return
        if frame_type not in {"response", "result"}:
            return
        request_id = str(frame.get("requestId") or "")
        if not request_id:
            return
        with self._pending_lock:
            pending = self._pending.get(request_id)
        if pending is None or pending.event.is_set():
            return
        if frame.get("ok") is False:
            error = frame.get("error") if isinstance(frame.get("error"), dict) else {}
            pending.error = PiBridgeRemoteError(
                str(error.get("code") or "REMOTE_ERROR"),
                str(error.get("message") or "bridge request failed"),
            )
        else:
            pending.result = dict(frame.get("data") or {})
        pending.event.set()

    def _remove_pending(self, pending: _PendingRequest) -> None:
        with self._pending_lock:
            if self._pending.get(pending.request_id) is pending:
                self._pending.pop(pending.request_id, None)
            if pending.job_id and self._pending_by_job.get(pending.job_id) is pending:
                self._pending_by_job.pop(pending.job_id, None)
            if pending.command == "run" and pending.job_id:
                self._active_run_jobs.discard(pending.job_id)

    def _fail_all_pending(self, error: BaseException) -> None:
        with self._pending_lock:
            pending = list(self._pending.values())
        for item in pending:
            if item.event.is_set():
                continue
            item.error = error
            item.event.set()

    def _exit_error(self, process: subprocess.Popen | None) -> PiBridgeExited:
        code = None
        if process is not None:
            code = process.poll()
            if code is None:
                try:
                    code = process.wait(timeout=0.05)
                except subprocess.TimeoutExpired:
                    code = None
        if self._stderr_thread is not None and self._stderr_thread is not threading.current_thread():
            self._stderr_thread.join(timeout=0.15)
        tail = self._stderr_tail()
        detail = f"bridge exited with code {code}" if code is not None else "bridge process is unavailable"
        if tail:
            detail += f"; stderr: {tail}"
        return PiBridgeExited(detail)

    def _as_bridge_failure(self, exc: BaseException | None) -> PiBridgeError:
        if isinstance(exc, PiBridgeError):
            return exc
        if exc is None:
            return PiBridgeExited("bridge failed")
        return PiBridgeExited("bridge failed")

    def _terminate_process(self, timeout_ms: int) -> None:
        process = self._process
        if process is None or process.poll() is not None:
            return
        deadline = max(0.001, timeout_ms / 1000)
        try:
            process.wait(timeout=deadline)
            return
        except subprocess.TimeoutExpired:
            pass
        try:
            process.terminate()
            process.wait(timeout=deadline)
            return
        except (OSError, subprocess.TimeoutExpired):
            pass
        try:
            process.kill()
            process.wait(timeout=deadline)
        except (OSError, subprocess.TimeoutExpired):
            pass

    def _join_threads(self, timeout_ms: int) -> None:
        deadline = time.monotonic() + max(0.001, timeout_ms / 1000)
        for thread in (self._stdout_thread, self._stderr_thread):
            if thread is None or thread is threading.current_thread():
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            thread.join(remaining)
