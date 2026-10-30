from __future__ import annotations

import os
import tkinter as tk
from tkinter import messagebox
import webbrowser

import customtkinter
from PIL import Image, ImageOps

from src.gui.screenshot_chat.composer import ScreenshotChatComposer
from src.gui.screenshot_chat.markdown_render import apply_markdown
from src.gui.screenshot_chat.mermaid import extract_mermaid_blocks, write_mermaid_preview
from src.gui.screenshot_chat.pi_harness import extract_message_structured

try:
    import pyperclip
except Exception:  # pragma: no cover
    pyperclip = None


COLORS = {
    "bg": "#090d16",
    "panel": "#111827",
    "panel_soft": "#1e293b",
    "border": "#263346",
    "text": "#f8fafc",
    "muted": "#94a3b8",
    "accent": "#38bdf8",
    "emerald": "#10b981",
    "user": "#1e293b",
    "assistant": "#111827",
}

MESSAGE_BUBBLE_WIDTH_RATIO = 0.78

MOUSEWHEEL_SCROLL_UNITS = 140
LONG_MESSAGE_CHAR_THRESHOLD = 20_000
LONG_MESSAGE_VIEW_LINES = 32
STREAMING_MESSAGE_VIEW_LINES = 18
MARKDOWN_HIGHLIGHT_CHAR_LIMIT = 30_000


