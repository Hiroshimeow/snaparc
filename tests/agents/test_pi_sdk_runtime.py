from __future__ import annotations

import threading

from src.agents.pi_contract import PiRunRequest, PiRuntimeConfig
from src.agents.pi_sdk_runtime import PiSdkRuntimeFactory
from src.agents.pi_sdk_transport import PiBridgeFirstEventTimeout


class FakeTransport:
    def __init__(self):
        self.requests = []
        self.request_options = []
        self.notifications = []
        self.shutdown_calls = []
        self.approval_accepted = True
        self.block_run = False
        self.run_started = threading.Event()
        self.release_run = threading.Event()
        self.run_result = {
            "text": "sdk answer",
            "sideEffectStarted": False,
            "piSessionId": "s1",
            "piSessionFile": "C:/private/pi-session.jsonl",
        }

    def start(self):
        return object()

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
        self.requests.append((command, payload, job_id, session_key, timeout_ms))
        self.request_options.append((command, timeout_ms, first_event_timeout_ms, cancel_timeout_ms))
        if command == "run" and on_event:
            on_event({"kind": "message_delta", "text": "sdk ", "partial": True})
            on_event({"kind": "timeline_event", "name": "message_completed", "role": "assistant"})
            on_event({"kind": "runtime_warning", "name": "retry_started", "attempt": 1})
            on_event({
                "kind": "approval_request",
                "approvalId": "a1",
                "toolCallId": "call-1",
                "toolName": "write",
                "summary": "Write safe.txt",
                "expiresAt": "2026-09-06T00:00:00.000Z",
                "uiType": "tool",
                "payload": {"title": "Write approval"},
            })
        if command == "approval_response":
            return {"accepted": self.approval_accepted}
        if command == "run" and self.block_run:
            self.run_started.set()
            self.release_run.wait(2)
        return dict(self.run_result)

    def notify(self, command, payload, *, job_id=None):
        self.notifications.append((command, dict(payload), job_id))
        if command == "cancel" and self.block_run:
            self.release_run.set()
        return f"n{len(self.notifications)}"

    def shutdown(self, timeout_ms=3000):
        self.shutdown_calls.append(timeout_ms)


def request(job_id="j1"):
    return PiRunRequest(
        job_id=job_id,
        session_id="session-1",
        message_id="message-1",
        responder_name="Pi Agent",
        prompt_text="inspect",
        system_prompt="system",
        history=[{"role": "user", "parts": ["old"]}],
        attachments=[],
        metadata={"model": "provider/model", "thinking_level": "medium"},
    )


def test_sdk_prompt_construction_preserves_system_history_role_order_and_current_message():
    transport = FakeTransport()
    runtime = PiSdkRuntimeFactory(transport=transport)(PiRuntimeConfig(), lambda *args, **kwargs: None)
    fixed = PiRunRequest(
        job_id="parity-job",
        session_id="parity-session",
        message_id="parity-message",
        responder_name="pi",
        prompt_text="current question",
        system_prompt="system instruction",
        history=[
            {"role": "user", "parts": ["first user"]},
            {"role": "assistant", "parts": ["first answer"]},
        ],
        metadata={"model": "provider/model"},
    )

    runtime.run(fixed, threading.Event())
    sdk_payload = transport.requests[0][1]

    assert sdk_payload["systemPrompt"] == fixed.system_prompt
    assert sdk_payload["history"] == fixed.history
    assert [item["role"] for item in sdk_payload["history"]] == ["user", "assistant"]
    assert sdk_payload["prompt"] == fixed.prompt_text

def test_factory_creates_multiple_handles_over_one_transport_and_shutdown_once():
    transport = FakeTransport()
    factory = PiSdkRuntimeFactory(transport=transport)
    first = factory(PiRuntimeConfig(), lambda *args, **kwargs: None)
    second = factory(PiRuntimeConfig(), lambda *args, **kwargs: None)
    assert first is not second
    assert first.transport is second.transport is transport

    first.close()
    second.close()
    assert transport.shutdown_calls == []
    factory.shutdown(123)
    factory.shutdown(123)
    assert transport.shutdown_calls == [123]


