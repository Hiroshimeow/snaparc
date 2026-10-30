import queue
from dataclasses import dataclass


@dataclass(frozen=True)
class AgentMessage:
    type: str
    session_id: str
    responder_name: str = ""
    text: str = ""
    error: str = ""


class AgentMessageBus:
    def __init__(self):
        self._queue = queue.Queue()

    def publish(self, message: AgentMessage):
        self._queue.put(message)

    def drain(self, limit: int = 100) -> list[AgentMessage]:
        messages = []
        while len(messages) < limit:
            try:
                messages.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return messages
