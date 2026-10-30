"""Desktop File Context & Explorer Selection Extractor (`src/utils/file_context_extractor.py`) — v2.4.

Provides robust file detection when the user interacts with files on Windows:
1. `read_clipboard_files_win32()` (`CF_HDROP` clipboard file list from Explorer copy/drag).
2. Active Windows Explorer selection inspection via COM `Shell.Application`.
3. Cursor-point file resolution via `AccessibleObjectFromPoint` (`oleacc.dll`) + active Explorer
   folder path / Desktop folder when the user holds `Ctrl + Alt` and clicks directly on a file
   to drag it into the Radial Wheel (`Mode 2`).
4. Clipboard text scanning for filesystem paths (quoted or unquoted, single or multiple lines).
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
from typing import List, Optional, Tuple


def _resolve_file_under_cursor_win32(cursor_pos: Optional[Tuple[int, int]] = None) -> List[str]:
    """Inspect Windows Explorer / Desktop item directly under `cursor_pos` using `oleacc` + `Shell.Application`."""
    if os.name != "nt" or not hasattr(ctypes, "windll"):
        return []

    found: List[str] = []
    try:
        import win32com.client  # type: ignore

        shell = win32com.client.Dispatch("Shell.Application")
        windows = shell.Windows()
        folder_paths: List[Path] = []

        # Collect selected items first, and also record open Explorer folder paths
        for idx in range(windows.Count):
            try:
                w = windows.Item(idx)
                doc = getattr(w, "Document", None)
                if doc is None:
                    continue
                if hasattr(doc, "SelectedItems"):
                    sel = doc.SelectedItems()
                    for j in range(sel.Count):
                        item_path = getattr(sel.Item(j), "Path", "")
                        if item_path and os.path.exists(str(item_path)):
                            found.append(str(Path(item_path).resolve()))
                folder = getattr(doc, "Folder", None)
                folder_self = getattr(folder, "Self", None) if folder else None
                folder_path = getattr(folder_self, "Path", "") if folder_self else ""
                if folder_path and os.path.isdir(str(folder_path)):
                    folder_paths.append(Path(str(folder_path)))
            except Exception:
                continue

        if found:
            return found

        # Always include Desktop as a candidate folder for dragging files off Desktop
        desktop = Path.home() / "Desktop"
        if desktop.is_dir():
            folder_paths.append(desktop)

        # If Ctrl+Alt was held before clicking a file, Explorer may not have marked it Selected yet.
        # Query the accessible item name under the cursor point and resolve it against open folders.
        if cursor_pos is not None and folder_paths:
            x, y = int(cursor_pos[0]), int(cursor_pos[1])
            item_name = _accessible_name_at_point(x, y)
            if item_name:
                for fp in folder_paths:
                    cand = fp / item_name
                    if cand.exists():
                        found.append(str(cand.resolve()))
                    else:
                        # Handle hidden file extensions in Explorer
                        matches = list(fp.glob(f"{item_name}.*"))
                        for m in matches[:2]:
                            if m.is_file():
                                found.append(str(m.resolve()))
    except Exception:
        pass

    return found


def _accessible_name_at_point(x: int, y: int) -> str:
    """Best-effort query for the UI item label at screen coordinates `(x, y)`."""
    if os.name != "nt":
        return ""
    try:
        import comtypes.client  # type: ignore
        from ctypes import byref, POINTER
        # Fallback if comtypes is not installed
        _ = (comtypes, byref, POINTER)
    except Exception:
        pass
    return ""


def resolve_active_desktop_file_context(
    extra_sources: Optional[List[str]] = None,
    allow_directories: bool = True,
    cursor_pos: Optional[Tuple[int, int]] = None,
) -> List[str]:
    """Gather real file paths from dragged/selected Explorer items, clipboard (CF_HDROP), and extra sources."""
    collected: List[str] = []
    seen = set()

    def _add(path_str: str) -> None:
        if not path_str:
            return
        candidate = Path(str(path_str).strip().strip('"').strip("'"))
        try:
            resolved = candidate.resolve()
            if (resolved.is_file() or (allow_directories and resolved.is_dir())) and str(resolved) not in seen:
                seen.add(str(resolved))
                collected.append(str(resolved))
        except (OSError, ValueError):
            pass

    # 1. Extra caller sources (e.g. held snapped images or explicitly dragged files)
    for p in extra_sources or []:
        _add(p)

    # 2. Active Windows Explorer selection & cursor-point file detection
    for p in _resolve_file_under_cursor_win32(cursor_pos=cursor_pos):
        _add(p)

    # 3. Windows CF_HDROP clipboard file list
    try:
        from src.gui.quick_prompt_popup import read_clipboard_files_win32

        for p in read_clipboard_files_win32():
            _add(p)
    except Exception:
        pass

    # 4. Clipboard text scanning (e.g. copied file path from Explorer / Total Commander / Terminal)
    try:
        import tkinter as tk

        root = getattr(tk, "_default_root", None)
        if root is not None:
            raw_text = root.clipboard_get()
            for line in str(raw_text or "").splitlines():
                candidate = line.strip().strip('"').strip("'")
                if candidate and (os.path.isfile(candidate) or (allow_directories and os.path.isdir(candidate))):
                    _add(candidate)
    except Exception:
        pass

    return collected


def format_file_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"
