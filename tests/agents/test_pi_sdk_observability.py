from __future__ import annotations

import logging
import shutil
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.agents.pi_contract import PiRunRequest, PiRuntimeConfig
from src.agents.pi_sdk_runtime import PiSdkRuntimeFactory
from src.agents.pi_sdk_transport import (
    PiBridgeExited,
    PiBridgeRemoteError,
    PiSdkBridgeTransport,
    emit_observability,
    sanitize_observability_event,
)


ROOT = Path(__file__).resolve().parents[2]
FAKE_BRIDGE = ROOT / "tests" / "fixtures" / "pi_sdk" / "fake_bridge.mjs"


def make_transport(records, **kwargs):
    return PiSdkBridgeTransport(
        node_executable=shutil.which("node") or "node",
        bridge_path=str(FAKE_BRIDGE),
        startup_timeout_ms=1500,
        observability_sink=records.append,
        **kwargs,
    )


def request(job_id="job-1"):
    return PiRunRequest(
        job_id=job_id,
        session_id="private-session-id",
        message_id="message-1",
        responder_name="pi",
        prompt_text="TOP SECRET PROMPT",
        system_prompt="SECRET SYSTEM",
        metadata={"cwd": "C:/private/project", "model": "provider/model"},
    )


class FakeRuntimeTransport:
    max_image_bytes = 20 * 1024 * 1024
    max_run_frame_bytes = 28 * 1024 * 1024

    def __init__(self, *, result=None):
        self.result = result or {
            "text": "answer",
            "sideEffectStarted": False,
            "piSessionId": "pi-internal-id",
            "piSessionFile": "C:/private/session.jsonl",
            "piSessionAction": "created",
            "prompt": "REMOTE SECRET",
            "attachmentPath": "C:/private/image.png",
        }

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
        if command == "run":
            if on_event is not None:
                on_event({"kind": "message_delta", "text": "first", "partial": True})
            return dict(self.result)
        if command == "close_session":
            return {"closed": True}
        return {"ok": True}

    def notify(self, command, payload, *, job_id=None):
        return "notification"

    def start(self):
        return SimpleNamespace(
            protocol=1,
            bridge_version="0.1.0",
            sdk_version="0.84.2",
            sdk_package="@earendil-works/pi-coding-agent",
            pid=1234,
        )

    def shutdown(self, timeout_ms=3000):
        return None


