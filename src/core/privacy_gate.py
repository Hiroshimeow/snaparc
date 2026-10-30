"""Privacy Consent Gate & Token/Cost Accounting Controller.

Enforces:
1. Explicit user-scope permission checks for screen capture, microphone recording,
   clipboard reading, cloud provider uploads, and agent tool execution.
2. Offline-only mode that blocks external network egress while allowing local OCR & history.
3. Token, byte, latency, and USD cost accounting per provider/model/session with budget caps
   and zero-token provider readiness probes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
import threading
import time
from typing import Any


class ConsentScope(str, Enum):
    SCREEN_CAPTURE = "screen_capture"
    MICROPHONE_RECORD = "microphone_record"
    CLIPBOARD_READ = "clipboard_read"
    CLOUD_UPLOAD = "cloud_upload"
    AGENT_MUTATION = "agent_mutation"
    MCP_EXTERNAL = "mcp_external"


class PrivacyPermissionError(PermissionError):
    def __init__(self, scope: str, message: str):
        super().__init__(message)
        self.scope = scope


class PrivacyConsentGate:
    """Thread-safe consent and offline-mode policy enforcement."""

    def __init__(
        self,
        *,
        offline_mode: bool = False,
        allowed_scopes: set[str] | None = None,
        allowed_providers: set[str] | None = None,
    ):
        self._lock = threading.RLock()
        self.offline_mode = bool(offline_mode)
        if allowed_scopes is None:
            self._allowed_scopes = {
                ConsentScope.SCREEN_CAPTURE.value,
                ConsentScope.MICROPHONE_RECORD.value,
                ConsentScope.CLIPBOARD_READ.value,
                ConsentScope.CLOUD_UPLOAD.value,
            }
        else:
            self._allowed_scopes = {str(s) for s in allowed_scopes}
        self._allowed_providers = {str(p) for p in allowed_providers} if allowed_providers is not None else None
        self._audit_log: list[dict[str, Any]] = []

    def set_scope_consent(self, scope: str | ConsentScope, granted: bool) -> None:
        scope_val = scope.value if isinstance(scope, ConsentScope) else str(scope)
        with self._lock:
            if granted:
                self._allowed_scopes.add(scope_val)
            else:
                self._allowed_scopes.discard(scope_val)
            self._record_audit("set_consent", scope_val, granted=granted)

    def set_offline_mode(self, offline: bool) -> None:
        with self._lock:
            self.offline_mode = bool(offline)
            self._record_audit("set_offline_mode", "network", granted=not offline)

    def check_permission(self, scope: str | ConsentScope, *, provider: str = "", raise_on_deny: bool = False) -> bool:
        scope_val = scope.value if isinstance(scope, ConsentScope) else str(scope)
        with self._lock:
            if self.offline_mode and scope_val in {ConsentScope.CLOUD_UPLOAD.value, ConsentScope.MCP_EXTERNAL.value}:
                self._record_audit("check_denied_offline", scope_val, provider=provider, granted=False)
                if raise_on_deny:
                    raise PrivacyPermissionError(
                        scope_val,
                        f"Action '{scope_val}' blocked because Offline Mode is active.",
                    )
                return False

            if scope_val not in self._allowed_scopes:
                self._record_audit("check_denied_scope", scope_val, provider=provider, granted=False)
                if raise_on_deny:
                    raise PrivacyPermissionError(
                        scope_val,
                        f"Permission for '{scope_val}' has not been granted by the user.",
                    )
                return False

            if (
                scope_val == ConsentScope.CLOUD_UPLOAD.value
                and provider
                and self._allowed_providers is not None
                and provider not in self._allowed_providers
            ):
                self._record_audit("check_denied_provider", scope_val, provider=provider, granted=False)
                if raise_on_deny:
                    raise PrivacyPermissionError(
                        scope_val,
                        f"Cloud upload to provider '{provider}' is not in the user-approved allowlist.",
                    )
                return False

            self._record_audit("check_allowed", scope_val, provider=provider, granted=True)
            return True

    def _record_audit(self, action: str, scope: str, *, provider: str = "", granted: bool = True) -> None:
        self._audit_log.append(
            {
                "timestamp": time.time(),
                "action": action,
                "scope": scope,
                "provider": provider,
                "granted": granted,
            }
        )
        if len(self._audit_log) > 500:
            self._audit_log = self._audit_log[-500:]

    def audit_trail(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._audit_log)


# Approximate reference pricing per 1M tokens (USD) for cost visibility
_DEFAULT_PRICING_PER_1M: dict[str, tuple[float, float]] = {
    "gemini-2.5-flash": (0.15, 0.60),
    "gemini-2.5-flash-lite": (0.075, 0.30),
    "gemini-2.5-pro": (1.25, 5.00),
    "gemini-3-flash": (0.15, 0.60),
    "gemini-3-pro": (1.25, 5.00),
    "gpt-4o": (2.50, 10.00),
    "gpt-5.4": (2.00, 8.00),
    "gpt-5.4-mini": (0.20, 0.80),
    "claude-sonnet-4-6": (3.00, 15.00),
    "default": (0.50, 1.50),
}


@dataclass(frozen=True)
class UsageRecord:
    run_id: str
    session_id: str
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    upload_bytes: int
    download_bytes: int
    latency_ms: float
    estimated_cost_usd: float
    fallback_from: str = ""
    timestamp: float = field(default_factory=time.time)


class TokenBudgetController:
    """Tracks token consumption, network bytes, and estimated cost without ever sending
    paid tokens during readiness probes."""

    def __init__(self, *, max_session_tokens: int = 500_000, max_daily_cost_usd: float = 10.0):
        self.max_session_tokens = max(1, int(max_session_tokens))
        self.max_daily_cost_usd = max(0.01, float(max_daily_cost_usd))
        self._lock = threading.RLock()
        self._records: list[UsageRecord] = []

    @staticmethod
    def estimate_tokens(text: str = "", image_bytes_len: int = 0) -> int:
        text_tokens = max(0, math.ceil(len(str(text or "").encode("utf-8")) / 4.0))
        # Vision models typically charge ~258 tokens per image tile
        image_tokens = 258 if image_bytes_len > 0 else 0
        if image_bytes_len > 1_000_000:
            image_tokens += 258 * (image_bytes_len // 1_000_000)
        return text_tokens + image_tokens

    @staticmethod
    def estimate_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
        short_model = str(model or "").rsplit("/", 1)[-1].lower()
        rates = _DEFAULT_PRICING_PER_1M.get(short_model)
        if rates is None:
            for key, candidate_rates in _DEFAULT_PRICING_PER_1M.items():
                if key != "default" and key in short_model:
                    rates = candidate_rates
                    break
        if rates is None:
            rates = _DEFAULT_PRICING_PER_1M["default"]
        in_rate, out_rate = rates
        cost = (prompt_tokens / 1_000_000.0) * in_rate + (completion_tokens / 1_000_000.0) * out_rate
        return round(cost, 8)

    def check_budget(self, session_id: str, estimated_new_tokens: int = 0) -> tuple[bool, str]:
        with self._lock:
            session_tokens = sum(r.total_tokens for r in self._records if r.session_id == session_id)
            total_cost = sum(r.estimated_cost_usd for r in self._records)
            if session_tokens + estimated_new_tokens > self.max_session_tokens:
                return False, f"Session token budget exceeded ({session_tokens + estimated_new_tokens} > {self.max_session_tokens})"
            if total_cost >= self.max_daily_cost_usd:
                return False, f"Daily cost budget exceeded (${total_cost:.4f} >= ${self.max_daily_cost_usd:.2f})"
            return True, "ok"

    def record_usage(
        self,
        *,
        run_id: str,
        session_id: str = "default",
        provider: str,
        model: str,
        prompt_text: str = "",
        completion_text: str = "",
        image_bytes_len: int = 0,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        latency_ms: float = 0.0,
        fallback_from: str = "",
    ) -> UsageRecord:
        p_tok = prompt_tokens if prompt_tokens is not None else self.estimate_tokens(prompt_text, image_bytes_len)
        c_tok = completion_tokens if completion_tokens is not None else self.estimate_tokens(completion_text, 0)
        tot_tok = p_tok + c_tok
        up_bytes = len(str(prompt_text or "").encode("utf-8")) + int(image_bytes_len)
        down_bytes = len(str(completion_text or "").encode("utf-8"))
        cost = self.estimate_cost_usd(model, p_tok, c_tok)
        rec = UsageRecord(
            run_id=str(run_id or "run"),
            session_id=str(session_id or "default"),
            provider=str(provider or "unknown"),
            model=str(model or "unknown"),
            prompt_tokens=p_tok,
            completion_tokens=c_tok,
            total_tokens=tot_tok,
            upload_bytes=up_bytes,
            download_bytes=down_bytes,
            latency_ms=round(float(latency_ms), 2),
            estimated_cost_usd=cost,
            fallback_from=str(fallback_from or ""),
        )
        with self._lock:
            self._records.append(rec)
        return rec

    def summary(self, session_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            items = [r for r in self._records if session_id is None or r.session_id == session_id]
        return {
            "requests": len(items),
            "prompt_tokens": sum(r.prompt_tokens for r in items),
            "completion_tokens": sum(r.completion_tokens for r in items),
            "total_tokens": sum(r.total_tokens for r in items),
            "upload_bytes": sum(r.upload_bytes for r in items),
            "download_bytes": sum(r.download_bytes for r in items),
            "estimated_cost_usd": round(sum(r.estimated_cost_usd for r in items), 6),
        }