def test_run_maps_request_events_and_result_without_closing_transport():
    transport = FakeTransport()
    emitted = []
    runtime = PiSdkRuntimeFactory(transport=transport)(
        PiRuntimeConfig(),
        lambda event_type, **kwargs: emitted.append((event_type, kwargs)),
    )
    result = runtime.run(request(), threading.Event())

    command, payload, job_id, session_key, _timeout = transport.requests[0]
    assert command == "run"
    assert job_id == "j1"
    assert session_key == "session-1::pi agent"
    assert payload["applicationSessionId"] == "session-1"
    assert payload["responderName"] == "pi agent"
    assert payload["messageId"] == "message-1"
    assert payload["prompt"] == "inspect"
    assert payload["systemPrompt"] == "system"
    assert payload["history"][0]["role"] == "user"
    assert payload["attachments"] == []
    assert payload["model"] == "provider/model"
    assert payload["thinkingLevel"] == "medium"
    assert payload["toolPolicy"] == "read_only"
    assert payload["trustedRoots"] == ["."]
    assert payload["approvalTimeoutMs"] == 120000
    assert payload["cwd"]
    assert result.text == "sdk answer"
    assert result.metadata["piSessionId"] == "s1"
    assert result.metadata["piSessionFile"] == "C:/private/pi-session.jsonl"
    assert "pi-session.jsonl" not in result.text
    assert emitted[0][0] == "message_delta"
    assert emitted[0][1]["text"] == "sdk "
    assert emitted[1][0] == "runtime_notice"
    assert emitted[1][1]["metadata"]["name"] == "message_completed"
    assert emitted[2][0] == "runtime_notice"
    assert emitted[2][1]["metadata"]["name"] == "retry_started"
    assert emitted[3][0] == "approval_request"
    approval_metadata = emitted[3][1]["metadata"]
    assert approval_metadata["approval_id"] == "a1"
    assert approval_metadata["tool_call_id"] == "call-1"
    assert approval_metadata["tool_name"] == "write"
    assert approval_metadata["summary"] == "Write safe.txt"
    assert approval_metadata["expires_at"] == "2026-09-06T00:00:00.000Z"
    assert approval_metadata["ui_type"] == "tool"
    runtime.close()
    assert transport.shutdown_calls == []


def test_run_uses_first_event_watchdog_without_whole_run_terminal_timeout():
    transport = FakeTransport()
    runtime = PiSdkRuntimeFactory(transport=transport)(
        PiRuntimeConfig(first_event_timeout_ms=30_000, shutdown_timeout_ms=4_321),
        lambda *args, **kwargs: None,
    )

    assert runtime.run(request("watchdog"), threading.Event()).text == "sdk answer"
    assert transport.request_options[0] == ("run", None, 30_000, 4_321)


def test_first_event_timeout_falls_back_only_after_quiescence_is_proven():
    class TimeoutTransport(FakeTransport):
        def __init__(self, quiescent):
            super().__init__()
            self.quiescent = quiescent

        def request(self, command, payload, **kwargs):
            if command == "run":
                raise PiBridgeFirstEventTimeout("first event timed out", quiescent=self.quiescent)
            return super().request(command, payload, **kwargs)

    safe_runtime = PiSdkRuntimeFactory(transport=TimeoutTransport(True))(
        PiRuntimeConfig(fallback_to_llm=True), lambda *args, **kwargs: None
    )
    safe = safe_runtime.run(request("safe-timeout"), threading.Event())
    assert safe.fallback_to_llm is True
    assert safe.side_effect_started is False

    ambiguous_runtime = PiSdkRuntimeFactory(transport=TimeoutTransport(False))(
        PiRuntimeConfig(fallback_to_llm=True), lambda *args, **kwargs: None
    )
    ambiguous = ambiguous_runtime.run(request("ambiguous-timeout"), threading.Event())
    assert ambiguous.fallback_to_llm is False
    assert "quiescence" in ambiguous.error.lower()


def test_factory_close_session_is_explicit_and_idempotence_is_bridge_owned():
    transport = FakeTransport()
    factory = PiSdkRuntimeFactory(transport=transport)
    result = factory.close_session("session-1", "Pi Agent", timeout_ms=321)
    command, payload, job_id, session_key, timeout = transport.requests[-1]
    assert result == transport.run_result
    assert command == "close_session"
    assert payload == {"applicationSessionId": "session-1", "responderName": "pi agent"}
    assert job_id is None
    assert session_key is None
    assert timeout == 321


