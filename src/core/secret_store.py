"""OS Secret Store & Diagnostic Redaction Engine.

Provides:
1. Windows DPAPI (`CryptProtectData` / `CryptUnprotectData`) encrypted secret storage
   scoped to the current Windows user, plus environment variable resolution and
   in-memory vault for isolated tests.
2. Zero `.env` / plaintext token persistence.
3. Comprehensive secret redaction for logs, errors, and diagnostics.
"""

from __future__ import annotations

import base64
import ctypes
import json
import os
from pathlib import Path
import re
import sys
import threading


_SECRET_PATTERNS = (
    (re.compile(r"(authorization\s*:\s*bearer\s+)[^\s'\"<>]+", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"([?&](?:api[_-]?key|token|access[_-]?token|refresh[_-]?token)=)[^&\s'\"<>]+", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"((?:api[_-]?key|token|secret|password|refresh_token|client_secret)\s*[=:]\s*)[^\s'\",}]+", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"), "[REDACTED_SK_KEY]"),
    (re.compile(r"\bAIza[A-Za-z0-9_-]{16,}\b"), "[REDACTED_GOOGLE_KEY]"),
    (re.compile(r"\bGOCSPX-[A-Za-z0-9_-]{10,}\b"), "[REDACTED_OAUTH_SECRET]"),
    (re.compile(r"\b1//[A-Za-z0-9_-]{20,}\b"), "[REDACTED_REFRESH_TOKEN]"),
)


class SecretRedactor:
    """Scrubs sensitive credentials, tokens, and large base64 payloads from strings and dicts."""

    @classmethod
    def redact(cls, text: str | None, max_len: int = 2000) -> str:
        value = str(text or "")
        for pattern, replacement in _SECRET_PATTERNS:
            value = pattern.sub(replacement, value)
        value = re.sub(r"[A-Za-z0-9+/]{200,}={0,2}", "[REDACTED_BASE64]", value)
        if max_len and len(value) > max_len:
            return value[: max_len - 3] + "..."
        return value

    @classmethod
    def redact_obj(cls, obj):
        if isinstance(obj, str):
            return cls.redact(obj)
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                key_str = str(k).lower()
                if any(s in key_str for s in ("apikey", "api_key", "secret", "token", "password", "authorization")):
                    out[k] = "[REDACTED]" if v else ""
                else:
                    out[k] = cls.redact_obj(v)
            return out
        if isinstance(obj, list):
            return [cls.redact_obj(x) for x in obj]
        return obj


class _DpapiCipher:
    """Windows DPAPI wrapper using CryptProtectData / CryptUnprotectData."""

    CRYPTPROTECT_UI_FORBIDDEN = 0x01

    @classmethod
    def is_available(cls) -> bool:
        return sys.platform == "win32" and hasattr(ctypes, "windll") and hasattr(ctypes.windll, "crypt32")

    @classmethod
    def encrypt(cls, plaintext: bytes) -> bytes:
        if not cls.is_available():
            raise RuntimeError("Windows DPAPI is only available on win32.")
        import ctypes.wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [
                ("cbData", ctypes.wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char)),
            ]

        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        in_buf = ctypes.create_string_buffer(plaintext, len(plaintext))
        in_blob = DATA_BLOB(len(plaintext), in_buf)
        out_blob = DATA_BLOB()

        if not crypt32.CryptProtectData(
            ctypes.byref(in_blob),
            "ScreensTransChatbotSecret",
            None,
            None,
            None,
            cls.CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(out_blob),
        ):
            raise OSError("CryptProtectData failed")
        try:
            return ctypes.string_at(out_blob.pbData, out_blob.cbData)
        finally:
            kernel32.LocalFree(out_blob.pbData)

    @classmethod
    def decrypt(cls, ciphertext: bytes) -> bytes:
        if not cls.is_available():
            raise RuntimeError("Windows DPAPI is only available on win32.")
        import ctypes.wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [
                ("cbData", ctypes.wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char)),
            ]

        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        in_buf = ctypes.create_string_buffer(ciphertext, len(ciphertext))
        in_blob = DATA_BLOB(len(ciphertext), in_buf)
        out_blob = DATA_BLOB()

        if not crypt32.CryptUnprotectData(
            ctypes.byref(in_blob),
            None,
            None,
            None,
            None,
            cls.CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(out_blob),
        ):
            raise OSError("CryptUnprotectData failed")
        try:
            return ctypes.string_at(out_blob.pbData, out_blob.cbData)
        finally:
            kernel32.LocalFree(out_blob.pbData)


