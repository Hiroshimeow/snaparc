"""
Modern Pi Sidecar Composer (`src/gui/screenshot_chat/composer.py`) — v2.4.
=========================================================================
Clean dark-slate composer with:
1. Floating Slash Command Autocomplete (`/`):
   - `/model`, `/new`, `/pi`, `/thinking`, `/tools`, `/compact`, `/fork`, `/skills`, `/export`
2. Attachment & Context Chips Row:
   - `📄 file.md ×`, `🖼 preview.png ×`, `+ Attach file`
3. Cyan-Accented Input Container (`#090d16`, border `#263346` -> `#38bdf8` on focus)
4. Bottom Toolbar:
   - Left: `[⚡ model ▾]` (opens in-window Model Selector Popover), `📎 Attach`, `👁 Context: ON`
   - Right: Emerald `Send ↵` button (`#10b981`) and `Stop ■` button (`#ef4444`).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, List, Optional

import customtkinter

ATTACHMENT_PREVIEW_SIZE = (64, 48)

SLASH_COMMANDS = [
    {"cmd": "/model", "badge": "model", "desc": "Open Model Selector or switch model (/model <id>)"},
    {"cmd": "/new", "badge": "session", "desc": "Start a new Pi Sidecar session"},
    {"cmd": "/pi", "badge": "pi-agent", "desc": "Check Pi Sidecar status or run prompt with Pi"},
    {"cmd": "/thinking", "badge": "config", "desc": "Set Pi Sidecar thinking level (off|minimal|low|medium|high)"},
    {"cmd": "/tools", "badge": "security", "desc": "Set Pi tool policy (read_only|approve_mutations|none)"},
    {"cmd": "/compact", "badge": "session", "desc": "Compact session context to save tokens"},
    {"cmd": "/fork", "badge": "session", "desc": "Fork current conversation into a new branch"},
    {"cmd": "/skills", "badge": "skill · workspace", "desc": "List discovered workspace & multimodal skills"},
    {"cmd": "/export", "badge": "export", "desc": "Export conversation to standalone HTML file"},
]


class SlashCommandPopup(customtkinter.CTkFrame):
    """Floating slash command autocomplete popup."""

    def __init__(self, master, on_select: Callable[[str], None], colors: dict):
        super().__init__(
            master,
            fg_color="#0f172a",
            border_color="#38bdf8",
            border_width=1,
            corner_radius=10,
        )
        self.on_select = on_select
        self.colors = colors
        self.items = SLASH_COMMANDS
        self.filtered = list(SLASH_COMMANDS)
        self.selected_index = 0
        self._item_widgets: List[customtkinter.CTkFrame] = []
        self.grid_columnconfigure(0, weight=1)
        self._build_ui()

    def _build_ui(self):
        for widget in self.winfo_children():
            widget.destroy()
        self._item_widgets.clear()

        for idx, item in enumerate(self.filtered[:8]):
            is_sel = idx == self.selected_index
            row_frame = customtkinter.CTkFrame(
                self,
                fg_color="#1e3a5f" if is_sel else "transparent",
                corner_radius=6,
                cursor="hand2",
            )
            row_frame.grid(row=idx, column=0, sticky="ew", padx=6, pady=2)
            row_frame.grid_columnconfigure(2, weight=1)

            cmd_lbl = customtkinter.CTkLabel(
                row_frame,
                text=item["cmd"],
                text_color="#38bdf8",
                font=("Segoe UI", 11, "bold"),
                anchor="w",
            )
            cmd_lbl.grid(row=0, column=0, padx=(10, 6), pady=4)

            is_skill = "skill" in item["badge"] or "pi" in item["badge"]
            badge_pill = customtkinter.CTkLabel(
                row_frame,
                text=f" {item['badge']} ",
                fg_color="#064e3b" if is_skill else "#1e293b",
                text_color="#34d399" if is_skill else "#94a3b8",
                corner_radius=6,
                font=("Segoe UI", 9),
            )
            badge_pill.grid(row=0, column=1, padx=(0, 8), pady=4)

            desc_lbl = customtkinter.CTkLabel(
                row_frame,
                text=item["desc"],
                text_color="#f8fafc" if is_sel else "#94a3b8",
                font=("Segoe UI", 10),
                anchor="e",
            )
            desc_lbl.grid(row=0, column=2, sticky="ew", padx=(4, 10), pady=4)

            for w in (row_frame, cmd_lbl, badge_pill, desc_lbl):
                w.bind("<Button-1>", lambda _e, cmd=item["cmd"]: self._click_select(cmd))
                w.bind("<Enter>", lambda _e, i=idx: self._hover_index(i))

            self._item_widgets.append(row_frame)

    def _click_select(self, cmd: str):
        self.on_select(cmd)

    def _hover_index(self, index: int):
        self.selected_index = index
        self._highlight()

    def _highlight(self):
        for idx, frame in enumerate(self._item_widgets):
            is_sel = idx == self.selected_index
            frame.configure(fg_color="#1e3a5f" if is_sel else "transparent")

    def filter_query(self, query: str):
        q = query.strip().lower()
        if not q or q == "/":
            self.filtered = list(SLASH_COMMANDS)
        else:
            self.filtered = [
                item
                for item in SLASH_COMMANDS
                if q in item["cmd"].lower() or q in item["desc"].lower() or q in item["badge"].lower()
            ]
        self.selected_index = 0
        if self.filtered:
            self._build_ui()
            return True
        return False

    def select_next(self):
        if self.filtered:
            self.selected_index = (self.selected_index + 1) % len(self.filtered[:8])
            self._highlight()

    def select_prev(self):
        if self.filtered:
            self.selected_index = (self.selected_index - 1 + len(self.filtered[:8])) % len(self.filtered[:8])
            self._highlight()

    def get_current_command(self) -> Optional[str]:
        if self.filtered and 0 <= self.selected_index < len(self.filtered):
            return self.filtered[self.selected_index]["cmd"]
        return None


class ScreenshotChatComposer(customtkinter.CTkFrame):
    """Modern composer for SnapArc Chat."""

    PLACEHOLDER_TEXT = "Ask Pi Sidecar, paste images (Ctrl+V), drop files, run !ls, or type / for commands..."

    def __init__(self, master, window, controller, colors: dict):
        super().__init__(master, fg_color="transparent", corner_radius=0)
        self.window = window
        self.controller = controller
        self.colors = colors
        self.grid_columnconfigure(0, weight=1)

        self._slash_popup: Optional[SlashCommandPopup] = None
        self._context_enabled = True
        self._setup_ui()

    def _setup_ui(self):
        # 1. Attachment & Context Chips Tray
        self.attachment_tray = customtkinter.CTkScrollableFrame(
            self,
            fg_color="transparent",
            height=self._attachment_chip_height(),
            orientation="horizontal",
        )
        self.attachment_tray.grid(row=0, column=0, sticky="ew", padx=2, pady=(0, 4))
        self.attachment_tray.grid_remove()

        # 2. Main Composer Box Container (Clean Dark Slate with Cyan Focus Border)
        self.box_container = customtkinter.CTkFrame(
            self,
            fg_color="#111827",
            border_color="#263346",
            border_width=1,
            corner_radius=12,
        )
        self.box_container.grid(row=1, column=0, sticky="ew")
        self.box_container.grid_columnconfigure(0, weight=1)

        # 2a. Textbox inside Container
        self.entry = customtkinter.CTkTextbox(
            self.box_container,
            height=56,
            fg_color="transparent",
            text_color="#f8fafc",
            border_width=0,
            wrap="word",
            font=("Segoe UI", 11),
        )
        self.entry.grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 6))

        # Overlay Placeholder Label
        self.placeholder_label = customtkinter.CTkLabel(
            self.box_container,
            text=self.PLACEHOLDER_TEXT,
            text_color="#64748b",
            fg_color="transparent",
            font=("Segoe UI", 10),
            anchor="w",
        )
        self.placeholder_label.place(x=14, y=10)
        self.placeholder_label.bind("<Button-1>", lambda _e: self.entry.focus())

        self.entry.bind("<Return>", self._on_return)
        self.entry.bind("<Shift-Return>", self._insert_newline)
        self.entry.bind("<KeyRelease>", self._on_entry_changed)
        self.entry.bind("<Control-v>", self._on_paste)
        self.entry.bind("<Control-V>", self._on_paste)
        self.entry.bind("<Up>", self._on_key_up)
        self.entry.bind("<Down>", self._on_key_down)
        self.entry.bind("<Tab>", self._on_key_tab)
        self.entry.bind("<FocusIn>", self._on_focus_in)
        self.entry.bind("<FocusOut>", self._on_focus_out)

        # 3. Bottom Toolbar below Input Box
        self.toolbar = customtkinter.CTkFrame(self, fg_color="transparent")
        self.toolbar.grid(row=2, column=0, sticky="ew", padx=2, pady=(6, 0))
        self.toolbar.grid_columnconfigure(3, weight=1)

        # Model Selector Pill Button
        self.model_pill = customtkinter.CTkButton(
            self.toolbar,
            text="⚡ Model ▾",
            width=116,
            height=28,
            fg_color="#0f2942",
            hover_color="#0369a1",
            border_color="#38bdf8",
            border_width=1,
            text_color="#38bdf8",
            font=("Segoe UI", 10, "bold"),
            corner_radius=8,
            command=self._open_model_menu,
        )
        self.model_pill.grid(row=0, column=0, padx=(0, 8))

        # Attach Button (📎 Attach)
        self.attach_button = customtkinter.CTkButton(
            self.toolbar,
            text="📎 Attach",
            width=72,
            height=28,
            fg_color="#1e293b",
            hover_color="#334155",
            text_color="#e2e8f0",
            font=("Segoe UI", 10),
            corner_radius=8,
            command=self.window.show_attach_menu,
        )
        self.attach_button.grid(row=0, column=1, padx=(0, 6))

        # Context: ON/OFF Button
        self.context_button = customtkinter.CTkButton(
            self.toolbar,
            text="👁 Context: ON",
            width=96,
            height=28,
            fg_color="transparent",
            hover_color="#1e293b",
            text_color="#34d399",
            font=("Segoe UI", 10, "bold"),
            corner_radius=8,
            command=self._toggle_context,
        )
        self.context_button.grid(row=0, column=2, padx=(0, 6))

        # Send Button (Emerald Green `#10b981`)
        self.send_button = customtkinter.CTkButton(
            self.toolbar,
            text="Send ↵",
            width=78,
            height=28,
            fg_color="#10b981",
            hover_color="#059669",
            text_color="#052e16",
            font=("Segoe UI", 10, "bold"),
            corner_radius=8,
            command=self._send_current_message,
        )
        self.send_button.grid(row=0, column=4, padx=(0, 0))

        # Stop Button (When streaming)
        self.stop_button = customtkinter.CTkButton(
            self.toolbar,
            text="Stop ■",
            width=68,
            height=28,
            fg_color="#ef4444",
            hover_color="#dc2626",
            text_color="#ffffff",
            font=("Segoe UI", 10, "bold"),
            corner_radius=8,
            command=self._stop_current_jobs,
        )
        self.stop_button.grid(row=0, column=5, padx=(6, 0))
        self.stop_button.grid_remove()

    def _open_model_menu(self):
        if hasattr(self.window, "show_model_menu"):
            self.window.show_model_menu()
        elif hasattr(self.window, "_show_model_menu"):
            self.window._show_model_menu()

    def _on_focus_in(self, _event=None):
        self.box_container.configure(border_color="#38bdf8")
        self._update_placeholder_visibility()

    def _on_focus_out(self, _event=None):
        self.box_container.configure(border_color="#263346")
        self._update_placeholder_visibility()
        self.after(200, self._hide_slash_popup)

    def _update_placeholder_visibility(self):
        try:
            current = self.entry.get("1.0", "end-1c")
            if current:
                self.placeholder_label.place_forget()
            else:
                self.placeholder_label.place(x=14, y=10)
        except Exception:
            pass

    def _toggle_context(self):
        self._context_enabled = not self._context_enabled
        if self._context_enabled:
            self.context_button.configure(text="👁 Context: ON", text_color="#34d399")
        else:
            self.context_button.configure(text="👁 Context: OFF", text_color="#64748b")

    def refresh(self):
        session = self.controller.get_active_session()
        if session.pending_job_ids:
            self.stop_button.grid()
        else:
            self.stop_button.grid_remove()

        active_model = session.model_name or "Model"
        short_name = active_model.split("/")[-1] if "/" in active_model else active_model
        if active_model.startswith("pi://"):
            short_name = "Pi Agent"
        self.model_pill.configure(text=f"⚡ {short_name[:16]} ▾")
        self._update_placeholder_visibility()
        self._refresh_attachments()

    def focus(self):
        self.entry.focus()

    def bind_drop_target(self, target):
        try:
            target.drop_target_register("DND_Files")
            target.dnd_bind("<<Drop>>", self._on_drop_files)
        except Exception:
            pass

    def _refresh_attachments(self):
        for widget in self.attachment_tray.winfo_children():
            widget.destroy()
        attachments = list(self.controller.pending_attachments)
        if not attachments:
            self.attachment_tray.grid_remove()
            return

        self.attachment_tray.grid()
        for index, attachment in enumerate(attachments):
            self._render_attachment_chip(index, attachment)

        add_more = customtkinter.CTkButton(
            self.attachment_tray,
            text="+ Attach file",
            width=78,
            height=22,
            fg_color="#141c28",
            hover_color="#1f2b3d",
            border_color="#263346",
            border_width=1,
            text_color="#cbd5e1",
            font=("Segoe UI", 9),
            corner_radius=8,
            command=self.controller.import_file,
        )
        add_more.grid(row=0, column=len(attachments), padx=(0, 6), pady=2)

    def _render_attachment_chip(self, col_index: int, attachment):
        chip = customtkinter.CTkFrame(
            self.attachment_tray,
            fg_color="#1e293b",
            border_color="#38bdf8",
            border_width=1,
            corner_radius=8,
        )
        chip.grid(row=0, column=col_index, sticky="nsw", padx=(0, 6), pady=2)

        thumb = self.window.thumbnail(attachment.path, (18, 18))
        label = attachment.label or os.path.basename(attachment.path)
        ext = Path(label).suffix.lower()
        icon = "🖼" if ext in (".png", ".jpg", ".jpeg", ".webp") else "📄"

        customtkinter.CTkLabel(
            chip,
            image=thumb if thumb else None,
            text="" if thumb else icon,
            width=18,
            height=18,
            fg_color="transparent",
        ).pack(side="left", padx=(6, 4), pady=2)

        display_label = label[:22] + "..." if len(label) > 22 else label
        customtkinter.CTkLabel(
            chip,
            text=display_label,
            text_color="#f8fafc",
            font=("Segoe UI", 9, "bold"),
        ).pack(side="left", padx=(0, 4), pady=2)

        customtkinter.CTkButton(
            chip,
            text="×",
            width=16,
            height=16,
            fg_color="transparent",
            hover_color="#dc2626",
            text_color="#94a3b8",
            command=lambda aid=attachment.id: self.controller.clear_pending_attachment(aid),
        ).pack(side="right", padx=(0, 4), pady=2)

    def _attachment_chip_height(self) -> int:
        return 30

    def _on_return(self, _event=None):
        if self._slash_popup and self._slash_popup.winfo_exists():
            cmd = self._slash_popup.get_current_command()
            if cmd:
                self._apply_slash_command(cmd)
                return "break"
        self._send_current_message()
        return "break"

    def _insert_newline(self, _event=None):
        self.entry.insert("insert", "\n")
        self._update_placeholder_visibility()
        self._resize_entry_to_content()
        return "break"

    def _on_key_up(self, _event=None):
        if self._slash_popup and self._slash_popup.winfo_exists():
            self._slash_popup.select_prev()
            return "break"
        return None

    def _on_key_down(self, _event=None):
        if self._slash_popup and self._slash_popup.winfo_exists():
            self._slash_popup.select_next()
            return "break"
        return None

    def _on_key_tab(self, _event=None):
        if self._slash_popup and self._slash_popup.winfo_exists():
            cmd = self._slash_popup.get_current_command()
            if cmd:
                self._apply_slash_command(cmd)
                return "break"
        return None

    def _on_entry_changed(self, _event=None):
        self._update_placeholder_visibility()
        self._resize_entry_to_content()
        text = self.entry.get("1.0", "end-1c")

        if text.startswith("/"):
            query = text.split("\n")[0]
            self._show_slash_popup(query)
        else:
            self._hide_slash_popup()

    def _show_slash_popup(self, query: str):
        if self._slash_popup is None or not self._slash_popup.winfo_exists():
            self._slash_popup = SlashCommandPopup(self.window, self._apply_slash_command, self.colors)
        if self._slash_popup.filter_query(query):
            self._slash_popup.place(relx=0.02, rely=1.0, y=-150, anchor="sw", relwidth=0.96)
            self._slash_popup.lift()
        else:
            self._hide_slash_popup()

    def _hide_slash_popup(self):
        if self._slash_popup is not None and self._slash_popup.winfo_exists():
            self._slash_popup.place_forget()

    def _apply_slash_command(self, cmd: str):
        self._hide_slash_popup()
        if cmd == "/model":
            self.entry.delete("1.0", "end")
            self._update_placeholder_visibility()
            self._open_model_menu()
            return
        if cmd == "/new":
            self.entry.delete("1.0", "end")
            self._update_placeholder_visibility()
            self.controller.create_session()
            return
        if cmd == "/compact":
            self.entry.delete("1.0", "end")
            self._update_placeholder_visibility()
            self.controller.compact_session()
            return
        if cmd == "/fork":
            self.entry.delete("1.0", "end")
            self._update_placeholder_visibility()
            self.controller.fork_session()
            return
        if cmd in ("/export", "/skills", "/pi"):
            self.entry.delete("1.0", "end")
            self._update_placeholder_visibility()
            self.controller.execute_slash_command(cmd)
            return
        self.entry.delete("1.0", "end")
        self.entry.insert("1.0", f"{cmd} ")
        self._update_placeholder_visibility()
        self.entry.focus()
        self._resize_entry_to_content()

    def _resize_entry_to_content(self):
        text_widget = getattr(self.entry, "_textbox", self.entry)
        line_height = 18
        try:
            text_widget.update_idletasks()
            if text_widget.winfo_width() <= 1:
                self.after(20, self._resize_entry_to_content)
                return
            display_lines = text_widget.count("1.0", "end-1c", "displaylines")
            if isinstance(display_lines, tuple):
                display_lines = display_lines[0]
            display_lines = max(1, int(display_lines or 1))
            line_info = text_widget.dlineinfo("1.0")
            if line_info:
                line_height = max(16, int(line_info[3]))
        except Exception:
            display_lines = max(1, len(self.entry.get("1.0", "end-1c").split("\n")))
        try:
            window_height = max(1, self.window.winfo_height())
        except Exception:
            window_height = 560
        max_height = min(180, max(54, int(window_height * 0.32)))
        target_height = max(54, min(max_height, 26 + (display_lines * line_height)))
        self.entry.configure(height=target_height)

    def _on_paste(self, _event=None):
        if self.controller.import_clipboard_image(show_empty_status=False):
            self._refresh_attachments()
            return "break"

        try:
            from src.gui.quick_prompt_popup import read_clipboard_files_win32

            files = read_clipboard_files_win32()
            if files and hasattr(self.controller, "import_paths"):
                if self.controller.import_paths(files, source="clipboard"):
                    self._refresh_attachments()
                    return "break"
        except Exception:
            pass

        self.controller.set_status("")
        self.after(10, self._update_placeholder_visibility)
        self.after(10, self._resize_entry_to_content)
        self.after(20, self._refresh_attachments)
        return None

    def _send_current_message(self):
        if hasattr(self, "_hide_slash_popup"):
            self._hide_slash_popup()
        session = self.controller.get_active_session()
        text = self.entry.get("1.0", "end-1c")

        if not self.controller.send_message(text, session.model_name, session.prompt_type):
            return
        self.entry.delete("1.0", "end")
        if hasattr(self, "_update_placeholder_visibility"):
            self._update_placeholder_visibility()
        self._resize_entry_to_content()
        if hasattr(self, "_refresh_attachments"):
            self._refresh_attachments()
        if hasattr(self.window, "schedule_scroll_to_bottom"):
            self.window.schedule_scroll_to_bottom(session.id, reason="send")
        else:
            self.window.after(20, self.window.scroll_to_bottom)

    def _stop_current_jobs(self):
        self.controller.stop_active_session_jobs()

    def _on_drop_files(self, event):
        raw = getattr(event, "data", "") or ""
        paths = self.tk.splitlist(raw) if raw else []
        self.controller.import_paths(list(paths), source="drop")
        self._refresh_attachments()
        return "break"
