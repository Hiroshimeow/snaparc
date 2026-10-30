"""
Dynamic Parameter Popup for Radial Wheel Plugins (`Ctrl + Alt + Drag`) — v2.4
=============================================================================
When a user selects a Radial Wheel plugin that has $N > 0$ parameters (or requires
a file input when none was dragged), this compact, sleek popup appears right at
the cursor position:
- Automatically generates $N$ input controls matching `spec.params` (`string`, `int`,
  `float`, `path`, `file`, `bool`, `choice`).
- Pre-populates values from `RadialParamStore` (last-used values for fast 1-key re-runs)
  or falls back to each parameter's default (e.g., `{file_dir}` = root directory of
  the dragged PDF/PNG).
- Supports quick preset chips (e.g. `150 DPI`, `300 DPI` for `pdf2png`; `0.25x (25%)`,
  `0.5x (50%)`, `1.5x (150%)`, `2.0x (200%)`, `3.0x (300%)` for `png_resize`).
- Live CLI command preview bar (e.g. `uv run plugins/a.py --root 11235813x --host 0.0.0.0 --port 8000`).
- Press `Enter` anywhere to run immediately, `↺ Reset Default` to restore defaults, or `Esc` to cancel.
"""

from __future__ import annotations

import os
from pathlib import Path
import tkinter as tk
from tkinter import filedialog
from typing import Any, Callable, Dict, List, Optional

from src.services.radial_plugin_service import RadialParamSpec, RadialPluginService, RadialPluginSpec
from src.utils.file_context_extractor import format_file_size


