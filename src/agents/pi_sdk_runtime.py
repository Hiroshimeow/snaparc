from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
import threading
import time
from pathlib import Path
from typing import Any

from src.agents.pi_contract import PiEventSink, PiRunRequest, PiRunResult, PiRuntime, PiRuntimeConfig
from src.agents.pi_sdk_transport import (
    PiBridgeError,
    PiBridgeExited,
    PiBridgeFirstEventTimeout,
    PiBridgeRemoteError,
    PiSdkBridgeTransport,
    emit_observability,
)


DEFAULT_MAX_IMAGE_BYTES = 20 * 1024 * 1024
DEFAULT_MAX_RUN_FRAME_BYTES = 28 * 1024 * 1024
_IMAGE_MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
_CONTEXT_ATTACHMENT_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".log", ".pdf"}


class PiSdkAttachmentError(ValueError):
    pass


def _attachment_name(attachment: dict[str, Any], index: int) -> str:
    label = str(attachment.get("label") or "").strip()
    if label:
        return label[:160]
    path_value = str(attachment.get("path") or "")
    name = Path(path_value).name if path_value else ""
    return (name or f"attachment-{index + 1}")[:160]


def _attachment_error(index: int, display_name: str, message: str) -> PiSdkAttachmentError:
    return PiSdkAttachmentError(f"Attachment {index + 1} ({display_name}): {message}")


def serialize_sdk_attachments(
    attachments: list[dict[str, Any]],
    *,
    max_image_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
) -> list[dict[str, Any]]:
    encoded: list[dict[str, Any]] = []
    limit = max(1, int(max_image_bytes))
    for index, raw in enumerate(attachments or []):
        attachment = raw if isinstance(raw, dict) else {}
        display_name = _attachment_name(attachment, index)
        path_value = str(attachment.get("path") or "")
        suffix = Path(path_value).suffix.lower()
        if suffix in _CONTEXT_ATTACHMENT_EXTENSIONS:
            continue
        mime_type = _IMAGE_MIME_TYPES.get(suffix)
        if mime_type is None:
            raise _attachment_error(index, display_name, "unsupported attachment type")
        try:
            before = os.stat(path_value)
        except (OSError, ValueError):
            raise _attachment_error(index, display_name, "file is missing or unreadable") from None
        if not stat.S_ISREG(before.st_mode):
            raise _attachment_error(index, display_name, "path is not a regular file")
        if before.st_size > limit:
            raise _attachment_error(index, display_name, f"image is too large (maximum {limit} bytes)")
        try:
            with open(path_value, "rb") as handle:
                data = handle.read(limit + 1)
        except (OSError, ValueError):
            raise _attachment_error(index, display_name, "file is unreadable") from None
        if len(data) > limit:
            raise _attachment_error(index, display_name, f"image is too large (maximum {limit} bytes)")
        try:
            after = os.stat(path_value)
        except (OSError, ValueError):
            raise _attachment_error(index, display_name, "file changed while reading") from None
        if (
            after.st_size != before.st_size
            or after.st_mtime_ns != before.st_mtime_ns
            or len(data) != before.st_size
        ):
            raise _attachment_error(index, display_name, "file changed while reading")
        encoded.append(
            {
                "displayName": display_name,
                "mimeType": mime_type,
                "byteLength": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "data": base64.b64encode(data).decode("ascii"),
            }
        )
    return encoded


def _session_key_hash(application_session_id: str, responder_name: str) -> str:
    raw = f"{application_session_id}::{responder_name.strip().lower()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _run_frame_size(payload: dict[str, Any], job_id: str, session_key: str) -> int:
    frame = {
        "v": 1,
        "type": "run",
        "requestId": "py-" + ("x" * 48),
        "jobId": job_id,
        "sessionKey": session_key,
        "payload": payload,
    }
    return len(json.dumps(frame, separators=(",", ":"), ensure_ascii=False).encode("utf-8")) + 1


