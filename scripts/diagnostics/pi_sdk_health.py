from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.agents.pi_contract import PiRuntimeConfig  # noqa: E402
from src.agents.pi_sdk_transport import (  # noqa: E402
    PiBridgeError,
    PiSdkBridgeTransport,
    redact_diagnostic,
)


EXIT_READY = 0
EXIT_UNAVAILABLE = 2
EXIT_INVALID_CONFIG = 3
_VALID_BACKENDS = {"legacy", "sdk", "auto"}


class DiagnosticConfigError(ValueError):
    pass


def _load_config(path: Path) -> tuple[PiRuntimeConfig, dict[str, Any], str]:
    if not path.is_file():
        raise DiagnosticConfigError("configuration file is missing")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DiagnosticConfigError("configuration file is unreadable or invalid JSON") from exc
    if not isinstance(raw, dict):
        raise DiagnosticConfigError("configuration root must be an object")

    screenshot_chat = raw.get("screenshot_chat")
    if screenshot_chat is None:
        screenshot_chat = {}
    if not isinstance(screenshot_chat, dict):
        raise DiagnosticConfigError("screenshot_chat configuration must be an object")
    pi_payload = screenshot_chat.get("pi")
    if pi_payload is None:
        pi_payload = {}
    if not isinstance(pi_payload, dict):
        raise DiagnosticConfigError("screenshot_chat.pi configuration must be an object")

    backend_raw = pi_payload.get("backend", "legacy")
    backend = str(backend_raw or "legacy").strip().lower()
    if backend not in _VALID_BACKENDS:
        raise DiagnosticConfigError("screenshot_chat.pi.backend is invalid")

    runtime_config = PiRuntimeConfig.from_dict(pi_payload)
    model_ref = str(screenshot_chat.get("default_model") or "").strip()
    return runtime_config, screenshot_chat, model_ref


def check_session_dir_writable(path: str | os.PathLike[str]) -> bool:
    candidate = Path(path)
    while not candidate.exists() and candidate.parent != candidate:
        candidate = candidate.parent
    return candidate.is_dir() and os.access(candidate, os.W_OK)


def _invalid_report(message: str) -> dict[str, Any]:
    return {
        "status": "invalid_config",
        "error": redact_diagnostic(message),
    }


def collect_health(
    *,
    config_path: str | os.PathLike[str] = PROJECT_ROOT / "stc.json",
    transport_factory: Callable[[], PiSdkBridgeTransport] | None = None,
    writable_check: Callable[[str], bool] = check_session_dir_writable,
) -> tuple[int, dict[str, Any]]:
    try:
        runtime_config, _chat_config, model_ref = _load_config(Path(config_path))
    except DiagnosticConfigError as exc:
        return EXIT_INVALID_CONFIG, _invalid_report(str(exc))

    if transport_factory is None:
        transport = PiSdkBridgeTransport(
            startup_timeout_ms=runtime_config.bridge_startup_timeout_ms,
            shutdown_timeout_ms=runtime_config.shutdown_timeout_ms,
        )
    else:
        transport = transport_factory()
    startup_timeout_ms = int(getattr(transport, "startup_timeout_ms", runtime_config.bridge_startup_timeout_ms))
    shutdown_timeout_ms = max(1, int(runtime_config.shutdown_timeout_ms))
    session_dir = str(getattr(transport, "session_dir", PROJECT_ROOT / "history-xml" / "pi-sdk-sessions"))
    writable = False
    try:
        writable = bool(writable_check(session_dir))
        if not writable:
            return EXIT_INVALID_CONFIG, {
                "status": "invalid_config",
                "backend": {
                    "configured": runtime_config.backend,
                    "enabled": bool(runtime_config.enabled),
                },
                "sessionDirectory": {"path": session_dir, "writable": False},
                "error": "Pi SDK session directory is not writable",
            }

        probe = transport.probe_health(model_ref=model_ref)
        hello = probe["hello"]
        health = probe.get("health") if isinstance(probe.get("health"), dict) else {}
        model = health.get("model") if isinstance(health.get("model"), dict) else None
        if model is None:
            model = (
                {"configured": False, "available": None, "reason": "not_configured"}
                if not model_ref
                else {"configured": True, "available": None, "reason": "not_reported"}
            )

        report = {
            "status": "ready",
            "backend": {
                "configured": runtime_config.backend,
                "enabled": bool(runtime_config.enabled),
            },
            "node": {
                "path": str(getattr(transport, "node_executable", "")),
                "version": str(probe.get("nodeVersion") or ""),
            },
            "bridge": {
                "path": str(getattr(transport, "bridge_path", "")),
                "version": str(hello.bridge_version),
                "pid": int(hello.pid),
                "startupTimeoutMs": startup_timeout_ms,
            },
            "protocol": {
                "version": int(hello.protocol),
                "compatible": True,
            },
            "sdk": {
                "package": str(hello.sdk_package),
                "version": str(hello.sdk_version),
                "compatible": True,
            },
            "sessionDirectory": {"path": session_dir, "writable": True},
            "model": dict(model),
        }
        return EXIT_READY, report
    except PiBridgeError as exc:
        return EXIT_UNAVAILABLE, {
            "status": "unavailable",
            "backend": {
                "configured": runtime_config.backend,
                "enabled": bool(runtime_config.enabled),
            },
            "node": {"path": str(getattr(transport, "node_executable", ""))},
            "bridge": {
                "path": str(getattr(transport, "bridge_path", "")),
                "startupTimeoutMs": startup_timeout_ms,
            },
            "sessionDirectory": {"path": session_dir, "writable": writable},
            "error": redact_diagnostic(str(exc)),
        }
    finally:
        try:
            transport.shutdown(shutdown_timeout_ms)
        except Exception:
            pass


