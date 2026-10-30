"""
lolPing-inspired Dynamic Radial Plugin Wheel Overlay (`Ctrl + Alt + Drag / Hold`) — v2.4
========================================================================================
Renders a borderless, transparent circular radial wheel centered at the mouse
cursor when the user holds `Ctrl + Alt + Left/Right Mouse` (or drags a file while
holding `Ctrl + Alt`).

Supports 2 Seamless Execution Modes:
1. Mode 1 (Click-Hold & Drag to Slice):
   - Drag into any plugin slice and release mouse:
     * If `len(plugin.params) == 0` -> executes immediately!
     * If `len(plugin.params) > 0` -> opens `RadialParamPopup` with N parameter inputs
       pre-filled with last-used values (from `RadialParamStore`) or defaults.
2. Mode 2 (Hold `Ctrl + Alt` + Drag File into Slice):
   - Drag a file (`.pdf`, `.png`, `.jpg`, etc.) into a plugin slice (`pdf2png`, `png_resize`, etc.)
     and release -> passes the file as input to the plugin and opens the compact parameter
     popup (e.g. `--dpi`, `--output-path` defaulting to root PDF folder, or `--size 0.1..3.0`).
"""

from __future__ import annotations

import math
import os
from pathlib import Path
import tkinter as tk
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.gui.radial_param_popup import RadialParamPopup
from src.services.radial_plugin_service import RadialPluginService, RadialPluginSpec, RadialWheelStyle
from src.utils.file_context_extractor import format_file_size


