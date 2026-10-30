from __future__ import annotations

import os
import shutil
import sys
import threading
import time
from pathlib import Path

import pytest

from src.agents.pi_sdk_transport import (
    PiBridgeExited,
    PiBridgeFirstEventTimeout,
    PiBridgeFrameTooLarge,
    PiBridgeIncompatibleProtocol,
    PiBridgeMalformedFrame,
    PiBridgeRemoteError,
    PiBridgeTimeout,
    PiBridgeUnavailable,
    PiSdkBridgeTransport,
)


ROOT = Path(__file__).resolve().parents[2]
FAKE_BRIDGE = ROOT / "tests" / "fixtures" / "pi_sdk" / "fake_bridge.mjs"


def make_transport(**kwargs):
    return PiSdkBridgeTransport(
        node_executable=shutil.which("node") or "node",
        bridge_path=str(FAKE_BRIDGE),
        startup_timeout_ms=1500,
        **kwargs,
    )


def test_transport_starts_fake_bridge_lazily_and_reuses_one_process():
    transport = make_transport()
    assert transport.spawn_count == 0
    hello = transport.start()
    first_pid = hello.pid
    assert hello.protocol == 1
    assert hello.sdk_version == "0.84.2"
    assert hello.limits == {"maxFrameBytes": 1024, "maxRunFrameBytes": 4096, "maxImageBytes": 2048}
    assert transport.max_frame_bytes == 1024
    assert transport.max_run_frame_bytes == 4096
    assert transport.max_image_bytes == 2048
    assert transport.spawn_count == 1

    health = transport.request("health", {}, timeout_ms=1000)
    assert health["status"] == "ready"
    assert transport.start().pid == first_pid
    assert transport.spawn_count == 1
    transport.shutdown()


def test_transport_default_startup_timeout_is_15_seconds():
    assert PiSdkBridgeTransport().startup_timeout_ms == 15000


def test_hello_timeout_message_includes_duration_and_redacted_stderr_tail(tmp_path):
    silent_bridge = tmp_path / "silent_bridge.mjs"
    silent_bridge.write_text(
        "process.stderr.write('loading sdk Authorization: bearer-secret\\n');\n"
        "setInterval(() => {}, 1000);\n",
        encoding="utf-8",
    )
    transport = PiSdkBridgeTransport(
        node_executable=shutil.which("node") or "node",
        bridge_path=str(silent_bridge),
        startup_timeout_ms=1000,
    )
    try:
        with pytest.raises(PiBridgeTimeout) as caught:
            transport.start()
    finally:
        transport.shutdown(200)

    message = str(caught.value)
    assert "bridge request hello timed out" in message
    assert "after 1000 ms" in message
    assert "stderr: loading sdk" in message
    assert "bearer-secret" not in message


def test_transport_approval_response_uses_top_level_protocol_fields():
    transport = make_transport()
    result = transport.request(
        "approval_response",
        {
            "approvalId": "appr-1",
            "approved": True,
            "reason": "approved",
            "value": {"choice": "yes"},
        },
        job_id="job-approval",
        timeout_ms=1000,
    )
    assert result == {"accepted": True}
    transport.shutdown()


def test_transport_rejects_outgoing_frame_over_negotiated_limit_before_pipe_write():
    transport = make_transport()
    transport.start()
    with pytest.raises(PiBridgeFrameTooLarge):
        transport.request("health", {"padding": "x" * 1500}, timeout_ms=500)
    assert transport.request("health", {}, timeout_ms=500)["status"] == "ready"
    transport.shutdown()


def test_probe_health_reuses_production_bridge_and_passes_model_without_prompt():
    transport = make_transport()
    probe = transport.probe_health("provider/model")

    assert probe["nodeVersion"].startswith("v")
    assert probe["hello"].sdk_version == "0.84.2"
    assert probe["health"]["status"] == "ready"
    assert probe["health"]["model"] == {
        "configured": True,
        "available": True,
        "provider": "provider",
        "id": "model",
    }
    assert transport.spawn_count == 1
    transport.shutdown()


def test_sdk_version_mismatch_is_rejected_during_startup():
    transport = make_transport(hello_payload={"mode": "sdk_mismatch"})
    with pytest.raises(PiBridgeIncompatibleProtocol, match="SDK version"):
        transport.start()
    assert transport.state == transport.FAILED
    transport.shutdown()


