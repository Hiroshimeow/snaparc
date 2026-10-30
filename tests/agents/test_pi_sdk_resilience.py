from __future__ import annotations

import threading
import time

from src.agents.pi_contract import PiRunRequest, PiRuntimeConfig
from src.agents.pi_sdk_runtime import PiSdkRuntimeFactory
from src.agents.pi_sdk_transport import PiBridgeExited, PiBridgeRemoteError


class RaceTransport:
    max_image_bytes = 20 * 1024 * 1024
    max_run_frame_bytes = 28 * 1024 * 1024

    def __init__(self):
        self.requests = []
        self.notifications = []
        self.run_started = threading.Event()
        self.release_run = threading.Event()
        self.run_result = {"text": "done", "sideEffectStarted": False}
        self.event_during_run = None
        self.run_error = None

    def request(
        self,
        command,
        payload,
        *,
        job_id=None,
        session_key=None,
        timeout_ms,
        on_event=None,
        first_event_timeout_ms=None,
        cancel_timeout_ms=None,
    ):
        self.requests.append((command, dict(payload), job_id, session_key, timeout_ms))
        if command == "run":
            self.run_started.set()
            if self.event_during_run is not None and on_event is not None:
                on_event(dict(self.event_during_run))
            self.release_run.wait(2)
            if self.run_error is not None:
                raise self.run_error
            return dict(self.run_result)
        if command == "approval_response":
            return {"accepted": True}
        return {"ok": True}

    def notify(self, command, payload, *, job_id=None):
        self.notifications.append((command, dict(payload), job_id))
        if command == "cancel":
            self.release_run.set()
        return f"n{len(self.notifications)}"

    def shutdown(self, timeout_ms=3000):
        return None


def make_request(job_id: str = "job-1") -> PiRunRequest:
    return PiRunRequest(
        job_id=job_id,
        session_id="session-1",
        message_id="message-1",
        responder_name="pi",
        prompt_text="inspect",
        metadata={"cwd": "C:/app", "model": "provider/model"},
    )


def test_runtime_cancel_is_idempotent_and_late_transport_result_becomes_canceled():
    transport = RaceTransport()
    runtime = PiSdkRuntimeFactory(transport=transport)(PiRuntimeConfig(), lambda *args, **kwargs: None)
    result_box = {}

    thread = threading.Thread(
        target=lambda: result_box.setdefault("result", runtime.run(make_request(), threading.Event())),
        daemon=True,
    )
    thread.start()
    assert transport.run_started.wait(1)

    runtime.cancel("user")
    runtime.cancel("again")
    thread.join(1)

    assert not thread.is_alive()
    assert transport.notifications == [("cancel", {"reason": "user"}, "job-1")]
    assert result_box["result"].error == "[Canceled]"
    assert result_box["result"].text == ""
    assert result_box["result"].fallback_to_llm is False


def test_runtime_clears_terminal_job_and_pending_approval_so_late_actions_are_ignored():
    transport = RaceTransport()
    transport.event_during_run = {
        "kind": "approval_request",
        "approvalId": "appr-1",
        "toolName": "write",
        "summary": "Write file",
    }
    transport.release_run.set()
    emitted = []
    runtime = PiSdkRuntimeFactory(transport=transport)(
        PiRuntimeConfig(),
        lambda event_type, **kwargs: emitted.append((event_type, kwargs)),
    )

    result = runtime.run(make_request("terminal-job"), threading.Event())
    before_notifications = list(transport.notifications)
    runtime.cancel("too-late")

    assert result.text == "done"
    assert any(event_type == "approval_request" for event_type, _kwargs in emitted)
    assert runtime.resolve_approval("terminal-job", "appr-1", True) is False
    assert transport.notifications == before_notifications


def test_remote_canceled_result_maps_to_stable_canceled_error_without_fallback():
    transport = RaceTransport()
    transport.run_error = PiBridgeRemoteError("CANCELED", "bridge service failed")
    transport.release_run.set()
    runtime = PiSdkRuntimeFactory(transport=transport)(
        PiRuntimeConfig(fallback_to_llm=True),
        lambda *args, **kwargs: None,
    )

    result = runtime.run(make_request("remote-cancel"), threading.Event())

    assert result.error == "[Canceled]"
    assert result.fallback_to_llm is False


