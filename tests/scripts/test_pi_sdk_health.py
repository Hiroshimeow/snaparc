from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.agents.pi_sdk_transport import PiBridgeIncompatibleProtocol, PiBridgeUnavailable


class FakeTransport:
    node_executable = r"C:\Program Files\nodejs\node.exe"
    bridge_path = r"E:\repo\sidecars\pi-sdk\src\main.mjs"
    session_dir = r"E:\repo\history-xml\pi-sdk-sessions"

    def __init__(self, *, probe=None, error=None):
        self.probe = probe or {
            "nodeVersion": "v22.22.2",
            "hello": SimpleNamespace(
                protocol=1,
                bridge_version="0.1.0",
                sdk_package="@earendil-works/pi-coding-agent",
                sdk_version="0.84.2",
                pid=1234,
            ),
            "health": {
                "status": "ready",
                "model": {
                    "configured": True,
                    "available": True,
                    "provider": "provider",
                    "id": "model",
                },
            },
        }
        self.error = error
        self.probe_calls = []
        self.shutdown_calls = []

    def probe_health(self, model_ref=""):
        self.probe_calls.append(model_ref)
        if self.error is not None:
            raise self.error
        return self.probe

    def shutdown(self, timeout_ms=3000):
        self.shutdown_calls.append(timeout_ms)


def write_config(path: Path, *, backend="sdk", model="provider/model") -> None:
    path.write_text(
        json.dumps(
            {
                "models": {"providers": {}},
                "screenshot_chat": {
                    "default_model": model,
                    "pi": {
                        "backend": backend,
                        "enabled": True,
                        "shutdown_timeout_ms": 321,
                    },
                },
            }
        ),
        encoding="utf-8",
    )


def load_module():
    from scripts.diagnostics import pi_sdk_health

    return pi_sdk_health


def test_ready_probe_reports_production_paths_versions_backend_model_and_shutdown(tmp_path):
    module = load_module()
    config_path = tmp_path / "stc.json"
    write_config(config_path)
    fake = FakeTransport()

    code, report = module.collect_health(
        config_path=config_path,
        transport_factory=lambda: fake,
        writable_check=lambda _path: True,
    )

    assert code == 0
    assert report["status"] == "ready"
    assert report["backend"] == {"configured": "sdk", "enabled": True}
    assert report["node"] == {"path": fake.node_executable, "version": "v22.22.2"}
    assert report["bridge"]["path"] == fake.bridge_path
    assert report["bridge"]["version"] == "0.1.0"
    assert report["protocol"] == {"version": 1, "compatible": True}
    assert report["sdk"] == {
        "package": "@earendil-works/pi-coding-agent",
        "version": "0.84.2",
        "compatible": True,
    }
    assert report["sessionDirectory"] == {"path": fake.session_dir, "writable": True}
    assert report["model"]["available"] is True
    assert fake.probe_calls == ["provider/model"]
    assert fake.shutdown_calls == [321]


def test_default_transport_gets_floored_startup_timeout_and_report_exposes_it(tmp_path, monkeypatch):
    module = load_module()
    config_path = tmp_path / "stc.json"
    write_config(config_path)
    created = []

    class RecordingTransport(FakeTransport):
        def __init__(self, **kwargs):
            super().__init__()
            self.startup_timeout_ms = kwargs["startup_timeout_ms"]
            created.append(kwargs)

    monkeypatch.setattr(module, "PiSdkBridgeTransport", RecordingTransport)

    code, report = module.collect_health(config_path=config_path, writable_check=lambda _path: True)

    assert code == 0
    assert created == [{"startup_timeout_ms": 15000, "shutdown_timeout_ms": 321}]
    assert report["bridge"]["startupTimeoutMs"] == 15000


def test_injected_transport_factory_is_still_called_without_arguments(tmp_path):
    module = load_module()
    config_path = tmp_path / "stc.json"
    write_config(config_path)
    fake = FakeTransport()

    _code, report = module.collect_health(
        config_path=config_path,
        transport_factory=lambda: fake,
        writable_check=lambda _path: True,
    )

    assert report["bridge"]["startupTimeoutMs"] == 15000