class SecretStore:
    """Unified OS-backed secret store with DPAPI on Windows, environment lookup, and memory vault."""

    def __init__(self, storage_path: str | Path | None = None, allow_env_fallback: bool = True):
        if storage_path is None:
            local_app_data = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
            storage_path = Path(local_app_data) / ".stc_multimodal" / "secrets.dpapi.json"
        self.storage_path = Path(storage_path)
        self.allow_env_fallback = allow_env_fallback
        self._lock = threading.RLock()
        self._memory_vault: dict[str, str] = {}
        self._dpapi_store: dict[str, str] = {}
        self._load_dpapi_store()

    def _load_dpapi_store(self) -> None:
        with self._lock:
            if not self.storage_path.is_file():
                return
            try:
                payload = json.loads(self.storage_path.read_text(encoding="utf-8"))
                if isinstance(payload, dict) and isinstance(payload.get("entries"), dict):
                    self._dpapi_store = {
                        str(k): str(v) for k, v in payload["entries"].items() if isinstance(v, str)
                    }
            except Exception:
                self._dpapi_store = {}

    def _save_dpapi_store(self) -> None:
        with self._lock:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self.storage_path.with_suffix(".tmp")
            payload = {"version": 1, "backend": "win32-dpapi", "entries": self._dpapi_store}
            tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            os.replace(tmp_path, self.storage_path)

    def set_secret(self, key: str, value: str, *, persist_dpapi: bool = True) -> str:
        clean_key = str(key or "").strip()
        if not clean_key:
            raise ValueError("Secret key must not be empty")
        clean_val = str(value or "")
        with self._lock:
            if persist_dpapi and _DpapiCipher.is_available():
                encrypted = _DpapiCipher.encrypt(clean_val.encode("utf-8"))
                self._dpapi_store[clean_key] = base64.b64encode(encrypted).decode("ascii")
                self._save_dpapi_store()
                return "dpapi"
            self._memory_vault[clean_key] = clean_val
            return "memory"

    def get_secret(self, key: str, default: str = "") -> str:
        clean_key = str(key or "").strip()
        if not clean_key:
            return default
        with self._lock:
            if clean_key in self._memory_vault:
                return self._memory_vault[clean_key]
            if clean_key in self._dpapi_store and _DpapiCipher.is_available():
                try:
                    raw = base64.b64decode(self._dpapi_store[clean_key].encode("ascii"))
                    return _DpapiCipher.decrypt(raw).decode("utf-8")
                except Exception:
                    pass
        if self.allow_env_fallback:
            env_candidates = [
                clean_key,
                clean_key.upper().replace("-", "_").replace(".", "_"),
                f"STC_{clean_key.upper().replace('-', '_').replace('.', '_')}",
            ]
            for env_name in env_candidates:
                val = os.environ.get(env_name)
                if val:
                    return val
        return default

    def delete_secret(self, key: str) -> bool:
        clean_key = str(key or "").strip()
        with self._lock:
            removed = False
            if clean_key in self._memory_vault:
                del self._memory_vault[clean_key]
                removed = True
            if clean_key in self._dpapi_store:
                del self._dpapi_store[clean_key]
                self._save_dpapi_store()
                removed = True
            return removed

    def resolve_config_secret(self, raw_value: str | None, provider_name: str = "") -> str:
        """Resolves placeholder tokens like `<GOOGLE_AI_STUDIO_API_KEY>` or `secret://...`
        from the OS Secret Store / environment without ever storing plaintext keys in config."""
        val = str(raw_value or "").strip()
        if not val:
            if provider_name:
                return self.get_secret(f"{provider_name}_API_KEY", "")
            return ""
        if val.startswith("secret://"):
            secret_key = val[len("secret://") :].strip()
            return self.get_secret(secret_key, "")
        if val.startswith("<") and val.endswith(">"):
            placeholder_key = val[1:-1].strip()
            resolved = self.get_secret(placeholder_key, "")
            if resolved:
                return resolved
            if provider_name:
                return self.get_secret(f"{provider_name}_API_KEY", "")
            return ""
        return val

    @staticmethod
    def mask_secret(secret: str | None) -> str:
        val = str(secret or "").strip()
        if not val or (val.startswith("<") and val.endswith(">")):
            return "unconfigured"
        if len(val) <= 8:
            return "****"
        return f"{val[:4]}...{val[-4:]}"
