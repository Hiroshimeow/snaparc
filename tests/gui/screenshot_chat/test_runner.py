import importlib.util
import queue
import os
import tempfile
import threading
import time
import unittest

from src.agents.pi_contract import PiRunResult
from src.gui.screenshot_chat.models import (
    ScreenshotChatAttachment,
    ScreenshotChatMessage,
    ScreenshotChatResponder,
    ScreenshotChatSession,
)
from src.gui.screenshot_chat.runner import ScreenshotChatRunner


class FakeModelClient:
    instances = []
    callback_response = {"text": "ok"}
    raise_on_send = False
    defer_callback = False

    def __init__(self):
        self.model = ""
        self.prompt_type = ""
        self.sent = []
        self.image_calls = []
        self.pending_callback = None
        FakeModelClient.instances.append(self)

    def set_model(self, model_name):
        self.model = model_name

    def send_text_message(self, *args, **kwargs):
        if FakeModelClient.raise_on_send:
            raise RuntimeError("send failed")
        self.sent.append((args, kwargs))
        callback = kwargs["callback"]
        if FakeModelClient.defer_callback:
            self.pending_callback = callback
        else:
            callback(FakeModelClient.callback_response)

    def process_image(self, *args, **kwargs):
        self.image_calls.append((args, kwargs))
        kwargs["callback"](FakeModelClient.callback_response)


class FakePiRuntime:
    instances = []
    result = PiRunResult(text="pi answer")

    def __init__(self, config, emit):
        self.config = config
        self.emit = emit
        self.requests = []
        self.canceled = []
        self.closed = []
        FakePiRuntime.instances.append(self)

    def run(self, request, cancel_event):
        self.requests.append(request)
        self.emit("tool_event", metadata={"kind": "tool_started", "tool_name": "bash", "status": "running"})
        return FakePiRuntime.result

    def cancel(self, reason=""):
        self.canceled.append(reason)

    def close(self, timeout_ms=3000):
        self.closed.append(timeout_ms)

    def resolve_approval(self, job_id, approval_id, approved, reason="", payload=None):
        return approval_id == "appr_1" and job_id in [request.job_id for request in self.requests]


class BlockingPiRuntime(FakePiRuntime):
    started = threading.Event()
    release = threading.Event()

    def __init__(self, config, emit):
        super().__init__(config, emit)
        BlockingPiRuntime.started.clear()
        BlockingPiRuntime.release.clear()
        self.approval_calls = []

    def run(self, request, cancel_event):
        self.requests.append(request)
        BlockingPiRuntime.started.set()
        BlockingPiRuntime.release.wait(2)
        self.emit("tool_event", metadata={"kind": "tool_started", "tool_name": "late", "status": "running"})
        return PiRunResult(text="late answer")

    def cancel(self, reason=""):
        super().cancel(reason)
        BlockingPiRuntime.release.set()

    def resolve_approval(self, job_id, approval_id, approved, reason="", payload=None):
        self.approval_calls.append((job_id, approval_id, approved, reason, payload))
        return True


