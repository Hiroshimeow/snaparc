from __future__ import annotations

from dataclasses import dataclass
import os
import queue
import threading
from typing import Callable

from src.agents.pi_contract import PiRunRequest, PiRuntime, PiRuntimeConfig, PiRuntimeFactory
from src.agents.pi_runtime_factory import build_pi_runtime_factory
from src.gui.chat_text_sanitizer import history_text_for_author, strip_repeated_author_prefixes
from src.gui.screenshot_chat.models import (
    ScreenshotChatAttachment,
    ScreenshotChatJob,
    ScreenshotChatMessage,
    ScreenshotChatResponder,
    ScreenshotChatSession,
)
from src.llm.client import ModelClient
from src.llm.service import LLMService


PI_MODEL_ID_PREFIX = "pi://agent/"


@dataclass
class RunnerEvent:
    type: str
    session_id: str
    job_id: str
    responder_name: str
    message_id: str = ""
    text: str = ""
    error: str = ""
    metadata: dict | None = None
    partial: bool = False


class ScreenshotChatRunner:
    MAX_TEXT_ATTACHMENT_CHARS = 12000
    MAX_PDF_ATTACHMENT_PAGES = 20
    TEXT_ATTACHMENT_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".log"}
    PDF_ATTACHMENT_EXTENSIONS = {".pdf"}

    def __init__(
        self,
        event_queue: queue.Queue | None = None,
        llm_service: LLMService | None = None,
        model_client_factory: Callable[[], ModelClient] | None = None,
        pi_runtime_factory: PiRuntimeFactory | Callable | None = None,
    ):
        self.event_queue = event_queue or queue.Queue()
        self.llm_service = llm_service
        self.model_client_factory = model_client_factory or ModelClient
        self._cancel_lock = threading.Lock()
        self._cancel_flags: dict[str, threading.Event] = {}
        self.pi_runtime_factory = pi_runtime_factory
        self._owned_pi_runtime_factory = None
        self._owned_pi_backend = ""
        self._pi_factory_lock = threading.Lock()
        self._active_pi_runtimes: dict[str, PiRuntime] = {}
        self._active_pi_lock = threading.Lock()

    def register_pi_runtime(self, job_id: str, runtime):
        with self._active_pi_lock:
            self._active_pi_runtimes[job_id] = runtime

    def unregister_pi_runtime(self, job_id: str):
        with self._active_pi_lock:
            self._active_pi_runtimes.pop(job_id, None)

    def resolve_approval(
        self,
        job_id: str,
        approval_id: str,
        approved: bool,
        reason: str = "",
        payload: dict | None = None,
    ) -> bool:
        with self._cancel_lock:
            cancel_flag = self._cancel_flags.get(job_id)
            if cancel_flag is not None and cancel_flag.is_set():
                return False
        with self._active_pi_lock:
            runtime = self._active_pi_runtimes.get(job_id)
        if runtime is None or not hasattr(runtime, "resolve_approval"):
            return False
        return bool(runtime.resolve_approval(job_id, approval_id, approved, reason, payload))

    def cancel_job(self, job_id: str):
        with self._cancel_lock:
            cancel_flag = self._cancel_flags.get(job_id)
            already_canceled = bool(cancel_flag is not None and cancel_flag.is_set())
            if cancel_flag is not None:
                cancel_flag.set()
        if already_canceled:
            return
        with self._active_pi_lock:
            runtime = self._active_pi_runtimes.get(job_id)
        if runtime is not None:
            try:
                if hasattr(runtime, "cancel"):
                    runtime.cancel("job_canceled")
            except Exception:
                pass

    def cancel_jobs(self, job_ids: list[str]):
        for job_id in list(job_ids):
            self.cancel_job(job_id)

    def close_pi_session(self, application_session_id: str, responder_names: list[str], timeout_ms: int = 3000):
        factories = [self.pi_runtime_factory, self._owned_pi_runtime_factory]
        seen_factories = set()
        names = list(dict.fromkeys(str(name or "").strip() for name in responder_names if str(name or "").strip()))
        for factory in factories:
            if factory is None or id(factory) in seen_factories:
                continue
            seen_factories.add(id(factory))
            close_session = getattr(factory, "close_session", None)
            if not callable(close_session):
                continue
            for responder_name in names:
                try:
                    close_session(application_session_id, responder_name, timeout_ms=timeout_ms)
                except Exception:
                    pass

    def shutdown(self, timeout_ms: int = 3000):
        with self._active_pi_lock:
            runtimes = list(self._active_pi_runtimes.items())
            self._active_pi_runtimes.clear()
        for job_id, runtime in runtimes:
            with self._cancel_lock:
                flag = self._cancel_flags.get(job_id)
            if flag is not None:
                flag.set()
            try:
                if hasattr(runtime, "cancel"):
                    runtime.cancel("app_shutdown")
                if hasattr(runtime, "close"):
                    runtime.close(timeout_ms)
            except Exception:
                pass
        factories = [self.pi_runtime_factory, self._owned_pi_runtime_factory]
        seen = set()
        for factory in factories:
            if factory is None or id(factory) in seen:
                continue
            seen.add(id(factory))
            shutdown_factory = getattr(factory, "shutdown", None)
            if callable(shutdown_factory):
                try:
                    shutdown_factory(timeout_ms)
                except Exception:
                    pass

    def start_response_jobs(
        self,
        session: ScreenshotChatSession,
        user_message: ScreenshotChatMessage,
        prompt_text: str,
        responders: list[ScreenshotChatResponder],
        temperature: float = 0.7,
    ) -> list[ScreenshotChatJob]:
        jobs = self.prepare_response_jobs(session, user_message, responders)
        self.start_prepared_jobs(session, user_message, prompt_text, responders, jobs, temperature)
        return jobs

    def prepare_response_jobs(
        self,
        session: ScreenshotChatSession,
        user_message: ScreenshotChatMessage,
        responders: list[ScreenshotChatResponder],
    ) -> list[ScreenshotChatJob]:
        jobs = []
        for responder in responders:
            job = ScreenshotChatJob(
                session_id=session.id,
                message_id=user_message.id,
                responder_name=responder.name,
            )
            cancel_flag = threading.Event()
            with self._cancel_lock:
                self._cancel_flags[job.id] = cancel_flag
            jobs.append(job)
        return jobs

    def start_prepared_jobs(
        self,
        session: ScreenshotChatSession,
        user_message: ScreenshotChatMessage,
        prompt_text: str,
        responders: list[ScreenshotChatResponder],
        jobs: list[ScreenshotChatJob],
        temperature: float = 0.7,
    ):
        responder_by_name = {item.name: item for item in responders}
        for job in jobs:
            responder = responder_by_name.get(job.responder_name)
            if responder is None:
                continue
            with self._cancel_lock:
                cancel_flag = self._cancel_flags.get(job.id)
            if cancel_flag is None:
                cancel_flag = threading.Event()
                with self._cancel_lock:
                    self._cancel_flags[job.id] = cancel_flag
            thread = threading.Thread(
                target=self._run_responder,
                args=(session, user_message, prompt_text, responder, job, temperature, cancel_flag),
                name=f"screenshot-chat-{responder.name}",
                daemon=True,
            )
            thread.start()

    def _run_responder(
        self,
        session: ScreenshotChatSession,
        user_message: ScreenshotChatMessage,
        prompt_text: str,
        responder: ScreenshotChatResponder,
        job: ScreenshotChatJob,
        temperature: float,
        cancel_flag: threading.Event,
    ):
        runtime = (getattr(responder, "runtime", "llm") or "llm").lower()
        provider = (getattr(responder, "metadata", {}) or {}).get("provider", "")
        if runtime == "pi" or provider == "pi":
            self._run_pi_responder(session, user_message, prompt_text, responder, job, temperature, cancel_flag)
            return
        self._run_llm_responder(session, user_message, prompt_text, responder, job, temperature, cancel_flag)

    def _run_llm_responder(
        self,
        session: ScreenshotChatSession,
        user_message: ScreenshotChatMessage,
        prompt_text: str,
        responder: ScreenshotChatResponder,
        job: ScreenshotChatJob,
        temperature: float,
        cancel_flag: threading.Event,
        emit_start: bool = True,
    ):
        if emit_start:
            self._emit("job_started", session.id, job.id, responder.name, user_message.id)
        try:
            model_name = responder.model_name or session.model_name
            if self._is_pi_model_name(model_name):
                self._emit(
                    "job_error",
                    session.id,
                    job.id,
                    responder.name,
                    user_message.id,
                    error="Pi fallback requires a normal LLM model, not the Pi Agent sentinel.",
                )
                self._emit("job_finished", session.id, job.id, responder.name, user_message.id)
                self._forget_cancel_flag(job.id)
                return
            bot = self.llm_service or self.model_client_factory()
            uses_shared_service = self.llm_service is not None
            if model_name and hasattr(bot, "set_model") and not uses_shared_service:
                bot.set_model(model_name)
            request_model_name = self._model_id_only(model_name)
            if responder.kind == "main" and responder.prompt_type and not uses_shared_service:
                bot.prompt_type = responder.prompt_type

            history = self.build_history_payload(session, exclude_message_id=user_message.id)
            image_attachments = self._existing_image_attachments(user_message.attachments)
            text_context = self._text_attachment_context(user_message.attachments)
            request_text = self._request_text_with_attachments(
                prompt_text.strip(),
                bool(image_attachments),
                text_context,
            )
            system_prompt = responder.system_prompt or None

            def callback(response):
                if cancel_flag.is_set():
                    self._emit("job_finished", session.id, job.id, responder.name, user_message.id)
                    self._forget_cancel_flag(job.id)
                    return
                text = strip_repeated_author_prefixes(
                    self._response_to_text(response),
                    extra_authors=(responder.name,),
                )
                if text.startswith("[Error:"):
                    self._emit(
                        "job_error",
                        session.id,
                        job.id,
                        responder.name,
                        user_message.id,
                        error=text,
                    )
                else:
                    self._emit(
                        "message_final",
                        session.id,
                        job.id,
                        responder.name,
                        user_message.id,
                        text=text,
                    )
                self._emit("job_finished", session.id, job.id, responder.name, user_message.id)
                self._forget_cancel_flag(job.id)

            if self._should_use_image_processing(request_text, image_attachments):
                if hasattr(bot, "current_prompt"):
                    bot.current_prompt = request_text
                bot.process_image(
                    [item.path for item in image_attachments],
                    callback=callback,
                    history=history,
                    custom_prompt_text=request_text if responder.kind == "main" else system_prompt or request_text,
                    **({"model_full_name": model_name} if uses_shared_service else {}),
                )
            else:
                image_path = image_attachments[0].path if image_attachments else None
                bot.send_text_message(
                    request_text,
                    callback=callback,
                    history=history,
                    system_prompt=system_prompt,
                    temperature=temperature,
                    model_name=(model_name if uses_shared_service else request_model_name) or None,
                    image=image_path,
                )
        except Exception as exc:
            if not cancel_flag.is_set():
                self._emit(
                    "job_error",
                    session.id,
                    job.id,
                    responder.name,
                    user_message.id,
                    error=f"[Error: {exc}]",
                )
                self._emit("job_finished", session.id, job.id, responder.name, user_message.id)
            self._forget_cancel_flag(job.id)

    def _run_pi_responder(
        self,
        session: ScreenshotChatSession,
        user_message: ScreenshotChatMessage,
        prompt_text: str,
        responder: ScreenshotChatResponder,
        job: ScreenshotChatJob,
        temperature: float,
        cancel_flag: threading.Event,
    ):
        self._emit("job_started", session.id, job.id, responder.name, user_message.id)
        metadata = getattr(responder, "metadata", {}) or {}
        pi_config = metadata.get("pi_config") if isinstance(metadata.get("pi_config"), dict) else {}

        def emit(event_type: str, text: str = "", error: str = "", metadata: dict | None = None, partial: bool = False):
            if cancel_flag.is_set():
                return
            self._emit(
                event_type,
                session.id,
                job.id,
                responder.name,
                user_message.id,
                text=text,
                error=error,
                metadata=metadata,
                partial=partial,
            )

        runtime_config = PiRuntimeConfig.from_dict(pi_config)
        runtime = None
        registered_runtime = False
        fallback_handoff = False
        try:
            factory = self.pi_runtime_factory
            if factory is None:
                with self._pi_factory_lock:
                    if self._owned_pi_runtime_factory is None or self._owned_pi_backend != runtime_config.backend:
                        previous = self._owned_pi_runtime_factory
                        if previous is not None:
                            try:
                                previous.shutdown(runtime_config.shutdown_timeout_ms)
                            except Exception:
                                pass
                        self._owned_pi_runtime_factory = build_pi_runtime_factory(runtime_config, emit)
                        self._owned_pi_backend = runtime_config.backend
                    factory = self._owned_pi_runtime_factory
            runtime = factory(runtime_config, emit)
            self.register_pi_runtime(job.id, runtime)
            registered_runtime = True
            history = self.build_history_payload(session, exclude_message_id=user_message.id)
            image_attachments = self._existing_image_attachments(user_message.attachments)
            text_context = self._text_attachment_context(user_message.attachments)
            request_text = self._request_text_with_attachments(
                prompt_text.strip(),
                bool(image_attachments),
                text_context,
            )
            attachments = [item.to_dict() for item in user_message.attachments]
            request = PiRunRequest(
                job_id=job.id,
                session_id=session.id,
                message_id=user_message.id,
                responder_name=responder.name,
                prompt_text=request_text,
                system_prompt=responder.system_prompt or "",
                history=history,
                attachments=attachments,
                metadata=dict(metadata),
            )
            result = runtime.run(request, cancel_flag)
            if cancel_flag.is_set():
                self._emit("job_finished", session.id, job.id, responder.name, user_message.id)
                return
            if result.fallback_to_llm and not result.side_effect_started:
                self._emit(
                    "runtime_notice",
                    session.id,
                    job.id,
                    responder.name,
                    user_message.id,
                    text=result.error,
                    metadata={"kind": "runtime_notice", "status": "done"},
                )
                fallback_responder = self._fallback_llm_responder(responder)
                if fallback_responder is None:
                    self._emit(
                        "job_error",
                        session.id,
                        job.id,
                        responder.name,
                        user_message.id,
                        error="Pi fallback requires a normal LLM model, not the Pi Agent sentinel.",
                    )
                    self._emit("job_finished", session.id, job.id, responder.name, user_message.id)
                    return
                fallback_handoff = True
                self._run_llm_responder(
                    session,
                    user_message,
                    prompt_text,
                    fallback_responder,
                    job,
                    temperature,
                    cancel_flag,
                    emit_start=False,
                )
                return
            if result.error:
                self._emit(
                    "job_error",
                    session.id,
                    job.id,
                    responder.name,
                    user_message.id,
                    error=result.error,
                )
            else:
                final_text = strip_repeated_author_prefixes(result.text, extra_authors=(responder.name,))
                if final_text:
                    self._emit(
                        "message_final",
                        session.id,
                        job.id,
                        responder.name,
                        user_message.id,
                        text=final_text,
                    )
                else:
                    self._emit(
                        "job_error",
                        session.id,
                        job.id,
                        responder.name,
                        user_message.id,
                        error="Pi protocol error: no response received from Pi runtime",
                    )
            self._emit("job_finished", session.id, job.id, responder.name, user_message.id)
        except Exception as exc:
            if not cancel_flag.is_set():
                self._emit(
                    "job_error",
                    session.id,
                    job.id,
                    responder.name,
                    user_message.id,
                    error=f"Pi protocol error: {exc}",
                )
                self._emit("job_finished", session.id, job.id, responder.name, user_message.id)
        finally:
            try:
                if runtime is not None and hasattr(runtime, "close"):
                    runtime.close(3000)
            except Exception:
                pass
            if registered_runtime:
                self.unregister_pi_runtime(job.id)
            if not fallback_handoff:
                self._forget_cancel_flag(job.id)

    def _fallback_llm_responder(self, responder: ScreenshotChatResponder) -> ScreenshotChatResponder | None:
        fallback_model = self._normal_fallback_model_name(responder)
        if not fallback_model:
            return None
        metadata = dict(responder.metadata or {})
        metadata["pi_fallback"] = True
        metadata.pop("provider", None)
        return ScreenshotChatResponder(
            name=responder.name,
            kind=responder.kind,
            model_name=fallback_model,
            prompt_type=responder.prompt_type,
            system_prompt=responder.system_prompt,
            runtime="llm",
            metadata=metadata,
        )

    def _normal_fallback_model_name(self, responder: ScreenshotChatResponder) -> str:
        metadata = responder.metadata if isinstance(responder.metadata, dict) else {}
        for candidate in (metadata.get("fallback_model_name"), responder.model_name):
            if isinstance(candidate, str):
                model_name = candidate.strip()
                if model_name and not self._is_pi_model_name(model_name):
                    return model_name
        return ""

    def _is_pi_model_name(self, model_name: str) -> bool:
        return isinstance(model_name, str) and model_name.startswith(PI_MODEL_ID_PREFIX)

    def _emit(
        self,
        event_type: str,
        session_id: str,
        job_id: str,
        responder_name: str,
        message_id: str = "",
        text: str = "",
        error: str = "",
        metadata: dict | None = None,
        partial: bool = False,
    ):
        self.event_queue.put(
            RunnerEvent(
                type=event_type,
                session_id=session_id,
                job_id=job_id,
                responder_name=responder_name,
                message_id=message_id,
                text=text,
                error=error,
                metadata=metadata,
                partial=partial,
            )
        )

    def _forget_cancel_flag(self, job_id: str):
        with self._cancel_lock:
            self._cancel_flags.pop(job_id, None)

    def _model_id_only(self, model_name: str) -> str:
        if not model_name:
            return ""
        if "/" not in model_name:
            return model_name
        return model_name.split("/", 1)[1]

    def _response_to_text(self, response) -> str:
        if response is None:
            return "[Error: No response received]"
        if isinstance(response, dict):
            return str(response.get("text") or "[Error: Response has no content]")
        return str(response)

    def _existing_image_attachments(
        self,
        attachments: list[ScreenshotChatAttachment],
    ) -> list[ScreenshotChatAttachment]:
        return [
            item
            for item in attachments
            if item.kind == "image"
            and item.path
            and os.path.exists(item.path)
        ]

    def _request_text_with_attachments(
        self,
        prompt_text: str,
        has_image_attachment: bool,
        text_context: str,
    ) -> str:
        if not text_context:
            return prompt_text or "Please analyze the attached image."
        base_text = prompt_text
        if not base_text:
            base_text = "Please answer using the attached file contents."
            if has_image_attachment:
                base_text = "Please analyze the attached image and file contents."
        return f"{base_text}\n\n{text_context}"

    def _text_attachment_context(
        self,
        attachments: list[ScreenshotChatAttachment],
    ) -> str:
        blocks = []
        for attachment in attachments:
            if not self._is_readable_text_attachment(attachment):
                continue
            label = attachment.label or os.path.basename(attachment.path)
            content = self._attachment_text_content(attachment)
            truncated = len(content) > self.MAX_TEXT_ATTACHMENT_CHARS
            content = content[: self.MAX_TEXT_ATTACHMENT_CHARS].strip()
            if not content:
                continue
            suffix = "\n[truncated]" if truncated else ""
            blocks.append(f"--- {label} ---\n{content}{suffix}")
        if not blocks:
            return ""
        return "Attached file contents:\n" + "\n\n".join(blocks)

    def _attachment_text_content(self, attachment: ScreenshotChatAttachment) -> str:
        ext = os.path.splitext(attachment.path)[1].lower()
        if ext in self.PDF_ATTACHMENT_EXTENSIONS:
            return self._pdf_attachment_text(attachment.path)
        try:
            with open(attachment.path, "r", encoding="utf-8-sig", errors="replace") as handle:
                return handle.read(self.MAX_TEXT_ATTACHMENT_CHARS + 1)
        except OSError:
            return ""

    def _pdf_attachment_text(self, path: str) -> str:
        try:
            from pypdf import PdfReader

            reader = PdfReader(path)
            if reader.is_encrypted:
                try:
                    reader.decrypt("")
                except Exception:
                    return "[Could not read PDF: encrypted file]"
            page_texts = []
            for index, page in enumerate(reader.pages[: self.MAX_PDF_ATTACHMENT_PAGES]):
                text = (page.extract_text() or "").strip()
                if text:
                    page_texts.append(f"[page {index + 1}]\n{text}")
            if not page_texts:
                return "[PDF has no extractable text]"
            content = "\n\n".join(page_texts)
            if len(reader.pages) > self.MAX_PDF_ATTACHMENT_PAGES:
                content += f"\n[truncated after {self.MAX_PDF_ATTACHMENT_PAGES} pages]"
            return content
        except Exception as exc:
            return f"[Could not read PDF: {exc}]"

    def _is_readable_text_attachment(self, attachment: ScreenshotChatAttachment) -> bool:
        if attachment.kind != "file" or not attachment.path or not os.path.exists(attachment.path):
            return False
        ext = os.path.splitext(attachment.path)[1].lower()
        return ext in self.TEXT_ATTACHMENT_EXTENSIONS or ext in self.PDF_ATTACHMENT_EXTENSIONS

    def _should_use_image_processing(
        self,
        request_text: str,
        attachments: list[ScreenshotChatAttachment],
    ) -> bool:
        if len(attachments) > 1:
            return True
        text = request_text.strip().lower()
        return bool(attachments) and text in {
            "",
            "please analyze the attached image.",
            "analyze image",
            "analyze the image",
        }

    def build_history_payload(
        self,
        session: ScreenshotChatSession,
        exclude_message_id: str = "",
        limit: int = 20,
    ) -> list[dict]:
        payload = []
        for message in session.messages[-limit:]:
            if (
                message.id == exclude_message_id
                or message.status in {"thinking", "queued", "canceled", "note", "deleted"}
                or message.author == "note"
            ):
                continue
            text = (message.text or "").strip()
            if message.role == "user":
                parts = []
                if text:
                    parts.append(text)
                for attachment in message.attachments:
                    if attachment.path:
                        parts.append(f"[Attached file: {os.path.basename(attachment.path)}]")
                payload.append({"role": "user", "parts": parts or [""]})
            elif message.role == "assistant" and text:
                history_text = history_text_for_author(text, message.author)
                if history_text:
                    payload.append({"role": "model", "parts": [history_text]})
        return payload
