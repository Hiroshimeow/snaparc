from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from src.gui.screenshot_chat.models import ScreenshotChatResponder


MENTION_RE = re.compile(r"(?<!\w)@([A-Za-z0-9_-]+)")


@dataclass
class MentionParseResult:
    prompt_text: str
    responders: list[ScreenshotChatResponder]
    unknown_mentions: list[str]


def _normalize_agent_configs(agent_configs: dict | None) -> dict[str, dict]:
    if not isinstance(agent_configs, dict):
        return {}
    normalized = {}
    for name, config in agent_configs.items():
        clean_name = str(name or "").strip().lower()
        if clean_name and isinstance(config, dict) and config.get("enabled", True):
            normalized[clean_name] = config
    return normalized


def _main_responder(model_name: str = "", prompt_type: str = "prompt_translate") -> ScreenshotChatResponder:
    return ScreenshotChatResponder(
        name="main",
        kind="main",
        model_name=model_name or "",
        prompt_type=prompt_type or "prompt_translate",
    )


def _agent_responder(name: str, config: dict, fallback_model: str = "") -> ScreenshotChatResponder:
    metadata = config.get("metadata") if isinstance(config.get("metadata"), dict) else {}
    metadata = dict(metadata)
    provider = str(config.get("provider") or "")
    if provider:
        metadata.setdefault("provider", provider)
    runtime = str(config.get("runtime") or provider or "llm").strip().lower() or "llm"
    return ScreenshotChatResponder(
        name=name,
        kind="agent",
        model_name=str(config.get("model") or fallback_model or ""),
        system_prompt=str(config.get("system_prompt") or ""),
        runtime=runtime,
        metadata=metadata,
    )


def _dedupe_responders(responders: Iterable[ScreenshotChatResponder]) -> list[ScreenshotChatResponder]:
    result = []
    seen = set()
    for responder in responders:
        key = responder.name.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(responder)
    return result


def parse_mentions(
    text: str,
    agent_configs: dict | None,
    selected_model: str = "",
    selected_prompt_type: str = "prompt_translate",
) -> MentionParseResult:
    raw_text = text or ""
    agents = _normalize_agent_configs(agent_configs)
    found_mentions = [match.group(1).lower() for match in MENTION_RE.finditer(raw_text)]
    responders: list[ScreenshotChatResponder] = []
    unknown_mentions = []
    strip_mentions = set()

    if not found_mentions:
        responders.append(_main_responder(selected_model, selected_prompt_type))
    else:
        for mention in found_mentions:
            if mention == "main":
                responders.append(_main_responder(selected_model, selected_prompt_type))
                strip_mentions.add(mention)
            elif mention == "agents":
                for agent_name in sorted(agents.keys()):
                    responders.append(_agent_responder(agent_name, agents[agent_name], selected_model))
                strip_mentions.add(mention)
            elif mention in agents:
                responders.append(_agent_responder(mention, agents[mention], selected_model))
                strip_mentions.add(mention)
            else:
                unknown_mentions.append(mention)

        if not responders:
            responders.append(_main_responder(selected_model, selected_prompt_type))

    def replace_known(match):
        mention = match.group(1).lower()
        return "" if mention in strip_mentions else match.group(0)

    prompt_text = MENTION_RE.sub(replace_known, raw_text)
    prompt_text = re.sub(r"[ \t]{2,}", " ", prompt_text)
    prompt_text = re.sub(r"\n{3,}", "\n\n", prompt_text).strip()

    return MentionParseResult(
        prompt_text=prompt_text,
        responders=_dedupe_responders(responders),
        unknown_mentions=list(dict.fromkeys(unknown_mentions)),
    )
