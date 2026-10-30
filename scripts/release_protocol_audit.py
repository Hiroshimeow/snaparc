from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOTS = [ROOT / "src", ROOT / "main.py", ROOT / "sample"]

FORBIDDEN = {
    "google.genai": "vendor-specific Gemini SDK",
    "google.generativeai": "vendor-specific Gemini SDK",
    "google-gemini": "vendor-specific Gemini API mode",
    "from openai import": "OpenAI vendor SDK import",
    "import openai": "OpenAI vendor SDK import",
    "api.openai.com": "hard-coded OpenAI cloud endpoint",
    "/audio/transcriptions": "separate audio transcription endpoint",
    "/responses": "separate Responses API endpoint",
    "/v1/models": "remote model-discovery endpoint",
    ".models.list": "remote model-discovery client",
    "NEW_API_": "legacy environment API fallback",
    "OPENAI_BASE_URL": "legacy vendor/base-url environment path",
    "OPENAI_API_KEY": "legacy vendor-key environment path",
    "GEMINI_API_KEY": "legacy Gemini key path",
    "100.82.151.89": "private/internal endpoint default",
    "src.models.Gemini": "legacy Gemini facade import",
}

FORBIDDEN_FILES = (
    ROOT / "src" / "models" / "Gemini.py",
    ROOT / "src" / "models" / "new_api.py",
    ROOT / "src" / "llm" / "providers" / "openai.py",
)


def iter_runtime_files():
    for root in RUNTIME_ROOTS:
        if root.is_file():
            yield root
            continue
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.lower() in {".py", ".yaml", ".yml", ".json", ".toml"}:
                yield path


def main() -> int:
    failures: list[str] = []
    endpoint_literals = 0

    for path in FORBIDDEN_FILES:
        if path.exists():
            failures.append(f"legacy/vendor-specific file must not exist: {path.relative_to(ROOT)}")

    for path in iter_runtime_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = path.relative_to(ROOT)
        for needle, reason in FORBIDDEN.items():
            if needle in text:
                failures.append(f"{rel}: forbidden {reason}: {needle}")
        endpoint_literals += text.count('"chat/completions"') + text.count("'chat/completions'")

    provider_path = ROOT / "src" / "llm" / "providers" / "completions.py"
    if not provider_path.is_file():
        failures.append("generic Chat Completions provider is missing")
    elif endpoint_literals != 1:
        failures.append(
            f"expected exactly one runtime chat/completions endpoint literal, found {endpoint_literals}"
        )

    if failures:
        for failure in failures:
            print(f"ERROR: {failure}")
        return 1

    print("protocol=openai-completions transport=httpx endpoint_literals=1")
    print("no vendor SDK, vendor cloud endpoint, legacy Gemini facade, model discovery, or alternate inference endpoint found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
