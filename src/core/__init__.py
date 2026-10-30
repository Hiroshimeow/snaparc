"""Core Architecture Package for Screens-Trans-Chatbot v1.1."""

from src.core.action_context import ActionContextBuilder, ActionTimeContext, ForegroundWindowInfo
from src.core.dpi_capture import (
    DpiCoordinateEngine,
    MonitorDescriptor,
    VirtualScreenTopology,
    WindowMoveSafeCapturer,
)
from src.core.event_bus import AppEventBus
from src.core.memory_guard import MemoryGuard
from src.core.model_state import ModelStateStore
from src.core.ocr_pipeline import (
    HybridOcrEngine,
    OcrResult,
    RegionAnnotator,
    ScreenshotComparator,
    ScreenshotDiffResult,
)
from src.core.privacy_gate import ConsentScope, PrivacyConsentGate, TokenBudgetController, UsageRecord
from src.core.secret_store import SecretRedactor, SecretStore
from src.core.singleton_guard import CrashRecoveryJournal, SingleInstanceGuard
from src.core.skills_mcp import MCPClientAdapter, SkillPlugin, SkillRegistry

__all__ = [
    "ActionContextBuilder",
    "ActionTimeContext",
    "AppEventBus",
    "ConsentScope",
    "CrashRecoveryJournal",
    "DpiCoordinateEngine",
    "ForegroundWindowInfo",
    "HybridOcrEngine",
    "MCPClientAdapter",
    "MemoryGuard",
    "ModelStateStore",
    "MonitorDescriptor",
    "OcrResult",
    "PrivacyConsentGate",
    "RegionAnnotator",
    "ScreenshotComparator",
    "ScreenshotDiffResult",
    "SecretRedactor",
    "SecretStore",
    "SingleInstanceGuard",
    "SkillPlugin",
    "SkillRegistry",
    "TokenBudgetController",
    "UsageRecord",
    "VirtualScreenTopology",
    "WindowMoveSafeCapturer",
]
