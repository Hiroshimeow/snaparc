"""Offline HTTP-boundary coverage of approved per-request completion bounds."""
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("scenario", ["arithmetic", "translation", "ocr"])
def test_application_propagates_request_bounds(scenario):
    result = subprocess.run(
        [sys.executable, "-I", str(Path(__file__).resolve()), scenario],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("options", [
    {"max_output_tokens": 0}, {"max_output_tokens": True}, {"max_output_tokens": 1.5},
    {"timeout_seconds": 0}, {"timeout_seconds": float("inf")},
    {"timeout_seconds": float("nan")}, {"timeout_seconds": True},
])
def test_invalid_bounds_are_rejected_before_dispatch(options):
    from src.llm.request_bounds import completion_options
    with pytest.raises(ValueError):
        completion_options(**options)


def _check(scenario):
    import json
    import tempfile
    import threading
    import httpx

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    requests = []

    def send(client, request, **kwargs):
        requests.append(request)
        return httpx.Response(200, request=request, headers={"content-type": "text/event-stream"},
                              content=b'data: {"choices":[{"delta":{"content":"OK"}}]}\n\ndata: [DONE]\n\n')

    httpx.Client.send = send  # Offline boundary only; actual application and SDK remain real.
    with tempfile.TemporaryDirectory() as root:
        if scenario == "arithmetic":
            from src.agents.pi_rpc_bridge import NativePiRpcServer
            server = NativePiRpcServer.__new__(NativePiRpcServer)
            server.canceled_jobs = set()
            server.write_lock = threading.Lock()
            text, side_effect = server._run_via_openai_completions_agent(
                endpoint={"api_key": "synthetic", "is_placeholder_key": False,
                          "base_url": "https://offline.invalid/v1", "model_id": "synthetic"},
                prompt="2 + 2", system_prompt="", history=[], attachments=[],
                job_id="offline", cwd=root, thinking_level="off", tool_policy="none",
                trusted_roots=[root], approval_timeout_ms=1000,
                max_output_tokens=19, timeout_seconds=7,
            )
            assert text == "OK" and not side_effect
        else:
            from src.utils.ConfigManager import ConfigManager
            from src.llm.service import LLMService
            config = Path(root) / "external.json"
            config.write_text(json.dumps({
                "models": {"providers": {"synthetic": {
                    "api": "openai-completions", "baseUrl": "https://offline.invalid/v1",
                    "apiKey": "synthetic", "models": ["synthetic"], "supportsImages": True,
                }}},
                "agents": {"defaults": {"model": {"primary": "synthetic/synthetic"}}},
                "privacy": {"allow_cloud_upload": True},
            }), encoding="utf-8")
            service = LLMService(config_manager=ConfigManager(str(config)))
            from src.agents.pi_rpc_bridge import NativePiRpcServer
            server = NativePiRpcServer.__new__(NativePiRpcServer)
            server.project_root = Path(root) / "not-the-config-directory"
            endpoint = server._resolve_openai_endpoint_for_model("synthetic/synthetic", config_path=config)
            assert endpoint and endpoint["api_key"] == "synthetic"
            assert server._resolve_openai_endpoint_for_model(
                "synthetic/synthetic", config_path=Path(root) / "missing.json",
            ) is None
            done = threading.Event()
            results = []
            def completed(value):
                results.append(value)
                done.set()
            options = dict(callback=completed, max_output_tokens=19, timeout_seconds=7)
            if scenario == "translation":
                service.send_text_message("Hello", **options)
            else:
                from PIL import Image
                raster = Path(root) / "image.png"
                Image.new("RGB", (10, 10)).save(raster)
                service.process_image([str(raster)], custom_prompt_text="Read", **options)
            assert done.wait(5)
            assert results[0]["text"] == "OK", results
            service._get_provider("synthetic", "synthetic").client.close()
        assert len(requests) == 1
        assert json.loads(requests[0].content)["max_completion_tokens"] == 19
        assert set(requests[0].extensions["timeout"].values()) == {7.0}


if __name__ == "__main__":
    _check(sys.argv[1])