class ScreenshotChatRunnerTests(unittest.TestCase):
    def test_default_runtime_factory_is_selected_from_job_config(self):
        runner = ScreenshotChatRunner(queue.Queue(), model_client_factory=FakeModelClient)
        self.assertIsNone(runner.pi_runtime_factory)
        self.assertIsNone(runner._owned_pi_runtime_factory)

    def setUp(self):
        FakeModelClient.instances = []
        FakeModelClient.callback_response = {"text": "ok"}
        FakeModelClient.raise_on_send = False
        FakeModelClient.defer_callback = False
        self.events = queue.Queue()
        FakePiRuntime.instances = []
        FakePiRuntime.result = PiRunResult(text="pi answer")
        self.runner = ScreenshotChatRunner(self.events, model_client_factory=FakeModelClient, pi_runtime_factory=FakePiRuntime)

    def drain_events(self):
        events = []
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                events.append(self.events.get(timeout=0.05))
                while True:
                    events.append(self.events.get_nowait())
            except queue.Empty:
                if events:
                    return events
        return events

    def wait_until(self, predicate):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.01)
        return False

    def test_each_responder_uses_fresh_model_client(self):
        session = ScreenshotChatSession(id="s1", model_name="provider/main")
        user_message = ScreenshotChatMessage(id="m1", role="user", text="@main @critic hi")
        responders = [
            ScreenshotChatResponder(name="main", kind="main", model_name="provider/main", prompt_type="prompt_translate"),
            ScreenshotChatResponder(name="critic", kind="agent", model_name="provider/critic", system_prompt="Review."),
        ]

        self.runner.start_response_jobs(session, user_message, "hi", responders)
        events = self.drain_events()
        while [event.type for event in events].count("message_final") < 2:
            more = self.drain_events()
            if not more:
                break
            events.extend(more)

        self.assertEqual(len(FakeModelClient.instances), 2)
        self.assertEqual(FakeModelClient.instances[0].model, "provider/main")
        self.assertEqual(FakeModelClient.instances[1].model, "provider/critic")
        self.assertEqual([event.type for event in events].count("message_final"), 2)


    def test_cancel_ignores_late_response(self):
        FakeModelClient.defer_callback = True
        session = ScreenshotChatSession(id="s1", model_name="provider/main")
        user_message = ScreenshotChatMessage(id="m1", role="user", text="hi")
        responder = ScreenshotChatResponder(name="main", kind="main")

        jobs = self.runner.start_response_jobs(session, user_message, "hi", [responder])
        deadline = time.monotonic() + 2
        while not FakeModelClient.instances and time.monotonic() < deadline:
            time.sleep(0.01)
        self.runner.cancel_job(jobs[0].id)
        FakeModelClient.instances[0].pending_callback({"text": "late"})
        events = self.drain_events()

        self.assertNotIn("message_final", [event.type for event in events])
        self.assertIn("job_finished", [event.type for event in events])

    def test_send_error_emits_job_error(self):
        FakeModelClient.raise_on_send = True
        session = ScreenshotChatSession(id="s1", model_name="provider/main")
        user_message = ScreenshotChatMessage(id="m1", role="user", text="hi")
        responder = ScreenshotChatResponder(name="main", kind="main")

        self.runner.start_response_jobs(session, user_message, "hi", [responder])
        events = self.drain_events()

        error_events = [event for event in events if event.type == "job_error"]
        self.assertEqual(len(error_events), 1)
        self.assertIn("send failed", error_events[0].error)

    def test_text_call_passes_raw_model_id_without_provider_prefix(self):
        session = ScreenshotChatSession(id="s1", model_name="cliproxy-2080/gemini-3-flash")
        user_message = ScreenshotChatMessage(id="m1", role="user", text="hi")
        responder = ScreenshotChatResponder(
            name="main",
            kind="main",
            model_name="cliproxy-2080/gemini-3-flash",
            prompt_type="prompt_translate",
        )

        self.runner.start_response_jobs(session, user_message, "hi", [responder])
        self.assertTrue(
            self.wait_until(
                lambda: FakeModelClient.instances and FakeModelClient.instances[0].sent
            )
        )

        self.assertEqual(FakeModelClient.instances[0].model, "cliproxy-2080/gemini-3-flash")
        args, kwargs = FakeModelClient.instances[0].sent[0]
        self.assertEqual(kwargs["model_name"], "gemini-3-flash")

    def test_multi_image_attachment_routes_to_process_image(self):
        with tempfile.NamedTemporaryFile(suffix=".png") as first, tempfile.NamedTemporaryFile(suffix=".png") as second:
            session = ScreenshotChatSession(id="s1", model_name="provider/main")
            user_message = ScreenshotChatMessage(
                id="m1",
                role="user",
                text="",
                attachments=[
                    ScreenshotChatAttachment(path=first.name),
                    ScreenshotChatAttachment(path=second.name),
                ],
            )
            responder = ScreenshotChatResponder(name="main", kind="main")

            self.runner.start_response_jobs(session, user_message, "", [responder])
            self.assertTrue(
                self.wait_until(
                    lambda: FakeModelClient.instances and FakeModelClient.instances[0].image_calls
                )
            )

            self.assertEqual(len(FakeModelClient.instances[0].image_calls), 1)
            args, kwargs = FakeModelClient.instances[0].image_calls[0]
            self.assertEqual(args[0], [first.name, second.name])

    def test_text_attachment_contents_are_added_to_prompt(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", encoding="utf-8", delete=False) as handle:
            handle.write("requirement body")
            path = handle.name
        try:
            session = ScreenshotChatSession(id="s1", model_name="provider/main")
            user_message = ScreenshotChatMessage(
                id="m1",
                role="user",
                text="summarize",
                attachments=[
                    ScreenshotChatAttachment(path=path, kind="file", label="requirement.txt"),
                ],
            )
            responder = ScreenshotChatResponder(name="main", kind="main")

            self.runner.start_response_jobs(session, user_message, "summarize", [responder])
            self.assertTrue(
                self.wait_until(
                    lambda: FakeModelClient.instances and FakeModelClient.instances[0].sent
                )
            )

            args, kwargs = FakeModelClient.instances[0].sent[0]
            self.assertIn("summarize", args[0])
            self.assertIn("--- requirement.txt ---", args[0])
            self.assertIn("requirement body", args[0])
            self.assertIsNone(kwargs["image"])
        finally:
            os.unlink(path)

    @unittest.skipUnless(importlib.util.find_spec("pypdf"), "pypdf is optional in this test environment")
    def test_pdf_attachment_contents_are_added_to_first_prompt(self):
        from pypdf import PdfWriter

        fd, path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        try:
            writer = PdfWriter()
            writer.add_blank_page(width=72, height=72)
            writer.add_metadata({"/Title": "first turn pdf"})
            with open(path, "wb") as handle:
                writer.write(handle)

            session = ScreenshotChatSession(id="s1", model_name="provider/main")
            user_message = ScreenshotChatMessage(
                id="m1",
                role="user",
                text="what is attached",
                attachments=[ScreenshotChatAttachment(path=path, kind="file", label="brief.pdf")],
            )
            responder = ScreenshotChatResponder(name="main", kind="main")

            self.runner.start_response_jobs(session, user_message, "what is attached", [responder])
            self.assertTrue(
                self.wait_until(
                    lambda: FakeModelClient.instances and FakeModelClient.instances[0].sent
                )
            )

            args, kwargs = FakeModelClient.instances[0].sent[0]
            self.assertIn("what is attached", args[0])
            self.assertIn("--- brief.pdf ---", args[0])
            self.assertIn("PDF", args[0])
            self.assertIsNone(kwargs["image"])
        finally:
            os.unlink(path)

    def test_pdf_attachment_extractable_text_is_added_to_first_prompt(self):
        import sys
        import types

        from unittest import mock

        class FakePage:
            def __init__(self, text):
                self.text = text

            def extract_text(self):
                return self.text

        class FakePdfReader:
            is_encrypted = False

            def __init__(self, path):
                self.pages = [FakePage("page one body"), FakePage("page two body")]

        fd, path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        try:
            fake_pypdf = types.SimpleNamespace(PdfReader=FakePdfReader)
            session = ScreenshotChatSession(id="s1", model_name="provider/main")
            user_message = ScreenshotChatMessage(
                id="m1",
                role="user",
                text="summarize pdf",
                attachments=[ScreenshotChatAttachment(path=path, kind="file", label="brief.pdf")],
            )
            responder = ScreenshotChatResponder(name="main", kind="main")

            with mock.patch.dict(sys.modules, {"pypdf": fake_pypdf}):
                self.runner.start_response_jobs(session, user_message, "summarize pdf", [responder])
                self.assertTrue(
                    self.wait_until(
                        lambda: FakeModelClient.instances and FakeModelClient.instances[0].sent
                    )
                )

            args, kwargs = FakeModelClient.instances[0].sent[0]
            self.assertIn("--- brief.pdf ---", args[0])
            self.assertIn("[page 1]\npage one body", args[0])
            self.assertIn("[page 2]\npage two body", args[0])
            self.assertIsNone(kwargs["image"])
        finally:
            os.unlink(path)

    def test_pi_responder_routes_to_runtime_closes_and_emits_timeline(self):
        session = ScreenshotChatSession(id="s1", model_name="provider/main")
        user_message = ScreenshotChatMessage(id="m1", role="user", text="@pi hi")
        responder = ScreenshotChatResponder(name="pi", runtime="pi", metadata={"pi_config": {"enabled": True}})

        self.runner.start_response_jobs(session, user_message, "hi", [responder])
        events = self.drain_events()

        self.assertEqual(FakePiRuntime.instances[0].requests[0].prompt_text, "hi")
        self.assertEqual(FakePiRuntime.instances[0].closed, [3000])
        self.assertIn("tool_event", [event.type for event in events])
        self.assertIn("message_final", [event.type for event in events])

    def test_close_pi_session_uses_existing_factory_and_is_idempotent_at_bridge_boundary(self):
        class ClosingFactory:
            def __init__(self):
                self.calls = []

            def __call__(self, config, emit):
                return FakePiRuntime(config, emit)

            def close_session(self, session_id, responder_name, timeout_ms=3000):
                self.calls.append((session_id, responder_name, timeout_ms))
                return {"closed": True}

        factory = ClosingFactory()
        runner = ScreenshotChatRunner(self.events, model_client_factory=FakeModelClient, pi_runtime_factory=factory)

        runner.close_pi_session("s1", ["Pi Agent", "pi", "pi"])

        self.assertEqual(factory.calls, [("s1", "Pi Agent", 3000), ("s1", "pi", 3000)])

    def test_pi_session_metadata_is_not_rendered_into_chat_text(self):
        FakePiRuntime.result = PiRunResult(
            text="visible answer",
            metadata={"piSessionId": "s1", "piSessionFile": "C:/private/session.jsonl"},
        )
        session = ScreenshotChatSession(id="s1", model_name="provider/main")
        user_message = ScreenshotChatMessage(id="m1", role="user", text="@pi hi")
        responder = ScreenshotChatResponder(name="pi", runtime="pi", metadata={"pi_config": {"enabled": True}})

        self.runner.start_response_jobs(session, user_message, "hi", [responder])
        events = self.drain_events()

        final = next(event for event in events if event.type == "message_final")
        self.assertEqual(final.text, "visible answer")
        self.assertNotIn("session.jsonl", final.text)
        self.assertTrue(all("session.jsonl" not in (event.text or "") for event in events))

    def test_explicit_sdk_startup_failure_emits_error_finished_and_cleans_runner_state(self):
        class FailingFactory:
            def __call__(self, config, emit):
                raise RuntimeError("SDK startup incompatible")

        runner = ScreenshotChatRunner(self.events, model_client_factory=FakeModelClient, pi_runtime_factory=FailingFactory())
        session = ScreenshotChatSession(id="s1", model_name="pi://agent/pi")
        user_message = ScreenshotChatMessage(id="m1", role="user", text="hi")
        responder = ScreenshotChatResponder(
            name="pi",
            runtime="pi",
            model_name="pi://agent/pi",
            metadata={"pi_config": {"enabled": True, "backend": "sdk"}},
        )
        jobs = runner.prepare_response_jobs(session, user_message, [responder])

        runner._run_pi_responder(session, user_message, "hi", responder, jobs[0], 0.7, runner._cancel_flags[jobs[0].id])
        events = self.drain_events()

        self.assertEqual([event.type for event in events], ["job_started", "job_error", "job_finished"])
        self.assertIn("SDK startup incompatible", events[1].error)
        self.assertNotIn(jobs[0].id, runner._cancel_flags)
        self.assertNotIn(jobs[0].id, runner._active_pi_runtimes)

    def test_pi_error_closes_runtime(self):
        FakePiRuntime.result = PiRunResult(error="pi failed")
        session = ScreenshotChatSession(id="s1", model_name="provider/main")
        user_message = ScreenshotChatMessage(id="m1", role="user", text="@pi hi")
        responder = ScreenshotChatResponder(name="pi", runtime="pi", metadata={"pi_config": {"enabled": True}})

        self.runner.start_response_jobs(session, user_message, "hi", [responder])
        events = self.drain_events()

        self.assertEqual(FakePiRuntime.instances[0].closed, [3000])
        self.assertIn("job_error", [event.type for event in events])

    def test_pi_empty_runtime_result_is_error_not_empty_final_message(self):
        FakePiRuntime.result = PiRunResult(text="")
        session = ScreenshotChatSession(id="s1", model_name="provider/main")
        user_message = ScreenshotChatMessage(id="m1", role="user", text="@pi hi")
        responder = ScreenshotChatResponder(name="pi", runtime="pi", metadata={"pi_config": {"enabled": True}})

        self.runner.start_response_jobs(session, user_message, "hi", [responder])
        events = self.drain_events()

        event_types = [event.type for event in events]
        self.assertNotIn("message_final", event_types)
        self.assertIn("job_error", event_types)
        self.assertIn("no response received", next(event.error for event in events if event.type == "job_error"))

    def test_pi_unavailable_fallback_uses_llm_before_side_effects(self):
        FakePiRuntime.result = PiRunResult(error="missing", fallback_to_llm=True, side_effect_started=False)
        session = ScreenshotChatSession(id="s1", model_name="provider/main")
        user_message = ScreenshotChatMessage(id="m1", role="user", text="@pi hi")
        responder = ScreenshotChatResponder(name="pi", runtime="pi", model_name="provider/main", metadata={"pi_config": {"enabled": True}})

        self.runner.start_response_jobs(session, user_message, "hi", [responder])
        events = self.drain_events()

        self.assertTrue(FakeModelClient.instances)
        self.assertEqual(FakePiRuntime.instances[0].closed, [3000])
        self.assertIn("runtime_notice", [event.type for event in events])
        self.assertIn("message_final", [event.type for event in events])

    def test_pi_ambiguous_mutation_never_falls_back_even_if_fallback_flag_is_true(self):
        FakePiRuntime.result = PiRunResult(
            error="Pi SDK bridge crashed after mutation; outcome is ambiguous",
            fallback_to_llm=True,
            side_effect_started=True,
        )
        session = ScreenshotChatSession(id="s1", model_name="provider/main")
        user_message = ScreenshotChatMessage(id="m1", role="user", text="@pi write")
        responder = ScreenshotChatResponder(
            name="pi",
            runtime="pi",
            model_name="provider/main",
            metadata={"pi_config": {"enabled": True}, "fallback_model_name": "provider/main"},
        )

        self.runner.start_response_jobs(session, user_message, "write", [responder])
        events = self.drain_events()

        self.assertEqual(FakeModelClient.instances, [])
        error = next(event.error for event in events if event.type == "job_error")
        self.assertIn("ambiguous", error.lower())
        self.assertIn("job_finished", [event.type for event in events])

    def test_pi_unavailable_fallback_uses_metadata_fallback_model(self):
        FakePiRuntime.result = PiRunResult(error="missing", fallback_to_llm=True, side_effect_started=False)
        session = ScreenshotChatSession(id="s1", model_name="pi://agent/pi")
        user_message = ScreenshotChatMessage(id="m1", role="user", text="hi")
        responder = ScreenshotChatResponder(
            name="Pi Agent",
            runtime="pi",
            model_name="pi://agent/pi",
            metadata={"pi_config": {"enabled": True}, "fallback_model_name": "provider/main"},
        )

        self.runner.start_response_jobs(session, user_message, "hi", [responder])
        events = self.drain_events()

        self.assertTrue(FakeModelClient.instances)
        self.assertEqual(FakeModelClient.instances[0].model, "provider/main")
        self.assertIn("message_final", [event.type for event in events])

    def test_pi_unavailable_fallback_rejects_pi_sentinel_model(self):
        FakePiRuntime.result = PiRunResult(error="missing", fallback_to_llm=True, side_effect_started=False)
        session = ScreenshotChatSession(id="s1", model_name="pi://agent/pi")
        user_message = ScreenshotChatMessage(id="m1", role="user", text="hi")
        responder = ScreenshotChatResponder(
            name="Pi Agent",
            runtime="pi",
            model_name="pi://agent/pi",
            metadata={"pi_config": {"enabled": True}, "fallback_model_name": "pi://agent/pi"},
        )

        self.runner.start_response_jobs(session, user_message, "hi", [responder])
        events = self.drain_events()

        self.assertEqual(FakeModelClient.instances, [])
        self.assertEqual([event.type for event in events].count("job_error"), 1)
        self.assertIn("normal LLM model", next(event.error for event in events if event.type == "job_error"))

    def test_pi_text_attachment_contents_are_added_to_prompt(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", encoding="utf-8", delete=False) as handle:
            handle.write("pi requirement body")
            path = handle.name
        try:
            session = ScreenshotChatSession(id="s1", model_name="provider/main")
            user_message = ScreenshotChatMessage(
                id="m1",
                role="user",
                text="summarize",
                attachments=[ScreenshotChatAttachment(path=path, kind="file", label="requirement.txt")],
            )
            responder = ScreenshotChatResponder(name="pi", runtime="pi", metadata={"pi_config": {"enabled": True}})

            self.runner.start_response_jobs(session, user_message, "summarize", [responder])
            self.assertTrue(self.wait_until(lambda: FakePiRuntime.instances and FakePiRuntime.instances[0].requests))
            self.drain_events()

            request_text = FakePiRuntime.instances[0].requests[0].prompt_text
            self.assertIn("summarize", request_text)
            self.assertIn("--- requirement.txt ---", request_text)
            self.assertIn("pi requirement body", request_text)
        finally:
            os.unlink(path)

    def test_pi_pdf_attachment_extractable_text_is_added_to_prompt(self):
        import sys
        import types
        from unittest import mock

        class FakePage:
            def __init__(self, text):
                self.text = text

            def extract_text(self):
                return self.text

        class FakePdfReader:
            is_encrypted = False

            def __init__(self, path):
                self.pages = [FakePage("pi page one"), FakePage("pi page two")]

        fd, path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        try:
            fake_pypdf = types.SimpleNamespace(PdfReader=FakePdfReader)
            session = ScreenshotChatSession(id="s1", model_name="provider/main")
            user_message = ScreenshotChatMessage(
                id="m1",
                role="user",
                text="summarize pdf",
                attachments=[ScreenshotChatAttachment(path=path, kind="file", label="brief.pdf")],
            )
            responder = ScreenshotChatResponder(name="pi", runtime="pi", metadata={"pi_config": {"enabled": True}})

            with mock.patch.dict(sys.modules, {"pypdf": fake_pypdf}):
                self.runner.start_response_jobs(session, user_message, "summarize pdf", [responder])
                self.assertTrue(self.wait_until(lambda: FakePiRuntime.instances and FakePiRuntime.instances[0].requests))
                self.drain_events()

            request_text = FakePiRuntime.instances[0].requests[0].prompt_text
            self.assertIn("--- brief.pdf ---", request_text)
            self.assertIn("[page 1]\npi page one", request_text)
            self.assertIn("[page 2]\npi page two", request_text)
        finally:
            os.unlink(path)

    def test_pi_fallback_cancel_ignores_late_llm_response(self):
        FakeModelClient.defer_callback = True
        FakePiRuntime.result = PiRunResult(error="missing", fallback_to_llm=True, side_effect_started=False)
        session = ScreenshotChatSession(id="s1", model_name="provider/main")
        user_message = ScreenshotChatMessage(id="m1", role="user", text="@pi hi")
        responder = ScreenshotChatResponder(name="pi", runtime="pi", model_name="provider/main", metadata={"pi_config": {"enabled": True}})

        jobs = self.runner.start_response_jobs(session, user_message, "hi", [responder])
        self.assertTrue(self.wait_until(lambda: FakeModelClient.instances and FakeModelClient.instances[0].pending_callback))
        self.assertIn(jobs[0].id, self.runner._cancel_flags)
        self.runner.cancel_job(jobs[0].id)
        FakeModelClient.instances[0].pending_callback({"text": "late fallback"})
        events = self.drain_events()

        self.assertNotIn("message_final", [event.type for event in events])
        self.assertIn("job_finished", [event.type for event in events])
        self.assertNotIn(jobs[0].id, self.runner._cancel_flags)

    def test_cancel_aborts_active_pi_runtime_without_early_close(self):
        FakePiRuntime.result = PiRunResult(text="late")
        session = ScreenshotChatSession(id="s1", model_name="provider/main")
        user_message = ScreenshotChatMessage(id="m1", role="user", text="@pi hi")
        responder = ScreenshotChatResponder(name="pi", runtime="pi", metadata={"pi_config": {"enabled": True}})

        jobs = self.runner.prepare_response_jobs(session, user_message, [responder])
        runtime = FakePiRuntime({}, lambda *_args, **_kwargs: None)
        self.runner.register_pi_runtime(jobs[0].id, runtime)
        self.runner.cancel_job(jobs[0].id)

        self.assertEqual(runtime.canceled, ["job_canceled"])
        self.assertEqual(runtime.closed, [])

    def test_repeated_pi_cancel_is_idempotent_and_late_approval_is_ignored(self):
        session = ScreenshotChatSession(id="s1", model_name="provider/main")
        user_message = ScreenshotChatMessage(id="m1", role="user", text="@pi hi")
        responder = ScreenshotChatResponder(name="pi", runtime="pi", metadata={"pi_config": {"enabled": True}})
        jobs = self.runner.prepare_response_jobs(session, user_message, [responder])
        runtime = BlockingPiRuntime({}, lambda *_args, **_kwargs: None)
        runtime.requests.append(type("Request", (), {"job_id": jobs[0].id})())
        self.runner.register_pi_runtime(jobs[0].id, runtime)

        self.assertTrue(self.runner.resolve_approval(jobs[0].id, "appr_1", True))
        self.runner.cancel_job(jobs[0].id)
        self.runner.cancel_job(jobs[0].id)

        self.assertEqual(runtime.canceled, ["job_canceled"])
        self.assertEqual(runtime.closed, [])
        self.assertFalse(self.runner.resolve_approval(jobs[0].id, "appr_1", True))
        self.assertEqual(len(runtime.approval_calls), 1)

    def test_pi_cancel_discards_late_runtime_events_and_emits_one_terminal_lifecycle(self):
        runner = ScreenshotChatRunner(self.events, model_client_factory=FakeModelClient, pi_runtime_factory=BlockingPiRuntime)
        session = ScreenshotChatSession(id="s1", model_name="provider/main")
        user_message = ScreenshotChatMessage(id="m1", role="user", text="@pi hi")
        responder = ScreenshotChatResponder(name="pi", runtime="pi", metadata={"pi_config": {"enabled": True}})

        jobs = runner.start_response_jobs(session, user_message, "hi", [responder])
        self.assertTrue(BlockingPiRuntime.started.wait(1))
        runner.cancel_job(jobs[0].id)
        self.assertTrue(self.wait_until(lambda: jobs[0].id not in runner._cancel_flags))
        events = self.drain_events()

        event_types = [event.type for event in events]
        self.assertNotIn("tool_event", event_types)
        self.assertNotIn("message_final", event_types)
        self.assertEqual(event_types.count("job_finished"), 1)
        runtime = BlockingPiRuntime.instances[-1]
        self.assertEqual(runtime.canceled, ["job_canceled"])
        self.assertEqual(runtime.closed, [3000])

    def test_history_payload_skips_thinking_and_excluded_message(self):
        session = ScreenshotChatSession(
            id="s1",
            messages=[
                ScreenshotChatMessage(id="u1", role="user", text="hello"),
                ScreenshotChatMessage(id="a1", role="assistant", author="main", text="answer"),
                ScreenshotChatMessage(id="a2", role="assistant", text="Thinking", status="thinking"),
                ScreenshotChatMessage(id="u2", role="user", text="queued", status="queued"),
                ScreenshotChatMessage(id="u3", role="user", text="canceled", status="canceled"),
                ScreenshotChatMessage(id="u4", role="user", author="note", text="note", status="note"),
                ScreenshotChatMessage(id="u5", role="user", text="deleted", status="deleted"),
            ],
        )

        payload = self.runner.build_history_payload(session, exclude_message_id="u1")

        self.assertEqual(payload, [{"role": "model", "parts": ["answer"]}])

    def test_history_payload_strips_repeated_main_prefixes(self):
        session = ScreenshotChatSession(
            id="s1",
            messages=[
                ScreenshotChatMessage(id="a1", role="assistant", author="main", text="main: main: hello"),
            ],
        )

        payload = self.runner.build_history_payload(session)

        self.assertEqual(payload, [{"role": "model", "parts": ["hello"]}])

    def test_history_payload_keeps_one_non_main_prefix(self):
        session = ScreenshotChatSession(
            id="s1",
            messages=[
                ScreenshotChatMessage(id="a1", role="assistant", author="critic", text="critic: critic: check this"),
            ],
        )

        payload = self.runner.build_history_payload(session)

        self.assertEqual(payload, [{"role": "model", "parts": ["critic: check this"]}])

    def test_response_to_text_empty_paths_are_explicit_errors(self):
        self.assertEqual(self.runner._response_to_text(None), "[Error: No response received]")
        self.assertEqual(self.runner._response_to_text({}), "[Error: Response has no content]")
        self.assertEqual(self.runner._response_to_text({"text": ""}), "[Error: Response has no content]")

    def test_llm_callback_sanitizes_provider_prefixed_response(self):
        FakeModelClient.callback_response = {"text": "main: main: clean answer"}
        session = ScreenshotChatSession(id="s1", model_name="provider/main")
        user_message = ScreenshotChatMessage(id="m1", role="user", text="hi")
        responder = ScreenshotChatResponder(name="main", kind="main")

        self.runner.start_response_jobs(session, user_message, "hi", [responder])
        events = self.drain_events()

        final = next(event for event in events if event.type == "message_final")
        self.assertEqual(final.text, "clean answer")


if __name__ == "__main__":
    unittest.main()
