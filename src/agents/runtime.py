from src.agents.message_bus import AgentMessage, AgentMessageBus
from src.agents.session_context import AgentSessionContext
from src.llm.service import LLMService


class AgentRuntime:
    def __init__(self, llm_service: LLMService, message_bus: AgentMessageBus | None = None):
        self.llm_service = llm_service
        self.message_bus = message_bus or AgentMessageBus()

    def run_once(self, context: AgentSessionContext):
        responders = context.responders or []
        if not responders:
            responders = [
                type(
                    "DefaultResponder",
                    (),
                    {
                        "name": "main",
                        "model_name": context.model_name,
                        "system_prompt": "",
                    },
                )()
            ]

        for responder in responders:
            self.message_bus.publish(
                AgentMessage(
                    type="started",
                    session_id=context.session_id,
                    responder_name=responder.name,
                )
            )

            def callback(response, responder_name=responder.name):
                if response is None:
                    self.message_bus.publish(
                        AgentMessage(
                            type="error",
                            session_id=context.session_id,
                            responder_name=responder_name,
                            error="No response received",
                        )
                    )
                    return
                text = response.get("text", "") if isinstance(response, dict) else str(response)
                self.message_bus.publish(
                    AgentMessage(
                        type="final",
                        session_id=context.session_id,
                        responder_name=responder_name,
                        text=text,
                    )
                )

            if context.attachment_paths:
                self.llm_service.process_image(
                    context.attachment_paths,
                    callback=callback,
                    history=context.history,
                    custom_prompt_text=context.prompt_text,
                    model_full_name=responder.model_name or context.model_name,
                )
            else:
                self.llm_service.send_text_message(
                    context.prompt_text,
                    callback=callback,
                    history=context.history,
                    system_prompt=responder.system_prompt or None,
                    temperature=context.temperature,
                    model_name=responder.model_name or context.model_name or None,
                )
