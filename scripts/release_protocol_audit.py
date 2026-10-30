from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOTS = [ROOT / "src", ROOT / "main.py", ROOT / "sample"]
FORBIDDEN = {
    "google.genai": "vendor-specific Gemini SDK",
    "google.generativeai": "vendor-specific Gemini SDK",
    "google-gemini": "legacy Gemini API mode",
    ".audio.transcriptions": "separate audio transcription endpoint",
    ".models.list": "remote model-discovery endpoint",
    "NEW_API_": "legacy environment API fallback",
    "OPENAI_BASE_URL": "legacy vendor/base-url environment path",
    "OPENAI_API_KEY": "legacy vendor-key environment path",
    "GEMINI_API_KEY": "legacy Gemini key path",
    "100.82.151.89": "private/internal endpoint default",
}


def iter_runtime_files():
    for root in RUNTIME_ROOTS:
        if root.is_file():
            yield root
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.lower() in {".py", ".yaml", ".yml", ".json", ".toml"}:
                yield path


def main() -> int:
    failures: list[str] = []
    completion_calls = 0
    other_client_create_calls = 0

    for path in iter_runtime_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = path.relative_to(ROOT)
        for needle, reason in FORBIDDEN.items():
            if needle in text:
                failures.append(f"{rel}: forbidden {reason}: {needle}")
        completion_calls += text.count(".chat.completions.create(")
        for line in text.splitlines():
            if "client." in line and ".create(" in line and ".chat.completions.create(" not in line:
                other_client_create_calls += 1
                failures.append(f"{rel}: non-Chat-Completions client create call: {line.strip()}")

    if completion_calls < 1:
        failures.append("No Chat Completions inference call found")
    if failures:
        for failure in failures:
            print(f"ERROR: {failure}")
        return 1

    print(f"protocol=openai-completions chat_completion_calls={completion_calls}")
    print("no vendor-specific Gemini SDK/API mode, model-list endpoint, audio-transcription endpoint, or legacy API env fallback found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
