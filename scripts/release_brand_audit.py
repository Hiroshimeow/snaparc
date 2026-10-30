from __future__ import annotations

from pathlib import Path

FILES = [
    Path("main.py"),
    Path("src/gui/radial_wheel.py"),
    Path("src/gui/screenshot_chat/window.py"),
    Path("src/gui/screenshot_chat/web_assets/index.html"),
    Path("src/gui/screenshot_chat/web_assets/app.js"),
]
BANNED = (
    "Screens-Trans-Chatbot",
    "Pi Sidecar Studio",
    "SCREENSHOT & TRANSLATE CHATBOT",
    "RADIAL v2.4",
    "Pi Sidecar Workspace (v2.4)",
    "STC Workspace",
)


def main() -> int:
    failures: list[str] = []
    for path in FILES:
        text = path.read_text(encoding="utf-8")
        for token in BANNED:
            if token.lower() in text.lower():
                failures.append(f"{path}: legacy user-facing identity: {token}")
    if "# SnapArc v0.0.2" not in Path("README.md").read_text(encoding="utf-8"):
        failures.append("README missing SnapArc v0.0.2 identity")
    if failures:
        for failure in failures:
            print(f"ERROR: {failure}")
        return 1
    print("release brand audit passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