def test_bridge_crash_before_side_effect_allows_only_explicit_crash_fallback():
    transport = RaceTransport()
    transport.run_error = PiBridgeExited("bridge exited")
    transport.release_run.set()
    runtime = PiSdkRuntimeFactory(transport=transport)(
        PiRuntimeConfig(fallback_to_llm=True, crash_fallback_to_llm=True),
        lambda *args, **kwargs: None,
    )

    result = runtime.run(make_request("crash-safe"), threading.Event())

    assert result.side_effect_started is False
    assert result.fallback_to_llm is True
    assert "bridge exited" in result.error


def test_bridge_crash_does_not_fallback_when_crash_fallback_is_disabled():
    transport = RaceTransport()
    transport.run_error = PiBridgeExited("bridge exited")
    transport.release_run.set()
    runtime = PiSdkRuntimeFactory(transport=transport)(
        PiRuntimeConfig(fallback_to_llm=True, crash_fallback_to_llm=False),
        lambda *args, **kwargs: None,
    )

    result = runtime.run(make_request("crash-no-fallback"), threading.Event())

    assert result.side_effect_started is False
    assert result.fallback_to_llm is False


def test_read_only_tool_before_crash_does_not_mark_side_effect():
    transport = RaceTransport()
    transport.event_during_run = {
        "kind": "tool_event",
        "name": "tool_started",
        "toolName": "read",
        "mutating": False,
    }
    transport.run_error = PiBridgeExited("bridge exited")
    transport.release_run.set()
    runtime = PiSdkRuntimeFactory(transport=transport)(
        PiRuntimeConfig(fallback_to_llm=True, crash_fallback_to_llm=True),
        lambda *args, **kwargs: None,
    )

    result = runtime.run(make_request("crash-read"), threading.Event())

    assert result.side_effect_started is False
    assert result.fallback_to_llm is True


def test_mutation_marker_stays_true_even_if_terminal_payload_reports_false():
    transport = RaceTransport()
    transport.event_during_run = {
        "kind": "runtime_notice",
        "name": "side_effect_started",
        "toolName": "write",
        "sideEffectStarted": True,
    }
    transport.run_result = {"text": "done", "sideEffectStarted": False, "fallbackToLlm": True}
    transport.release_run.set()
    runtime = PiSdkRuntimeFactory(transport=transport)(
        PiRuntimeConfig(fallback_to_llm=True),
        lambda *args, **kwargs: None,
    )

    result = runtime.run(make_request("marker-monotonic"), threading.Event())

    assert result.text == "done"
    assert result.side_effect_started is True
    assert result.fallback_to_llm is False


def test_mutation_marker_is_monotonic_across_crash_and_blocks_fallback():
    transport = RaceTransport()
    transport.event_during_run = {
        "kind": "runtime_notice",
        "name": "side_effect_started",
        "toolName": "write",
        "sideEffectStarted": True,
    }
    transport.run_error = PiBridgeExited("bridge exited")
    transport.release_run.set()
    runtime = PiSdkRuntimeFactory(transport=transport)(
        PiRuntimeConfig(fallback_to_llm=True, crash_fallback_to_llm=True),
        lambda *args, **kwargs: None,
    )

    result = runtime.run(make_request("crash-write"), threading.Event())

    assert result.side_effect_started is True
    assert result.fallback_to_llm is False
    assert "ambiguous" in result.error.lower()


def test_late_delta_after_cancel_is_not_rendered_and_caller_wakes_promptly():
    transport = RaceTransport()
    emitted = []
    runtime = PiSdkRuntimeFactory(transport=transport)(
        PiRuntimeConfig(shutdown_timeout_ms=500),
        lambda event_type, **kwargs: emitted.append((event_type, kwargs)),
    )
    result_box = {}

    def run_job():
        result_box["result"] = runtime.run(make_request("late-delta"), threading.Event())

    thread = threading.Thread(target=run_job, daemon=True)
    thread.start()
    assert transport.run_started.wait(1)
    started = time.monotonic()
    runtime.cancel("user")
    runtime._handle_event("late-delta", {"kind": "message_delta", "text": "late", "partial": True})
    thread.join(0.5)

    assert not thread.is_alive()
    assert time.monotonic() - started < 0.5
    assert all(kwargs.get("text") != "late" for _event_type, kwargs in emitted)
    assert result_box["result"].error == "[Canceled]"
