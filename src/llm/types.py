from dataclasses import dataclass, field
from typing import Any

from src.llm.attachments import LLMAttachment
from src.llm.messages import LLMMessage


@dataclass(frozen=True)
class LLMRequest:
    provider_name: str
    model_name: str
    messages: list[LLMMessage] = field(default_factory=list)
    system_prompt: str | None = None
    attachments: list[LLMAttachment] = field(default_factory=list)
    temperature: float = 0.7
    stream: bool = True
    prompt_text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMResult:
    text: str
    provider_name: str = ""
    model_name: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_legacy_payload(self, key_mask: str = "") -> dict:
        return {
            "text": self.text,
            "api_index": self.provider_name,
            "key_mask": key_mask or self.metadata.get("key_mask", ""),
        }


@dataclass(frozen=True)
class LLMErrorResult:
    error: str
    provider_name: str = ""
    model_name: str = ""

    def to_legacy_payload(self) -> dict:
        return {
            "text": f"Error: {self.error}",
            "api_index": self.provider_name,
            "key_mask": "Error",
        }