def compute_radial_sector(
    center_x: float,
    center_y: float,
    cursor_x: float,
    cursor_y: float,
    num_sectors: int,
    deadzone_radius: float = 46.0,
) -> Tuple[Optional[int], float, float]:
    """
    Pure math helper for computing which radial sector `(cursor_x, cursor_y)` falls into.
    Sector 0 is centered at the top (12 o'clock / -90 deg), proceeding clockwise.
    Returns `(sector_index_or_None_if_in_deadzone, distance_px, angle_clockwise_from_top_deg)`.
    """
    if num_sectors <= 0:
        return None, 0.0, 0.0
    dx = float(cursor_x) - float(center_x)
    dy = float(cursor_y) - float(center_y)
    dist = math.hypot(dx, dy)
    if dist < float(deadzone_radius):
        return None, dist, 0.0

    rad_from_east = math.atan2(dy, dx)
    deg_from_north = (math.degrees(rad_from_east) + 90.0) % 360.0

    span = 360.0 / float(num_sectors)
    adjusted_deg = (deg_from_north + (span / 2.0)) % 360.0
    sector_idx = int(adjusted_deg // span) % num_sectors
    return sector_idx, dist, deg_from_north


class RadialWheelOverlay:
    """Tkinter-based circular HUD overlay for the Dynamic Radial Plugin Wheel."""

    TRANSPARENT_KEY = "#010101"

    def __init__(
        self,
        root: tk.Tk,
        plugin_service: RadialPluginService,
        on_select: Callable[..., None],
        auto_popup_params: bool = False,
    ) -> None:
        self.root = root
        self.plugin_service = plugin_service
        self.on_select = on_select
        self.auto_popup_params = auto_popup_params
        self.window: Optional[tk.Toplevel] = None
        self.canvas: Optional[tk.Canvas] = None
        self.is_open: bool = False
        self.center_screen_x: int = 0
        self.center_screen_y: int = 0
        self.active_index: Optional[int] = None
        self.plugins: List[RadialPluginSpec] = []
        self.style: RadialWheelStyle = self.plugin_service.style
        self.active_files: List[str] = []
        self.last_param_popup: Optional[RadialParamPopup] = None

    def show_at(self, screen_x: int, screen_y: int, active_files: Optional[List[str]] = None) -> None:
        """Open the radial wheel centered at `(screen_x, screen_y)` with optional target file(s)."""
        self.close()
        self.plugins = self.plugin_service.list_plugins(reload_if_changed=True)
        self.style = self.plugin_service.style
        if not self.plugins:
            return

        self.center_screen_x = int(screen_x)
        self.center_screen_y = int(screen_y)
        self.active_index = None
        self.is_open = True
        self.active_files = [str(f) for f in (active_files or []) if f and os.path.exists(str(f))]

        outer_r = int(self.style.outer_radius)
        pad = 30
        size = (outer_r + pad) * 2
        left = self.center_screen_x - (size // 2)
        top = self.center_screen_y - (size // 2)

        win = tk.Toplevel(self.root)
        win.withdraw()
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        try:
            win.configure(bg=self.TRANSPARENT_KEY)
            win.attributes("-transparentcolor", self.TRANSPARENT_KEY)
        except Exception:
            win.configure(bg=self.style.bg_color)

        win.geometry(f"{size}x{size}+{left}+{top}")
        canvas = tk.Canvas(
            win,
            width=size,
            height=size,
            bg=self.TRANSPARENT_KEY,
            highlightthickness=0,
            bd=0,
        )
        canvas.pack(fill=tk.BOTH, expand=True)

        self.window = win
        self.canvas = canvas
        self._render()

        win.bind("<Escape>", lambda _e: self.close())
        canvas.bind("<Motion>", self._on_canvas_motion)
        canvas.bind("<ButtonRelease-1>", self._on_canvas_release)
        canvas.bind("<ButtonRelease-2>", self._on_canvas_release)
        canvas.bind("<ButtonRelease-3>", self._on_canvas_release)

        win.deiconify()
        win.lift()

    def attach_dropped_files(self, files: List[str]) -> None:
        """Attach files dragged onto the wheel in real time and refresh the center HUD."""
        valid = [str(f) for f in (files or []) if f and os.path.exists(str(f))]
        if valid:
            for vf in valid:
                if vf not in self.active_files:
                    self.active_files.append(vf)
            if self.is_open:
                self._render()

    def update_cursor(self, screen_x: int, screen_y: int) -> Optional[int]:
        """Update hovered sector from global mouse coordinates."""
        if not self.is_open:
            return None
        new_idx, _dist, _angle = compute_radial_sector(
            self.center_screen_x,
            self.center_screen_y,
            screen_x,
            screen_y,
            len(self.plugins),
            deadzone_radius=float(self.style.deadzone_radius),
        )
        if new_idx != self.active_index:
            self.active_index = new_idx
            self._render()
        return self.active_index

    def complete_at(self, screen_x: int, screen_y: int) -> Optional[RadialPluginSpec]:
        """Called when the mouse button or Ctrl+Alt is released."""
        if not self.is_open:
            return None
        idx, _dist, _angle = compute_radial_sector(
            self.center_screen_x,
            self.center_screen_y,
            screen_x,
            screen_y,
            len(self.plugins),
            deadzone_radius=float(self.style.deadzone_radius),
        )
        selected = (
            self.plugins[idx]
            if idx is not None and 0 <= idx < len(self.plugins)
            else None
        )
        origin = (self.center_screen_x, self.center_screen_y)
        files = list(self.active_files)
        self.close()
        if selected is not None and self.on_select is not None:
            try:
                self.on_select(selected, origin, active_files=files)
            except TypeError:
                self.on_select(selected, origin)
        return selected

    def open_param_popup(
        self,
        spec: RadialPluginSpec,
        origin: tuple[int, int] = (400, 300),
        active_files: Optional[List[str]] = None,
        on_execute: Optional[Callable[[RadialPluginSpec, Dict[str, Any], List[str], str], None]] = None,
        on_run: Optional[Callable[[Dict[str, Any], List[str]], None]] = None,
    ) -> RadialParamPopup:
        """Open the dynamic N-parameter configuration popup for `spec`."""
        if self.last_param_popup is not None:
            self.last_param_popup.close()
        popup = RadialParamPopup(
            root=self.root,
            plugin_service=self.plugin_service,
            spec=spec,
            origin=origin,
            active_files=active_files,
            on_execute=on_execute,
            on_run=on_run,
        )
        self.last_param_popup = popup.show()
        return self.last_param_popup

    def close(self) -> None:
        self.is_open = False
        self.active_index = None
        self.active_files = []
        if self.window is not None:
            try:
                self.window.destroy()
            except Exception:
                pass
            self.window = None
            self.canvas = None

    def _on_canvas_motion(self, event: tk.Event) -> None:
        if self.window is None:
            return
        sx = self.window.winfo_rootx() + int(event.x)
        sy = self.window.winfo_rooty() + int(event.y)
        self.update_cursor(sx, sy)

    def _on_canvas_release(self, event: tk.Event) -> None:
        if self.window is None:
            return
        sx = self.window.winfo_rootx() + int(event.x)
        sy = self.window.winfo_rooty() + int(event.y)
        self.complete_at(sx, sy)

    def _render(self) -> None:
        if self.canvas is None or not self.is_open:
            return
        c = self.canvas
        c.delete("all")

        outer_r = int(self.style.outer_radius)
        inner_r = int(self.style.inner_radius)
        pad = 30
        cx = outer_r + pad
        cy = outer_r + pad
        n = len(self.plugins)
        if n == 0:
            return

        span = 360.0 / float(n)
        has_file = bool(self.active_files)
        first_file = Path(self.active_files[0]) if has_file else None

        # 1. Outer Glow Halo
        c.create_oval(
            cx - outer_r - 6,
            cy - outer_r - 6,
            cx + outer_r + 6,
            cy + outer_r + 6,
            fill="#05080f",
            outline="#1e293b",
            width=2,
        )

        # 2. Outer Border Ring
        c.create_oval(
            cx - outer_r - 2,
            cy - outer_r - 2,
            cx + outer_r + 2,
            cy + outer_r + 2,
            fill="#0b1120",
            outline="#f59e0b" if has_file else self.style.ring_border_color,
            width=2,
        )

        # 3. Draw Each Wedge Slice
        for idx, plugin in enumerate(self.plugins):
            cw_center_deg = idx * span
            tk_start_deg = (90.0 - cw_center_deg - (span / 2.0)) % 360.0
            is_hovered = idx == self.active_index
            file_compatible = has_file and first_file is not None and plugin.matches_file(str(first_file))

            if is_hovered:
                fill_color = "#1e3a5f" if not has_file else "#1e3f3b"
                outline_color = "#38bdf8" if not has_file else "#34d399"
            elif file_compatible and plugin.requires_file_input:
                fill_color = "#172a3a"
                outline_color = "#0284c7"
            else:
                fill_color = "#131c2e" if (idx % 2 == 0) else "#0f172a"
                outline_color = "#1e293b"

            c.create_arc(
                cx - outer_r,
                cy - outer_r,
                cx + outer_r,
                cy + outer_r,
                start=tk_start_deg,
                extent=span,
                fill=fill_color,
                outline=outline_color,
                width=3 if is_hovered else 1,
                style=tk.PIESLICE,
            )

            # Icon, label, and param/instant indicator along bisector
            rad = math.radians(cw_center_deg)
            mid_r = (outer_r + inner_r) * 0.55
            ix = cx + math.sin(rad) * mid_r
            iy = cy - math.cos(rad) * mid_r

            icon_font = ("Segoe UI Emoji", 18 if is_hovered else 15, "bold")
            label_font = ("Segoe UI", 9, "bold" if is_hovered else "normal")
            text_color = "#ffffff" if is_hovered else self.style.text_color

            c.create_text(
                ix,
                iy - 13,
                text=plugin.icon,
                fill=text_color,
                font=icon_font,
            )
            c.create_text(
                ix,
                iy + 9,
                text=plugin.name,
                fill="#38bdf8" if is_hovered else text_color,
                font=label_font,
            )
            mode_tag = f"⚙ {len(plugin.params)}p" if plugin.has_params else "⚡ Run"
            c.create_text(
                ix,
                iy + 23,
                text=mode_tag,
                fill="#fbbf24" if plugin.has_params else "#34d399",
                font=("Segoe UI", 7, "bold"),
            )

        # 4. Spoke Dividers
        for idx in range(n):
            angle_deg = (idx * span) - (span / 2.0)
            rad = math.radians(angle_deg)
            x1 = cx + math.sin(rad) * inner_r
            y1 = cy - math.cos(rad) * inner_r
            x2 = cx + math.sin(rad) * outer_r
            y2 = cy - math.cos(rad) * outer_r
            c.create_line(x1, y1, x2, y2, fill="#263548", width=1)

        # 5. Inner Medallion (Center Circle)
        c.create_oval(
            cx - inner_r - 4,
            cy - inner_r - 4,
            cx + inner_r + 4,
            cy + inner_r + 4,
            fill="#06131f",
            outline="#f59e0b" if has_file else self.style.ring_border_color,
            width=2,
        )
        c.create_oval(
            cx - inner_r,
            cy - inner_r,
            cx + inner_r,
            cy + inner_r,
            fill="#09111e",
            outline="#38bdf8" if self.active_index is not None else self.style.center_border_color,
            width=2,
        )

        # 6. Center Content (Active File / Hovered Plugin / Cancel)
        if self.active_index is not None and 0 <= self.active_index < len(self.plugins):
            hovered = self.plugins[self.active_index]
            c.create_text(
                cx,
                cy - 24,
                text=f"{hovered.icon} {hovered.badge}",
                fill="#38bdf8",
                font=("Segoe UI", 9, "bold"),
            )
            c.create_text(
                cx,
                cy - 5,
                text=hovered.name,
                fill="#ffffff",
                font=("Segoe UI", 10, "bold"),
            )
            if has_file and first_file:
                fname = first_file.name
                if len(fname) > 15:
                    fname = fname[:12] + "..."
                c.create_text(
                    cx,
                    cy + 12,
                    text=f"📄 {fname}",
                    fill="#fbbf24",
                    font=("Segoe UI", 8, "bold"),
                )
            else:
                param_hint = f"{len(hovered.params)} param(s) popup" if hovered.has_params else "Instant Run"
                c.create_text(
                    cx,
                    cy + 12,
                    text=param_hint,
                    fill="#94a3b8",
                    font=("Segoe UI", 8),
                )
            c.create_text(
                cx,
                cy + 28,
                text="⚡ THẢ CHUỘT ĐỂ CHẠY",
                fill="#34d399",
                font=("Segoe UI", 7, "bold"),
            )
        elif has_file and first_file:
            fname = first_file.name
            if len(fname) > 15:
                fname = fname[:12] + "..."
            size_str = format_file_size(first_file.stat().st_size) if first_file.is_file() else "DIR"
            c.create_text(
                cx,
                cy - 22,
                text="📥 FILE INPUT",
                fill="#fbbf24",
                font=("Segoe UI", 8, "bold"),
            )
            c.create_text(
                cx,
                cy - 4,
                text=fname,
                fill="#ffffff",
                font=("Segoe UI", 9, "bold"),
            )
            c.create_text(
                cx,
                cy + 12,
                text=size_str,
                fill="#94a3b8",
                font=("Segoe UI", 8),
            )
            c.create_text(
                cx,
                cy + 27,
                text="Kéo vào ô plugin",
                fill="#38bdf8",
                font=("Segoe UI", 7, "bold"),
            )
        else:
            c.create_text(
                cx,
                cy - 18,
                text="SNAPARC",
                fill=self.style.subtext_color,
                font=("Segoe UI", 8, "bold"),
            )
            c.create_text(
                cx,
                cy + 1,
                text="PLUGINS",
                fill="#ffffff",
                font=("Segoe UI", 11, "bold"),
            )
            c.create_text(
                cx,
                cy + 21,
                text="✖ HỦY (Giữa)",
                fill="#64748b",
                font=("Segoe UI", 7, "bold"),
            )
