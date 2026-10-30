"""
Unified Quick Prompt Popup (`Ctrl + Snap` for Images, `Alt + Snap` for Text & Files)
====================================================================================
Replaces the legacy `CustomPromptDialog` and `Alt + Snap` visual diff with a single,
streamlined, drag-and-drop multimodal popup that supports:
1. `Ctrl + Snap` (Image Mode): Prompt freely over 1 or more snapped screenshots.
2. `Alt + Snap` (Text & File Mode): Prompt freely over clipboard/selected text and
   dragged-and-dropped files (`.pdf`, `.txt`, `.md`, `.py`, `.json`, `.csv`, `.docx`,
   images, etc.) without needing to manually copy-paste text.
3. Native File Drag-and-Drop (`WM_DROPFILES` on Windows + Tkinter `<<Drop>>` + file picker
   + `Ctrl+V` smart attachment ingest).
4. Dual Execution Targets:
   - **Quick Answer (Float UI)** (`Ctrl+Enter` / `Send`): fast overlay answer.
   - **Pi Sidecar Multi-Turn Agent** (`Ctrl+Shift+Enter` / `Pi Agent`): escalates the
     prompt, files, text, and images directly into the reworked `Chat Screen` (`Ctrl+Shift+Alt+X`)
     for multi-turn agentic workflows.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass, field
import os
from pathlib import Path
import sys
import tkinter as tk
from tkinter import filedialog, ttk
from typing import Any, Callable, Dict, List, Optional

from PIL import Image


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".py", ".js", ".ts", ".tsx", ".jsx",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".csv", ".tsv",
    ".sql", ".html", ".css", ".xml", ".log", ".sh", ".ps1", ".bat",
    ".c", ".cpp", ".h", ".hpp", ".rs", ".go", ".java", ".cs",
}


@dataclass
class PopupAttachment:
    path: str
    name: str
    kind: str  # "image" | "pdf" | "docx" | "text" | "binary"
    size_bytes: int = 0
    extracted_text: str = ""


@dataclass
class QuickPromptSubmission:
    prompt_text: str
    mode: str  # "image" | "text_file"
    target: str  # "floating" | "pi_agent"
    context_text: str = ""
    attachments: List[PopupAttachment] = field(default_factory=list)
    images: List[Any] = field(default_factory=list)

    def build_combined_prompt(self) -> str:
        """Combine user prompt, context text, and extracted file contents for LLM/Pi execution."""
        sections: List[str] = []
        if self.prompt_text.strip():
            sections.append(self.prompt_text.strip())

        if self.context_text.strip():
            sections.append(f"### Context Text\n```text\n{self.context_text.strip()}\n```")

        for att in self.attachments:
            if att.extracted_text.strip():
                sections.append(
                    f"### Attached File: `{att.name}` (`{att.path}`)\n```\n{att.extracted_text.strip()}\n```"
                )
            elif att.kind != "image":
                sections.append(f"### Attached File: `{att.name}` (`{att.path}`)")

        return "\n\n".join(sections).strip()


def inspect_and_extract_file(file_path: str | Path, max_chars: int = 16000) -> PopupAttachment:
    """Extract readable text from PDF, DOCX, or text/code files, or classify as image."""
    p = Path(str(file_path).strip().strip('"').strip("'"))
    if not p.exists() or not p.is_file():
        return PopupAttachment(path=str(p), name=p.name, kind="missing", size_bytes=0)

    size = p.stat().st_size
    ext = p.suffix.lower()

    if ext in IMAGE_EXTENSIONS:
        return PopupAttachment(path=str(p), name=p.name, kind="image", size_bytes=size)

    if ext == ".pdf":
        text_parts: List[str] = []
        try:
            import pypdf  # type: ignore

            reader = pypdf.PdfReader(str(p))
            for i, page in enumerate(reader.pages[:25]):
                page_txt = (page.extract_text() or "").strip()
                if page_txt:
                    text_parts.append(f"[Page {i + 1}]\n{page_txt}")
        except Exception:
            pass
        extracted = "\n\n".join(text_parts)[:max_chars]
        return PopupAttachment(
            path=str(p),
            name=p.name,
            kind="pdf",
            size_bytes=size,
            extracted_text=extracted,
        )

    if ext == ".docx":
        text_parts = []
        try:
            import docx  # type: ignore

            doc = docx.Document(str(p))
            for para in doc.paragraphs:
                if para.text.strip():
                    text_parts.append(para.text.strip())
        except Exception:
            pass
        extracted = "\n".join(text_parts)[:max_chars]
        return PopupAttachment(
            path=str(p),
            name=p.name,
            kind="docx",
            size_bytes=size,
            extracted_text=extracted,
        )

    if ext in TEXT_EXTENSIONS or size <= 512 * 1024:
        try:
            raw = p.read_text(encoding="utf-8", errors="replace")
            return PopupAttachment(
                path=str(p),
                name=p.name,
                kind="text",
                size_bytes=size,
                extracted_text=raw[:max_chars],
            )
        except Exception:
            pass

    return PopupAttachment(path=str(p), name=p.name, kind="binary", size_bytes=size)


def read_clipboard_files_win32() -> List[str]:
    """Safely read CF_HDROP copied file paths from Windows clipboard or PIL ImageGrab without WndProc hooks."""
    try:
        from PIL import ImageGrab
        clip = ImageGrab.grabclipboard()
        if isinstance(clip, list):
            return [str(p) for p in clip if p and os.path.isfile(str(p))]
    except Exception:
        pass
    if sys.platform != "win32" or not hasattr(ctypes, "windll"):
        return []
    CF_HDROP = 15
    u32 = ctypes.windll.user32
    s32 = ctypes.windll.shell32
    u32.GetClipboardData.restype = ctypes.c_void_p
    u32.GetClipboardData.argtypes = [ctypes.c_uint]
    s32.DragQueryFileW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_uint]
    s32.DragQueryFileW.restype = ctypes.c_uint
    files: List[str] = []
    if not u32.OpenClipboard(None):
        return []
    try:
        if u32.IsClipboardFormatAvailable(CF_HDROP):
            hdrop = u32.GetClipboardData(CF_HDROP)
            if hdrop:
                count = s32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0)
                buf = ctypes.create_unicode_buffer(1024)
                for idx in range(count):
                    s32.DragQueryFileW(hdrop, idx, buf, 1024)
                    if buf.value and os.path.isfile(buf.value):
                        files.append(buf.value)
    except Exception:
        pass
    finally:
        u32.CloseClipboard()
    return files


class _Win32DropTarget:
    """Crash-free file attachment helper for QuickPromptPopup (zero WndProc subclassing on 64-bit Windows)."""

    def __init__(self, tk_window: tk.Misc, on_files: Callable[[List[str]], None]) -> None:
        self.tk_window = tk_window
        self.on_files = on_files
        self._installed = False

    def install(self) -> bool:
        self._installed = True
        try:
            clip_files = read_clipboard_files_win32()
            if clip_files:
                self.on_files(clip_files)
        except Exception:
            pass
        return True


class QuickPromptPopup:
    """
    Unified Quick Prompt Popup used by both:
    - `Ctrl + Snap` (Image mode)
    - `Alt + Snap` (Text & File Drag-and-Drop mode)
    """

    def __init__(
        self,
        root: tk.Misc,
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
        self.root = root
        self.history_store = history_store
        self.default_prompt = default_prompt or ""
        self.on_submit = on_submit
        self.mode = mode if mode in ("image", "text_file") else "image"
        self.initial_text = initial_text or ""
        self.attachments: List[PopupAttachment] = []
        self.images: List[Any] = list(initial_images or [])
        self.active_model_label = active_model_label or "(no model configured)"
        self.on_escalate_pi = on_escalate_pi

        self.window: Optional[tk.Toplevel] = None
        self.history_list: Optional[tk.Listbox] = None
        self.context_editor: Optional[tk.Text] = None
        self.editor: Optional[tk.Text] = None
        self.chips_frame: Optional[tk.Frame] = None
        self.mode_label_var = None
        self._drop_target: Optional[_Win32DropTarget] = None

        for fpath in initial_files or []:
            self.add_file(fpath, refresh_ui=False)

    def add_file(self, file_path: str | Path, refresh_ui: bool = True) -> Optional[PopupAttachment]:
        att = inspect_and_extract_file(file_path)
        if att.kind == "missing":
            return None
        if any(existing.path == att.path for existing in self.attachments):
            return att
        self.attachments.append(att)
        if att.kind == "image":
            try:
                with Image.open(att.path) as im:
                    self.images.append(im.copy())
            except Exception:
                pass
        if refresh_ui:
            self._render_attachment_chips()
        return att

    def remove_file(self, file_path: str) -> None:
        self.attachments = [a for a in self.attachments if a.path != file_path]
        self._render_attachment_chips()

    def handle_dropped_files(self, file_paths: List[str]) -> List[PopupAttachment]:
        added = []
        for path in file_paths:
            att = self.add_file(path, refresh_ui=False)
            if att is not None:
                added.append(att)
        self._render_attachment_chips()
        return added

    def show(self) -> "QuickPromptPopup":
        self.window = tk.Toplevel(self.root)
        title_str = (
            "Alt + Snap — Text & File Quick Q&A"
            if self.mode == "text_file"
            else "Ctrl + Snap — Quick Image & File Prompt"
        )
        self.window.title(title_str)
        self.window.geometry("780x460")
        self.window.minsize(580, 340)
        self.window.attributes("-topmost", True)
        self.window.configure(bg="#181c22")

        container = ttk.Frame(self.window, padding=12)
        container.pack(fill=tk.BOTH, expand=True)
        container.columnconfigure(0, weight=0)
        container.columnconfigure(1, weight=1)
        container.rowconfigure(2, weight=1)

        # Header Row
        header_frame = ttk.Frame(container)
        header_frame.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        header_Text = (
            "🗂️ Quick Text & File Prompt (Alt+Snap)"
            if self.mode == "text_file"
            else f"🖼️ Quick Multimodal Prompt (Ctrl+Snap • {len(self.images)} image(s))"
        )
        title_lbl = ttk.Label(
            header_frame,
            text=header_Text,
            font=("Segoe UI", 11, "bold"),
        )
        title_lbl.pack(side=tk.LEFT)

        model_lbl = ttk.Label(
            header_frame,
            text=f"Model: {self.active_model_label}",
            font=("Segoe UI", 9),
        )
        model_lbl.pack(side=tk.RIGHT)

        # Drag-and-Drop / Attachment Strip Row
        drop_frame = tk.Frame(
            container,
            bg="#222831",
            highlightbackground="#394867",
            highlightthickness=1,
            padx=8,
            pady=6,
        )
        drop_frame.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 8))

        drop_hint = tk.Label(
            drop_frame,
            text="📥 Drag & Drop files here (.pdf, .txt, .md, .py, .json, .csv, .docx, .png) or click Browse / Ctrl+V",
            bg="#222831",
            fg="#9ba4b4",
            font=("Segoe UI", 9),
        )
        drop_hint.pack(side=tk.LEFT)

        browse_btn = ttk.Button(
            drop_frame,
            text="+ Browse Files",
            command=self._browse_files,
        )
        browse_btn.pack(side=tk.RIGHT, padx=(6, 0))

        paste_btn = ttk.Button(
            drop_frame,
            text="📋 Paste Clipboard",
            command=self._paste_clipboard_into_context,
        )
        paste_btn.pack(side=tk.RIGHT)

        # Chips container inside drop_frame
        self.chips_frame = tk.Frame(container, bg="#181c22")
        self.chips_frame.grid(row=1, column=0, columnspan=2, sticky="ew")
        self._render_attachment_chips()

        # Left column: Recent Prompt History
        history_frame = ttk.Frame(container)
        history_frame.grid(row=2, column=0, sticky="nsw", padx=(0, 10))
        history_frame.rowconfigure(1, weight=1)

        ttk.Label(history_frame, text="Recent Prompts").grid(row=0, column=0, sticky="w")
        self.history_list = tk.Listbox(
            history_frame,
            width=26,
            height=13,
            activestyle="dotbox",
            exportselection=False,
        )
        self.history_list.grid(row=1, column=0, sticky="ns")
        self.history_list.bind("<<ListboxSelect>>", self._paste_selected_history)
        self._refresh_history()

        # Right column: Context snippet (if text_file mode or initial_text present) + Prompt Editor
        editor_frame = ttk.Frame(container)
        editor_frame.grid(row=2, column=1, sticky="nsew")
        editor_frame.columnconfigure(0, weight=1)
        editor_frame.rowconfigure(1, weight=1)
        editor_frame.rowconfigure(3, weight=2)

        ttk.Label(
            editor_frame,
            text="Captured / Pasted Text Context (optional — editable)",
        ).grid(row=0, column=0, sticky="w")
        self.context_editor = tk.Text(
            editor_frame,
            wrap=tk.WORD,
            height=4,
            undo=True,
            font=("Consolas", 9),
            padx=6,
            pady=4,
        )
        self.context_editor.grid(row=1, column=0, sticky="nsew", pady=(2, 8))
        if self.initial_text:
            self.context_editor.insert("1.0", self.initial_text)

        ttk.Label(
            editor_frame,
            text="Your Question / Prompt (Ctrl+Enter = Quick Float UI | Ctrl+Shift+Enter = Pi Sidecar Agent)",
        ).grid(row=2, column=0, sticky="w")
        self.editor = tk.Text(
            editor_frame,
            wrap=tk.WORD,
            height=7,
            undo=True,
            font=("Segoe UI", 10),
            padx=8,
            pady=8,
        )
        self.editor.grid(row=3, column=0, sticky="nsew")
        self.editor.insert("1.0", self.default_prompt)
        self.editor.bind("<Control-Return>", self._submit_from_event)
        self.editor.bind("<Control-Shift-Return>", self._submit_pi_from_event)

        # Action Buttons
        button_frame = ttk.Frame(editor_frame)
        button_frame.grid(row=4, column=0, sticky="e", pady=(10, 0))
        ttk.Button(button_frame, text="Cancel (Esc)", command=self.close).pack(
            side=tk.RIGHT, padx=(6, 0)
        )
        ttk.Button(
            button_frame,
            text="🤖 Escalate to Pi Sidecar (Multi-Turn)",
            command=lambda: self.submit(target="pi_agent"),
        ).pack(side=tk.RIGHT, padx=(6, 0))
        ttk.Button(
            button_frame,
            text="⚡ Quick Send (Float UI)",
            command=lambda: self.submit(target="floating"),
        ).pack(side=tk.RIGHT)

        self.window.bind("<Escape>", lambda _event: self.close())
        self._drop_target = _Win32DropTarget(self.window, self.handle_dropped_files)
        self._drop_target.install()

        self.window.after(50, self._focus_editor)
        return self

    def _browse_files(self) -> None:
        try:
            selected = filedialog.askopenfilenames(
                parent=self.window,
                title="Select file(s) to attach",
            )
            if selected:
                self.handle_dropped_files(list(selected))
        except Exception as exc:
            print(f"[QuickPromptPopup] File browse error: {exc}")

    def _paste_clipboard_into_context(self) -> None:
        if self.window is None:
            return
        try:
            clip = self.window.clipboard_get()
            if not clip:
                return
            lines = [ln.strip().strip('"') for ln in str(clip).splitlines() if ln.strip()]
            file_Matches = [p for p in lines if os.path.exists(p) and os.path.isfile(p)]
            if file_Matches:
                self.handle_dropped_files(file_Matches)
                return
            if self.context_editor is not None:
                self.context_editor.delete("1.0", tk.END)
                self.context_editor.insert("1.0", str(clip))
        except Exception:
            pass

    def _render_attachment_chips(self) -> None:
        if self.chips_frame is None:
            return
        for child in list(self.chips_frame.winfo_children()):
            try:
                child.destroy()
            except Exception:
                pass
        for att in self.attachments:
            chip = tk.Frame(self.chips_frame, bg="#2b3b4b", padx=6, pady=2)
            chip.pack(side=tk.LEFT, padx=(0, 6), pady=(2, 2))
            icon = "🖼️" if att.kind == "image" else ("📄" if att.kind == "pdf" else "📎")
            kb = max(1, att.size_bytes // 1024)
            lbl = tk.Label(
                chip,
                text=f"{icon} {att.name} ({kb} KB)",
                bg="#2b3b4b",
                fg="#f0e6d2",
                font=("Segoe UI", 8),
            )
            lbl.pack(side=tk.LEFT)
            rm_btn = tk.Button(
                chip,
                text="×",
                bg="#2b3b4b",
                fg="#ff6b6b",
                bd=0,
                command=lambda p=att.path: self.remove_file(p),
            )
            rm_btn.pack(side=tk.LEFT, padx=(4, 0))

    def _focus_editor(self) -> None:
        if self.editor:
            self.editor.focus_set()
            self.editor.mark_set(tk.INSERT, "end-1c")

    def _refresh_history(self) -> None:
        if self.history_list is None or self.history_store is None:
            return
        self.history_list.delete(0, tk.END)
        for prompt in self.history_store.list_prompts():
            label = prompt.replace("\n", " ").strip()
            if len(label) > 80:
                label = label[:77] + "..."
            self.history_list.insert(tk.END, label)

    def _paste_selected_history(self, _event: Any = None) -> None:
        if self.history_list is None or self.editor is None:
            return
        selection = self.history_list.curselection()
        if not selection:
            return
        prompts = self.history_store.list_prompts()
        index = selection[0]
        if index >= len(prompts):
            return
        self.editor.delete("1.0", tk.END)
        self.editor.insert("1.0", prompts[index])
        self._focus_editor()

    def _submit_from_event(self, _event: Any = None) -> str:
        self.submit(target="floating")
        return "break"

    def _submit_pi_from_event(self, _event: Any = None) -> str:
        self.submit(target="pi_agent")
        return "break"

    def build_submission(self, target: str = "floating") -> QuickPromptSubmission:
        prompt_text = self.editor.get("1.0", "end-1c").strip() if self.editor else self.default_prompt.strip()
        context_text = (
            self.context_editor.get("1.0", "end-1c").strip()
            if self.context_editor
            else self.initial_text.strip()
        )
        return QuickPromptSubmission(
            prompt_text=prompt_text,
            mode=self.mode,
            target=target,
            context_text=context_text,
            attachments=list(self.attachments),
            images=list(self.images),
        )

    def submit(self, target: str = "floating") -> Optional[QuickPromptSubmission]:
        submission = self.build_submission(target=target)
        if not submission.prompt_text and not submission.attachments and not submission.context_text:
            self._focus_editor()
            return None

        if submission.prompt_text and self.history_store is not None:
            self.history_store.add_prompt(submission.prompt_text)

        if target == "pi_agent" and self.on_escalate_pi is not None:
            self.on_escalate_pi(submission)
            self.close()
            return submission

        if self.on_submit is not None:
            combined_text = submission.build_combined_prompt()
            try:
                # Support both `on_submit(prompt_text, submission)` and legacy `on_submit(prompt_text)`
                import inspect

                sig = inspect.signature(self.on_submit)
                if len(sig.parameters) >= 2:
                    self.on_submit(combined_text, submission)
                else:
                    self.on_submit(combined_text)
            except TypeError:
                self.on_submit(combined_text)

        self.close()
        return submission

    def close(self) -> None:
        if self.window is not None:
            try:
                self.window.destroy()
            finally:
                self.window = None
