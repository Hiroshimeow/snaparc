"""
Unified Custom Prompt Dialog Adapter (v1.1)
===========================================
Routes all `CustomPromptDialog` and `show_custom_prompt_dialog` calls directly to
the unified `QuickPromptPopup` (with zero legacy `screen_chat_workspace` coupling).
"""

from __future__ import annotations

from typing import Any, Callable, List, Optional

from src.gui.quick_prompt_popup import (
    PopupAttachment,
    QuickPromptPopup,
    QuickPromptSubmission,
    inspect_and_extract_file,
)

WORKSPACE_UI_AVAILABLE = False


class CustomPromptDialog(QuickPromptPopup):
    """Backwards-compatible class alias for QuickPromptPopup."""

    def __init__(
        self,
        root: Any,
        history_store: Any,
        default_prompt: str = "",
        on_submit: Optional[Callable[..., Any]] = None,
        mode: str = "image",
        initial_text: str = "",
        initial_files: Optional[List[str]] = None,
        initial_images: Optional[List[Any]] = None,
        active_model_label: str = "",
        on_escalate_pi: Optional[Callable[[QuickPromptSubmission], Any]] = None,
    ) -> None:
        super().__init__(
            root=root,
            history_store=history_store,
            default_prompt=default_prompt,
            on_submit=on_submit,
            mode=mode,
            initial_text=initial_text,
            initial_files=initial_files,
            initial_images=initial_images,
            active_model_label=active_model_label,
            on_escalate_pi=on_escalate_pi,
        )


def show_custom_prompt_dialog(
    root: Any,
    history_store: Any,
    default_prompt: str = "",
    on_submit: Optional[Callable[..., Any]] = None,
    force_dialog: bool = True,
    mode: str = "image",
    initial_text: str = "",
    initial_files: Optional[List[str]] = None,
    initial_images: Optional[List[Any]] = None,
    active_model_label: str = "",
    on_escalate_pi: Optional[Callable[[QuickPromptSubmission], Any]] = None,
) -> QuickPromptPopup:
    """Open the unified QuickPromptPopup directly (chat-in-workspace has been removed)."""
    _ = force_dialog
    return QuickPromptPopup(
        root=root,
        history_store=history_store,
        default_prompt=default_prompt,
        on_submit=on_submit,
        mode=mode,
        initial_text=initial_text,
        initial_files=initial_files,
        initial_images=initial_images,
        active_model_label=active_model_label,
        on_escalate_pi=on_escalate_pi,
    ).show()


__all__ = [
    "CustomPromptDialog",
    "PopupAttachment",
    "QuickPromptPopup",
    "QuickPromptSubmission",
    "WORKSPACE_UI_AVAILABLE",
    "inspect_and_extract_file",
    "show_custom_prompt_dialog",
]
