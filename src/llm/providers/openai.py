import base64
import os
import threading
import traceback

import httpx
from openai import OpenAI
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


class OpenAICompatibleProvider:
    """Unified OpenAI-compatible Completions Provider (`openai-completions`) for v2.4."""

    platform = "openai"

    def __init__(
        self,
        api_key=None,
        base_url=None,
        model=None,
        provider_name="cliproxy",
        timeout=120.0,
        trust_env=True,
        extra_headers=None,
    ):
        self.api_key = api_key or os.getenv("NEW_API_KEY") or "sk-local-proxy"
        self.base_url = base_url or os.getenv(
            "NEW_API_BASE_URL", "http://100.82.151.89:8317/v1"
        )
        self.model = model or os.getenv("NEW_API_MODEL", "gpt-5.4")
        self.provider_name = provider_name
        self.timeout = _coerce_timeout(timeout, 120.0)
        self.trust_env = _coerce_bool(trust_env, True)
        self.extra_headers = (
            dict(extra_headers) if isinstance(extra_headers, dict) else None
        )
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout,
            max_retries=0,
            http_client=httpx.Client(
                trust_env=self.trust_env,
                timeout=self.timeout,
            ),
        )

    def supports(self, feature_name):
        return feature_name in {"text", "images", "audio", "stream", "tools"}

    def list_remote_models(self) -> list[str]:
        """Fetch available model IDs from `{base_url}/models` on the OpenAI-compatible endpoint."""
        try:
            resp = self.client.models.list(extra_headers=self.extra_headers)
            data = getattr(resp, "data", None) or []
            models = []
            for item in data:
                mid = getattr(item, "id", None) or (item.get("id") if isinstance(item, dict) else None)
                if mid and str(mid).strip():
                    models.append(str(mid).strip())
            return models
        except Exception:
            return []

    def _convert_history(self, history):
        """Converts role/parts or role/content history into OpenAI chat messages."""
        openai_history = []
        if not history:
            return openai_history

        for item in history:
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            if role == "model":
                role = "assistant"
            if role not in ("user", "assistant", "system"):
                continue

            content = ""
            if isinstance(item.get("content"), str):
                content = item["content"]
            else:
                parts = item.get("parts", [])
                if isinstance(parts, list):
                    for part in parts:
                        if isinstance(part, str):
                            content += part
                elif isinstance(parts, str):
                    content = parts

            if content:
                openai_history.append({"role": role, "content": content})

        return openai_history

    def _encode_image(self, image_path):
        with open(image_path, "rb") as image_file:
            return (
                _guess_mime_type(image_path),
                base64.b64encode(image_file.read()).decode("utf-8"),
            )

    def process_image(self, image_paths, prompt, callback=None, history=None, model=None,
                      max_output_tokens=None, timeout_seconds=None):
        options = completion_options(max_output_tokens, timeout_seconds)
        def task():
            try:
                messages = []
                if history:
                    messages.extend(self._convert_history(history))

                local_paths = image_paths if isinstance(image_paths, list) else [image_paths]

                content_list = [{"type": "text", "text": prompt}]
                for path in local_paths:
                    mime_type, base64_image = self._encode_image(path)
                    content_list.append(
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime_type};base64,{base64_image}"
                            },
                        }
                    )

                messages.append({"role": "user", "content": content_list})

                response = self.client.chat.completions.create(
                    model=model or self.model,
                    messages=messages,
                    **options,
                    stream=True,
                    extra_headers=self.extra_headers,
                )

                full_response = self._collect_stream_text(response)
                if callback:
                    callback(self._success_payload(full_response))

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
        options = completion_options(max_output_tokens, timeout_seconds)
        def task():
            try:
                messages = []
                if system_prompt:
                    messages.append({"role": "system", "content": system_prompt})

                if history:
                    messages.extend(self._convert_history(history))

                if image:
                    mime_type, base64_image = self._encode_image(image)
                    messages.append(
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": text_prompt},
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:{mime_type};base64,{base64_image}"
                                    },
                                },
                            ],
                        }
                    )
                else:
                    messages.append({"role": "user", "content": text_prompt})

                response = self.client.chat.completions.create(
                    model=model_name or self.model,
                    messages=messages,
                    temperature=temperature,
                    **options,
                    stream=True,
                    extra_headers=self.extra_headers,
                )

                full_response = self._collect_stream_text(response)
                if callback:
                    callback(self._success_payload(full_response))

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
    ):
        def task():
            try:
                with open(audio_path, "rb") as audio_file:
                    response = self.client.audio.transcriptions.create(
                        model=model or self.model,
                        file=audio_file,
                        prompt=prompt,
                    )

                if isinstance(response, str):
                    text = response
                else:
                    text = getattr(response, "text", "") or ""

                if callback:
                    callback(text)
            except Exception as exc:
                print(f"Error in process_audio: {exc}")
                traceback.print_exc()
                if callback:
                    callback(None)

        threading.Thread(target=task, daemon=True).start()

    def _collect_stream_text(self, response):
        full_response = ""
        for chunk in response:
            if not chunk or not hasattr(chunk, "choices") or not chunk.choices:
                continue

            try:
                if len(chunk.choices) == 0:
                    continue
                choice = chunk.choices[0]
            except (IndexError, TypeError):
                continue

            if hasattr(choice, "delta") and choice.delta:
                delta = choice.delta
                if hasattr(delta, "content") and delta.content:
                    full_response += delta.content
        return full_response

    def _success_payload(self, text):
        return {
            "text": text,
            "api_index": self.provider_name,
            "key_mask": self.base_url.split("//")[-1].split("/")[0]
            if self.base_url
            else "???",
        }

    def _error_payload(self, exc):
        return {
            "text": f"Error: {str(exc)}",
            "api_index": self.provider_name,
            "key_mask": "Error",
        }


ApiProvider = OpenAICompatibleProvider