def test_json_default_and_human_render_are_concise_and_secret_free(tmp_path, capsys):
    module = load_module()
    config_path = tmp_path / "stc.json"
    write_config(config_path)
    fake = FakeTransport()

    exit_code = module.main(
        ["--config", str(config_path)],
        transport_factory=lambda: fake,
        writable_check=lambda _path: True,
    )
    captured = capsys.readouterr()
    parsed = json.loads(captured.out)
    assert exit_code == 0
    assert parsed["status"] == "ready"
    assert captured.err == ""

    fake_human = FakeTransport()
    exit_code = module.main(
        ["--config", str(config_path), "--human"],
        transport_factory=lambda: fake_human,
        writable_check=lambda _path: True,
    )
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Pi SDK: READY" in captured.out
    assert "Node v22.22.2" in captured.out
    assert "SDK 0.84.2" in captured.out
    assert "provider/model" not in captured.out


@pytest.mark.parametrize(
    "error",
    [
        PiBridgeUnavailable("node missing"),
        PiBridgeIncompatibleProtocol("protocol mismatch"),
    ],
)
def test_unavailable_or_incompatible_returns_2_and_always_shuts_down(tmp_path, error):
    module = load_module()
    config_path = tmp_path / "stc.json"
    write_config(config_path)
    fake = FakeTransport(error=error)

    code, report = module.collect_health(
        config_path=config_path,
        transport_factory=lambda: fake,
        writable_check=lambda _path: True,
    )

    assert code == 2
    assert report["status"] == "unavailable"
    assert fake.shutdown_calls == [321]


def test_invalid_config_returns_3_without_spawning_probe(tmp_path):
    module = load_module()
    config_path = tmp_path / "stc.json"
    config_path.write_text('{"screenshot_chat":{"pi":{"backend":"future"}}}', encoding="utf-8")
    constructed = []

    code, report = module.collect_health(
        config_path=config_path,
        transport_factory=lambda: constructed.append(True),
        writable_check=lambda _path: True,
    )

    assert code == 3
    assert report["status"] == "invalid_config"
    assert constructed == []


def test_unwritable_session_directory_is_invalid_local_config_and_probe_is_shutdown(tmp_path):
    module = load_module()
    config_path = tmp_path / "stc.json"
    write_config(config_path)
    fake = FakeTransport()

    code, report = module.collect_health(
        config_path=config_path,
        transport_factory=lambda: fake,
        writable_check=lambda _path: False,
    )

    assert code == 3
    assert report["status"] == "invalid_config"
    assert report["sessionDirectory"]["writable"] is False
    assert fake.shutdown_calls == [321]


def test_diagnostic_redacts_probe_failure_and_never_modifies_session_files(tmp_path):
    module = load_module()
    config_path = tmp_path / "stc.json"
    write_config(config_path)
    session_dir = tmp_path / "pi-sdk-sessions"
    session_dir.mkdir()
    binding = session_dir / "bindings-v1.json"
    session = session_dir / "existing.jsonl"
    binding.write_text('{"version":1,"bindings":{}}', encoding="utf-8")
    session.write_text('{"type":"session"}\n', encoding="utf-8")
    before = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in (binding, session)}

    secret = "bearer-sensitive-token"
    base64 = "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo="
    fake = FakeTransport(error=PiBridgeUnavailable(f"Authorization: Bearer {secret} prompt='TOP SECRET' data='{base64}'"))
    fake.session_dir = str(session_dir)

    code, report = module.collect_health(
        config_path=config_path,
        transport_factory=lambda: fake,
        writable_check=lambda _path: True,
    )

    serialized = json.dumps(report)
    assert code == 2
    for forbidden in (secret, "TOP SECRET", base64):
        assert forbidden not in serialized
    assert "[REDACTED]" in serialized
    after = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in (binding, session)}
    assert after == before