def test_transport_argv_is_list_and_never_uses_shell(monkeypatch):
    seen = {}
    import src.agents.pi_sdk_transport as module

    real_popen = module.subprocess.Popen

    def capture(argv, *args, **kwargs):
        seen["argv"] = argv
        seen["shell"] = kwargs.get("shell")
        return real_popen(argv, *args, **kwargs)

    monkeypatch.setattr(module.subprocess, "Popen", capture)
    transport = make_transport()
    transport.start()
    try:
        assert isinstance(seen["argv"], list)
        assert os.path.samefile(seen["argv"][1], FAKE_BRIDGE)
        assert seen["shell"] is False
    finally:
        transport.shutdown()


def test_incompatible_hello_fails_before_transport_becomes_ready():
    transport = make_transport(hello_payload={"mode": "incompatible"})
    with pytest.raises(PiBridgeIncompatibleProtocol):
        transport.start()
    assert transport.state == transport.FAILED
    assert transport.spawn_count == 1
    transport.shutdown()


def test_transport_handles_fragmented_frames_and_ordered_events():
    events = []
    transport = make_transport()
    result = transport.request(
        "run",
        {"mode": "fragmented", "text": "done"},
        job_id="job-frag",
        timeout_ms=1500,
        on_event=events.append,
    )
    assert result["text"] == "done"
    assert [event["text"] for event in events] == ["event:job-frag"]
    transport.shutdown()


def test_concurrent_requests_can_finish_reverse_order_without_crossing_ids():
    transport = make_transport()
    results = {}

    def run(name, delay):
        results[name] = transport.request(
            "run",
            {"delayMs": delay, "text": name},
            job_id=name,
            timeout_ms=2000,
        )["text"]

    slow = threading.Thread(target=run, args=("slow", 120))
    fast = threading.Thread(target=run, args=("fast", 10))
    slow.start(); fast.start(); slow.join(); fast.join()
    assert results == {"fast": "fast", "slow": "slow"} or results == {"slow": "slow", "fast": "fast"}
    transport.shutdown()


def test_first_event_watchdog_disarms_after_correlated_event_and_allows_long_run():
    transport = make_transport()
    events = []
    result = transport.request(
        "run",
        {"mode": "event_then_delayed", "delayMs": 120, "text": "done"},
        job_id="eventful",
        timeout_ms=None,
        first_event_timeout_ms=30,
        cancel_timeout_ms=300,
        on_event=events.append,
    )
    assert result["text"] == "done"
    assert [event["text"] for event in events] == ["event:eventful"]
    transport.shutdown()


def test_approval_wait_may_exceed_first_event_deadline_after_approval_event_arrives():
    transport = make_transport()
    events = []
    result = {}

    def run_job():
        result["value"] = transport.request(
            "run",
            {"mode": "approval_wait", "text": "approved"},
            job_id="approval-long",
            timeout_ms=None,
            first_event_timeout_ms=30,
            cancel_timeout_ms=300,
            on_event=events.append,
        )

    thread = threading.Thread(target=run_job, daemon=True)
    thread.start()
    deadline = time.monotonic() + 1
    while not events and time.monotonic() < deadline:
        time.sleep(0.005)
    assert events and events[0]["kind"] == "approval_request"

    time.sleep(0.08)
    assert thread.is_alive()
    assert transport.request(
        "approval_response",
        {"approvalId": "appr-1", "approved": True},
        job_id="approval-long",
        timeout_ms=500,
    ) == {"accepted": True}
    thread.join(1)

    assert not thread.is_alive()
    assert result["value"]["text"] == "approved"
    transport.shutdown()


def test_first_event_timeout_cancels_correlated_job_and_waits_for_terminal_quiescence():
    transport = make_transport()
    with pytest.raises(PiBridgeFirstEventTimeout) as caught:
        transport.request(
            "run",
            {"mode": "delayed", "delayMs": 250},
            job_id="late",
            timeout_ms=None,
            first_event_timeout_ms=30,
            cancel_timeout_ms=300,
        )
    assert caught.value.quiescent is True
    time.sleep(0.3)
    health = transport.request("health", {"includeStats": True}, timeout_ms=500)
    assert health["runCount"] == 0
    assert transport.pending_count == 0
    transport.shutdown()


def test_malformed_frame_fails_pending_request_but_reader_survives():
    transport = make_transport()
    with pytest.raises(PiBridgeMalformedFrame):
        transport.request("run", {"mode": "malformed"}, job_id="bad", timeout_ms=500)
    assert transport.request("health", {}, timeout_ms=500)["status"] == "ready"
    transport.shutdown()


