import unittest

from src.agents.runtime import AgentRuntime
from src.agents.session_context import AgentResponder, AgentSessionContext


class AgentRuntimeTests(unittest.TestCase):
    def test_run_once_publishes_final_message_from_service_callback(self):
        class FakeService:
            def send_text_message(self, *args, **kwargs):
                kwargs["callback"]({"text": "ok"})

        runtime = AgentRuntime(FakeService())
        runtime.run_once(
            AgentSessionContext(
                session_id="s1",
                prompt_text="hello",
                responders=[AgentResponder(name="main", model_name="p/m")],
            )
        )

        events = runtime.message_bus.drain()
        self.assertEqual([event.type for event in events], ["started", "final"])
        self.assertEqual(events[-1].text, "ok")


if __name__ == "__main__":
    unittest.main()