class RadialParamPopup:
    """Compact, keyboard-first dark UI popup for configuring N plugin parameters."""

    BG_MAIN = "#0b0f19"
    BG_CARD = "#111827"
    BG_INPUT = "#1e293b"
    BG_PREVIEW = "#090d16"
    BORDER_COLOR = "#38bdf8"
    TEXT_PRIMARY = "#f8fafc"
    TEXT_SECONDARY = "#94a3b8"
    ACCENT_CYAN = "#06b6d4"
    ACCENT_GREEN = "#10b981"
    ACCENT_AMBER = "#f59e0b"

    def __init__(
        self,
        root: tk.Tk,
        plugin_service: RadialPluginService,
        spec: RadialPluginSpec,
        origin: tuple[int, int] = (400, 300),
        active_files: Optional[List[str]] = None,
        on_execute: Optional[Callable[[RadialPluginSpec, Dict[str, Any], List[str], str], None]] = None,
        on_run: Optional[Callable[[Dict[str, Any], List[str]], None]] = None,
    ) -> None:
        self.root = root
        self.plugin_service = plugin_service
        self.spec = spec
        self.origin = (int(origin[0]), int(origin[1]))
        self.active_files: List[str] = [
            str(f) for f in (active_files or []) if f and os.path.exists(str(f))
        ]
        self.on_execute = on_execute
        self.on_run = on_run

        self.window: Optional[tk.Toplevel] = None
        self.param_vars: Dict[str, tk.StringVar] = {}
        self.param_entries: Dict[str, tk.Entry] = {}
        self.file_var = tk.StringVar(
            master=self.root,
            value=self.active_files[0] if self.active_files else "",
        )
        self.preview_var = tk.StringVar(master=self.root, value="")
        self.error_var = tk.StringVar(master=self.root, value="")
        self.exec_mode_var = tk.StringVar(
            master=self.root,
            value=str(self.spec.execution_mode or "capture"),
        )
        self.is_open: bool = False

        # Pre-populate parameter values from RadialParamStore (last-used or defaults)
        initial_vals = self.plugin_service.get_prefilled_params(
            self.spec.id,
            active_files=self.active_files,
            use_last_saved=True,
        )
        for p in self.spec.params:
            val = initial_vals.get(p.name, "")
            self.param_vars[p.name] = tk.StringVar(master=self.root, value=str(val))

        self._refresh_command_preview()

    def show(self) -> "RadialParamPopup":
        self.close()
        self.is_open = True

        win = tk.Toplevel(self.root)
        win.withdraw()
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=self.BORDER_COLOR, bd=1, relief=tk.SOLID)

        width = 480
        base_height = 210 + max(1, len(self.spec.params)) * 62
        if self.spec.requires_file_input or self.active_files:
            base_height += 54
        height = min(620, max(280, base_height))

        sw = win.winfo_screenwidth()
        sh = win.winfo_screenheight()
        left = max(16, min(self.origin[0] - (width // 2), sw - width - 16))
        top = max(16, min(self.origin[1] - 40, sh - height - 24))
        win.geometry(f"{width}x{height}+{left}+{top}")

        container = tk.Frame(win, bg=self.BG_MAIN, padx=14, pady=12)
        container.pack(fill=tk.BOTH, expand=True)

        # --- Header ---
        header = tk.Frame(container, bg=self.BG_MAIN)
        header.pack(fill=tk.X, pady=(0, 8))

        title_lbl = tk.Label(
            header,
            text=f"{self.spec.icon}  {self.spec.name}",
            fg=self.TEXT_PRIMARY,
            bg=self.BG_MAIN,
            font=("Segoe UI", 12, "bold"),
            anchor="w",
        )
        title_lbl.pack(side=tk.LEFT)

        badge_lbl = tk.Label(
            header,
            text=f" {self.spec.badge} • {len(self.spec.params)} PARAMS ",
            fg=self.BG_MAIN,
            bg=self.ACCENT_CYAN,
            font=("Segoe UI", 8, "bold"),
            padx=6,
            pady=2,
        )
        badge_lbl.pack(side=tk.LEFT, padx=(8, 0))

        close_btn = tk.Button(
            header,
            text="✕",
            command=self.close,
            fg=self.TEXT_SECONDARY,
            bg=self.BG_MAIN,
            activeforeground=self.TEXT_PRIMARY,
            activebackground=self.BG_CARD,
            bd=0,
            font=("Segoe UI", 10, "bold"),
            cursor="hand2",
        )
        close_btn.pack(side=tk.RIGHT)

        if self.spec.description:
            desc_lbl = tk.Label(
                container,
                text=self.spec.description,
                fg=self.TEXT_SECONDARY,
                bg=self.BG_MAIN,
                font=("Segoe UI", 9),
                anchor="w",
            )
            desc_lbl.pack(fill=tk.X, pady=(0, 8))

        # --- Target File Input Bar (Mode 2: File Drag-and-Drop or Browse) ---
        if self.spec.requires_file_input or self.active_files:
            file_card = tk.Frame(container, bg=self.BG_CARD, padx=10, pady=6)
            file_card.pack(fill=tk.X, pady=(0, 8))

            file_top = tk.Frame(file_card, bg=self.BG_CARD)
            file_top.pack(fill=tk.X)

            tk.Label(
                file_top,
                text="📎 Input File:",
                fg=self.ACCENT_AMBER,
                bg=self.BG_CARD,
                font=("Segoe UI", 9, "bold"),
            ).pack(side=tk.LEFT)

            if self.active_files and os.path.isfile(self.active_files[0]):
                fsize = format_file_size(os.path.getsize(self.active_files[0]))
                tk.Label(
                    file_top,
                    text=f"({fsize})",
                    fg=self.TEXT_SECONDARY,
                    bg=self.BG_CARD,
                    font=("Segoe UI", 8),
                ).pack(side=tk.LEFT, padx=(6, 0))

            browse_file_btn = tk.Button(
                file_top,
                text="📂 Browse...",
                command=self._browse_input_file,
                fg=self.TEXT_PRIMARY,
                bg=self.BG_INPUT,
                bd=0,
                font=("Segoe UI", 8),
                padx=8,
                pady=2,
                cursor="hand2",
            )
            browse_file_btn.pack(side=tk.RIGHT)

            file_entry = tk.Entry(
                file_card,
                textvariable=self.file_var,
                fg=self.TEXT_PRIMARY,
                bg=self.BG_INPUT,
                insertbackground=self.TEXT_PRIMARY,
                relief=tk.FLAT,
                font=("Consolas", 9),
            )
            file_entry.pack(fill=tk.X, pady=(4, 0))
            file_entry.bind("<KeyRelease>", lambda _e: self._on_file_entry_changed())

        # --- Dynamic Parameter Rows (N params) ---
        params_frame = tk.Frame(container, bg=self.BG_MAIN)
        params_frame.pack(fill=tk.BOTH, expand=True)

        first_entry: Optional[tk.Entry] = None
        for param in self.spec.params:
            row = tk.Frame(params_frame, bg=self.BG_CARD, padx=10, pady=6)
            row.pack(fill=tk.X, pady=(0, 6))

            lbl_row = tk.Frame(row, bg=self.BG_CARD)
            lbl_row.pack(fill=tk.X)

            tk.Label(
                lbl_row,
                text=param.label or f"{param.name} ({param.flag})",
                fg=self.TEXT_PRIMARY,
                bg=self.BG_CARD,
                font=("Segoe UI", 9, "bold"),
                anchor="w",
            ).pack(side=tk.LEFT)

            range_hint = ""
            if param.min_val is not None and param.max_val is not None:
                range_hint = f"[{param.min_val} .. {param.max_val}]"
            elif param.default not in (None, ""):
                range_hint = f"default: {param.default}"
            if range_hint:
                tk.Label(
                    lbl_row,
                    text=range_hint,
                    fg=self.TEXT_SECONDARY,
                    bg=self.BG_CARD,
                    font=("Consolas", 8),
                ).pack(side=tk.RIGHT)

            input_row = tk.Frame(row, bg=self.BG_CARD)
            input_row.pack(fill=tk.X, pady=(4, 0))

            var = self.param_vars[param.name]
            entry = tk.Entry(
                input_row,
                textvariable=var,
                fg=self.TEXT_PRIMARY,
                bg=self.BG_INPUT,
                insertbackground=self.TEXT_PRIMARY,
                relief=tk.FLAT,
                font=("Consolas", 10),
            )
            entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=3)
            entry.bind("<KeyRelease>", lambda _e: self._refresh_command_preview())
            entry.bind("<Return>", lambda _e: self.submit())
            self.param_entries[param.name] = entry
            if first_entry is None:
                first_entry = entry

            if param.type in ("path", "file"):
                tk.Button(
                    input_row,
                    text="📁",
                    command=lambda p=param: self._browse_param_path(p),
                    fg=self.TEXT_PRIMARY,
                    bg=self.BG_INPUT,
                    bd=0,
                    padx=6,
                    cursor="hand2",
                ).pack(side=tk.RIGHT, padx=(6, 0))

            # Quick preset chips for common parameters (--dpi, --size, --host)
            presets = self._get_presets_for_param(param)
            if presets:
                chip_row = tk.Frame(row, bg=self.BG_CARD)
                chip_row.pack(fill=tk.X, pady=(4, 0))
                for chip_label, chip_val in presets:
                    tk.Button(
                        chip_row,
                        text=chip_label,
                        command=lambda pname=param.name, val=chip_val: self.set_param_value(pname, val),
                        fg=self.ACCENT_CYAN,
                        bg=self.BG_MAIN,
                        bd=0,
                        font=("Consolas", 8),
                        padx=6,
                        pady=1,
                        cursor="hand2",
                    ).pack(side=tk.LEFT, padx=(0, 4))

        # --- Live CLI Command Preview ---
        preview_box = tk.Frame(container, bg=self.BG_PREVIEW, padx=8, pady=5)
        preview_box.pack(fill=tk.X, pady=(4, 6))
        tk.Label(
            preview_box,
            textvariable=self.preview_var,
            fg=self.ACCENT_CYAN,
            bg=self.BG_PREVIEW,
            font=("Consolas", 8),
            anchor="w",
            justify=tk.LEFT,
            wraplength=width - 48,
        ).pack(fill=tk.X)

        # --- Error Label (if validation fails) ---
        err_lbl = tk.Label(
            container,
            textvariable=self.error_var,
            fg="#ef4444",
            bg=self.BG_MAIN,
            font=("Segoe UI", 8, "bold"),
            anchor="w",
        )
        err_lbl.pack(fill=tk.X)

        # --- Footer Action Buttons ---
        footer = tk.Frame(container, bg=self.BG_MAIN)
        footer.pack(fill=tk.X, pady=(6, 0))

        reset_btn = tk.Button(
            footer,
            text="↺ Default",
            command=self.reset_to_defaults,
            fg=self.TEXT_SECONDARY,
            bg=self.BG_CARD,
            bd=0,
            font=("Segoe UI", 9),
            padx=10,
            pady=5,
            cursor="hand2",
        )
        reset_btn.pack(side=tk.LEFT)

        cancel_btn = tk.Button(
            footer,
            text="Cancel (Esc)",
            command=self.close,
            fg=self.TEXT_SECONDARY,
            bg=self.BG_CARD,
            bd=0,
            font=("Segoe UI", 9),
            padx=10,
            pady=5,
            cursor="hand2",
        )
        cancel_btn.pack(side=tk.RIGHT, padx=(6, 0))

        run_btn = tk.Button(
            footer,
            text="⚡ Run Plugin (Enter)",
            command=self.submit,
            fg="#052e16",
            bg=self.ACCENT_GREEN,
            bd=0,
            font=("Segoe UI", 9, "bold"),
            padx=14,
            pady=5,
            cursor="hand2",
        )
        run_btn.pack(side=tk.RIGHT)

        win.bind("<Escape>", lambda _e: self.close())
        win.bind("<Return>", lambda _e: self.submit())

        self.window = win
        win.deiconify()
        win.lift()
        if first_entry is not None:
            first_entry.focus_set()
        return self

    def _get_presets_for_param(self, param: RadialParamSpec) -> List[tuple[str, str]]:
        if param.choices:
            return [(str(c), str(c)) for c in param.choices[:6]]
        if param.name == "dpi":
            return [("72 DPI", "72"), ("150 DPI", "150"), ("300 DPI", "300"), ("600 DPI", "600")]
        if param.name == "size":
            return [
                ("10% (0.1)", "0.1"),
                ("25% (0.25)", "0.25"),
                ("50% (0.5)", "0.5"),
                ("100% (1.0)", "1.0"),
                ("200% (2.0)", "2.0"),
                ("300% (3.0)", "3.0"),
            ]
        if param.name == "host":
            return [("0.0.0.0", "0.0.0.0"), ("127.0.0.1", "127.0.0.1")]
        return []

    def set_param_value(self, name: str, value: Any) -> None:
        key = str(name).strip().lstrip("-").replace("-", "_")
        if key not in self.param_vars:
            self.param_vars[key] = tk.StringVar(master=self.root, value=str(value))
        else:
            self.param_vars[key].set(str(value))
        self._refresh_command_preview()

    def get_current_values(self) -> Dict[str, str]:
        return {k: str(v.get()).strip() for k, v in self.param_vars.items()}

    def reset_to_defaults(self) -> Dict[str, str]:
        """Reset all fields to their schema default (resolving `{file_dir}` to input file folder)."""
        defaults = self.plugin_service.get_prefilled_params(
            self.spec.id,
            active_files=self.active_files,
            use_last_saved=False,
        )
        for p in self.spec.params:
            val = defaults.get(p.name, "")
            if p.name in self.param_vars:
                self.param_vars[p.name].set(str(val))
        self.error_var.set("")
        self._refresh_command_preview()
        return self.get_current_values()

    def _on_file_entry_changed(self) -> None:
        raw = str(self.file_var.get() or "").strip().strip('"')
        if raw and os.path.exists(raw):
            self.active_files = [raw]
            # Update any `{file_dir}` parameter if it was still empty
            for p in self.spec.params:
                if str(p.default).strip() in ("{file_dir}", "{root_pdf}", "{root_file}", "{input_dir}"):
                    resolved = str(Path(raw).resolve().parent)
                    if p.name in self.param_vars:
                        self.param_vars[p.name].set(resolved)
        self._refresh_command_preview()

    def _browse_input_file(self) -> None:
        path = filedialog.askopenfilename(title=f"Select input file for {self.spec.name}")
        if path:
            self.file_var.set(path)
            self._on_file_entry_changed()

    def _browse_param_path(self, param: RadialParamSpec) -> None:
        if param.type == "file":
            chosen = filedialog.askopenfilename(title=f"Select {param.label}")
        else:
            chosen = filedialog.askdirectory(title=f"Select {param.label}")
        if chosen:
            self.set_param_value(param.name, chosen)

    def _refresh_command_preview(self) -> str:
        vals = self.get_current_values()
        preview = self.plugin_service.build_command_preview(
            self.spec,
            param_values=vals,
            active_files=self.active_files,
        )
        self.preview_var.set(f"$ {preview}")
        return preview

    def validate(self) -> tuple[bool, str]:
        vals = self.get_current_values()
        if self.spec.requires_file_input:
            raw_file = str(self.file_var.get() or "").strip().strip('"')
            if raw_file and os.path.exists(raw_file):
                self.active_files = [raw_file]
            if not self.active_files or not os.path.exists(self.active_files[0]):
                return False, f"Plugin '{self.spec.name}' requires an input file."

        for param in self.spec.params:
            raw = vals.get(param.name, "")
            if param.required and not raw:
                return False, f"Parameter '{param.flag}' is required."
            if not raw:
                continue
            if param.type == "int":
                try:
                    iv = int(float(raw))
                except ValueError:
                    return False, f"Parameter '{param.flag}' must be an integer."
                if param.min_val is not None and iv < int(param.min_val):
                    return False, f"Parameter '{param.flag}' must be >= {int(param.min_val)}."
                if param.max_val is not None and iv > int(param.max_val):
                    return False, f"Parameter '{param.flag}' must be <= {int(param.max_val)}."
            elif param.type == "float":
                try:
                    fv = float(raw)
                except ValueError:
                    return False, f"Parameter '{param.flag}' must be a number."
                if param.min_val is not None and fv < float(param.min_val) - 1e-9:
                    return False, f"Parameter '{param.flag}' must be >= {param.min_val}."
                if param.max_val is not None and fv > float(param.max_val) + 1e-9:
                    return False, f"Parameter '{param.flag}' must be <= {param.max_val}."

        return True, ""

    def submit(self) -> bool:
        ok, err = self.validate()
        if not ok:
            self.error_var.set(err)
            return False

        vals = self.get_current_values()
        files = list(self.active_files)
        exec_mode = str(self.exec_mode_var.get() or self.spec.execution_mode or "capture")

        # Persist values into RadialParamStore immediately
        self.plugin_service.param_store.record_values(self.spec.id, vals)
        self.close()

        if self.on_run is not None:
            self.on_run(vals, files)
        if self.on_execute is not None:
            self.on_execute(self.spec, vals, files, exec_mode)
        return True

    def close(self) -> None:
        self.is_open = False
        if self.window is not None:
            try:
                self.window.destroy()
            except Exception:
                pass
            self.window = None