def test_default_observability_path_is_info_enabled_and_emits_allow_listed_records():
    import src.agents.pi_sdk_transport as module

    logger = module._OBSERVABILITY_LOGGER
    assert logger.isEnabledFor(logging.INFO)
    assert logger.handlers

    records = []

    class CaptureHandler(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    original_handlers = list(logger.handlers)
    logger.handlers = [CaptureHandler()]
    try:
        emit_observability(
            None,
            "pi_run_lifecycle",
            job_id="job-default-log",
            stage="queued",
            duration_ms=0,
            prompt="UNLABELED_SECRET_SENTINEL",
        )
    finally:
        logger.handlers = original_handlers

    assert records
    assert '"event":"pi_run_lifecycle"' in records[0]
    assert "UNLABELED_SECRET_SENTINEL" not in records[0]


def test_schema_allow_list_drops_content_paths_auth_and_arbitrary_remote_fields():
    record = sanitize_observability_event(
        "pi_run_lifecycle",
        {
            "job_id": "job-1",
            "stage": "end",
            "duration_ms": 12.5,
            "terminal_category": "success",
            "side_effect_started": False,
            "prompt": "TOP SECRET PROMPT",
            "system_prompt": "SECRET SYSTEM",
            "attachment_path": "C:/private/image.png",
            "authorization": "Bearer sensitive-token",
            "remoteUnknown": "must-not-flow",
        },
    )

    assert record == {
        "event": "pi_run_lifecycle",
        "job_id": "job-1",
        "stage": "end",
        "duration_ms": 12.5,
        "terminal_category": "success",
        "side_effect_started": False,
    }


def test_backend_selection_emits_reason_and_compatible_versions():
    from src.agents.pi_runtime_factory import build_pi_runtime_factory

    records = []

    class HealthyFactory:
        def start(self):
            return SimpleNamespace(
                protocol=1,
                bridge_version="0.1.0",
                sdk_version="0.84.2",
                sdk_package="@earendil-works/pi-coding-agent",
                pid=7,
            )

    healthy = HealthyFactory()
    assert build_pi_runtime_factory(
        PiRuntimeConfig(backend="auto"),
        sdk_factory=healthy,
        observability_sink=records.append,
    ) is healthy
    assert records == [{
        "event": "pi_backend_selected",
        "backend": "sdk",
        "reason_code": "auto_sdk_healthy",
        "bridge_version": "0.1.0",
        "protocol_version": 1,
        "sdk_version": "0.84.2",
    }]


def test_transport_emits_started_stopped_and_crashed_lifecycle_with_restart_count():
    records = []
    transport = make_transport(records)
    hello = transport.start()
    assert hello.pid > 0
    transport.shutdown()

    lifecycle = [item for item in records if item["event"] == "pi_bridge_lifecycle"]
    assert [item["state"] for item in lifecycle] == ["started", "stopped"]
    assert lifecycle[0]["process_id"] == hello.pid
    assert lifecycle[0]["restart_count"] == 0
    assert all("prompt" not in item and "path" not in item for item in lifecycle)

    crash_records = []
    crashing = make_transport(crash_records)
    with pytest.raises(PiBridgeExited):
        crashing.request("run", {"mode": "exit"}, job_id="crash", timeout_ms=1000)
    crash = next(item for item in crash_records if item["event"] == "pi_bridge_lifecycle" and item["state"] == "crashed")
    assert crash["restart_count"] == 0
    assert crash["process_id"] > 0
    assert "detail" in crash
    crashing.shutdown()


def test_runtime_emits_non_negative_run_durations_one_terminal_and_hashed_session_key():
    records = []
    ticks = iter([10.0, 10.1, 10.4, 10.9, 11.0, 11.1])
    runtime = PiSdkRuntimeFactory(
        transport=FakeRuntimeTransport(),
        observability_sink=records.append,
        monotonic=lambda: next(ticks),
    )(PiRuntimeConfig(), lambda *args, **kwargs: None)

    result = runtime.run(request(), threading.Event())

    assert result.text == "answer"
    run_events = [item for item in records if item["event"] == "pi_run_lifecycle"]
    assert [item["stage"] for item in run_events] == ["queued", "start", "first_event", "end"]
    assert all(item["duration_ms"] >= 0 for item in run_events)
    terminal = [item for item in run_events if item["stage"] == "end"]
    assert len(terminal) == 1
    assert terminal[0]["terminal_category"] == "success"
    assert terminal[0]["side_effect_started"] is False

    session = next(item for item in records if item["event"] == "pi_session_lifecycle")
    assert session["action"] == "created"
    assert session["session_key_hash"]
    assert "private-session-id" not in str(session)
    serialized = str(records)
    for secret in ("TOP SECRET PROMPT", "SECRET SYSTEM", "REMOTE SECRET", "session.jsonl", "image.png"):
        assert secret not in serialized


@pytest.mark.parametrize("action", ["opened", "rebuilt"])
def test_runtime_emits_opened_and_rebuilt_session_lifecycle(action):
    records = []
    transport = FakeRuntimeTransport(result={
        "text": "answer",
        "sideEffectStarted": False,
        "piSessionAction": action,
    })
    runtime = PiSdkRuntimeFactory(
        transport=transport,
        observability_sink=records.append,
    )(PiRuntimeConfig(), lambda *args, **kwargs: None)

    assert runtime.run(request(f"session-{action}"), threading.Event()).text == "answer"
    event = next(item for item in records if item["event"] == "pi_session_lifecycle")
    assert event["action"] == action
    assert event["session_key_hash"]
    assert "private-session-id" not in str(event)


def test_terminal_lifecycle_preserves_true_side_effect_from_result_payload():
    records = []
    runtime = PiSdkRuntimeFactory(
        transport=FakeRuntimeTransport(result={"text": "done", "sideEffectStarted": True}),
        observability_sink=records.append,
    )(PiRuntimeConfig(), lambda *args, **kwargs: None)

    result = runtime.run(request("side-effect-terminal"), threading.Event())

    assert result.side_effect_started is True
    terminal = next(
        item for item in records
        if item["event"] == "pi_run_lifecycle" and item["stage"] == "end"
    )
    assert terminal["side_effect_started"] is True


def test_close_session_emits_hashed_closed_session_lifecycle():
    records = []
    factory = PiSdkRuntimeFactory(
        transport=FakeRuntimeTransport(),
        observability_sink=records.append,
    )

    assert factory.close_session("private-session-id", "Pi", timeout_ms=100)["closed"] is True
    event = next(item for item in records if item["event"] == "pi_session_lifecycle")
    assert event["action"] == "closed"
    assert event["session_key_hash"]
    assert "private-session-id" not in str(event)


def test_transport_restart_lifecycle_reports_incremented_restart_count():
    records = []
    transport = make_transport(records)
    with pytest.raises(PiBridgeExited):
        transport.request("run", {"mode": "exit"}, job_id="restart-crash", timeout_ms=1000)

    assert transport.request("health", {}, timeout_ms=1000)["status"] == "ready"
    started = [
        item for item in records
        if item["event"] == "pi_bridge_lifecycle" and item["state"] == "started"
    ]
    assert [item["restart_count"] for item in started] == [0, 1]
    transport.shutdown()


def test_observability_detail_redacts_auth_prompt_and_base64_data():
    record = sanitize_observability_event(
        "pi_bridge_lifecycle",
        {
            "state": "crashed",
            "detail": (
                'Authorization: Bearer bearer-sensitive-token '
                'prompt="TOP SECRET PROMPT" '
                'data="QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo="'
            ),
        },
    )
    serialized = str(record)
    assert "bearer-sensitive-token" not in serialized
    assert "TOP SECRET PROMPT" not in serialized
    assert "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo=" not in serialized
    assert "[REDACTED]" in serialized


def test_transport_resource_limit_emits_observed_and_allowed_counts():
    records = []
    transport = make_transport(records, max_active_jobs=1)
    first_done = {}

    def first_run():
        first_done["value"] = transport.request(
            "run",
            {"mode": "delayed", "delayMs": 200, "text": "first"},
            job_id="first",
            timeout_ms=1000,
        )

    thread = threading.Thread(target=first_run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 1
    while transport.pending_count < 1 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert transport.pending_count == 1

    with pytest.raises(PiBridgeRemoteError) as caught:
        transport.request("run", {"text": "second"}, job_id="second", timeout_ms=500)
    assert caught.value.code == "RESOURCE_LIMIT"
    event = next(item for item in records if item["event"] == "pi_resource_limit")
    assert event == {
        "event": "pi_resource_limit",
        "limit_name": "active_jobs",
        "observed": 2,
        "allowed": 1,
    }
    thread.join(1)
    transport.shutdown()


def test_observability_sink_failure_never_fails_backend_selection_or_model_run():
    from src.agents.pi_runtime_factory import build_pi_runtime_factory

    def broken_sink(_record):
        raise RuntimeError("logging failed")

    class HealthyFactory:
        def start(self):
            return SimpleNamespace(
                protocol=1,
                bridge_version="0.1.0",
                sdk_version="0.84.2",
                sdk_package="@earendil-works/pi-coding-agent",
                pid=7,
            )

    healthy = HealthyFactory()
    assert build_pi_runtime_factory(
        PiRuntimeConfig(backend="auto"),
        sdk_factory=healthy,
        observability_sink=broken_sink,
    ) is healthy

    runtime = PiSdkRuntimeFactory(
        transport=FakeRuntimeTransport(),
        observability_sink=broken_sink,
    )(PiRuntimeConfig(), lambda *args, **kwargs: None)
    assert runtime.run(request("sink-failure"), threading.Event()).text == "answer"


def test_fake_bridge_reuses_one_process_handshake_and_threads_for_100_jobs():
    records = []
    transport = make_transport(records)
    transport.start()
    thread_ids = (id(transport._stdout_thread), id(transport._stderr_thread))

    for index in range(100):
        job_id = f"sequential-{index}"
        events = []
        result = transport.request(
            "run",
            {"mode": "event_flood", "eventCount": 3, "text": job_id},
            job_id=job_id,
            session_key="one-session::pi",
            timeout_ms=1000,
            on_event=events.append,
        )
        assert result["text"] == job_id
        assert [event["text"] for event in events] == [
            f"event:{job_id}:1",
            f"event:{job_id}:2",
            f"event:{job_id}:3",
        ]

    stats = transport.request("health", {"includeStats": True}, timeout_ms=1000)
    assert transport.spawn_count == 1
    assert stats["helloCount"] == 1
    assert stats["runCount"] == 100
    assert transport.pending_count == 0
    assert not transport._active_run_jobs
    assert (id(transport._stdout_thread), id(transport._stderr_thread)) == thread_ids
    transport.shutdown()


def test_fake_bridge_rss_does_not_grow_monotonically_across_three_equal_batches():
    transport = make_transport([])
    transport.start()
    rss = [transport.request("health", {"includeStats": True}, timeout_ms=1000)["rssBytes"]]

    for batch in range(3):
        for index in range(100):
            job_id = f"rss-{batch}-{index}"
            transport.request(
                "run",
                {"text": job_id},
                job_id=job_id,
                session_key="rss-session::pi",
                timeout_ms=1000,
            )
        rss.append(transport.request("health", {"includeStats": True}, timeout_ms=1000)["rssBytes"])

    growth = [later - earlier for earlier, later in zip(rss, rss[1:])]
    monotonic_growth = all(delta > 0 for delta in growth)
    non_converging_growth = bool(growth) and growth[-1] >= growth[0] * 0.5
    assert not (monotonic_growth and non_converging_growth), f"unbounded monotonic RSS growth: {rss}"
    assert transport.pending_count == 0
    assert not transport._active_run_jobs
    transport.shutdown()
