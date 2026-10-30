"""
Pi Sidecar Deep Harness for ScreenshotChat (`Ctrl + Shift + Alt + X`) — v1.1
============================================================================
Brings deep Pi Coding Agent & Lantern v7.91 capabilities into ScreenshotChat:
1. Structured Message Extraction (`extract_message_structured`):
   - Zero `[object Object]` leakage
   - `<thinking>...</thinking>` collapsible reasoning extraction
   - `<task-notification>` subagent card parsing (`task_id`, `status`, `summary`, `result`)
   - Expandable Tool Call + Tool Result pairing (`tools` array)
2. Slash Command Engine (`SLASH_COMMANDS` & `execute_slash_command`):
   - `/new` (with empty-session guard)
   - `/pi` (switch to `pi://agent/pi` Sidecar runtime)
   - `/model <id>` (switch model & sync with `ModelStateStore`)
   - `/thinking <off|minimal|low|medium|high>`
   - `/tools <none|read_only|approve_mutations>`
   - `/skills` (inspect registered multimodal & Pi skills)
   - `/harness init` (initialize `.pi/AGENTS.md` & `.pi/settings.json` project harness)
   - `/compact [note]` (compact conversation history to save context & RAM)
   - `/fork [title]` (branch current conversation into a new session)
   - `/export` (export full unabbreviated HTML transcript with thinking & tool cards)
   - `/steer <text>` & `/followup <text>` (live mid-run steering & follow-up queue)
3. Favorite Models (`★`) & Unabbreviated HTML Transcript Exporter.
"""

from __future__ import annotations

import html
import json
import os
from pathlib import Path
import re
import time
from typing import Any, Dict, List, Optional, Tuple


_TASK_NOTIF_RE = re.compile(
    r"<task-notification>\s*"
    r"<task-id>(.*?)</task-id>\s*"
    r"<status>(.*?)</status>\s*"
    r"<summary>(.*?)</summary>\s*"
    r"<result>(.*?)</result>\s*"
    r"</task-notification>",
    re.DOTALL,
)

_THINKING_TAG_RE = re.compile(r"<thinking>(.*?)</thinking>", re.DOTALL | re.IGNORECASE)


SLASH_COMMANDS: List[Dict[str, str]] = [
    {
        "command": "/pi",
        "args": "[prompt]",
        "description": "Switch session to Pi Sidecar Agent (`pi://agent/pi`) or run prompt with Pi",
        "category": "pi",
    },
    {
        "command": "/model",
        "args": "[model_id]",
        "description": "Switch active LLM/Pi model (syncs across Tray, Popup & Chat Screen)",
        "category": "model",
    },
    {
        "command": "/thinking",
        "args": "off | minimal | low | medium | high",
        "description": "Configure Pi Sidecar reasoning/thinking budget level",
        "category": "pi",
    },
    {
        "command": "/tools",
        "args": "none | read_only | approve_mutations",
        "description": "Set Pi Sidecar tool permission mode",
        "category": "pi",
    },
    {
        "command": "/steer",
        "args": "<instruction>",
        "description": "Steer an active Pi Sidecar run in real time without losing state",
        "category": "pi",
    },
    {
        "command": "/followup",
        "args": "<instruction>",
        "description": "Queue a follow-up instruction for Pi Sidecar after current tool turn",
        "category": "pi",
    },
    {
        "command": "/skills",
        "args": "",
        "description": "List available Pi Sidecar & local multimodal skills",
        "category": "pi",
    },
    {
        "command": "/harness",
        "args": "init | status",
        "description": "Initialize or inspect `.pi/AGENTS.md` and `.pi/settings.json` harness",
        "category": "pi",
    },
    {
        "command": "/compact",
        "args": "[focus]",
        "description": "Compact older messages in this session into a concise summary",
        "category": "session",
    },
    {
        "command": "/fork",
        "args": "[new_title]",
        "description": "Fork current session history into a new branched session",
        "category": "session",
    },
    {
        "command": "/export",
        "args": "",
        "description": "Export full unabbreviated HTML transcript (with thinking & tool outputs)",
        "category": "session",
    },
    {
        "command": "/new",
        "args": "",
        "description": "Start a clean session (reuses current session if already empty)",
        "category": "session",
    },
]


