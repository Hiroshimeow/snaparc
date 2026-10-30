from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, runtime_checkable


PiEventSink = Callable[[dict[str, Any]], None]

PI_BRIDGE_MIN_STARTUP_TIMEOUT_MS = 15000


@dataclass
class PiAvailability:
    available: bool
    reason: str
    executable: str = ""
    package_available: bool = False
    package_origin: str = ""
    python_executable: str = ""
    detail: str = ""


@dataclass
class PiRuntimeConfig:
    backend: str = "auto"
    enabled: bool = False
    auto_detect: bool = True
    force_disabled: bool = False
    executable: str = ""
    agent_names: list[str] = field(default_factory=lambda: ["pi"])
    fallback_to_llm: bool = True
    show_timeline: bool = True
    approval_mode: str = "tk_modal"
    approval_timeout_ms: int = 120000
    tool_policy: str = "read_only"
    trusted_roots: list[str] = field(default_factory=lambda: ["."])
    startup_timeout_ms: int = 15000
    first_event_timeout_ms: int = 30000
    shutdown_timeout_ms: int = 3000
    crash_fallback_to_llm: bool = False

    @property
    def bridge_startup_timeout_ms(self) -> int:
        # Floor: older configs carry an explicit 6000 that never took effect and is too tight for node cold start.
        return max(int(self.startup_timeout_ms), PI_BRIDGE_MIN_STARTUP_TIMEOUT_MS)

    @classmethod
    def from_dict(cls, payload: dict | None) -> "PiRuntimeConfig":
        data = payload if isinstance(payload, dict) else {}
        names = data.get("agent_names", ["pi"])
        if not isinstance(names, list):
            names = ["pi"]
        backend = str(data.get("backend") or "auto").strip().lower()
        if backend not in {"legacy", "sdk", "auto"}:
            backend = "auto"
        tool_policy = str(data.get("tool_policy") or "read_only").strip().lower()
        if tool_policy not in {"none", "read_only", "approve_mutations"}:
            tool_policy = "none"
        raw_roots = data.get("trusted_roots")
        if raw_roots is None:
            trusted_roots = ["."]
        elif isinstance(raw_roots, list):
            trusted_roots = [str(item).strip() for item in raw_roots if str(item).strip()]
        else:
            trusted_roots = []
        return cls(
            backend=backend,
            enabled=bool(data.get("enabled", False)),
            auto_detect=bool(data.get("auto_detect", True)),
            force_disabled=bool(data.get("force_disabled", False)),
            executable=str(data.get("executable") or ""),
            agent_names=[str(item).strip().lower() for item in names if str(item).strip()],
            fallback_to_llm=bool(data.get("fallback_to_llm", True)),
            show_timeline=bool(data.get("show_timeline", True)),
            approval_mode=str(data.get("approval_mode") or "tk_modal"),
            approval_timeout_ms=int(data.get("approval_timeout_ms", 120000) or 120000),
            tool_policy=tool_policy,
            trusted_roots=trusted_roots,
            startup_timeout_ms=int(data.get("startup_timeout_ms", 15000) or 15000),
            first_event_timeout_ms=int(data.get("first_event_timeout_ms", 30000) or 30000),
            shutdown_timeout_ms=int(data.get("shutdown_timeout_ms", 3000) or 3000),
            crash_fallback_to_llm=bool(data.get("crash_fallback_to_llm", False)),
        )


@dataclass
class PiRunRequest:
    job_id: str
    session_id: str
    message_id: str
    responder_name: str
    prompt_text: str
    system_prompt: str = ""
    history: list[dict] = field(default_factory=list)
    attachments: list[dict] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PiApprovalDecision:
    approval_id: str
    approved: bool
    reason: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    timed_out: bool = False
    canceled: bool = False


@dataclass
class PiRunResult:
    text: str = ""
    error: str = ""
    fallback_to_llm: bool = False
    side_effect_started: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class PiRuntime(Protocol):
    def run(self, request: PiRunRequest, cancel_event: threading.Event) -> PiRunResult: ...

    def cancel(self, reason: str = "") -> None: ...

    def resolve_approval(
        self,
        job_id: str,
        approval_id: str,
        approved: bool,
        reason: str = "",
        payload: dict[str, Any] | None = None,
    ) -> bool: ...

    def close(self, timeout_ms: int = 3000) -> None: ...


@runtime_checkable
class PiRuntimeFactory(Protocol):
    def __call__(self, config: PiRuntimeConfig, emit_event: PiEventSink) -> PiRuntime: ...

    def shutdown(self, timeout_ms: int = 3000) -> None: ...