class PiSdkRuntime:
    def __init__(
        self,
        config: PiRuntimeConfig,
        emit_event: PiEventSink,
        transport: PiSdkBridgeTransport,
        *,
        observability_sink=None,
        monotonic=time.monotonic,
    ):
        self.config = config
        self.emit_event = emit_event
        self.transport = transport
        self.observability_sink = observability_sink
        self._monotonic = monotonic
        self._lock = threading.RLock()
        self._active_jobs: set[str] = set()
        self._current_job_id = ""
        self._approvals: dict[tuple[str, str], dict[str, Any]] = {}
        self._canceled_jobs: set[str] = set()
        self._terminal_jobs: set[str] = set()
        self._side_effect_started_jobs: set[str] = set()
        self._closed = False

    def run(self, request: PiRunRequest, cancel_event: threading.Event) -> PiRunResult:
        job_id = request.job_id
        queued_at = self._monotonic()
        emit_observability(
            self.observability_sink,
            "pi_run_lifecycle",
            job_id=job_id,
            stage="queued",
            duration_ms=0.0,
        )
        with self._lock:
            if self._closed:
                emit_observability(
                    self.observability_sink,
                    "pi_run_lifecycle",
                    job_id=job_id,
                    stage="end",
                    duration_ms=0.0,
                    terminal_category="closed",
                    side_effect_started=False,
                )
                return PiRunResult(error="Pi SDK runtime is closed")
            self._active_jobs.add(job_id)
            self._canceled_jobs.discard(job_id)
            self._terminal_jobs.discard(job_id)
            self._side_effect_started_jobs.discard(job_id)
            self._current_job_id = job_id

        started_at = self._monotonic()
        emit_observability(
            self.observability_sink,
            "pi_run_lifecycle",
            job_id=job_id,
            stage="start",
            duration_ms=(started_at - queued_at) * 1000,
        )
        terminal_category = "error"
        first_event_emitted = False
        session_key_hash = ""

        try:
            if cancel_event.is_set():
                terminal_category = "canceled"
                with self._lock:
                    self._canceled_jobs.add(job_id)
                return PiRunResult(error="[Canceled]")

            responder_name = request.responder_name.strip().lower()
            metadata = request.metadata if isinstance(request.metadata, dict) else {}
            try:
                encoded_attachments = serialize_sdk_attachments(
                    list(request.attachments),
                    max_image_bytes=int(getattr(self.transport, "max_image_bytes", DEFAULT_MAX_IMAGE_BYTES)),
                )
            except PiSdkAttachmentError as exc:
                terminal_category = "validation_error"
                return PiRunResult(error=f"Pi SDK attachment error: {exc}", fallback_to_llm=self.config.fallback_to_llm)

            cwd = str(metadata.get("cwd") or os.getcwd())
            payload = {
                "applicationSessionId": request.session_id,
                "responderName": responder_name,
                "messageId": request.message_id,
                "prompt": request.prompt_text,
                "systemPrompt": request.system_prompt,
                "history": list(request.history),
                "attachments": encoded_attachments,
                "cwd": cwd,
                "model": str(metadata.get("model") or metadata.get("model_name") or ""),
                "thinkingLevel": str(metadata.get("thinkingLevel") or metadata.get("thinking_level") or "medium"),
                "toolPolicy": self.config.tool_policy,
                "trustedRoots": list(self.config.trusted_roots),
                "approvalTimeoutMs": max(1, int(self.config.approval_timeout_ms)),
            }
            session_key = f"{request.session_id}::{responder_name}"
            session_key_hash = _session_key_hash(request.session_id, responder_name)
            max_run_frame_bytes = int(getattr(self.transport, "max_run_frame_bytes", DEFAULT_MAX_RUN_FRAME_BYTES))
            if _run_frame_size(payload, job_id, session_key) > max_run_frame_bytes:
                terminal_category = "resource_limit"
                return PiRunResult(
                    error="Pi SDK attachment error: run frame exceeds negotiated size limit",
                    fallback_to_llm=self.config.fallback_to_llm,
                )

            def on_event(event: dict[str, Any]) -> None:
                nonlocal first_event_emitted
                if not first_event_emitted:
                    first_event_emitted = True
                    emit_observability(
                        self.observability_sink,
                        "pi_run_lifecycle",
                        job_id=job_id,
                        stage="first_event",
                        duration_ms=(self._monotonic() - started_at) * 1000,
                    )
                self._handle_event(job_id, event)

            try:
                data = self.transport.request(
                    "run",
                    payload,
                    job_id=job_id,
                    session_key=session_key,
                    timeout_ms=None,
                    on_event=on_event,
                    first_event_timeout_ms=self.config.first_event_timeout_ms,
                    cancel_timeout_ms=self.config.shutdown_timeout_ms,
                )
            except PiBridgeFirstEventTimeout as exc:
                with self._lock:
                    side_effect_started = job_id in self._side_effect_started_jobs
                if side_effect_started or not exc.quiescent:
                    terminal_category = "ambiguous"
                    return PiRunResult(
                        error=(
                            "Pi SDK first-event timeout could not establish safe quiescence; "
                            "automatic fallback is disabled."
                        ),
                        side_effect_started=side_effect_started,
                    )
                terminal_category = "first_event_timeout"
                return PiRunResult(
                    error="Pi SDK bridge error: first event timed out and the SDK job was canceled safely",
                    fallback_to_llm=self.config.fallback_to_llm,
                    side_effect_started=False,
                )
            except PiBridgeRemoteError as exc:
                with self._lock:
                    canceled = job_id in self._canceled_jobs
                    side_effect_started = job_id in self._side_effect_started_jobs
                if canceled or exc.code == "CANCELED":
                    terminal_category = "canceled"
                    return PiRunResult(error="[Canceled]", side_effect_started=side_effect_started)
                terminal_category = "resource_limit" if exc.code == "RESOURCE_LIMIT" else "bridge_error"
                return PiRunResult(
                    error=f"Pi SDK bridge error: {exc}",
                    fallback_to_llm=self.config.fallback_to_llm and not side_effect_started,
                    side_effect_started=side_effect_started,
                )
            except PiBridgeExited as exc:
                with self._lock:
                    canceled = job_id in self._canceled_jobs
                    side_effect_started = job_id in self._side_effect_started_jobs
                if canceled:
                    terminal_category = "canceled"
                    return PiRunResult(error="[Canceled]", side_effect_started=side_effect_started)
                if side_effect_started:
                    terminal_category = "ambiguous"
                    return PiRunResult(
                        error=(
                            "Pi SDK bridge crashed after a mutating tool started; "
                            "the outcome is ambiguous and automatic fallback is disabled."
                        ),
                        side_effect_started=True,
                    )
                terminal_category = "bridge_crash"
                return PiRunResult(
                    error=f"Pi SDK bridge error: {exc}",
                    fallback_to_llm=self.config.fallback_to_llm and self.config.crash_fallback_to_llm,
                    side_effect_started=False,
                )
            except PiBridgeError as exc:
                with self._lock:
                    canceled = job_id in self._canceled_jobs
                    side_effect_started = job_id in self._side_effect_started_jobs
                if canceled:
                    terminal_category = "canceled"
                    return PiRunResult(error="[Canceled]", side_effect_started=side_effect_started)
                terminal_category = "bridge_error"
                return PiRunResult(
                    error=f"Pi SDK bridge error: {exc}",
                    fallback_to_llm=self.config.fallback_to_llm and not side_effect_started,
                    side_effect_started=side_effect_started,
                )

            with self._lock:
                canceled = job_id in self._canceled_jobs
            if canceled or cancel_event.is_set():
                terminal_category = "canceled"
                return PiRunResult(error="[Canceled]")

            result_metadata = {
                key: value
                for key, value in data.items()
                if key not in {"text", "error", "fallbackToLlm", "sideEffectStarted"}
            }
            with self._lock:
                side_effect_started = job_id in self._side_effect_started_jobs or bool(data.get("sideEffectStarted", False))
                if side_effect_started:
                    self._side_effect_started_jobs.add(job_id)
            session_action = str(data.get("piSessionAction") or "")
            if session_action in {"created", "opened", "rebuilt"} and session_key_hash:
                emit_observability(
                    self.observability_sink,
                    "pi_session_lifecycle",
                    session_key_hash=session_key_hash,
                    action=session_action,
                )
            terminal_category = "error" if str(data.get("error") or "") else "success"
            return PiRunResult(
                text=str(data.get("text") or ""),
                error=str(data.get("error") or ""),
                fallback_to_llm=bool(data.get("fallbackToLlm", False)) and not side_effect_started,
                side_effect_started=side_effect_started,
                metadata=result_metadata,
            )
        finally:
            with self._lock:
                side_effect_started = job_id in self._side_effect_started_jobs
            emit_observability(
                self.observability_sink,
                "pi_run_lifecycle",
                job_id=job_id,
                stage="end",
                duration_ms=(self._monotonic() - started_at) * 1000,
                terminal_category=terminal_category,
                side_effect_started=side_effect_started,
            )
            with self._lock:
                self._active_jobs.discard(job_id)
                if self._current_job_id == job_id:
                    self._current_job_id = ""
                for key in [key for key in self._approvals if key[0] == job_id]:
                    self._approvals.pop(key, None)
                self._canceled_jobs.discard(job_id)
                self._side_effect_started_jobs.discard(job_id)
                self._terminal_jobs.add(job_id)

    def cancel(self, reason: str = "") -> None:
        with self._lock:
            job_id = self._current_job_id
            if not job_id or job_id in self._canceled_jobs or job_id in self._terminal_jobs:
                return
            self._canceled_jobs.add(job_id)
            for key in [key for key in self._approvals if key[0] == job_id]:
                self._approvals.pop(key, None)
        try:
            self.transport.notify("cancel", {"reason": reason or "job_canceled"}, job_id=job_id)
        except PiBridgeError:
            return

    def resolve_approval(
        self,
        job_id: str,
        approval_id: str,
        approved: bool,
        reason: str = "",
        payload: dict[str, Any] | None = None,
    ) -> bool:
        key = (job_id, approval_id)
        with self._lock:
            if job_id in self._canceled_jobs or job_id in self._terminal_jobs or key not in self._approvals:
                return False
            self._approvals.pop(key, None)
        try:
            result = self.transport.request(
                "approval_response",
                {
                    "approvalId": approval_id,
                    "approved": bool(approved),
                    "reason": reason or ("approved" if approved else "denied"),
                    "value": dict(payload or {}),
                },
                job_id=job_id,
                timeout_ms=max(1, int(self.config.approval_timeout_ms)),
            )
            return bool(result.get("accepted", False))
        except PiBridgeError:
            return False

    def close(self, timeout_ms: int = 3000) -> None:
        del timeout_ms
        with self._lock:
            self._closed = True
            self._active_jobs.clear()
            self._current_job_id = ""
            self._approvals.clear()
            self._canceled_jobs.clear()
            self._terminal_jobs.clear()
            self._side_effect_started_jobs.clear()

    def _handle_event(self, job_id: str, event: dict[str, Any]) -> None:
        with self._lock:
            if self._closed or job_id in self._canceled_jobs or job_id in self._terminal_jobs:
                return
        kind = str(event.get("kind") or "runtime_notice")
        if kind == "runtime_notice" and str(event.get("name") or "") == "side_effect_started":
            with self._lock:
                self._side_effect_started_jobs.add(job_id)
            self.emit_event("runtime_notice", metadata=dict(event))
            return
        if kind == "approval_request":
            approval_id = str(event.get("approvalId") or event.get("approval_id") or "")
            if approval_id:
                with self._lock:
                    self._approvals[(job_id, approval_id)] = dict(event)
            self.emit_event(
                "approval_request",
                metadata={
                    "kind": "approval_request",
                    "approval_id": approval_id,
                    "tool_call_id": str(event.get("toolCallId") or ""),
                    "tool_name": str(event.get("toolName") or ""),
                    "summary": str(event.get("summary") or ""),
                    "expires_at": str(event.get("expiresAt") or ""),
                    "ui_type": str(event.get("uiType") or ""),
                    "payload": event.get("payload") or {},
                    "status": "pending",
                },
            )
            return
        if kind == "message_delta":
            self.emit_event(
                "message_delta",
                text=str(event.get("text") or ""),
                metadata=dict(event),
                partial=bool(event.get("partial", True)),
            )
            return
        if kind == "tool_event" or kind.startswith("tool_"):
            metadata = dict(event)
            metadata.setdefault("kind", kind)
            self.emit_event("tool_event", metadata=metadata)
            return
        if kind == "thinking_delta":
            self.emit_event(
                "runtime_notice",
                text=str(event.get("text") or ""),
                metadata=dict(event),
                partial=bool(event.get("partial", True)),
            )
            return
        if kind in {"timeline_event", "runtime_warning"}:
            self.emit_event("runtime_notice", metadata=dict(event))
            return
        self.emit_event("runtime_notice", text=str(event.get("text") or ""), metadata={"kind": "runtime_warning"})


