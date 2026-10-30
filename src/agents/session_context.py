from dataclasses import dataclass, field


@dataclass
class AgentResponder:
    name: str
    model_name: str = ""
    system_prompt: str = ""


@dataclass
class AgentSessionContext:
    session_id: str
    prompt_text: str
    model_name: str = ""
    responders: list[AgentResponder] = field(default_factory=list)
    attachment_paths: list[str] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)
    temperature: float = 0.7
