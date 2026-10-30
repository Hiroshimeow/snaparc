from __future__ import annotations

import os
import tkinter as tk

from src.gui.screenshot_chat.module import ChatbotModule
from src.models.Gemini import Gemini
from src.utils.ConfigManager import ConfigManager


_STANDALONE_VISIBILITY_POLL_MS = 100


def resolve_project_root() -> str:
    package_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(os.path.dirname(package_dir)))


def resolve_config_path() -> str:
    return os.path.join(resolve_project_root(), "config.yaml")


def build_module(root):
    project_root = resolve_project_root()
    config_manager = ConfigManager(resolve_config_path())
    gemini = Gemini()
    return ChatbotModule(
        root,
        config_manager=config_manager,
        project_root=project_root,
        llm_service=gemini.llm_service,
        gemini_factory=lambda: gemini,
    )


def _schedule_exit_when_hidden(root, module):
    def poll_visibility():
        controller = getattr(module, "controller", None)
        window = getattr(controller, "window", None)
        if window is None:
            root.quit()
            return
        try:
            exists = bool(window.winfo_exists())
            visible = exists and bool(window.is_visible())
        except Exception:
            visible = False
        if not visible:
            root.quit()
            return
        root.after(_STANDALONE_VISIBILITY_POLL_MS, poll_visibility)

    root.after(_STANDALONE_VISIBILITY_POLL_MS, poll_visibility)


def main() -> int:
    root = tk.Tk()
    root.withdraw()
    module = build_module(root)
    try:
        module.show()
        _schedule_exit_when_hidden(root, module)
        root.mainloop()
    finally:
        module.shutdown()
        try:
            root.destroy()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
