
"""Offline regression: real HTTP clients must not retry failed requests.

Run in fresh interpreters because other suite modules may install HTTP doubles.
The HTTP boundary is replaced; these tests never contact a provider.
"""
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("client_path", ["provider", "pi"])
def test_real_application_client_makes_only_one_http_attempt(client_path):
    result = subprocess.run(
        [sys.executable, "-I", str(Path(__file__).resolve()), client_path],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _check(client_path):
    import tempfile
    import httpx

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    attempts = []

    def unavailable(client, request, **kwargs):
        attempts.append(request)
        return httpx.Response(503, request=request, json={"error": "unavailable"})

    original_send = httpx.Client.send
    httpx.Client.send = unavailable
    try:
        if client_path == "provider":
            import threading
            from src.llm.providers.completions import ChatCompletionsProvider
            provider = ChatCompletionsProvider(
                api_key="synthetic-offline-key", base_url="https://offline.invalid/v1",
                model="synthetic-model", trust_env=False,
            )
            done = threading.Event()
            try:
                provider.send_text_message(
                    "offline retry probe",
                    callback=lambda _payload: done.set(),
                    model_name="synthetic-model",
                )
                assert done.wait(5), "Provider callback timed out"
            finally:
                provider.client.close()
        else:
            from src.agents.pi_rpc_bridge import NativePiRpcServer
            # Avoid unrelated config/session initialization; execute the real runner.
            server = NativePiRpcServer.__new__(NativePiRpcServer)
            server.canceled_jobs = set()
            with tempfile.TemporaryDirectory() as root:
                try:
                    server._run_via_openai_completions_agent(
                        endpoint={"api_key": "synthetic-offline-key", "is_placeholder_key": False,
                                  "base_url": "https://offline.invalid/v1", "model_id": "synthetic-model",
                                  "trust_env": False},
                        prompt="2 + 2", system_prompt="", history=[], attachments=[],
                        job_id="offline", cwd=root, thinking_level="off", tool_policy="none",
                        trusted_roots=[root], approval_timeout_ms=1000,
                    )
                except httpx.HTTPStatusError:
                    pass
                else:
                    raise AssertionError("Expected the synthetic HTTP failure")
        assert len(attempts) == 1, f"{client_path}: expected one HTTP attempt, got {len(attempts)}"
    finally:
        httpx.Client.send = original_send


if __name__ == "__main__":
    _check(sys.argv[1])