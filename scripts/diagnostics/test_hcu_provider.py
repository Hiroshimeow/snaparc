import argparse
import base64
import json
import sys
from pathlib import Path

import httpx
from openai import OpenAI


DEFAULT_PROVIDER = "cliproxy-2080"
DEFAULT_MODEL = "gemini-3-flash"
DEFAULT_TEXT_PROMPT = "hello"
DEFAULT_IMAGE_PROMPT = "Describe this image briefly."


def load_provider_config(repo_root: Path, provider_name: str) -> dict:
    config_path = repo_root / "stc.json"
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    providers = payload.get("models", {}).get("providers", {})
    provider = providers.get(provider_name)
    if not isinstance(provider, dict):
        raise KeyError(f"Provider not found in stc.json: {provider_name}")
    return provider


def build_client(provider: dict) -> OpenAI:
    timeout = float(provider.get("timeout", 120.0))
    trust_env = bool(provider.get("trustEnv", True))
    return OpenAI(
        base_url=provider["baseUrl"],
        api_key=provider["apiKey"],
        timeout=timeout,
        http_client=httpx.Client(trust_env=trust_env, timeout=timeout),
    )


def make_data_url(image_path: Path | None) -> str:
    if image_path is not None:
        data = image_path.read_bytes()
        suffix = image_path.suffix.lower()
        mime_type = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
        }.get(suffix, "application/octet-stream")
    else:
        # 1x1 PNG
        data = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
            "/w8AAn8B9oNcamQAAAAASUVORK5CYII="
        )
        mime_type = "image/png"

    return f"data:{mime_type};base64,{base64.b64encode(data).decode('ascii')}"


def create_messages(multimodal: bool, prompt: str, image_data_url: str | None) -> list[dict]:
    if not multimodal:
        return [{"role": "user", "content": prompt}]

    return [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": image_data_url}},
            ],
        }
    ]


def call_case(
    client: OpenAI,
    model: str,
    extra_headers: dict | None,
    multimodal: bool,
    stream: bool,
    prompt: str,
    image_data_url: str | None,
) -> tuple[bool, str]:
    response = client.chat.completions.create(
        model=model,
        messages=create_messages(multimodal, prompt, image_data_url),
        stream=stream,
        extra_headers=extra_headers,
    )

    if stream:
        chunks = []
        for chunk in response:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta and delta.content:
                chunks.append(delta.content)
        return True, "".join(chunks).strip()

    message = response.choices[0].message.content
    return True, (message or "").strip()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Diagnose HCU-compatible OpenAI proxy behavior across text/image and stream modes."
    )
    parser.add_argument("--provider", default=DEFAULT_PROVIDER)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--image", help="Optional path to an image file for multimodal cases.")
    parser.add_argument("--skip-stream", action="store_true")
    parser.add_argument("--skip-multimodal", action="store_true")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[2]
    provider = load_provider_config(repo_root, args.provider)
    client = build_client(provider)
    extra_headers = provider.get("extraHeaders")
    image_data_url = None if args.skip_multimodal else make_data_url(Path(args.image) if args.image else None)

    stream_modes = [False] if args.skip_stream else [False, True]
    multimodal_modes = [False] if args.skip_multimodal else [False, True]

    print(f"Provider   : {args.provider}")
    print(f"Base URL   : {provider.get('baseUrl')}")
    print(f"Model      : {args.model}")
    print(f"Trust Env  : {provider.get('trustEnv', True)}")
    print(f"Timeout    : {provider.get('timeout', 120.0)}")
    print(f"Headers    : {extra_headers}")
    print("")

    failed = False
    for multimodal in multimodal_modes:
        for stream in stream_modes:
            prompt = DEFAULT_IMAGE_PROMPT if multimodal else DEFAULT_TEXT_PROMPT
            case_name = f"multimodal={multimodal} stream={stream}"
            print(f"=== {case_name} ===")
            try:
                ok, content = call_case(
                    client=client,
                    model=args.model,
                    extra_headers=extra_headers,
                    multimodal=multimodal,
                    stream=stream,
                    prompt=prompt,
                    image_data_url=image_data_url,
                )
                print("status    : ok")
                print(f"preview   : {content[:200]}")
            except Exception as exc:
                failed = True
                print("status    : error")
                print(f"type      : {type(exc).__name__}")
                print(f"message   : {exc}")
            print("")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
