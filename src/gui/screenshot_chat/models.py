from __future__ import annotations

from dataclasses import dataclass, field
import time
import uuid


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def now_ts() -> float:
    return time.time()


@dataclass
class ScreenshotChatAttachment:
    id: str = field(default_factory=lambda: new_id("att"))
    path: str = ""
    kind: str = "image"
    source: str = ""
    label: str = ""
    created_at: float = field(default_factory=now_ts)

    @classmethod
    def from_dict(cls, payload: dict) -> "ScreenshotChatAttachment":
        return cls(
            id=str(payload.get("id") or new_id("att")),
            path=str(payload.get("path") or ""),
            kind=str(payload.get("kind") or "image"),
            source=str(payload.get("source") or ""),
            label=str(payload.get("label") or ""),
            created_at=float(payload.get("created_at", now_ts())),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "path": self.path,
            "kind": self.kind,
            "source": self.source,
            "label": self.label,
            "created_at": self.created_at,
        }


@dataclass
class ScreenshotChatMessage:
    id: str = field(default_factory=lambda: new_id("msg"))
    role: str = "assistant"
    author: str = "main"
    text: str = ""
    attachments: list[ScreenshotChatAttachment] = field(default_factory=list)
    created_at: float = field(default_factory=now_ts)
    job_id: str = ""
    status: str = "done"
    events: list[dict] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict) -> "ScreenshotChatMessage":
        attachments = payload.get("attachments", [])
        if not isinstance(attachments, list):
            attachments = []
        events = payload.get("events", [])
        if not isinstance(events, list):
            events = []
        metadata = payload.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        return cls(
            id=str(payload.get("id") or new_id("msg")),
            role=str(payload.get("role") or "assistant"),
            author=str(payload.get("author") or "main"),
            text=str(payload.get("text") or ""),
            attachments=[
                ScreenshotChatAttachment.from_dict(item)
                for item in attachments
                if isinstance(item, dict)
            ],
            created_at=float(payload.get("created_at", now_ts())),
            job_id=str(payload.get("job_id") or ""),
            status=str(payload.get("status") or "done"),
            events=[dict(item) for item in events if isinstance(item, dict)],
            metadata=dict(metadata),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "role": self.role,
            "author": self.author,
            "text": self.text,
            "attachments": [item.to_dict() for item in self.attachments],
            "created_at": self.created_at,
            "job_id": self.job_id,
            "status": self.status,
            "events": [dict(item) for item in self.events if isinstance(item, dict)],
            "metadata": dict(self.metadata),
        }


@dataclass
class ScreenshotChatSession:
    id: str = field(default_factory=lambda: new_id("ssch"))
    title: str = "New Chat"
    created_at: float = field(default_factory=now_ts)
    updated_at: float = field(default_factory=now_ts)
    model_name: str = ""
    prompt_type: str = "prompt_translate"
    mode: str = "chat"
    system_prompt: str = ""
    system_prompt_label: str = "Blank"
    messages: list[ScreenshotChatMessage] = field(default_factory=list)
    attachments: list[ScreenshotChatAttachment] = field(default_factory=list)
    is_pinned: bool = False
    pending_job_ids: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: dict) -> "ScreenshotChatSession":
        messages = payload.get("messages", [])
        attachments = payload.get("attachments", [])
        pending_job_ids = payload.get("pending_job_ids", [])
        if not isinstance(messages, list):
            messages = []
        if not isinstance(attachments, list):
            attachments = []
        if not isinstance(pending_job_ids, list):
            pending_job_ids = []
        created_at = float(payload.get("created_at", now_ts()))
        return cls(
            id=str(payload.get("id") or new_id("ssch")),
            title=str(payload.get("title") or "New Chat"),
            created_at=created_at,
            updated_at=float(payload.get("updated_at", created_at)),
            model_name=str(payload.get("model_name") or ""),
            prompt_type=str(payload.get("prompt_type") or "prompt_translate"),
            mode=str(payload.get("mode") or "chat"),
            system_prompt=str(payload.get("system_prompt") or ""),
            system_prompt_label=str(payload.get("system_prompt_label") or "Blank"),
            messages=[
                ScreenshotChatMessage.from_dict(item)
                for item in messages
                if isinstance(item, dict)
            ],
            attachments=[
                ScreenshotChatAttachment.from_dict(item)
                for item in attachments
                if isinstance(item, dict)
            ],
            is_pinned=bool(payload.get("is_pinned", False)),
            pending_job_ids=[str(item) for item in pending_job_ids if item],
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "model_name": self.model_name,
            "prompt_type": self.prompt_type,
            "mode": self.mode,
            "system_prompt": self.system_prompt,
            "system_prompt_label": self.system_prompt_label,
            "messages": [item.to_dict() for item in self.messages],
            "attachments": [item.to_dict() for item in self.attachments],
            "is_pinned": self.is_pinned,
            "pending_job_ids": list(self.pending_job_ids),
        }


@dataclass
class ScreenshotChatResponder:
    name: str
    kind: str = "agent"
    model_name: str = ""
    prompt_type: str = ""
    system_prompt: str = ""
    runtime: str = "llm"
    metadata: dict = field(default_factory=dict)


@dataclass
class ScreenshotChatJob:
    id: str = field(default_factory=lambda: new_id("job"))
    session_id: str = ""
    message_id: str = ""
    responder_name: str = "main"
    status: str = "queued"
    created_at: float = field(default_factory=now_ts)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "message_id": self.message_id,
            "responder_name": self.responder_name,
            "status": self.status,
            "created_at": self.created_at,
        }