def test_cancel_and_approval_are_scoped_to_outstanding_job_and_require_bridge_ack():
    transport = FakeTransport()
    transport.block_run = True
    runtime = PiSdkRuntimeFactory(transport=transport)(PiRuntimeConfig(), lambda *args, **kwargs: None)
    first = threading.Thread(target=lambda: runtime.run(request("job-a"), threading.Event()), daemon=True)
    first.start()
    assert transport.run_started.wait(1)

    assert runtime.resolve_approval("job-a", "wrong", True) is False
    assert runtime.resolve_approval("other", "a1", True) is False
    assert runtime.resolve_approval("job-a", "a1", True, "ok", {"value": 1}) is True
    command, payload, job_id, session_key, timeout = transport.requests[-1]
    assert command == "approval_response"
    assert payload == {"approvalId": "a1", "approved": True, "reason": "ok", "value": {"value": 1}}
    assert job_id == "job-a"
    assert session_key is None
    assert timeout == 120000
    transport.release_run.set()
    first.join(1)
    assert not first.is_alive()

    transport.approval_accepted = False
    transport.run_started.clear()
    transport.release_run.clear()
    second = threading.Thread(target=lambda: runtime.run(request("job-b"), threading.Event()), daemon=True)
    second.start()
    assert transport.run_started.wait(1)
    assert runtime.resolve_approval("job-b", "a1", True) is False

    runtime.cancel("user")
    assert transport.notifications[-1] == ("cancel", {"reason": "user"}, "job-b")
    second.join(1)
    assert not second.is_alive()


def test_cancel_clears_pending_approvals_and_ignores_late_approval_events():
    transport = FakeTransport()
    transport.block_run = True
    emitted = []
    runtime = PiSdkRuntimeFactory(transport=transport)(PiRuntimeConfig(), lambda *args, **kwargs: emitted.append((args, kwargs)))
    running = threading.Thread(target=lambda: runtime.run(request("cancel-approval"), threading.Event()), daemon=True)
    running.start()
    assert transport.run_started.wait(1)
    assert runtime.resolve_approval("cancel-approval", "a1", False, "pre-cancel") is True

    runtime._handle_event("cancel-approval", {
        "kind": "approval_request",
        "approvalId": "late-before-cancel",
        "toolName": "write",
        "summary": "Write before cancel",
    })
    runtime.cancel("user")
    before = len(emitted)
    runtime._handle_event("cancel-approval", {
        "kind": "approval_request",
        "approvalId": "late-after-cancel",
        "toolName": "write",
        "summary": "Write after cancel",
    })

    assert runtime.resolve_approval("cancel-approval", "late-before-cancel", True) is False
    assert runtime.resolve_approval("cancel-approval", "late-after-cancel", True) is False
    assert len(emitted) == before
    running.join(1)
    assert not running.is_alive()


def test_runtime_propagates_explicit_tool_policy_roots_and_approval_timeout():
    transport = FakeTransport()
    config = PiRuntimeConfig(
        tool_policy="approve_mutations",
        trusted_roots=[".", "C:/shared"],
        approval_timeout_ms=4321,
    )
    runtime = PiSdkRuntimeFactory(transport=transport)(config, lambda *args, **kwargs: None)
    runtime.run(request("policy-job"), threading.Event())
    payload = transport.requests[0][1]
    assert payload["toolPolicy"] == "approve_mutations"
    assert payload["trustedRoots"] == [".", "C:/shared"]
    assert payload["approvalTimeoutMs"] == 4321


def test_two_runtime_handles_keep_distinct_job_correlation():
    transport = FakeTransport()
    factory = PiSdkRuntimeFactory(transport=transport)
    one = factory(PiRuntimeConfig(), lambda *args, **kwargs: None)
    two = factory(PiRuntimeConfig(), lambda *args, **kwargs: None)
    one.run(request("one"), threading.Event())
    two.run(request("two"), threading.Event())
    assert [entry[2] for entry in transport.requests] == ["one", "two"]


def test_terminal_result_is_authoritative_over_intermediate_completion_event():
    transport = FakeTransport()
    transport.run_result = {"text": "terminal answer", "sideEffectStarted": False}
    emitted = []
    runtime = PiSdkRuntimeFactory(transport=transport)(
        PiRuntimeConfig(),
        lambda event_type, **kwargs: emitted.append((event_type, kwargs)),
    )

    result = runtime.run(request(), threading.Event())

    assert result.text == "terminal answer"
    assert any(
        event_type == "runtime_notice" and kwargs["metadata"].get("name") == "message_completed"
        for event_type, kwargs in emitted
    )
    assert all(kwargs.get("text") != "terminal answer" for _event_type, kwargs in emitted)