def test_stderr_context_is_redacted_before_reaching_exception_message():
    transport = make_transport()
    with pytest.raises(PiBridgeExited) as caught:
        transport.request("run", {"mode": "stderr_exit"}, job_id="stderr", timeout_ms=1000)
    text = str(caught.value)
    assert "fixture-key" not in text
    assert "fixture-token" not in text
    assert "bearer-secret" not in text
    assert "[REDACTED]" in text
    transport.shutdown()


def test_interrupted_run_is_never_replayed_and_later_request_restarts_bridge_once():
    transport = make_transport(max_restarts_per_window=1, restart_window_ms=30_000, restart_cooldown_ms=30_000)
    with pytest.raises(PiBridgeExited) as caught:
        transport.request("run", {"mode": "exit"}, job_id="crash", timeout_ms=1000)
    assert "23" in str(caught.value)
    assert transport.spawn_count == 1
    time.sleep(0.05)
    assert transport.spawn_count == 1

    assert transport.request("health", {}, timeout_ms=1000)["status"] == "ready"
    assert transport.spawn_count == 2
    transport.shutdown()


def test_restart_storm_opens_cooldown_without_spawning_third_bridge():
    transport = make_transport(max_restarts_per_window=1, restart_window_ms=30_000, restart_cooldown_ms=30_000)
    with pytest.raises(PiBridgeExited):
        transport.request("run", {"mode": "exit"}, job_id="first-crash", timeout_ms=1000)
    assert transport.request("health", {}, timeout_ms=1000)["status"] == "ready"
    assert transport.spawn_count == 2

    with pytest.raises(PiBridgeExited):
        transport.request("run", {"mode": "exit"}, job_id="second-crash", timeout_ms=1000)
    with pytest.raises(PiBridgeUnavailable, match="cooldown"):
        transport.request("health", {}, timeout_ms=500)
    assert transport.spawn_count == 2
    transport.shutdown()


def test_active_job_cap_rejects_second_run_with_resource_limit():
    transport = make_transport(max_active_jobs=1)
    transport.start()
    result = {}

    def hold_first():
        result["first"] = transport.request(
            "run",
            {"mode": "delayed", "delayMs": 250, "text": "first"},
            job_id="first",
            timeout_ms=1000,
        )

    thread = threading.Thread(target=hold_first, daemon=True)
    thread.start()
    deadline = time.monotonic() + 1
    while transport.pending_count < 1 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert transport.pending_count == 1

    with pytest.raises(PiBridgeRemoteError) as caught:
        transport.request("run", {"text": "second"}, job_id="second", timeout_ms=500)
    assert caught.value.code == "RESOURCE_LIMIT"
    thread.join(1)
    assert result["first"]["text"] == "first"
    transport.shutdown()


def test_event_flood_terminates_only_affected_job_with_resource_limit():
    transport = make_transport(max_events_per_job=2)
    events = []
    with pytest.raises(PiBridgeRemoteError) as caught:
        transport.request(
            "run",
            {"mode": "event_flood", "eventCount": 8},
            job_id="flood",
            timeout_ms=1000,
            on_event=events.append,
        )
    assert caught.value.code == "RESOURCE_LIMIT"
    assert len(events) <= 2
    assert transport.request("health", {}, timeout_ms=500)["status"] == "ready"
    transport.shutdown()


def test_stderr_ring_redacts_auth_prompt_and_base64_and_stays_bounded():
    transport = make_transport(stderr_max_lines=3, stderr_max_bytes=256)
    with pytest.raises(PiBridgeExited) as caught:
        transport.request("run", {"mode": "stderr_sensitive"}, job_id="sensitive", timeout_ms=1000)
    text = str(caught.value)
    for secret in (
        "bearer-sensitive-token",
        "sensitive-api-key",
        "TOP SECRET PROMPT",
        "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo=",
    ):
        assert secret not in text
    assert "[REDACTED]" in text
    assert len(text.encode("utf-8")) < 512
    transport.shutdown()


def test_shutdown_is_idempotent_and_reader_threads_stop():
    transport = make_transport()
    transport.start()
    transport.shutdown()
    transport.shutdown()
    assert not transport.reader_threads_alive


def test_unresponsive_shutdown_is_terminated_within_bound():
    transport = make_transport(shutdown_timeout_ms=100)
    transport.request("run", {"mode": "ignore_shutdown"}, job_id="hold", timeout_ms=500)
    started = time.monotonic()
    transport.shutdown(timeout_ms=100)
    assert time.monotonic() - started < 1.5
    assert not transport.reader_threads_alive
