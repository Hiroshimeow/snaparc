from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from src.agents.pi_contract import PiAvailability, PiEventSink, PiRuntime, PiRuntimeConfig
from src.agents.pi_rpc_bridge import resolve_pi_cli
from src.agents.pi_sdk_runtime import PiSdkRuntimeFactory
from src.agents.pi_sdk_transport import emit_observability


class LegacyBackendRemovedError(RuntimeError):
    code = "LEGACY_BACKEND_REMOVED"

    def __init__(self):
        super().__init__(
            "LEGACY_BACKEND_REMOVED: the legacy sidecar backend was replaced by native Pi-RPC; "
            "use screenshot_chat.pi.backend=rpc, auto, or sdk"
        )


def detect_pi_availability(config: PiRuntimeConfig | dict | None = None) -> PiAvailability:
    runtime_config = config if isinstance(config, PiRuntimeConfig) else PiRuntimeConfig.from_dict(config)
    if runtime_config.force_disabled:
        return PiAvailability(False, "disabled_by_user", detail="Pi-RPC runtime is disabled by user configuration.")
    if not runtime_config.enabled and not runtime_config.auto_detect:
        return PiAvailability(False, "disabled_by_user", detail="Pi-RPC auto-detection is disabled by user configuration.")
    if runtime_config.backend == "legacy":
        return PiAvailability(False, "legacy_backend_removed", detail=str(LegacyBackendRemovedError()))

    bridge_py = Path(__file__).with_name("pi_rpc_bridge.py")
    if not bridge_py.is_file():
        return PiAvailability(False, "missing_bridge", executable=sys.executable, detail="Pi-RPC bridge module is missing.")

    pi_cli = resolve_pi_cli(runtime_config.executable)
    return PiAvailability(
        True,
        "available",
        executable=pi_cli or sys.executable,
        package_available=True,
        package_origin=os.fspath(bridge_py),
        detail="Native Pi-RPC bridge is available.",
    )


def build_pi_runtime_factory(
    config: PiRuntimeConfig | dict | None,
    emit_event=None,
    *,
    sdk_factory=None,
    observability_sink=None,
):
    del emit_event
    runtime_config = config if isinstance(config, PiRuntimeConfig) else PiRuntimeConfig.from_dict(config)
    if runtime_config.backend == "legacy":
        emit_observability(
            observability_sink,
            "pi_backend_selected",
            backend="legacy",
            reason_code="legacy_backend_removed",
        )
        raise LegacyBackendRemovedError()

    factory = sdk_factory or PiSdkRuntimeFactory(
        observability_sink=observability_sink,
        startup_timeout_ms=runtime_config.bridge_startup_timeout_ms,
        shutdown_timeout_ms=runtime_config.shutdown_timeout_ms,
    )
    hello = factory.start()
    emit_observability(
        observability_sink,
        "pi_backend_selected",
        backend="sdk",
        reason_code="explicit_sdk" if runtime_config.backend in ("sdk", "rpc") else "auto_sdk_healthy",
        bridge_version=getattr(hello, "bridge_version", ""),
        protocol_version=getattr(hello, "protocol", 0),
        sdk_version=getattr(hello, "sdk_version", ""),
    )
    return factory
