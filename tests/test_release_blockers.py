"""Release regressions: completion errors must never fabricate success."""
from pathlib import Path

import pytest

from src.agents.pi_rpc_bridge import NativePiRpcServer


@pytest.mark.parametrize(
    "outcome",
    ["missing", "disabled", "placeholder", "exception", "empty", "whitespace", "canceled", "success"],
)
def test_bridge_never_fabricates_completion(tmp_path, monkeypatch, outcome):
    monkeypatch.setattr(Path, "mkdir", lambda *args, **kwargs: None)
    server = NativePiRpcServer()
    server.project_root = tmp_path
    frames = []
    server._emit = frames.append
    endpoint = None if outcome == "missing" else {"is_placeholder_key": outcome == "placeholder"}
    monkeypatch.delenv("STC_FORCE_LIVE_PI_OPENAI", raising=False)
    if outcome == "disabled":
        monkeypatch.setenv("STC_DISABLE_OPENAI_PI_AGENT", "1")
    else:
        monkeypatch.delenv("STC_DISABLE_OPENAI_PI_AGENT", raising=False)
    server._resolve_openai_endpoint_for_model = lambda model: endpoint

    def complete(**kwargs):
        if outcome == "exception":
            raise RuntimeError("provider failed")
        if outcome == "canceled":
            server.canceled_jobs.add("job")
        return (
            "real completion"
            if outcome == "success"
            else "   "
            if outcome == "whitespace"
            else ""
        ), False

    server._run_via_openai_completions_agent = complete
    server._handle_run(
        "request",
        "job",
        "session",
        {"prompt": "synthetic task", "cwd": str(tmp_path)},
    )
    responses = [frame for frame in frames if frame["type"] == "response"]
    assert len(responses) == 1
    response = responses[0]
    assert response["requestId"] == "request"
    assert response["jobId"] == "job"
    if outcome == "success":
        assert response["ok"]
        assert response["data"]["text"] == "real completion"
    else:
        assert not response["ok"], response
        assert response["error"]["code"]
        assert not any(
            frame.get("event", {}).get("kind") == "message_delta" for frame in frames
        )
        assert not server.sessions