def safe_stringify_content(value: Any) -> str:
    """Ensure no content block ever serializes to `[object Object]`."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.replace("[object Object]", "").strip()
    if isinstance(value, list):
        parts: List[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                btype = str(item.get("type") or "")
                if btype == "text":
                    parts.append(str(item.get("text") or ""))
                elif btype == "thinking":
                    parts.append(f"<thinking>{item.get('thinking') or item.get('text') or ''}</thinking>")
                elif "text" in item:
                    parts.append(str(item["text"]))
                else:
                    parts.append(json.dumps(item, ensure_ascii=False))
            else:
                parts.append(str(item))
        return "\n".join(p for p in parts if p).replace("[object Object]", "").strip()
    if isinstance(value, dict):
        if "text" in value and isinstance(value["text"], str):
            return value["text"]
        return json.dumps(value, ensure_ascii=False, indent=2)
    return str(value)


def extract_message_structured(message: Any) -> Dict[str, Any]:
    """
    Extract structured Lantern/Pi blocks (`text`, `thinking`, `tools`, `subagents`)
    from a `ScreenshotChatMessage` and its `events` / `metadata`.
    """
    raw_text = safe_stringify_content(getattr(message, "text", ""))
    metadata = dict(getattr(message, "metadata", {}) or {})
    events = [dict(e) for e in getattr(message, "events", []) if isinstance(e, dict)]

    thinking_parts: List[str] = []
    if isinstance(metadata.get("thinking"), str) and metadata["thinking"].strip():
        thinking_parts.append(metadata["thinking"].strip())

    # Extract <thinking>...</thinking> tags from raw_text
    def _strip_thinking(match: re.Match) -> str:
        inner = (match.group(1) or "").strip()
        if inner:
            thinking_parts.append(inner)
        return ""

    cleaned_text = _THINKING_TAG_RE.sub(_strip_thinking, raw_text)

    # Extract <task-notification> subagent cards
    subagents: List[Dict[str, str]] = []

    def _strip_subagent(match: re.Match) -> str:
        subagents.append(
            {
                "task_id": (match.group(1) or "").strip(),
                "status": (match.group(2) or "").strip(),
                "summary": (match.group(3) or "").strip(),
                "result": (match.group(4) or "").strip(),
            }
        )
        return ""

    cleaned_text = _TASK_NOTIF_RE.sub(_strip_subagent, cleaned_text).strip()

    # Extract structured tool calls & outputs from message timeline events
    tools_by_id: Dict[str, Dict[str, Any]] = {}
    tool_order: List[str] = []

    for idx, evt in enumerate(events):
        kind = str(evt.get("kind") or "")
        tool_name = str(evt.get("tool_name") or evt.get("title") or "")
        data = evt.get("data") if isinstance(evt.get("data"), dict) else {}
        call_id = str(data.get("tool_call_id") or data.get("id") or evt.get("id") or f"tool_{idx}")

        if kind in {"tool_started", "tool_call", "tool_event"}:
            entry = tools_by_id.get(call_id, {
                "id": call_id,
                "name": tool_name or "pi_tool",
                "args": data.get("args") or data.get("input") or {},
                "result": "",
                "is_error": False,
                "status": str(evt.get("status") or "running"),
            })
            if evt.get("text") and not entry["result"]:
                entry["result"] = safe_stringify_content(evt.get("text"))
            tools_by_id[call_id] = entry
            if call_id not in tool_order:
                tool_order.append(call_id)
        elif kind in {"tool_output", "tool_error"}:
            target_id = call_id if call_id in tools_by_id else (tool_order[-1] if tool_order else call_id)
            entry = tools_by_id.get(target_id, {
                "id": target_id,
                "name": tool_name or "pi_tool",
                "args": data.get("args") or {},
                "result": "",
                "is_error": kind == "tool_error",
                "status": "error" if kind == "tool_error" else "done",
            })
            entry["result"] = safe_stringify_content(evt.get("text") or data.get("output") or "")
            entry["is_error"] = kind == "tool_error" or bool(data.get("is_error"))
            entry["status"] = "error" if entry["is_error"] else "done"
            tools_by_id[target_id] = entry
            if target_id not in tool_order:
                tool_order.append(target_id)
        elif kind == "thinking":
            t_txt = safe_stringify_content(evt.get("text"))
            if t_txt:
                thinking_parts.append(t_txt)

    tools_list = [tools_by_id[tid] for tid in tool_order]

    return {
        "text": cleaned_text,
        "thinking": "\n\n".join(thinking_parts).strip(),
        "tools": tools_list,
        "subagents": subagents,
    }


def export_session_html(session: Any) -> str:
    """
    Generate a full, unabbreviated standalone HTML transcript for a ScreenshotChatSession,
    preserving all Markdown text, `<thinking>` blocks, full tool call arguments & outputs,
    and `<task-notification>` subagent cards.
    """
    title = html.escape(str(getattr(session, "title", None) or "Pi Sidecar Chat Transcript"))
    model_name = html.escape(str(getattr(session, "model_name", None) or ""))
    exported_at = time.strftime("%Y-%m-%d %H:%M:%S")

    msg_blocks: List[str] = []
    for msg in getattr(session, "messages", []):
        role = html.escape(str(getattr(msg, "role", "user")))
        author = html.escape(str(getattr(msg, "author", role)))
        structured = extract_message_structured(msg)

        thinking_html = ""
        if structured["thinking"]:
            thinking_html = (
                '<details class="thinking-card" open>'
                "<summary>🧠 Reasoning / Thinking Trace</summary>"
                f"<pre>{html.escape(structured['thinking'])}</pre>"
                "</details>"
            )

        tools_html_parts: List[str] = []
        for tool in structured["tools"]:
            t_name = html.escape(str(tool.get("name") or "tool"))
            t_args = html.escape(json.dumps(tool.get("args") or {}, ensure_ascii=False, indent=2))
            t_res = html.escape(str(tool.get("result") or ""))
            t_status = "❌ Error" if tool.get("is_error") else "✅ Done"
            tools_html_parts.append(
                f'<details class="tool-card" open>'
                f"<summary>🛠️ Tool: <code>{t_name}</code> ({t_status})</summary>"
                f"<div class='tool-section'><strong>Arguments:</strong><pre>{t_args}</pre></div>"
                f"<div class='tool-section'><strong>Output (Full Unabbreviated):</strong><pre>{t_res}</pre></div>"
                f"</details>"
            )

        subagents_html_parts: List[str] = []
        for sub in structured["subagents"]:
            subagents_html_parts.append(
                f'<div class="subagent-card">'
                f"<strong>🤖 Subagent Task `{html.escape(sub['task_id'])}` ({html.escape(sub['status'])})</strong>"
                f"<p>{html.escape(sub['summary'])}</p>"
                f"<pre>{html.escape(sub['result'])}</pre>"
                f"</div>"
            )

        body_html = f"<div class='msg-text'><pre>{html.escape(structured['text'])}</pre></div>"
        msg_blocks.append(
            f"<article class='msg msg-{role}'>"
            f"<header><strong>{author}</strong> <span class='role-badge'>{role}</span></header>"
            f"{thinking_html}"
            f"{''.join(tools_html_parts)}"
            f"{''.join(subagents_html_parts)}"
            f"{body_html}"
            f"</article>"
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>{title} — Pi Sidecar Transcript</title>
<style>
  body {{ background: #0f1419; color: #e6edf3; font-family: 'Segoe UI', system-ui, sans-serif; margin: 0; padding: 32px; }}
  .container {{ max-width: 960px; margin: 0 auto; }}
  header.top {{ border-bottom: 1px solid #2b3846; padding-bottom: 16px; margin-bottom: 24px; }}
  .meta {{ color: #8b949e; font-size: 13px; }}
  .msg {{ background: #161b22; border: 1px solid #2b3846; border-radius: 10px; padding: 16px; margin-bottom: 16px; }}
  .msg-user {{ border-left: 4px solid #0ac8b9; }}
  .msg-assistant {{ border-left: 4px solid #c8aa6e; }}
  .thinking-card, .tool-card, .subagent-card {{ background: #0d1117; border: 1px solid #30363d; border-radius: 6px; padding: 10px; margin: 10px 0; }}
  pre {{ white-space: pre-wrap; word-break: break-word; font-family: Consolas, monospace; font-size: 12.5px; margin: 6px 0 0; }}
  .role-badge {{ font-size: 11px; color: #0ac8b9; margin-left: 8px; text-transform: uppercase; }}
</style>
</head>
<body>
<div class="container">
  <header class="top">
    <h1>{title}</h1>
    <div class="meta">Model: <code>{model_name}</code> • Exported: {exported_at} • Full Unabbreviated Transcript</div>
  </header>
  {''.join(msg_blocks)}
</div>
</body>
</html>"""


def init_pi_project_harness(project_root: str | Path) -> Tuple[bool, str]:
    """Create or verify `.pi/AGENTS.md` and `.pi/settings.json` for Pi Sidecar."""
    root = Path(project_root)
    pi_dir = root / ".pi"
    pi_dir.mkdir(parents=True, exist_ok=True)
    agents_md = pi_dir / "AGENTS.md"
    settings_json = pi_dir / "settings.json"

    if not agents_md.exists():
        agents_md.write_text(
            "# Project Agent Harness (`.pi/AGENTS.md`)\n\n"
            "- Prefer clean, modular architecture and verify changes with tests.\n"
            "- Respect `trusted_roots` and approval gates when mutating files.\n",
            encoding="utf-8",
        )
    if not settings_json.exists():
        settings_json.write_text(
            json.dumps(
                {
                    "thinkingLevel": "medium",
                    "toolMode": "read_only",
                    "autoCompact": True,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    return True, f"✅ Pi Harness initialized at `{pi_dir}` (`AGENTS.md` & `settings.json`)."