def render_human(report: dict[str, Any]) -> str:
    status = str(report.get("status") or "unknown").upper()
    lines = [f"Pi SDK: {status}"]
    backend = report.get("backend") if isinstance(report.get("backend"), dict) else {}
    if backend:
        lines.append(f"Backend: {backend.get('configured', '')} (enabled={bool(backend.get('enabled'))})")
    node = report.get("node") if isinstance(report.get("node"), dict) else {}
    if node.get("version"):
        lines.append(f"Node {node['version']}")
    bridge = report.get("bridge") if isinstance(report.get("bridge"), dict) else {}
    protocol = report.get("protocol") if isinstance(report.get("protocol"), dict) else {}
    if bridge.get("version") or protocol.get("version") is not None:
        lines.append(f"Bridge {bridge.get('version', '?')} / protocol {protocol.get('version', '?')}")
    sdk = report.get("sdk") if isinstance(report.get("sdk"), dict) else {}
    if sdk.get("version"):
        lines.append(f"SDK {sdk['version']}")
    session_dir = report.get("sessionDirectory") if isinstance(report.get("sessionDirectory"), dict) else {}
    if session_dir:
        lines.append(f"Session dir writable: {bool(session_dir.get('writable'))}")
    model = report.get("model") if isinstance(report.get("model"), dict) else {}
    if model:
        if not model.get("configured"):
            model_status = "not configured"
        elif model.get("available") is True:
            model_status = "available"
        elif model.get("available") is False:
            model_status = "unavailable"
        else:
            model_status = "unknown"
        lines.append(f"Model: {model_status}")
    if report.get("error"):
        lines.append(f"Error: {redact_diagnostic(str(report['error']))}")
    return "\n".join(lines)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only Pi SDK bridge health diagnostic.")
    parser.add_argument("--config", default=str(PROJECT_ROOT / "stc.json"), help="Path to stc.json")
    parser.add_argument("--human", action="store_true", help="Print concise human-readable status")
    return parser.parse_args(argv)


def main(
    argv: list[str] | None = None,
    *,
    transport_factory: Callable[[], PiSdkBridgeTransport] | None = None,
    writable_check: Callable[[str], bool] = check_session_dir_writable,
) -> int:
    args = _parse_args(argv)
    code, report = collect_health(
        config_path=args.config,
        transport_factory=transport_factory,
        writable_check=writable_check,
    )
    if args.human:
        print(render_human(report))
    else:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
