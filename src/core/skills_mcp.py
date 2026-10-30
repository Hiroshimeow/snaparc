"""Local Skill Plugin Registry & Optional MCP (Model Context Protocol) Adapter.

Provides:
1. Built-in extensible skill plugins (`ocr_extract`, `translate_multilingual`,
   `compare_screenshots`, `explain_error`, `extract_markdown_table`) callable from
   Floating UI, Chatbot `@skill`, or Agent workflows.
2. Optional, privacy-preserving MCP Client Adapter (`MCPClientAdapter`) for external
   tools or Mind Palace memory reuse — disabled by default unless explicitly opted in,
   and enforcing strict tool allowlists and user-scope consent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from src.core.ocr_pipeline import HybridOcrEngine, ScreenshotComparator
from src.core.privacy_gate import ConsentScope, PrivacyConsentGate
from src.core.secret_store import SecretRedactor


@dataclass(frozen=True)
class SkillPlugin:
    skill_id: str
    name: str
    description: str
    system_prompt: str
    requires_image: bool = False
    handler: Callable[[dict[str, Any]], dict[str, Any]] | None = None


class SkillRegistry:
    """Registry of local multimodal skills available to the assistant and agent runtime."""

    def __init__(self, ocr_engine: HybridOcrEngine | None = None):
        self.ocr_engine = ocr_engine or HybridOcrEngine()
        self.comparator = ScreenshotComparator(self.ocr_engine)
        self._skills: dict[str, SkillPlugin] = {}
        self._register_defaults()

    def _register_defaults(self) -> None:
        self.register(
            SkillPlugin(
                skill_id="ocr_extract",
                name="Exact OCR Extract",
                description="Extract verbatim text and layout blocks from screenshots offline or via vision model.",
                system_prompt="Extract all text from the image verbatim, preserving line breaks, code blocks, and formatting.",
                requires_image=True,
                handler=self._handle_ocr_extract,
            )
        )
        self.register(
            SkillPlugin(
                skill_id="translate_multilingual",
                name="Multilingual Translate (VI/EN/JA)",
                description="Translate screenshot or clipboard text accurately between Vietnamese, English, and Japanese.",
                system_prompt="Translate the content accurately while preserving technical terms, code identifiers, and tone.",
                requires_image=False,
                handler=self._handle_translate,
            )
        )
        self.register(
            SkillPlugin(
                skill_id="compare_screenshots",
                name="Compare Screenshots (Visual + OCR Diff)",
                description="Compare two screenshots side-by-side to highlight pixel changes and text diffs.",
                system_prompt="Compare the two screenshots and summarize all visual and textual changes.",
                requires_image=True,
                handler=self._handle_compare,
            )
        )
        self.register(
            SkillPlugin(
                skill_id="explain_error",
                name="Root-Cause Error & Code Diagnostic",
                description="Analyze stack traces, terminal errors, or UI dialogs captured on screen.",
                system_prompt="Identify the exact root cause of the error shown and provide a minimal, verified fix.",
                requires_image=False,
            )
        )
        self.register(
            SkillPlugin(
                skill_id="extract_markdown_table",
                name="Extract Structured Table",
                description="Convert tabular data in a screenshot into a clean Markdown/CSV table.",
                system_prompt="Extract the table in the image into a clean Markdown pipe table with exact numbers.",
                requires_image=True,
            )
        )

    def register(self, skill: SkillPlugin) -> None:
        self._skills[skill.skill_id] = skill

    def get(self, skill_id: str) -> SkillPlugin | None:
        return self._skills.get(str(skill_id or "").strip().lower())

    def list_skills(self) -> list[SkillPlugin]:
        return list(self._skills.values())

    def execute_local(self, skill_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        skill = self.get(skill_id)
        if skill is None:
            return {"ok": False, "error": f"Unknown skill: {skill_id}"}
        if skill.handler is None:
            return {
                "ok": True,
                "skill_id": skill.skill_id,
                "system_prompt": skill.system_prompt,
                "mode": "prompt_augmentation",
            }
        return skill.handler(payload)

    def _handle_ocr_extract(self, payload: dict[str, Any]) -> dict[str, Any]:
        images = payload.get("images") or []
        if not images:
            return {"ok": False, "error": "ocr_extract requires at least one image"}
        res = self.ocr_engine.extract_text(images[0], mode=str(payload.get("mode") or "offline"))
        return {"ok": not bool(res.error), "skill_id": "ocr_extract", "result": res.to_dict()}

    def _handle_translate(self, payload: dict[str, Any]) -> dict[str, Any]:
        target_lang = str(payload.get("target_lang") or "vi")
        text = str(payload.get("text") or "").strip()
        return {
            "ok": True,
            "skill_id": "translate_multilingual",
            "target_lang": target_lang,
            "prepared_prompt": f"Translate accurately to {target_lang}, preserving technical terms:\n\n{text}",
        }

    def _handle_compare(self, payload: dict[str, Any]) -> dict[str, Any]:
        images = payload.get("images") or []
        if len(images) < 2:
            return {"ok": False, "error": "compare_screenshots requires two images"}
        diff = self.comparator.compare(
            images[0],
            images[1],
            save_diff_path=str(payload.get("save_diff_path") or ""),
        )
        return {
            "ok": True,
            "skill_id": "compare_screenshots",
            "changed_pixel_ratio": diff.changed_pixel_ratio,
            "similarity_score": diff.similarity_score,
            "changed_bboxes": [list(b) for b in diff.changed_bboxes],
            "text_diff_lines": list(diff.text_diff_lines),
            "diff_image_path": diff.diff_image_path,
        }


@dataclass
class MCPClientAdapter:
    """Optional Model Context Protocol (MCP) & Mind Palace memory adapter.
    Strictly opt-in (`enabled=False` by default) and guarded by PrivacyConsentGate."""

    enabled: bool = False
    allowed_tools: set[str] = field(default_factory=lambda: {"memory_search", "memory_store"})
    consent_gate: PrivacyConsentGate | None = None
    _memory_palace: list[dict[str, Any]] = field(default_factory=list)

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled:
            return {"ok": False, "error": "MCP integration is disabled (opt-in only)."}
        if self.consent_gate is not None and not self.consent_gate.check_permission(ConsentScope.MCP_EXTERNAL):
            return {"ok": False, "error": "MCP tool call blocked by privacy consent gate."}
        clean_tool = str(tool_name or "").strip()
        if clean_tool not in self.allowed_tools:
            return {"ok": False, "error": f"MCP tool '{clean_tool}' is not in the user allowlist."}

        safe_args = SecretRedactor.redact_obj(arguments or {})
        if clean_tool == "memory_store":
            entry = {
                "id": f"mem_{len(self._memory_palace) + 1}",
                "topic": str(safe_args.get("topic") or "general"),
                "content": str(safe_args.get("content") or ""),
            }
            self._memory_palace.append(entry)
            return {"ok": True, "tool": clean_tool, "stored": entry}
        if clean_tool == "memory_search":
            query = str(safe_args.get("query") or "").lower()
            matches = [
                item
                for item in self._memory_palace
                if query in item["topic"].lower() or query in item["content"].lower()
            ]
            return {"ok": True, "tool": clean_tool, "matches": matches}
        return {"ok": True, "tool": clean_tool, "arguments": safe_args}
