from unittest import mock

import pytest

from src.agents.pi_contract import PiRuntimeConfig


def test_build_factory_legacy_is_removed_and_never_constructs_sdk(monkeypatch):
    from src.agents import pi_runtime_factory
    from src.agents.pi_runtime_factory import LegacyBackendRemovedError, build_pi_runtime_factory

    monkeypatch.setattr(
        pi_runtime_factory,
        "PiSdkRuntimeFactory",
        lambda: (_ for _ in ()).throw(AssertionError("SDK must not be constructed for removed legacy selection")),
    )
    with pytest.raises(LegacyBackendRemovedError, match="LEGACY_BACKEND_REMOVED"):
        build_pi_runtime_factory(PiRuntimeConfig(backend="legacy"))


def test_build_factory_passes_floored_bridge_timeouts_to_sdk_factory(monkeypatch):
    from src.agents import pi_runtime_factory

    captured = {}

    class FakeSdkFactory:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def start(self):
            return object()

    monkeypatch.setattr(pi_runtime_factory, "PiSdkRuntimeFactory", FakeSdkFactory)

    pi_runtime_factory.build_pi_runtime_factory({"backend": "sdk", "startup_timeout_ms": 6000, "shutdown_timeout_ms": 777})
    assert captured["startup_timeout_ms"] == 15000
    assert captured["shutdown_timeout_ms"] == 777

    pi_runtime_factory.build_pi_runtime_factory({"backend": "sdk", "startup_timeout_ms": 40000})
    assert captured["startup_timeout_ms"] == 40000


def test_sdk_runtime_factory_forwards_timeouts_to_default_transport(monkeypatch):
    from src.agents import pi_sdk_runtime

    captured = {}

    class FakeTransport:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(pi_sdk_runtime, "PiSdkBridgeTransport", FakeTransport)

    pi_sdk_runtime.PiSdkRuntimeFactory(startup_timeout_ms=21000, shutdown_timeout_ms=4000)
    assert captured["startup_timeout_ms"] == 21000
    assert captured["shutdown_timeout_ms"] == 4000

    captured.clear()
    pi_sdk_runtime.PiSdkRuntimeFactory()
    assert "startup_timeout_ms" not in captured


def test_build_factory_sdk_requires_compatible_bridge():
    from src.agents.pi_runtime_factory import build_pi_runtime_factory
    from src.agents.pi_sdk_transport import PiBridgeIncompatibleProtocol

    class FakeSdkFactory:
        def start(self):
            raise PiBridgeIncompatibleProtocol("bad protocol")

        def shutdown(self, timeout_ms=3000):
            pass

    with pytest.raises(PiBridgeIncompatibleProtocol):
        build_pi_runtime_factory(PiRuntimeConfig(backend="sdk"), sdk_factory=FakeSdkFactory())


def test_build_factory_omitted_config_prefers_sdk_when_healthy():
    from src.agents.pi_runtime_factory import build_pi_runtime_factory

    class HealthySdkFactory:
        def start(self):
            return object()

    healthy = HealthySdkFactory()
    assert build_pi_runtime_factory({}, sdk_factory=healthy) is healthy


def test_build_factory_auto_selects_sdk_when_healthy_and_reports_unavailable_bridge():
    from src.agents.pi_runtime_factory import build_pi_runtime_factory
    from src.agents.pi_sdk_transport import PiBridgeUnavailable

    class HealthySdkFactory:
        def start(self):
            return object()

    healthy = HealthySdkFactory()
    assert build_pi_runtime_factory(PiRuntimeConfig(backend="auto"), sdk_factory=healthy) is healthy

    class MissingSdkFactory:
        def start(self):
            raise PiBridgeUnavailable("missing")

        def shutdown(self, timeout_ms=3000):
            pass

    with pytest.raises(PiBridgeUnavailable, match="missing"):
        build_pi_runtime_factory(PiRuntimeConfig(backend="auto"), sdk_factory=MissingSdkFactory())


def test_application_factory_shutdown_is_owned_by_sdk_factory_once():
    from src.agents.pi_runtime_factory import build_pi_runtime_factory

    class HealthySdkFactory:
        def __init__(self):
            self.shutdown_calls = []

        def start(self):
            return object()

        def shutdown(self, timeout_ms=3000):
            self.shutdown_calls.append(timeout_ms)

    healthy = HealthySdkFactory()
    selected = build_pi_runtime_factory(PiRuntimeConfig(backend="auto"), sdk_factory=healthy)
    selected.shutdown(4321)
    assert healthy.shutdown_calls == [4321]


def test_runtime_factory_module_has_no_legacy_sidecar_import():
    from pathlib import Path
    import src.agents.pi_runtime_factory as factory_module

    source = Path(factory_module.__file__).read_text(encoding="utf-8")
    assert "pi_sidecar" not in source
    assert "PiSidecarRuntime" not in source
