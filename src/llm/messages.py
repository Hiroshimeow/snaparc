from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LLMMessage:
    role: str
    content: Any

    def to_openai(self) -> dict:
        return {"role": self.role, "content": self.content}


def history_to_messages(history) -> list[LLMMessage]:
    messages = []
    if not history:
        return messages

    for item in history:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        if role == "model":
            role = "assistant"

        content = ""
        parts = item.get("parts", [])
        if isinstance(parts, list):
            for part in parts:
                if isinstance(part, str):
                    content += part
        elif isinstance(parts, str):
            content = parts

        if role in {"user", "assistant", "system"} and content:
            messages.append(LLMMessage(role=role, content=content))
    return messages


def history_to_openai_messages(history) -> list[dict]:
    return [message.to_openai() for message in history_to_messages(history)]
