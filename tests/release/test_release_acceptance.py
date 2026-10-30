"""Release gates; offline discovery NEVER sends requests.

Opt in with PI_SDK_LIVE=1 and STC_RELEASE_APPROVAL pointing to an operator-
approved JSON file (not credentials). See docs/release-acceptance.md. Each gate
runs in a fresh interpreter: other tests' sys.modules doubles cannot satisfy it.
No response, application service, Pi runner, or HTTP response is stubbed.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
CONFIG = Path("E:/test/stc/screens-trans-chatbot-v2.4/config.yaml")


def _live_gate(scenario):
    if os.environ.get("PI_SDK_LIVE") != "1":
        pytest.skip("Required live release gate: explicit authorization is not enabled")
    assert os.environ.get("PI_OFFLINE") != "1", "Live verification conflicts with PI_OFFLINE=1"
    assert os.environ.get("STC_DISABLE_OPENAI_PI_AGENT") != "1", "Pi live path is disabled"
    approval = os.environ.get("STC_RELEASE_APPROVAL")
    assert approval, "Provide the reviewed approval file; configuration is not authorization"
    # No inherited personal Pi identity, model, key, or CLI selection is used.
    env = dict(os.environ)
    for key in list(env):
        if key.startswith(("PI_", "STC_FORCE_", "STC_USE_EXTERNAL_PI")):
            env.pop(key)
    result = subprocess.run(
        [sys.executable, "-I", str(Path(__file__).resolve()), scenario, approval],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=100,
    )
    # Child diagnostics intentionally exclude SDK errors, keys, and config reprs.
    assert result.returncode == 0, result.stdout.strip() or "Live gate failed (details withheld to protect credentials)"


def test_external_configuration_is_authorized_isolated_and_vision_capable():
    _live_gate("configuration")


def test_live_pi_arithmetic_has_one_bounded_authenticated_attempt():
    _live_gate("arithmetic")


def test_live_translation_has_one_bounded_configured_service_attempt():
    _live_gate("translation")


def test_live_ocr_recognizes_synthetic_raster_in_one_bounded_attempt():
    _live_gate("ocr")


def _run(scenario, approval_path):
    import hashlib
    import tempfile
    import threading
    import time
    from urllib.parse import urlsplit

    sys.path.insert(0, str(ROOT))
    from src.utils.yaml_io import load_yaml_or_json
    from src.utils.ConfigManager import ConfigManager
    from src.llm.registry import ModelRegistry
    from src.llm.service import LLMService
    from src.agents.pi_rpc_bridge import NativePiRpcServer

    approval = json.loads(Path(approval_path).read_text(encoding="utf-8"))
    assert approval.get("authorized_by"), "Missing authorizing owner"
    assert approval.get("vision_evidence"), "Missing provider/model vision-capability evidence"
    assert approval.get("authentication_method") == "chat-completions-probe", "Live authentication must use Chat Completions"
    assert Path(approval["config_path"]).resolve() == CONFIG.resolve(), "Wrong external config path"
    assert CONFIG.is_file(), "External config must exist; never generate a substitute"
    before = CONFIG.read_bytes()
    assert hashlib.sha256(before).hexdigest() == approval["config_sha256"], "Approval does not cover this configuration"
    raw = load_yaml_or_json(before.decode("utf-8"))
    selected = raw["agents"]["defaults"]["model"]["primary"]
    assert selected == approval["model"], "Selected model is not the authorized model"
    provider_name, model_id = selected.split("/", 1)
    provider_raw = raw["models"]["providers"][provider_name]
    assert provider_raw.get("supportsImages") is True, "Vision must be explicit, not a permissive default"
    assert provider_raw.get("baseUrl") and provider_raw.get("apiKey"), "No default endpoint or credential substitution"
    for name in ("configuration", "arithmetic", "translation", "ocr"):
        budget = approval["budgets"][name]
        assert budget["requests"] == 1, "Each workflow needs its own one-request budget"
        assert 0 < budget["max_output_tokens"] <= 256, "Agree a small output limit"
        assert 0 < budget["timeout_seconds"] <= 60, "Agree a bounded timeout"

    with tempfile.TemporaryDirectory(prefix="stc-release-") as temporary:
        # The native Python bridge never reads Pi stores. Guard against future
        # regressions that start consulting personal auth/models/settings files.
        personal_pi = (Path.home() / ".pi").resolve()
        def audit(event, args):
            if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
                candidate = Path(os.fsdecode(args[0])).resolve()
                assert not candidate.is_relative_to(personal_pi), "Personal Pi store access is forbidden"
        sys.addaudithook(audit)
        os.environ["PI_CODING_AGENT_DIR"] = temporary
        os.environ.pop("STC_USE_EXTERNAL_PI_CLI", None)
        assert ConfigManager._instance is None, "Configuration singleton was initialized before external selection"
        cm = ConfigManager(config_path=str(CONFIG))
        assert Path(cm.config_path).resolve() == CONFIG.resolve()
        registry = ModelRegistry(cm)
        assert registry.default_model() == selected
        target = registry.resolve(selected)
        assert target.provider.supports_images
        service = LLMService(config_manager=cm, default_model=selected)
        provider = service._get_provider(provider_name, model_id)
        assert provider is not None
        endpoint = NativePiRpcServer()._resolve_openai_endpoint_for_model(selected, config_path=CONFIG)
        assert endpoint is not None, "Pi cannot resolve the explicit application configuration"
        assert endpoint["full_model"] == selected
        assert endpoint["base_url"].rstrip("/") == target.provider.base_url.rstrip("/") == str(provider.client.base_url).rstrip("/")
        assert endpoint["api_key"] == provider.api_key and provider.api_key, "Pi/service credential resolution differs"
        assert not endpoint["is_placeholder_key"], "Unresolved credential reference"
        # SDK defaults permit retries: this assertion must fail until the real
        # application disables them; a test must not silently repair that policy.
        assert provider.client.max_retries == 0, "Application client retries violate the one-attempt budget"

        import httpx
        original_send = httpx.Client.send
        attempts = {"completion": 0}
        started = time.monotonic()
        expected_url = urlsplit(target.provider.base_url)
        budget = approval["budgets"].get(scenario)

        def observed_send(client, request, *args, **kwargs):
            # Observe and bound actual outbound traffic, then forward unchanged.
            # No canned response or replacement application behavior is provided.
            nonlocal attempts
            actual = urlsplit(str(request.url))
            assert (actual.scheme, actual.netloc) == (expected_url.scheme, expected_url.netloc), "Unexpected destination"
            assert request.headers.get("authorization") == "Bearer " + provider.api_key, "Wrong authentication"
            attempts["completion"] += 1
            assert attempts["completion"] == 1, "Completion retry/failover blocked"
            assert request.method == "POST" and actual.path.endswith("/chat/completions")
            body = json.loads(request.content)
            assert body["model"] == model_id, "Model substitution"
            assert not body.get("tools"), "Release probes must not execute tools"
            cap = body.get("max_completion_tokens", body.get("max_tokens"))
            assert isinstance(cap, int) and 0 < cap <= budget["max_output_tokens"], "Application omitted the agreed output bound"
            assert time.monotonic() - started <= budget["timeout_seconds"], "Request exceeded its time budget"
            return original_send(client, request, *args, **kwargs)

        httpx.Client.send = observed_send
        try:
            # Every live validation uses the same Chat Completions protocol.
            started = time.monotonic()
            if scenario == "configuration":
                done = threading.Event()
                responses = []
                provider.send_text_message(
                    "Reply with exactly OK.",
                    callback=lambda response: (responses.append(response), done.set()),
                    model_name=model_id,
                    max_output_tokens=budget["max_output_tokens"],
                    timeout_seconds=budget["timeout_seconds"],
                )
                assert done.wait(budget["timeout_seconds"]), "Configuration completion timed out"
                assert len(responses) == 1
                assert str(responses[0].get("text") or "").strip().upper() == "OK", "Authenticated Chat Completions probe failed"
                assert attempts == {"completion": 1}
                return
            if scenario == "arithmetic":
                server = NativePiRpcServer()
                text, side_effect = server._run_via_openai_completions_agent(
                    endpoint=endpoint, prompt="What is 2 + 2? Reply with only the numeric answer.",
                    system_prompt="Follow the user's output format exactly.", history=[], attachments=[],
                    job_id="release-arithmetic", cwd=temporary, thinking_level="off",
                    tool_policy="none", trusted_roots=[temporary], approval_timeout_ms=1000,
                    max_output_tokens=budget["max_output_tokens"], timeout_seconds=budget["timeout_seconds"],
                )
                assert not side_effect
                assert text.strip() == "4", "Live arithmetic result was not exactly 4"
            else:
                done = threading.Event()
                responses = []
                def completed(response):
                    responses.append(response)
                    done.set()
                if scenario == "translation":
                    service.send_text_message(
                        "Hello", callback=completed, history=[],
                        system_prompt="Translate the user's text into French. Reply only with the translation.",
                        temperature=0, model_name=selected,
                        max_output_tokens=budget["max_output_tokens"], timeout_seconds=budget["timeout_seconds"],
                    )
                else:
                    from PIL import Image, ImageDraw, ImageFont
                    image = Image.new("RGB", (700, 100), "white")
                    font = ImageFont.truetype("DejaVuSans.ttf", 40) if os.name != "nt" else ImageFont.truetype("arial.ttf", 40)
                    ImageDraw.Draw(image).text((15, 20), "HELLO RELEASE 123", fill="black", font=font)
                    raster = Path(temporary) / "synthetic.png"
                    image.save(raster)  # No metadata/embedded answer or precomputed OCR.
                    service.process_image(
                        [str(raster)], callback=completed, history=[],
                        custom_prompt_text="Recognize the text in this image. Return only the recognized text.",
                        model_full_name=selected,
                        max_output_tokens=budget["max_output_tokens"], timeout_seconds=budget["timeout_seconds"],
                    )
                assert done.wait(budget["timeout_seconds"]), "Live service timed out; no retry authorized"
                assert len(responses) == 1
                text = responses[0]["text"].strip()
                if scenario == "translation":
                    if text and text[-1] in ".!?,;:":
                        text = text[:-1]
                    assert text.casefold() == "bonjour", "Live French translation was not bonjour"
                else:
                    assert " ".join(text.split()) == "HELLO RELEASE 123", "Live OCR did not recognize the raster"
            assert attempts == {"completion": 1}, "Missing or extra real request"
            assert time.monotonic() - started <= budget["timeout_seconds"], "Live workflow exceeded its budget"
        finally:
            httpx.Client.send = original_send
            provider.client.close()
            assert CONFIG.read_bytes() == before, "Verification modified external configuration"


if __name__ == "__main__":
    try:
        _run(sys.argv[1], sys.argv[2])
    except AssertionError as exc:
        # Only our literal assertions are printed; never print SDK/config values.
        print(str(exc) if str(exc) else "Release assertion failed")
        sys.exit(1)
    except Exception:
        print("Release prerequisites or real request failed; inspect locally without exposing credentials")
        sys.exit(1)