class ScreenshotChatWindow(customtkinter.CTkToplevel):
    """Pi Sidecar Primary Studio Window (v2.4) — Modern CustomTkinter UI with In-Window Model & Session Popovers."""

    def __init__(self, master, controller):
        super().__init__(master)
        self.withdraw()
        self.controller = controller
        self._thumb_cache = {}
        self._refresh_job = None
        self._message_refresh_job = None
        self._layout_resize_job = None
        self._last_layout_width = 0
        self._pinned_picker = None
        self._model_popover = None
        self._session_popover = None
        self._welcome_card = None
        self._message_widgets = {}
        self._message_signatures = {}
        self._active_rendered_session_id = ""
        self._last_message_signature = ()
        self._has_unseen_bottom_message = False
        self._text_fit_jobs = {}
        self._scroll_jobs = []
        self._move_start_x = 0
        self._move_start_y = 0
        self._win_x = 0
        self._win_y = 0
        self._resize_start_x = 0
        self._resize_start_y = 0
        self._resize_start_width = 0
        self._resize_start_height = 0
        self.visible = False
        self._setup_window()
        self._setup_ui()
        self.after(100, self._poll_runner_events)

    def _setup_window(self):
        self.geometry("680x820")
        self.minsize(500, 580)
        self.title("SnapArc Chat")
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(fg_color=COLORS["bg"])
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

    def _setup_ui(self):
        self._is_expanded = False
        self._saved_geom = "680x820"

        # 1. Top header bar
        self.header = customtkinter.CTkFrame(
            self,
            fg_color="#0f172a",
            corner_radius=0,
            border_width=1,
            border_color="#1e293b",
        )
        self.header.grid(row=0, column=0, sticky="ew", padx=0, pady=0)
        self.header.grid_columnconfigure(2, weight=1)
        self.header.bind("<ButtonPress-1>", self._start_move)
        self.header.bind("<B1-Motion>", self._do_move)

        self.title_label = customtkinter.CTkLabel(
            self.header,
            text="🤖 Pi Sidecar",
            text_color="#f8fafc",
            font=("Segoe UI", 13, "bold"),
            anchor="w",
        )
        self.title_label.grid(row=0, column=0, padx=(14, 6), pady=8)
        self.title_label.bind("<ButtonPress-1>", self._start_move)
        self.title_label.bind("<B1-Motion>", self._do_move)

        self.chats_button = customtkinter.CTkButton(
            self.header,
            text="💬 New Chat ▾",
            width=140,
            height=28,
            fg_color="#1e293b",
            hover_color="#334155",
            text_color="#e2e8f0",
            border_color="#334155",
            border_width=1,
            corner_radius=8,
            font=("Segoe UI", 11, "bold"),
            anchor="w",
            command=self._toggle_session_popover,
        )
        self.chats_button.grid(row=0, column=1, sticky="w", padx=(2, 6), pady=8)

        # Spacer column=2 (drags window)
        spacer = customtkinter.CTkLabel(self.header, text="", fg_color="transparent")
        spacer.grid(row=0, column=2, sticky="ew")
        spacer.bind("<ButtonPress-1>", self._start_move)
        spacer.bind("<B1-Motion>", self._do_move)

        # Mode switch (Chat / Note)
        self.mode_frame = customtkinter.CTkFrame(self.header, fg_color="#1e293b", corner_radius=8)
        self.mode_frame.grid(row=0, column=3, padx=(0, 6), pady=8)
        self.mode_buttons = {
            "chat": customtkinter.CTkButton(
                self.mode_frame,
                text="Chat",
                width=46,
                height=24,
                corner_radius=6,
                fg_color="transparent",
                hover_color="#334155",
                font=("Segoe UI", 10, "bold"),
                command=lambda: self.controller.set_session_mode("chat"),
            ),
            "note": customtkinter.CTkButton(
                self.mode_frame,
                text="Note",
                width=46,
                height=24,
                corner_radius=6,
                fg_color="transparent",
                hover_color="#334155",
                font=("Segoe UI", 10, "bold"),
                command=lambda: self.controller.set_session_mode("note"),
            ),
        }
        self.mode_buttons["chat"].grid(row=0, column=0, padx=2, pady=2)
        self.mode_buttons["note"].grid(row=0, column=1, padx=2, pady=2)

        # Top-right Model Selector Pill + New + Expand + Close
        self.model_button = customtkinter.CTkButton(
            self.header,
            text="⚡ Model ▾",
            width=120,
            height=28,
            fg_color="#0f2942",
            hover_color="#0369a1",
            border_color="#38bdf8",
            border_width=1,
            text_color="#38bdf8",
            corner_radius=8,
            font=("Segoe UI", 10, "bold"),
            command=self.show_model_menu,
        )
        self.model_button.grid(row=0, column=4, padx=3, pady=8)

        self.new_chat_btn = customtkinter.CTkButton(
            self.header,
            text="＋",
            width=30,
            height=28,
            fg_color="#1e293b",
            hover_color="#334155",
            text_color="#f8fafc",
            corner_radius=8,
            font=("Segoe UI", 13, "bold"),
            command=self._new_chat_shortcut,
        )
        self.new_chat_btn.grid(row=0, column=5, padx=2, pady=8)

        self.expand_btn = customtkinter.CTkButton(
            self.header,
            text="↗",
            width=30,
            height=28,
            fg_color="#1e293b",
            hover_color="#334155",
            text_color="#cbd5e1",
            corner_radius=8,
            font=("Segoe UI", 12),
            command=self._toggle_expand_window,
        )
        self.expand_btn.grid(row=0, column=6, padx=2, pady=8)

        self.close_button = customtkinter.CTkButton(
            self.header,
            text="×",
            width=32,
            height=28,
            fg_color="transparent",
            hover_color="#dc2626",
            text_color="#94a3b8",
            corner_radius=8,
            font=("Segoe UI", 16, "bold"),
            command=self.controller.hide_window,
        )
        self.close_button.grid(row=0, column=7, padx=(2, 10), pady=8)

        # 2. Main Chat Area (Pi Sidecar Subbar + Message Stream)
        self.chat_area = customtkinter.CTkFrame(self, fg_color=COLORS["bg"], corner_radius=0)
        self.chat_area.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 6))
        self.chat_area.grid_rowconfigure(1, weight=1)
        self.chat_area.grid_columnconfigure(0, weight=1)

        # 2a. Pi Sidecar Sub-header Bar (Status + Thinking + Tools + Tokens)
        self.subbar = customtkinter.CTkFrame(
            self.chat_area,
            fg_color="#0f172a",
            height=30,
            corner_radius=8,
            border_width=1,
            border_color="#1e293b",
        )
        self.subbar.grid(row=0, column=0, sticky="ew", padx=2, pady=(6, 6))
        self.subbar.grid_columnconfigure(3, weight=1)

        self.subbar_status = customtkinter.CTkLabel(
            self.subbar,
            text="● Pi Sidecar Ready",
            text_color="#34d399",
            font=("Segoe UI", 10, "bold"),
            anchor="w",
        )
        self.subbar_status.grid(row=0, column=0, sticky="w", padx=(10, 8), pady=4)

        self.thinking_pill = customtkinter.CTkButton(
            self.subbar,
            text="🧠 Thinking: medium ▾",
            width=120,
            height=22,
            fg_color="#1e293b",
            hover_color="#334155",
            text_color="#cbd5e1",
            corner_radius=6,
            font=("Segoe UI", 9, "bold"),
            command=self._cycle_thinking_level,
        )
        self.thinking_pill.grid(row=0, column=1, padx=3, pady=4)

        self.tools_pill = customtkinter.CTkButton(
            self.subbar,
            text="🛠️ Tools: approve ▾",
            width=116,
            height=22,
            fg_color="#1e293b",
            hover_color="#334155",
            text_color="#cbd5e1",
            corner_radius=6,
            font=("Segoe UI", 9, "bold"),
            command=self._cycle_tool_mode,
        )
        self.tools_pill.grid(row=0, column=2, padx=3, pady=4)

        self.subbar_tokens = customtkinter.CTkLabel(
            self.subbar,
            text="0 msgs · ~0 tok",
            text_color="#94a3b8",
            font=("Segoe UI", 10),
            anchor="e",
        )
        self.subbar_tokens.grid(row=0, column=4, sticky="e", padx=(0, 10), pady=4)

        self.message_frame = customtkinter.CTkScrollableFrame(
            self.chat_area,
            fg_color=COLORS["bg"],
            corner_radius=0,
        )
        self.message_frame.grid(row=1, column=0, sticky="nsew")
        self.message_frame.grid_columnconfigure(0, weight=1)
        self._bind_scroll_state()

        self.scroll_bottom_button = customtkinter.CTkButton(
            self.chat_area,
            text="↓ Latest",
            width=64,
            height=26,
            fg_color="#0284c7",
            hover_color="#0369a1",
            text_color="#ffffff",
            corner_radius=12,
            font=("Segoe UI", 10, "bold"),
            command=lambda: self.scroll_to_bottom(),
        )
        self.scroll_bottom_button.grid(row=1, column=0, sticky="se", padx=14, pady=14)
        self.scroll_bottom_button.grid_remove()

        self.status_label = customtkinter.CTkLabel(
            self,
            text="",
            text_color="#38bdf8",
            anchor="w",
            font=("Segoe UI", 10, "bold"),
        )
        self.status_label.grid(row=2, column=0, sticky="ew", padx=18, pady=(0, 2))
        self.status_label.grid_remove()

        self.composer = ScreenshotChatComposer(self, self, self.controller, COLORS)
        self.composer.grid(row=3, column=0, sticky="ew", padx=14, pady=(0, 12))

        self.resize_grip = customtkinter.CTkLabel(
            self,
            text="◢",
            width=18,
            height=18,
            text_color=COLORS["muted"],
            cursor="size_nw_se",
        )
        self.resize_grip.place(relx=1.0, rely=1.0, anchor="se", x=-2, y=-2)
        self.resize_grip.bind("<ButtonPress-1>", self._start_resize)
        self.resize_grip.bind("<B1-Motion>", self._do_resize)

        self.bind("<Escape>", self._hide_shortcut)
        self.bind("<Control-w>", self._hide_shortcut)
        self.bind("<Control-n>", self._new_chat_shortcut)
        self.bind("<Control-l>", self._focus_composer_shortcut)
        self.bind("<Configure>", self._on_window_configure, add="+")
        self._enable_drag_drop()
        self._bind_fast_mousewheel()

    def _cycle_thinking_level(self):
        levels = ["off", "minimal", "low", "medium", "high"]
        cur = getattr(self.controller, "get_pi_thinking_level", lambda: "medium")()
        nxt = levels[(levels.index(cur) + 1) % len(levels)] if cur in levels else "medium"
        if hasattr(self.controller, "set_thinking_level"):
            self.controller.set_thinking_level(nxt)
        else:
            self.controller.execute_slash_command(f"/thinking {nxt}")

    def _cycle_tool_mode(self):
        modes = ["approve_mutations", "read_only", "none"]
        cur = getattr(self.controller, "get_pi_tool_mode", lambda: "approve_mutations")()
        nxt = modes[(modes.index(cur) + 1) % len(modes)] if cur in modes else "approve_mutations"
        if hasattr(self.controller, "set_tool_mode"):
            self.controller.set_tool_mode(nxt)
        else:
            self.controller.execute_slash_command(f"/tools {nxt}")

    def _bind_scroll_state(self):
        try:
            self.message_frame._parent_canvas.configure(yscrollcommand=self._on_message_scroll)
        except Exception:
            pass

    def _bind_fast_mousewheel(self):
        targets = [self, self.message_frame]
        canvas = getattr(self.message_frame, "_parent_canvas", None)
        if canvas is not None:
            targets.append(canvas)
        for widget in targets:
            try:
                widget.bind("<MouseWheel>", self._on_mousewheel_fast)
                widget.bind("<Button-4>", self._on_mousewheel_fast)
                widget.bind("<Button-5>", self._on_mousewheel_fast)
            except Exception:
                pass

    def _bind_text_mousewheel(self, widget):
        try:
            widget.bind("<MouseWheel>", self._on_mousewheel_fast)
            widget.bind("<Button-4>", self._on_mousewheel_fast)
            widget.bind("<Button-5>", self._on_mousewheel_fast)
        except Exception:
            pass

    def _bind_long_text_mousewheel(self, widget):
        try:
            widget.bind("<MouseWheel>", lambda event, target=widget: self._on_long_text_mousewheel(event, target))
            widget.bind("<Button-4>", lambda event, target=widget: self._on_long_text_mousewheel(event, target))
            widget.bind("<Button-5>", lambda event, target=widget: self._on_long_text_mousewheel(event, target))
        except Exception:
            pass

    def _on_long_text_mousewheel(self, event, text_box):
        try:
            direction, steps = self._normalized_wheel_delta(event)
            first, last = text_box.yview()
            can_scroll_inside = (direction < 0 and first > 0.0) or (direction > 0 and last < 1.0)
            if can_scroll_inside:
                text_box.yview_scroll(direction * max(1, steps) * 3, "units")
                return "break"
        except Exception:
            pass
        return self._on_mousewheel_fast(event)

    def _on_mousewheel_fast(self, event):
        canvas = getattr(self.message_frame, "_parent_canvas", None)
        if canvas is None:
            return None
        try:
            self._cancel_pending_scroll_jobs()
            direction, steps = self._normalized_wheel_delta(event)
            if direction == 0:
                return "break"
            units = direction * max(1, steps) * MOUSEWHEEL_SCROLL_UNITS
            canvas.yview_scroll(units, "units")
            self._show_scroll_bottom_button_if_needed()
            return "break"
        except Exception:
            return "break"

    def _normalized_wheel_delta(self, event) -> tuple[int, int]:
        if getattr(event, "num", None) == 4:
            return -1, 1
        if getattr(event, "num", None) == 5:
            return 1, 1
        raw_delta = int(getattr(event, "delta", 0) or 0)
        if raw_delta == 0:
            return 0, 0
        return (-1 if raw_delta > 0 else 1), max(1, abs(raw_delta) // 120)

    def _on_message_scroll(self, first, last):
        try:
            self.message_frame._scrollbar.set(first, last)
        except Exception:
            pass
        try:
            if float(last) >= 0.98:
                self._has_unseen_bottom_message = False
        except Exception:
            return
        self._show_scroll_bottom_button_if_needed()

    def prewarm_hidden(self):
        self.withdraw()
        self.visible = False
        self.refresh_all()

    def show(self):
        self.show_fast()
        self.after(15, self.refresh_all)
        self.after(20, self._focus_entry)

    def show_fast(self):
        self.deiconify()
        self.lift()
        self.visible = True

    def _focus_entry(self):
        if not self.is_visible():
            return
        self.focus_force()
        self.composer.focus()

    def hide(self):
        self._close_popovers()
        self.withdraw()
        self.visible = False

    def is_visible(self):
        return self.visible and self.state() != "withdrawn"

    def refresh_all(self):
        if self._message_refresh_job is not None:
            try:
                self.after_cancel(self._message_refresh_job)
            except Exception:
                pass
            self._message_refresh_job = None
        if self._refresh_job is not None:
            try:
                self.after_cancel(self._refresh_job)
            except Exception:
                pass
        self._refresh_job = self.after_idle(self._do_refresh_all)

    def refresh_messages(self):
        if self._refresh_job is not None or self._message_refresh_job is not None:
            return
        self._message_refresh_job = self.after_idle(self._do_refresh_messages)

    def _do_refresh_messages(self):
        self._message_refresh_job = None
        self._refresh_messages()
        self._show_scroll_bottom_button_if_needed()

    def _do_refresh_all(self):
        self._refresh_job = None
        self._refresh_sessions()
        self._refresh_messages()
        self.composer.refresh()
        self.update_status(self.controller.get_status())
        self._show_scroll_bottom_button_if_needed()

    def _toggle_expand_window(self):
        if not getattr(self, "_is_expanded", False):
            self._saved_geom = self.geometry().split("+")[0]
            sw = self.winfo_screenwidth()
            sh = self.winfo_screenheight()
            w = min(1040, int(sw * 0.72))
            h = min(920, int(sh * 0.86))
            x = (sw - w) // 2
            y = (sh - h) // 2
            self.geometry(f"{w}x{h}+{x}+{y}")
            self.expand_btn.configure(text="↙")
            self._is_expanded = True
        else:
            self.geometry(self._saved_geom)
            self.expand_btn.configure(text="↗")
            self._is_expanded = False

    def _refresh_sessions(self):
        sessions = self.controller.list_display_sessions()
        active = self.controller.get_active_session()
        self._session_label_to_id = {
            self._session_label(item): item.id for item in sessions
        }
        stitle = (active.title or "New Chat").strip()
        self.title_label.configure(text="SnapArc Chat")
        self.chats_button.configure(text=f"💬 {stitle[:16]} ▾")
        self.model_button.configure(text=self._short_model(active.model_name))
        self._refresh_mode_buttons(active.mode or "chat")
        self._update_subbar_stats(active)
        self.composer.refresh()

    def _update_subbar_stats(self, session=None):
        try:
            active = session or self.controller.get_active_session()
            msg_count = len(active.messages)
            total_chars = sum(len(m.text or "") for m in active.messages)
            est_tok = max(0, total_chars // 4)
            is_busy = bool(active.pending_job_ids)
            self.subbar_status.configure(
                text="⚡ Pi Sidecar Running…" if is_busy else "● Pi Sidecar Ready",
                text_color="#f59e0b" if is_busy else "#34d399",
            )
            t_lvl = getattr(self.controller, "get_pi_thinking_level", lambda: "medium")()
            tool_m = getattr(self.controller, "get_pi_tool_mode", lambda: "approve_mutations")()
            short_tool = "approve" if "approve" in tool_m else tool_m
            self.thinking_pill.configure(text=f"🧠 Thinking: {t_lvl} ▾")
            self.tools_pill.configure(text=f"🛠️ Tools: {short_tool} ▾")
            self.subbar_tokens.configure(text=f"{msg_count} msgs · ~{est_tok:,} tok")
        except Exception:
            pass

    def _session_label(self, session):
        pending = " *" if session.pending_job_ids else ""
        title = session.title or "New Chat"
        return f"{title}{pending}"

    def _on_session_selected(self, label):
        session_id = getattr(self, "_session_label_to_id", {}).get(label)
        if session_id:
            self.controller.load_session(session_id)

    def _render_empty_welcome_card(self, session):
        if self._welcome_card is not None and self._welcome_card.winfo_exists():
            self._welcome_card.destroy()
        card = customtkinter.CTkFrame(
            self.message_frame,
            fg_color="#111827",
            border_color="#1e293b",
            border_width=1,
            corner_radius=14,
        )
        card.grid(row=0, column=0, sticky="ew", padx=12, pady=18)
        card.grid_columnconfigure(0, weight=1)

        customtkinter.CTkLabel(
            card,
            text="SnapArc Chat & Tools",
            text_color="#f8fafc",
            font=("Segoe UI", 14, "bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 4))

        active_model = session.model_name or "cliproxy/gpt-5.4"
        customtkinter.CTkLabel(
            card,
            text=(
                f"Active Model: {active_model}  ·  Single OpenAI-Compatible Completion API\n"
                "Pi Sidecar has full workspace tool access (read, write, edit, bash, grep, find, ls). "
                "Drop files/PDFs/images here, click the Model badge to switch models, or try a quick action:"
            ),
            text_color="#94a3b8",
            font=("Segoe UI", 10),
            anchor="w",
            justify="left",
            wraplength=540,
        ).grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 10))

        btn_row = customtkinter.CTkFrame(card, fg_color="transparent")
        btn_row.grid(row=2, column=0, sticky="w", padx=14, pady=(0, 14))

        quick_actions = [
            ("⚡ Switch Model", self._toggle_model_picker_popover),
            ("🗂️ !ls Workspace", lambda: self.controller.send_message("!ls", session.model_name, session.prompt_type)),
            ("🧰 /skills", lambda: self.controller.execute_slash_command("/skills")),
            ("📸 Add Held Snaps", self.controller.import_held_snaps),
            ("📎 Attach File", self.controller.import_file),
        ]
        for idx, (label_txt, cmd_fn) in enumerate(quick_actions):
            customtkinter.CTkButton(
                btn_row,
                text=label_txt,
                height=26,
                width=96,
                fg_color="#1e293b",
                hover_color="#0284c7",
                text_color="#e2e8f0",
                corner_radius=8,
                font=("Segoe UI", 9, "bold"),
                command=cmd_fn,
            ).grid(row=0, column=idx, padx=(0, 6), pady=2)

        self._welcome_card = card

    def _refresh_messages(self):
        session = self.controller.get_active_session()
        session_changed = self._active_rendered_session_id != session.id
        was_near_bottom = False if session_changed else self._is_scrolled_near_bottom()
        if session_changed:
            self._cancel_pending_scroll_jobs()
            self._clear_message_widgets()
            self._active_rendered_session_id = session.id
            self._last_message_signature = ()
            self._has_unseen_bottom_message = False
            self._scroll_canvas_to_top()

        if not session.messages:
            for message_id, widgets in list(self._message_widgets.items()):
                self._cancel_text_fit_jobs(widgets.get("text"))
                widgets["frame"].destroy()
                self._message_widgets.pop(message_id, None)
                self._message_signatures.pop(message_id, None)
            self._render_empty_welcome_card(session)
            return
        elif self._welcome_card is not None:
            try:
                self._welcome_card.destroy()
            except Exception:
                pass
            self._welcome_card = None

        active_ids = {message.id for message in session.messages}
        for message_id, widgets in list(self._message_widgets.items()):
            if message_id not in active_ids:
                self._cancel_text_fit_jobs(widgets.get("text"))
                widgets["frame"].destroy()
                self._message_widgets.pop(message_id, None)
                self._message_signatures.pop(message_id, None)
        for row, message in enumerate(session.messages):
            signature = self._message_signature(message)
            widgets = self._message_widgets.get(message.id)
            if widgets is None:
                widgets = self._render_message(message)
                self._message_widgets[message.id] = widgets
            elif self._message_signatures.get(message.id) != signature:
                self._update_message_widget(message, widgets)
            widgets["frame"].grid(row=row, column=0)
            self._message_signatures[message.id] = signature
        self._update_unseen_bottom_state(session, was_near_bottom=was_near_bottom)

    def _clear_message_widgets(self):
        self._cancel_text_fit_jobs()
        self._welcome_card = None
        for widget in self.message_frame.winfo_children():
            widget.destroy()
        self._message_widgets = {}
        self._message_signatures = {}

    def _message_signature(self, message):
        attachments = tuple((item.path, item.label, item.source, item.kind) for item in message.attachments)
        events = tuple(
            (item.get("id", ""), item.get("kind", ""), item.get("status", ""), item.get("text", ""))
            for item in getattr(message, "events", [])
            if isinstance(item, dict)
        )
        return (message.role, message.author, message.text, message.status, message.job_id, attachments, events)

    def _update_unseen_bottom_state(self, session, was_near_bottom=None):
        current_signature = tuple((message.id, len(message.text or ""), message.status) for message in session.messages)
        if current_signature != self._last_message_signature:
            if session.messages and was_near_bottom:
                self._has_unseen_bottom_message = False
                self.schedule_scroll_to_bottom(session.id, reason="update")
            elif session.messages and not self._is_scrolled_near_bottom():
                self._has_unseen_bottom_message = True
            self._last_message_signature = current_signature

    def _is_scrolled_near_bottom(self):
        try:
            _, bottom = self.message_frame._parent_canvas.yview()
            return bottom >= 0.98
        except Exception:
            return True

    def _bubble_width(self):
        try:
            width = self.message_frame.winfo_width()
            if width <= 80:
                width = self.winfo_width() - 28
        except Exception:
            width = 480
        return max(300, int(width * MESSAGE_BUBBLE_WIDTH_RATIO))

    def _on_window_configure(self, event=None):
        if getattr(event, "widget", None) is not self:
            return None
        width = int(getattr(event, "width", 0) or 0)
        if width <= 0 or width == self._last_layout_width:
            return None
        self._last_layout_width = width
        if self._layout_resize_job is not None:
            try:
                self.after_cancel(self._layout_resize_job)
            except Exception:
                pass
        self._layout_resize_job = self.after(80, self._apply_responsive_message_widths)
        return None

    def _apply_responsive_message_widths(self):
        self._layout_resize_job = None
        bubble_width = self._bubble_width()
        for widgets in self._message_widgets.values():
            frame = widgets.get("frame")
            if frame is not None:
                try:
                    frame.configure(width=bubble_width)
                except Exception:
                    pass
            text_box = widgets.get("text")
            if text_box is not None:
                try:
                    text_box.configure(width=max(24, (bubble_width + 7) // 8))
                except Exception:
                    pass
                if not getattr(text_box, "_stc_fixed_height", False):
                    self._schedule_text_fit(text_box)

    def _render_message(self, message):
        is_user = message.role == "user"
        frame = customtkinter.CTkFrame(
            self.message_frame,
            width=self._bubble_width(),
            fg_color=COLORS["user"] if is_user else COLORS["assistant"],
            corner_radius=14,
            border_color="#0284c7" if is_user else "#1e293b",
            border_width=1,
        )
        sticky = "e" if is_user else "w"
        side_gap = 10
        frame.grid(sticky=sticky, padx=(side_gap, 0) if is_user else (0, side_gap), pady=5)
        frame.grid_columnconfigure(0, weight=1)
        widgets = {"frame": frame}
        self._build_message_contents(frame, message, widgets)
        self._bind_message_context(frame, message)
        return widgets

    def _update_message_widget(self, message, widgets):
        if self._append_stream_delta(message, widgets):
            return
        frame = widgets["frame"]
        self._cancel_text_fit_jobs(widgets.get("text"))
        for child in frame.winfo_children():
            child.destroy()
        widgets.clear()
        widgets["frame"] = frame
        frame.configure(
            width=self._bubble_width(),
            fg_color=COLORS["user"] if message.role == "user" else COLORS["assistant"],
        )
        self._build_message_contents(frame, message, widgets)
        self._bind_message_context(frame, message)

    def _append_stream_delta(self, message, widgets) -> bool:
        text_box = widgets.get("text")
        old_text = widgets.get("text_value", "")
        new_text = message.text or ""
        if text_box is None or widgets.get("status") != "thinking" or message.status != "thinking":
            return False
        if widgets.get("structure_signature") != self._message_structure_signature(message):
            return False
        if not new_text.startswith(old_text):
            return False
        new_is_long = len(new_text) >= LONG_MESSAGE_CHAR_THRESHOLD
        new_is_bounded = new_is_long or message.status == "thinking"
        if bool(widgets.get("is_bounded")) != new_is_bounded:
            return False
        delta = new_text[len(old_text):]
        if delta:
            try:
                was_at_bottom = bool(widgets.get("is_bounded")) and text_box.yview()[1] >= 0.98
                text_box.configure(state=tk.NORMAL)
                text_box.insert("end-1c", delta)
                text_box.configure(state="disabled")
                if was_at_bottom:
                    text_box.yview_moveto(1.0)
            except Exception:
                return False
        widgets["text_value"] = new_text
        widgets["status"] = message.status
        copy_button = widgets.get("copy_button")
        if copy_button is not None:
            copy_button.configure(command=lambda text=new_text: self._copy_text(text))
        self._bind_message_context(text_box, message)
        self._bind_message_context(widgets["frame"], message)
        if not widgets.get("is_bounded"):
            self._schedule_text_fit(text_box)
        return True

    @staticmethod
    def _message_structure_signature(message):
        attachments = tuple((item.path, item.label, item.source, item.kind) for item in message.attachments)
        events = tuple(
            (item.get("id", ""), item.get("kind", ""), item.get("status", ""), item.get("text", ""))
            for item in getattr(message, "events", [])
            if isinstance(item, dict)
        )
        return (message.role, message.author, message.job_id, attachments, events)

    def _build_message_contents(self, frame, message, widgets):
        is_user = message.role == "user"
        content_row = 0
        structured = extract_message_structured(message) if not is_user else {"text": message.text or "", "thinking": "", "tools": [], "subagents": []}

        if not is_user:
            author_label = f"🤖 {message.author}" if message.author in ("main", "Pi Agent", "pi") else message.author
            customtkinter.CTkLabel(
                frame,
                text=author_label,
                text_color="#38bdf8",
                anchor="w",
                font=("Segoe UI Semibold", 11),
            ).grid(row=0, column=0, sticky="ew", padx=12, pady=(6, 2))
            if (message.text or "").strip():
                copy_button = customtkinter.CTkButton(
                    frame,
                    text="Copy",
                    width=50,
                    height=22,
                    fg_color="#1e293b",
                    hover_color="#334155",
                    text_color="#e2e8f0",
                    corner_radius=6,
                    command=lambda text=message.text: self._copy_text(text),
                )
                copy_button.grid(row=0, column=1, sticky="ne", padx=(0, 8), pady=(6, 0))
                widgets["copy_button"] = copy_button
            content_row = 1

            # Render Thinking trace card if present
            if structured.get("thinking"):
                think_box = customtkinter.CTkFrame(frame, fg_color="#090d16", corner_radius=8, border_width=1, border_color="#1e293b")
                think_box.grid(row=content_row, column=0, columnspan=2, sticky="ew", padx=12, pady=(2, 4))
                customtkinter.CTkLabel(
                    think_box,
                    text=f"🧠 Reasoning: {structured['thinking'][:420]}",
                    text_color="#94a3b8",
                    font=("Consolas", 9),
                    anchor="w",
                    justify="left",
                    wraplength=max(220, self._bubble_width() - 36),
                ).pack(fill="x", padx=8, pady=6)
                content_row += 1

        raw_text = message.text or ""
        text = raw_text
        text_color = COLORS["muted"] if message.status == "canceled" else COLORS["text"]
        is_long = len(text) >= LONG_MESSAGE_CHAR_THRESHOLD
        is_bounded = is_long or message.status == "thinking"
        if text.strip():
            text_parent = frame
            text_grid_options = {
                "row": content_row,
                "column": 0,
                "sticky": "ew",
                "padx": 12,
                "pady": (4, 8) if is_user else (2, 8),
            }
            if is_bounded:
                text_parent = tk.Frame(
                    frame,
                    bg=COLORS["user"] if is_user else COLORS["assistant"],
                    borderwidth=0,
                    highlightthickness=0,
                )
                text_parent.grid(**text_grid_options)
                text_parent.grid_columnconfigure(0, weight=1)
                text_grid_options = {"row": 0, "column": 0, "sticky": "nsew"}
                widgets["text_container"] = text_parent
            text_box = tk.Text(
                text_parent,
                height=(LONG_MESSAGE_VIEW_LINES if is_long else STREAMING_MESSAGE_VIEW_LINES) if is_bounded else 1,
                width=max(24, self._bubble_width() // 8),
                wrap="word",
                borderwidth=0,
                highlightthickness=0,
                relief="flat",
                bg=COLORS["user"] if is_user else COLORS["assistant"],
                fg=text_color,
                insertbackground=text_color,
                selectbackground=COLORS["panel_soft"],
                font=("Segoe UI", 10),
            )
            apply_markdown(
                text_box,
                text,
                COLORS,
                syntax_highlight=message.status != "thinking" and len(text) <= MARKDOWN_HIGHLIGHT_CHAR_LIMIT,
            )
            text_box.grid(**text_grid_options)
            text_box._stc_fixed_height = is_bounded
            widgets["text"] = text_box
            if is_bounded:
                scrollbar = customtkinter.CTkScrollbar(
                    text_parent,
                    orientation="vertical",
                    command=text_box.yview,
                    width=8,
                    fg_color="transparent",
                    button_color="#334155",
                    button_hover_color="#475569",
                )
                scrollbar.grid(row=0, column=1, sticky="ns")
                text_box.configure(yscrollcommand=scrollbar.set)
                widgets["text_scrollbar"] = scrollbar
                self._bind_long_text_mousewheel(text_box)
            else:
                self._bind_text_mousewheel(text_box)
                text_box.bind("<Configure>", self._on_text_widget_configure, add="+")
                self._schedule_text_fit(text_box)
            self._bind_message_context(text_box, message)
            content_row += 1
        if getattr(message, "events", None):
            self._render_message_events(frame, content_row, message.events)
            content_row += 1
        if message.status == "queued":
            customtkinter.CTkButton(
                frame,
                text="×",
                width=24,
                height=24,
                fg_color="transparent",
                hover_color=COLORS["panel_soft"],
                command=lambda mid=message.id: self.controller.cancel_queued_message(mid),
            ).grid(row=max(0, content_row - 1), column=1, sticky="ne", padx=(0, 8), pady=(6, 0))
        next_row = content_row
        mermaid_blocks = [] if is_bounded else extract_mermaid_blocks(text)
        for block_index, diagram in enumerate(mermaid_blocks):
            self._render_mermaid_block(frame, next_row + block_index, diagram)
        next_row += len(mermaid_blocks)
        for index, attachment in enumerate(message.attachments[:4]):
            self._render_attachment_preview(frame, next_row + index, attachment)
        widgets["text_value"] = text
        widgets["status"] = message.status
        widgets["is_long"] = is_long
        widgets["is_bounded"] = is_bounded
        widgets["structure_signature"] = self._message_structure_signature(message)

    def _render_message_events(self, frame, row, events):
        lines = []
        for item in events[-6:]:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or item.get("kind") or "Pi event")
            status = str(item.get("status") or "")
            text = str(item.get("text") or "")
            line = f"🛠️ {title}"
            if status:
                line += f" [{status}]"
            if text:
                line += f": {text}"
            lines.append(line[:240])
        if not lines:
            return
        evt_card = customtkinter.CTkFrame(frame, fg_color="#090d16", corner_radius=8)
        evt_card.grid(row=row, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 8))
        customtkinter.CTkLabel(
            evt_card,
            text="\n".join(lines),
            text_color="#38bdf8",
            anchor="w",
            justify="left",
            font=("Consolas", 9),
            wraplength=max(220, self._bubble_width() - 36),
        ).pack(fill="x", padx=8, pady=5)

    def _render_attachment_preview(self, frame, row, attachment):
        thumb = self._thumbnail(attachment.path)
        label = attachment.label or os.path.basename(attachment.path)
        tile = customtkinter.CTkFrame(frame, fg_color="transparent")
        tile.grid(row=row, column=0, sticky="w", padx=12, pady=(0, 6))
        image_label = customtkinter.CTkLabel(
            tile,
            image=thumb,
            text=label,
            compound="top",
            text_color=COLORS["muted"],
            anchor="w",
            justify="left",
        )
        image_label.grid(row=0, column=0, sticky="w")
        if thumb is not None:
            image_label.bind("<Button-1>", lambda event, path=attachment.path: self._open_image_preview(path))
            customtkinter.CTkButton(
                tile,
                text="Open",
                width=48,
                height=22,
                fg_color=COLORS["panel"],
                hover_color=COLORS["panel_soft"],
                command=lambda path=attachment.path: self._open_image_preview(path),
            ).grid(row=0, column=1, sticky="s", padx=(8, 0), pady=(0, 2))

    def _schedule_text_fit(self, text_box):
        if text_box is None or getattr(text_box, "_stc_fixed_height", False):
            return
        key = str(text_box)
        self._cancel_text_fit_jobs(text_box)

        def fit_guard(widget=text_box, job_key=key):
            self._text_fit_jobs.pop(job_key, None)
            self._fit_text_widget_to_content(widget)

        try:
            self._text_fit_jobs[key] = [self.after_idle(fit_guard)]
        except Exception:
            pass

    def _cancel_text_fit_jobs(self, text_box=None):
        if text_box is None:
            keys = list(self._text_fit_jobs.keys())
        else:
            keys = [str(text_box)]
        for key in keys:
            for job_id in self._text_fit_jobs.pop(key, []):
                try:
                    self.after_cancel(job_id)
                except Exception:
                    pass

    def _on_text_widget_configure(self, event=None):
        widget = getattr(event, "widget", None)
        if widget is None or getattr(widget, "_stc_fixed_height", False):
            return None
        width = getattr(event, "width", 0) or 0
        previous = getattr(widget, "_stc_last_fit_width", None)
        if previous != width:
            widget._stc_last_fit_width = width
            self._schedule_text_fit(widget)
        return None

    def _fit_text_widget_to_content(self, text_box):
        try:
            if not text_box.winfo_exists():
                return
            if getattr(text_box, "_stc_fixed_height", False):
                return
            text_box.update_idletasks()
            display_lines = text_box.count("1.0", "end-1c", "displaylines")
            if isinstance(display_lines, tuple):
                display_lines = display_lines[0]
            if not display_lines:
                display_lines = int(float(text_box.index("end-1c").split(".", 1)[0]))
            display_lines = max(1, int(display_lines))
            # Message bodies should not create a second scroll area; the outer chat owns scrolling.
            text_box.configure(height=display_lines + 1)
            text_box.yview_moveto(0.0)
        except Exception:
            try:
                text_box.configure(height=max(1, int(float(text_box.index("end-1c").split(".", 1)[0])) + 1))
            except Exception:
                pass

    def _render_mermaid_block(self, frame, row, diagram):
        block = customtkinter.CTkFrame(frame, fg_color=COLORS["panel_soft"], corner_radius=10)
        block.grid(row=row, column=0, sticky="ew", padx=12, pady=(0, 6))
        block.grid_columnconfigure(0, weight=1)
        customtkinter.CTkLabel(
            block,
            text="Mermaid diagram",
            text_color=COLORS["text"],
            anchor="w",
            font=("Segoe UI Semibold", 11),
        ).grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 2))
        customtkinter.CTkButton(
            block,
            text="Copy",
            width=48,
            height=24,
            fg_color=COLORS["panel"],
            hover_color=COLORS["assistant"],
            command=lambda text=diagram: self._copy_text(text),
        ).grid(row=0, column=1, padx=(4, 4), pady=(8, 2))
        customtkinter.CTkButton(
            block,
            text="Open preview",
            width=92,
            height=24,
            fg_color=COLORS["panel"],
            hover_color=COLORS["assistant"],
            command=lambda text=diagram: self._open_mermaid_preview(text),
        ).grid(row=0, column=2, padx=(0, 8), pady=(8, 2))

    def _refresh_mode_buttons(self, mode):
        for name, button in self.mode_buttons.items():
            active = name == mode
            button.configure(
                fg_color=COLORS["accent"] if active else "transparent",
                text_color=COLORS["bg"] if active else COLORS["text"],
            )

    def _bind_message_context(self, widget, message):
        try:
            widget.bind(
                "<Button-3>",
                lambda event, mid=message.id, text=message.text: self._show_message_context_menu(event, mid, text),
            )
        except Exception:
            pass

    def _show_message_context_menu(self, event, message_id, text):
        menu = tk.Menu(self, tearoff=0)
        if (text or "").strip():
            menu.add_command(label="Copy all response", command=lambda: self._copy_text(text))
        menu.add_command(label="Delete", command=lambda: self.controller.delete_message(message_id))
        menu.tk_popup(event.x_root, event.y_root)
        return "break"

    def _copy_text(self, text):
        copied = False
        if pyperclip is not None:
            try:
                pyperclip.copy(text)
                copied = True
            except Exception:
                copied = False
        if not copied:
            self.clipboard_clear()
            self.clipboard_append(text)
        self.update_status("Copied response text.")

    def _open_mermaid_preview(self, diagram):
        path = write_mermaid_preview(self.controller.project_root, diagram)
        webbrowser.open(path)

    def _open_image_preview(self, path):
        if not path or not os.path.exists(path):
            self.update_status("Image file is no longer available.")
            return
        dialog = customtkinter.CTkToplevel(self)
        dialog.title(os.path.basename(path) or "Image preview")
        dialog.geometry("900x700")
        dialog.attributes("-topmost", True)
        dialog.transient(self)
        dialog.grid_rowconfigure(0, weight=1)
        dialog.grid_columnconfigure(0, weight=1)
        try:
            with Image.open(path) as raw:
                img = ImageOps.exif_transpose(raw).convert("RGB")
                img.thumbnail((860, 620))
            preview = customtkinter.CTkImage(light_image=img, size=img.size)
            dialog._preview_image = preview
            customtkinter.CTkLabel(dialog, image=preview, text="").grid(row=0, column=0, sticky="nsew", padx=16, pady=16)
        except Exception as exc:
            customtkinter.CTkLabel(
                dialog,
                text=f"Could not open image: {exc}",
                text_color=COLORS["text"],
                wraplength=760,
            ).grid(row=0, column=0, sticky="nsew", padx=16, pady=16)
        actions = customtkinter.CTkFrame(dialog, fg_color="transparent")
        actions.grid(row=1, column=0, sticky="e", padx=16, pady=(0, 14))
        customtkinter.CTkButton(actions, text="Open file", width=84, command=lambda: webbrowser.open(path)).grid(row=0, column=0, padx=(0, 8))
        customtkinter.CTkButton(actions, text="Close", width=72, command=dialog.destroy).grid(row=0, column=1)
        dialog.bind("<Escape>", lambda event: dialog.destroy())
        dialog.focus_force()

    def update_status(self, status):
        clean_status = (status or "").strip()
        if "Pi unavailable" in clean_status or "Pi Agent unavailable" in clean_status:
            return
        if not clean_status:
            self.status_label.grid_remove()
            return
        self.status_label.configure(text=clean_status)
        self.status_label.grid()

    def show_attach_menu(self):
        menu = tk.Menu(self, tearoff=0)
        self._populate_attach_menu(menu)
        menu.tk_popup(self.composer.attach_button.winfo_rootx(), self.composer.attach_button.winfo_rooty() + 30)

    def _populate_attach_menu(self, menu):
        held_count = len(self.controller.held_images_getter())
        if held_count:
            menu.add_command(
                label=f"Add held snaps ({held_count})",
                command=self.controller.import_held_snaps,
            )
        else:
            menu.add_command(label="Add held snaps (0)", state="disabled")
        pins = self.controller.list_pinned_snaps()
        if pins:
            menu.add_command(label=f"Add pinned snap ({len(pins)})", command=self._show_pinned_snap_picker)
        else:
            menu.add_command(label="Add pinned snap", state="disabled")
        self._add_system_prompt_menu(menu)
        menu.add_command(label="Add file", command=self.controller.import_file)

    def _show_pinned_snap_picker(self):
        pins = self.controller.list_pinned_snaps()
        self._close_pinned_snap_picker()
        if not pins:
            return
        picker = tk.Menu(self, tearoff=0)
        for entry in pins:
            title = entry.get("display_title") or os.path.basename(entry.get("image_path", "Pinned"))
            prefix = "[x]" if self.controller.is_pinned_snap_used(entry) else "[ ]"
            picker.add_command(
                label=f"{prefix} {title}",
                command=lambda payload=entry: self._insert_pinned_snap_and_close(payload),
            )
        self._pinned_picker = picker
        picker.tk_popup(self.composer.attach_button.winfo_rootx(), self.composer.attach_button.winfo_rooty() + 30)

    def _insert_pinned_snap_and_close(self, entry):
        self.controller.import_pinned_snap(entry)
        self._close_pinned_snap_picker()

    def _close_pinned_snap_picker(self):
        if self._pinned_picker is None:
            return
        try:
            self._pinned_picker.unpost()
        except Exception:
            pass
        self._pinned_picker = None

    def _add_system_prompt_menu(self, menu):
        prompt_menu = tk.Menu(menu, tearoff=0)
        self._populate_system_prompt_menu(prompt_menu)
        menu.add_cascade(
            label=f"System prompt: {self.controller.system_prompt_summary()[:28]}",
            menu=prompt_menu,
        )

    def _populate_system_prompt_menu(self, menu):
        blank_selected = self.controller.is_system_prompt_selected("", "", "Blank")
        menu.add_command(
            label=("[x] " if blank_selected else "[ ] ") + "Blank",
            command=lambda: self.controller.set_session_system_prompt("", "", "Blank"),
        )
        prompts = self.controller.prompt_definitions()
        for key, value in prompts.items():
            selected = self.controller.is_system_prompt_selected(key, value, key)
            menu.add_command(
                label=("[x] " if selected else "[ ] ") + key.replace("prompt_", "").replace("_", " ").title()[:48],
                command=lambda prompt_key=key, prompt_text=value: self.controller.set_session_system_prompt(
                    prompt_key,
                    prompt_text,
                    prompt_key,
                ),
            )
        menu.add_separator()
        menu.add_command(label="Custom...", command=self._set_custom_system_prompt)

    def _set_custom_system_prompt(self):
        dialog = customtkinter.CTkToplevel(self)
        dialog.title("System prompt")
        dialog.geometry("520x360")
        dialog.attributes("-topmost", True)
        dialog.transient(self)
        dialog.grid_columnconfigure(0, weight=1)
        dialog.grid_rowconfigure(1, weight=1)
        customtkinter.CTkLabel(
            dialog,
            text="Custom system prompt",
            text_color=COLORS["text"],
            anchor="w",
            font=("Segoe UI Semibold", 13),
        ).grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 6))
        editor = customtkinter.CTkTextbox(
            dialog,
            fg_color=COLORS["panel"],
            text_color=COLORS["text"],
            wrap="word",
        )
        editor.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 10))
        session = self.controller.get_active_session()
        if session.system_prompt:
            editor.insert("1.0", session.system_prompt)
        actions = customtkinter.CTkFrame(dialog, fg_color="transparent")
        actions.grid(row=2, column=0, sticky="e", padx=14, pady=(0, 12))

        def save_prompt():
            self.controller.set_session_system_prompt("custom", editor.get("1.0", "end-1c"), "Custom")
            dialog.destroy()

        customtkinter.CTkButton(actions, text="Cancel", width=72, command=dialog.destroy).grid(row=0, column=0, padx=(0, 8))
        customtkinter.CTkButton(actions, text="Save", width=72, command=save_prompt).grid(row=0, column=1)
        dialog.focus_force()

    def _enable_drag_drop(self):
        for widget in (self, self.message_frame, self.composer):
            self.composer.bind_drop_target(widget)

    def _show_scroll_bottom_button_if_needed(self):
        if self._has_unseen_bottom_message:
            self.scroll_bottom_button.grid()
        else:
            self.scroll_bottom_button.grid_remove()

    def schedule_scroll_to_bottom(self, session_id=None, reason=""):
        self._schedule_scroll_to_bottom(session_id, reason)

    def scroll_to_bottom(self, session_id=None):
        self._schedule_scroll_to_bottom(session_id, "manual")

    def _schedule_scroll_to_bottom(self, session_id=None, reason=""):
        session = self.controller.get_active_session()
        target_session_id = session_id or session.id
        self._cancel_pending_scroll_jobs()
        delay = 180 if reason != "manual" else 20
        self._scroll_jobs.append(
            self.after(delay, lambda sid=target_session_id: self._scroll_canvas_to_bottom(sid))
        )
        self._has_unseen_bottom_message = False
        self.scroll_bottom_button.grid_remove()

    def _cancel_pending_scroll_jobs(self):
        for job_id in self._scroll_jobs:
            try:
                self.after_cancel(job_id)
            except Exception:
                pass
        self._scroll_jobs = []

    def _scroll_canvas_to_bottom(self, session_id=None):
        try:
            if session_id and self.controller.get_active_session().id != session_id:
                return
            self.message_frame.update_idletasks()
            self.message_frame._parent_canvas.update_idletasks()
            self.message_frame._parent_canvas.yview_moveto(1.0)
        except Exception:
            pass

    def _scroll_canvas_to_top(self):
        try:
            self.message_frame.update_idletasks()
            self.message_frame._parent_canvas.update_idletasks()
            self.message_frame._parent_canvas.yview_moveto(0.0)
        except Exception:
            pass

    def _poll_runner_events(self):
        has_backlog = self.controller.drain_runner_events()
        if self.winfo_exists():
            self.after(10 if has_backlog else 50, self._poll_runner_events)

    def thumbnail(self, path, size=(140, 90)):
        return self._thumbnail(path, size)

    def _thumbnail(self, path, size=(140, 90)):
        try:
            if not path or not os.path.exists(path):
                return None
            mtime = os.path.getmtime(path)
            with Image.open(path) as raw:
                img = ImageOps.exif_transpose(raw).convert("RGB")
                bounds = self._thumbnail_bounds(img.size, size)
                cache_key = (path, mtime, bounds)
                if cache_key in self._thumb_cache:
                    return self._thumb_cache[cache_key]
                img.thumbnail(bounds)
            thumb = customtkinter.CTkImage(light_image=img, size=img.size)
            self._thumb_cache[cache_key] = thumb
            return thumb
        except Exception:
            return None

    @staticmethod
    def _thumbnail_bounds(image_size, requested_size):
        width, height = image_size
        req_width, req_height = requested_size
        if height > width * 1.25:
            return (min(req_width, 96), max(req_height, 150))
        return requested_size

    def _close_popovers(self):
        if self._model_popover is not None:
            try:
                self._model_popover.destroy()
            except Exception:
                pass
            self._model_popover = None
        if self._session_popover is not None:
            try:
                self._session_popover.destroy()
            except Exception:
                pass
            self._session_popover = None

    def show_model_menu(self):
        return self._toggle_model_picker_popover()

    def show_session_menu(self):
        return self._toggle_session_popover()

    def _toggle_model_picker_popover(self):
        """Open a sleek in-window CustomTkinter Model Selector Popover so model selection never fails."""
        if self._model_popover is not None and self._model_popover.winfo_exists():
            self._close_popovers()
            return
        self._close_popovers()

        popover = customtkinter.CTkFrame(
            self,
            fg_color="#0f172a",
            border_color="#38bdf8",
            border_width=1,
            corner_radius=12,
        )
        popover.place(relx=0.97, y=46, anchor="ne", relwidth=0.66)
        popover.lift()
        popover.grid_columnconfigure(0, weight=1)
        self._model_popover = popover

        # Header Row
        hdr = customtkinter.CTkFrame(popover, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 6))
        hdr.grid_columnconfigure(0, weight=1)

        customtkinter.CTkLabel(
            hdr,
            text="⚡ Select Model (Pi Sidecar)",
            text_color="#f8fafc",
            font=("Segoe UI", 12, "bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="w")

        sync_btn = customtkinter.CTkButton(
            hdr,
            text="🔄 Reload config models",
            width=110,
            height=24,
            fg_color="#1e293b",
            hover_color="#0284c7",
            text_color="#38bdf8",
            corner_radius=6,
            font=("Segoe UI", 9, "bold"),
            command=self._sync_models_and_reopen,
        )
        sync_btn.grid(row=0, column=1, padx=(4, 4))

        close_btn = customtkinter.CTkButton(
            hdr,
            text="×",
            width=24,
            height=24,
            fg_color="transparent",
            hover_color="#334155",
            text_color="#94a3b8",
            command=self._close_popovers,
        )
        close_btn.grid(row=0, column=2)

        # Scrollable Model List
        list_frame = customtkinter.CTkScrollableFrame(
            popover,
            fg_color="#090d16",
            height=210,
            corner_radius=8,
        )
        list_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 8))
        list_frame.grid_columnconfigure(0, weight=1)

        active_model = self.controller.get_active_session().model_name
        options = self._model_options()

        row_idx = 0
        for opt in options:
            mid = str(opt.get("id") or "")
            if not mid or mid.startswith("pi://"):
                continue
            is_selected = mid == active_model
            is_fav = bool(opt.get("favorite", False))
            item_row = customtkinter.CTkFrame(
                list_frame,
                fg_color="#1e3a5f" if is_selected else "#111827",
                corner_radius=8,
            )
            item_row.grid(row=row_idx, column=0, sticky="ew", padx=4, pady=2)
            item_row.grid_columnconfigure(0, weight=1)

            prefix = "● " if is_selected else "○ "
            btn = customtkinter.CTkButton(
                item_row,
                text=f"{prefix}{mid}",
                fg_color="transparent",
                hover_color="#1e293b",
                text_color="#38bdf8" if is_selected else "#f8fafc",
                anchor="w",
                height=28,
                font=("Consolas", 10, "bold" if is_selected else "normal"),
                command=lambda m=mid: self._select_model_from_popover(m),
            )
            btn.grid(row=0, column=0, sticky="ew", padx=(4, 2), pady=2)

            fav_btn = customtkinter.CTkButton(
                item_row,
                text="★" if is_fav else "☆",
                width=28,
                height=26,
                fg_color="transparent",
                hover_color="#334155",
                text_color="#fbbf24" if is_fav else "#64748b",
                command=lambda m=mid: self._toggle_fav_in_popover(m),
            )
            fav_btn.grid(row=0, column=1, padx=(0, 4), pady=2)
            row_idx += 1

        # Custom Model Entry Row
        custom_row = customtkinter.CTkFrame(popover, fg_color="transparent")
        custom_row.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 10))
        custom_row.grid_columnconfigure(0, weight=1)

        custom_entry = customtkinter.CTkEntry(
            custom_row,
            placeholder_text="Or type custom model ID (e.g. cliproxy/gpt-5.4)...",
            height=28,
            fg_color="#090d16",
            border_color="#334155",
            text_color="#f8fafc",
            font=("Consolas", 10),
        )
        custom_entry.grid(row=0, column=0, sticky="ew", padx=(0, 6))

        def _use_custom(_event=None):
            val = custom_entry.get().strip()
            if val:
                if hasattr(self.controller, "add_custom_model"):
                    self.controller.add_custom_model(val)
                else:
                    self.controller.set_session_model(val)
                self._close_popovers()

        custom_entry.bind("<Return>", _use_custom)
        customtkinter.CTkButton(
            custom_row,
            text="Use",
            width=54,
            height=28,
            fg_color="#10b981",
            hover_color="#059669",
            text_color="#052e16",
            font=("Segoe UI", 10, "bold"),
            command=_use_custom,
        ).grid(row=0, column=1)

    def _select_model_from_popover(self, model_id: str):
        self._close_popovers()
        self.controller.set_session_model(model_id)

    def _toggle_fav_in_popover(self, model_id: str):
        if hasattr(self.controller, "toggle_favorite_model"):
            self.controller.toggle_favorite_model(model_id)
        self._close_popovers()
        self._toggle_model_picker_popover()

    def _sync_models_and_reopen(self):
        if hasattr(self.controller, "sync_remote_models"):
            self.controller.sync_remote_models()
        self._close_popovers()
        self._toggle_model_picker_popover()

    def _toggle_session_popover(self):
        if self._session_popover is not None and self._session_popover.winfo_exists():
            self._close_popovers()
            return
        self._close_popovers()

        popover = customtkinter.CTkFrame(
            self,
            fg_color="#0f172a",
            border_color="#334155",
            border_width=1,
            corner_radius=12,
        )
        popover.place(x=14, y=46, relwidth=0.58)
        popover.lift()
        popover.grid_columnconfigure(0, weight=1)
        self._session_popover = popover

        top_bar = customtkinter.CTkFrame(popover, fg_color="transparent")
        top_bar.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 6))
        top_bar.grid_columnconfigure(0, weight=1)

        customtkinter.CTkButton(
            top_bar,
            text="＋ New Session",
            height=28,
            fg_color="#10b981",
            hover_color="#059669",
            text_color="#052e16",
            font=("Segoe UI", 10, "bold"),
            command=lambda: (self._close_popovers(), self.controller.create_session()),
        ).grid(row=0, column=0, sticky="ew", padx=(0, 6))

        customtkinter.CTkButton(
            top_bar,
            text="⑂ Fork",
            width=60,
            height=28,
            fg_color="#1e293b",
            hover_color="#334155",
            command=lambda: (self._close_popovers(), self.controller.fork_session() if hasattr(self.controller, "fork_session") else None),
        ).grid(row=0, column=1, padx=(0, 4))

        customtkinter.CTkButton(
            top_bar,
            text="🗜 Compact",
            width=76,
            height=28,
            fg_color="#1e293b",
            hover_color="#334155",
            command=lambda: (self._close_popovers(), self.controller.compact_session() if hasattr(self.controller, "compact_session") else None),
        ).grid(row=0, column=2)

        list_box = customtkinter.CTkScrollableFrame(popover, fg_color="#090d16", height=180, corner_radius=8)
        list_box.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 10))
        list_box.grid_columnconfigure(0, weight=1)

        active_id = self.controller.get_active_session().id
        for idx, sess in enumerate(self.controller.list_display_sessions()):
            is_act = sess.id == active_id
            row = customtkinter.CTkFrame(list_box, fg_color="#1e3a5f" if is_act else "#111827", corner_radius=6)
            row.grid(row=idx, column=0, sticky="ew", padx=4, pady=2)
            row.grid_columnconfigure(0, weight=1)
            lbl = self._session_label(sess)[:38]
            customtkinter.CTkButton(
                row,
                text=lbl,
                anchor="w",
                height=26,
                fg_color="transparent",
                hover_color="#1e293b",
                text_color="#38bdf8" if is_act else "#f8fafc",
                command=lambda sid=sess.id: (self._close_popovers(), self.controller.load_session(sid)),
            ).grid(row=0, column=0, sticky="ew", padx=4, pady=2)
            customtkinter.CTkButton(
                row,
                text="🗑",
                width=26,
                height=24,
                fg_color="transparent",
                hover_color="#dc2626",
                text_color="#94a3b8",
                command=lambda sid=sess.id, t=lbl: (self._close_popovers(), self._confirm_delete_session(sid, t)),
            ).grid(row=0, column=1, padx=(0, 4), pady=2)

    def _show_model_menu(self):
        return self._toggle_model_picker_popover()

    def _model_options(self):
        provider = getattr(self.controller, "available_model_options", None)
        if callable(provider):
            return [dict(option) for option in provider()]
        return [
            {
                "id": model_name,
                "label": model_name.split("/")[-1] if model_name else "Model",
                "runtime": "llm",
                "available": True,
                "reason": "",
            }
            for model_name in self.controller.available_models()
        ]

    def _populate_model_menu(self, menu):
        """Flat, direct model menu population (also used by unit tests)."""
        options = self._model_options()
        active_model = self.controller.get_active_session().model_name
        pi_options = [option for option in options if str(option.get("id", "")).startswith("pi://")]
        normal_options = [option for option in options if not str(option.get("id", "")).startswith("pi://")]
        for option in pi_options:
            self._add_model_menu_command(menu, option, active_model)
        if pi_options and normal_options:
            menu.add_separator()
        for option in normal_options:
            self._add_model_menu_command(menu, option, active_model)
        if not options:
            menu.add_command(label="No models", state="disabled")

    def _add_model_menu_command(self, menu, option, active_model):
        model_id = option.get("id", "")
        label = option.get("label") or self._short_model(model_id)
        selected = model_id == active_model
        state = "normal" if option.get("available", True) else "disabled"
        reason = option.get("reason") or ""
        display = ("[x] " if selected else "[ ] ") + label
        if state == "disabled" and reason:
            display = f"{display} ({reason})"
        menu.add_command(
            label=display,
            state=state,
            command=lambda selected=model_id: self.controller.set_session_model(selected),
        )

    def _show_session_menu(self):
        return self._toggle_session_popover()

    def _confirm_delete_session(self, session_id, title):
        if not messagebox.askyesno("Delete chat", f"Delete chat '{title}'?", parent=self):
            return
        if self.controller.delete_session(session_id):
            self.update_status("Chat deleted.")
        else:
            self.update_status("Chat could not be deleted.")

    def _short_model(self, model_name):
        display = getattr(self.controller, "model_display_name", None)
        if callable(display):
            return display(model_name)
        if not model_name:
            return "Model"
        tail = model_name.split("/")[-1]
        return tail if len(tail) <= 18 else tail[:15] + "..."

    def _hide_shortcut(self, event=None):
        self.controller.hide_window()
        return "break"

    def _new_chat_shortcut(self, event=None):
        self._close_popovers()
        self.controller.create_session()
        self.after_idle(self._focus_entry)
        return "break"

    def _focus_composer_shortcut(self, event=None):
        self._focus_entry()
        return "break"

    def _start_move(self, event):
        self._move_start_x = event.x_root
        self._move_start_y = event.y_root
        self._win_x = self.winfo_x()
        self._win_y = self.winfo_y()

    def _do_move(self, event):
        dx = event.x_root - self._move_start_x
        dy = event.y_root - self._move_start_y
        self.geometry(f"+{self._win_x + dx}+{self._win_y + dy}")

    def _start_resize(self, event):
        self._resize_start_x = event.x_root
        self._resize_start_y = event.y_root
        self._resize_start_width = self.winfo_width()
        self._resize_start_height = self.winfo_height()

    def _do_resize(self, event):
        width = max(500, self._resize_start_width + event.x_root - self._resize_start_x)
        height = max(580, self._resize_start_height + event.y_root - self._resize_start_y)
        self.geometry(f"{width}x{height}")
