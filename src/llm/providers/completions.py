from __future__ import annotations

import base64
import io
import json
import os
import threading
import traceback
from typing import Iterable

import httpx

from src.llm.request_bounds import completion_options


def _coerce_bool(value, default=True):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _coerce_timeout(value, default=120.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _guess_mime_type(image_path):
    suffix = os.path.splitext(str(image_path))[1].lower()
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
    }.get(suffix, "image/jpeg")


class ChatCompletionsProvider:
    """Provider for the OpenAI-compatible Chat Completions wire protocol.

    This class is intentionally vendor-neutral. It uses httpx directly and only
    sends POST requests to the configured `baseUrl/chat/completions` endpoint.
    """

    platform = "openai-compatible"

    def __init__(
        self,
        api_key=None,
        base_url=None,
        model=None,
        provider_name="",
        timeout=120.0,
        trust_env=True,
        extra_headers=None,
    ):
        self.api_key = str(api_key or "").strip()
        self.base_url = str(base_url or "").strip().rstrip("/")
        if not self.base_url:
            raise ValueError("Chat Completions provider requires baseUrl in config.yaml")
        self.model = str(model or "").strip()
        self.provider_name = str(provider_name or "").strip()
        self.timeout = _coerce_timeout(timeout, 120.0)
        self.trust_env = _coerce_bool(trust_env, True)
        self.extra_headers = dict(extra_headers) if isinstance(extra_headers, dict) else {}
        self.client = httpx.Client(
            base_url=self.base_url + "/",
            timeout=self.timeout,
            trust_env=self.trust_env,
        )

    def supports(self, feature_name):
        return feature_name in {"text", "images", "audio", "stream", "tools"}

    def close(self):
        self.client.close()

    def _headers(self):
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        headers.update({str(k): str(v) for k, v in self.extra_headers.items()})
        return headers

    def _request_timeout(self, timeout_seconds):
        return self.timeout if timeout_seconds is None else float(timeout_seconds)

    def _send(self, payload: dict, *, stream: bool, timeout_seconds=None) -> httpx.Response:
        request = self.client.build_request(
            "POST",
            "chat/completions",
            json=payload,
            headers=self._headers(),
            timeout=self._request_timeout(timeout_seconds),
        )
        response = self.client.send(request, stream=stream)
        response.raise_for_status()
        return response

    def open_stream(self, payload: dict, *, timeout_seconds=None) -> httpx.Response:
        return self._send(payload, stream=True, timeout_seconds=timeout_seconds)

    @staticmethod
    def iter_sse(response: httpx.Response) -> Iterable[dict]:
        try:
            for raw_line in response.iter_lines():
                line = str(raw_line or "").strip()
                if not line:
                    continue
                if line.startswith("data:"):
                    line = line[5:].strip()
                if not line or line == "[DONE]":
                    if line == "[DONE]":
                        break
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    yield payload
        finally:
            response.close()

    def create_json(self, payload: dict, *, timeout_seconds=None) -> dict:
        response = self._send(payload, stream=False, timeout_seconds=timeout_seconds)
        try:
            data = response.json()
            return data if isinstance(data, dict) else {}
        finally:
            response.close()

    @staticmethod
    def _stream_text(chunks: Iterable[dict]) -> str:
        parts = []
        for chunk in chunks:
            choices = chunk.get("choices")
            if not isinstance(choices, list) or not choices:
                continue
            delta = choices[0].get("delta") if isinstance(choices[0], dict) else None
            if isinstance(delta, dict) and delta.get("content"):
                parts.append(str(delta["content"]))
        return "".join(parts)

    def _convert_history(self, history):
        messages = []
        for item in history or []:
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            if role == "model":
                role = "assistant"
            if role not in ("user", "assistant", "system"):
                continue
            content = item.get("content")
            if not isinstance(content, str):
                parts = item.get("parts", [])
                if isinstance(parts, list):
                    content = "".join(part for part in parts if isinstance(part, str))
                elif isinstance(parts, str):
                    content = parts
                else:
                    content = ""
            if content:
                messages.append({"role": role, "content": content})
        return messages

    @staticmethod
    def _encode_image(image_path):
        with open(image_path, "rb") as image_file:
            return _guess_mime_type(image_path), base64.b64encode(image_file.read()).decode("ascii")

    @staticmethod
    def _split_bounds(max_output_tokens=None, timeout_seconds=None):
        options = completion_options(max_output_tokens, timeout_seconds)
        request_timeout = options.pop("timeout", None)
        return options, request_timeout

    def process_image(
        self,
        image_paths,
        prompt,
        callback=None,
        history=None,
        model=None,
        max_output_tokens=None,
        timeout_seconds=None,
    ):
        options, request_timeout = self._split_bounds(max_output_tokens, timeout_seconds)

        def task():
            try:
                messages = self._convert_history(history)
                content = [{"type": "text", "text": prompt}]
                for path in image_paths if isinstance(image_paths, list) else [image_paths]:
                    mime_type, encoded = self._encode_image(path)
                    content.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime_type};base64,{encoded}"},
                        }
                    )
                messages.append({"role": "user", "content": content})
                payload = {
                    "model": model or self.model,
                    "messages": messages,
                    "stream": True,
                    **options,
                }
                response = self.open_stream(payload, timeout_seconds=request_timeout)
                text = self._stream_text(self.iter_sse(response))
                if callback:
                    callback(self._success_payload(text))
            except Exception as exc:
                print(f"Error in process_image: {exc}")
                traceback.print_exc()
                if callback:
                    callback(self._error_payload(exc))

        threading.Thread(target=task, daemon=True).start()

    def send_text_message(
        self,
        text_prompt,
        callback=None,
        history=None,
        system_prompt=None,
        temperature=0.7,
        model_name=None,
        image=None,
        max_output_tokens=None,
        timeout_seconds=None,
    ):
        options, request_timeout = self._split_bounds(max_output_tokens, timeout_seconds)

        def task():
            try:
                messages = []
                if system_prompt:
                    messages.append({"role": "system", "content": system_prompt})
                messages.extend(self._convert_history(history))
                if image:
                    mime_type, encoded = self._encode_image(image)
                    content = [
                        {"type": "text", "text": text_prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime_type};base64,{encoded}"},
                        },
                    ]
                else:
                    content = text_prompt
                messages.append({"role": "user", "content": content})
                payload = {
                    "model": model_name or self.model,
                    "messages": messages,
                    "temperature": temperature,
                    "stream": True,
                    **options,
                }
                response = self.open_stream(payload, timeout_seconds=request_timeout)
                text = self._stream_text(self.iter_sse(response))
                if callback:
                    callback(self._success_payload(text))
            except Exception as exc:
                print(f"Error in send_text_message: {exc}")
                traceback.print_exc()
                if callback:
                    callback(self._error_payload(exc))

        threading.Thread(target=task, daemon=True).start()

    def process_audio(
        self,
        audio_path,
        callback=None,
        prompt="Transcribe this audio",
        model=None,
        max_output_tokens=None,
        timeout_seconds=None,
    ):
        options, request_timeout = self._split_bounds(max_output_tokens, timeout_seconds)

        def task():
            try:
                audio_data, audio_format = self._encode_audio_input(audio_path)
                payload = {
                    "model": model or self.model,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {
                                    "type": "input_audio",
                                    "input_audio": {"data": audio_data, "format": audio_format},
                                },
                            ],
                        }
                    ],
                    "stream": False,
                    **options,
                }
                data = self.create_json(payload, timeout_seconds=request_timeout)
                choices = data.get("choices") if isinstance(data, dict) else None
                message = choices[0].get("message") if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
                text = message.get("content") if isinstance(message, dict) else ""
                if callback:
                    callback(text or None)
            except Exception as exc:
                print(f"Error in process_audio: {exc}")
                traceback.print_exc()
                if callback:
                    callback(None)

        threading.Thread(target=task, daemon=True).start()

    @staticmethod
    def _encode_audio_input(audio_path):
        suffix = os.path.splitext(str(audio_path))[1].lower()
        if suffix in {".wav", ".mp3"}:
            with open(audio_path, "rb") as audio_file:
                raw = audio_file.read()
            return base64.b64encode(raw).decode("ascii"), suffix[1:]

        import soundfile as sf

        samples, sample_rate = sf.read(audio_path, dtype="float32", always_2d=False)
        buffer = io.BytesIO()
        sf.write(buffer, samples, sample_rate, format="WAV", subtype="PCM_16")
        return base64.b64encode(buffer.getvalue()).decode("ascii"), "wav"

    def _success_payload(self, text):
        return {
            "text": text,
            "api_index": self.provider_name,
            "key_mask": self.base_url.split("//")[-1].split("/")[0] if self.base_url else "???",
        }

    def _error_payload(self, exc):
        return {
            "text": f"Error: {str(exc)}",
            "api_index": self.provider_name,
            "key_mask": "Error",
        }
