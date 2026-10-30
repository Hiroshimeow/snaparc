from __future__ import annotations

import os
import uuid

import pytest

from src.agents.pi_sdk_transport import PiSdkBridgeTransport


pytestmark = pytest.mark.skipif(
    os.environ.get("PI_SDK_LIVE") != "1",
    reason="SKIPPED_NEEDS_AUTHORIZATION: set PI_SDK_LIVE=1 explicitly to permit a billable Pi SDK smoke test",
)


def test_pi_sdk_live_text_smoke():
    provider = os.environ.get("PI_SDK_PROVIDER", "").strip()
    model = os.environ.get("PI_SDK_MODEL", "").strip()
    if not provider or not model:
        pytest.skip("SKIPPED_NEEDS_AUTHORIZATION: PI_SDK_PROVIDER and PI_SDK_MODEL are required")

    transport = PiSdkBridgeTransport(startup_timeout_ms=6000, shutdown_timeout_ms=3000)
    job_id = f"live-{uuid.uuid4().hex[:10]}"
    session_id = f"live-{uuid.uuid4().hex[:10]}"
    try:
        result = transport.request(
            "run",
            {
                "applicationSessionId": session_id,
                "responderName": "pi",
                "messageId": f"msg-{uuid.uuid4().hex[:10]}",
                "prompt": "Reply with exactly: OK",
                "systemPrompt": "Be concise and follow the user instruction exactly.",
                "history": [],
                "attachments": [],
                "cwd": os.getcwd(),
                "model": f"{provider}/{model}",
                "thinkingLevel": "off",
                "toolPolicy": "none",
            },
            job_id=job_id,
            session_key=f"{session_id}::pi",
            timeout_ms=15000,
        )
        assert isinstance(result.get("text"), str)
        assert result["text"].strip()
        assert result.get("sideEffectStarted") is False
    finally:
        transport.shutdown(3000)