class PiSdkRuntimeFactory:
    def __init__(
        self,
        transport: PiSdkBridgeTransport | None = None,
        *,
        observability_sink=None,
        monotonic=time.monotonic,
        startup_timeout_ms=None,
        shutdown_timeout_ms=None,
    ):
        self.observability_sink = observability_sink
        self._monotonic = monotonic
        if transport is None:
            timeouts = {}
            if startup_timeout_ms is not None:
                timeouts["startup_timeout_ms"] = startup_timeout_ms
            if shutdown_timeout_ms is not None:
                timeouts["shutdown_timeout_ms"] = shutdown_timeout_ms
            transport = PiSdkBridgeTransport(
                observability_sink=observability_sink,
                monotonic=monotonic,
                **timeouts,
            )
        self.transport = transport
        self._lock = threading.Lock()
        self._shutdown = False

    def __call__(self, config: PiRuntimeConfig, emit_event: PiEventSink) -> PiRuntime:
        return PiSdkRuntime(
            config,
            emit_event,
            self.transport,
            observability_sink=self.observability_sink,
            monotonic=self._monotonic,
        )

    def start(self):
        return self.transport.start()

    def close_session(
        self,
        application_session_id: str,
        responder_name: str,
        *,
        timeout_ms: int = 3000,
    ) -> dict[str, Any]:
        normalized_responder = str(responder_name).strip().lower()
        result = self.transport.request(
            "close_session",
            {
                "applicationSessionId": str(application_session_id),
                "responderName": normalized_responder,
            },
            timeout_ms=max(1, int(timeout_ms)),
        )
        if bool(result.get("closed", False)):
            emit_observability(
                self.observability_sink,
                "pi_session_lifecycle",
                session_key_hash=_session_key_hash(str(application_session_id), normalized_responder),
                action="closed",
            )
        return result

    def shutdown(self, timeout_ms: int = 3000) -> None:
        with self._lock:
            if self._shutdown:
                return
            self._shutdown = True
        self.transport.shutdown(timeout_ms)
