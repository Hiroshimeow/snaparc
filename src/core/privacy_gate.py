"""Privacy consent gate for local capture and remote-provider access."""

from __future__ import annotations

from enum import Enum
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
