from __future__ import annotations

import re
import tkinter as tk
import tkinter.font as tkfont

try:
    from pygments import lex
    from pygments.lexers import get_lexer_by_name
    from pygments.token import Comment, Keyword, Name, Number, String, Token
except Exception:  # pragma: no cover - optional runtime dependency fallback
    lex = None
    get_lexer_by_name = None
    Comment = Keyword = Name = Number = String = Token = None


MARKDOWN_TAGS = (
    "md_h1",
    "md_h2",
    "md_h3",
    "md_bold",
    "md_italic",
    "md_inline_code",
    "md_code_block",
    "md_quote",
    "md_list",
    "md_hr",
    "md_link",
    "md_code_keyword",
    "md_code_string",
    "md_code_comment",
    "md_code_number",
    "md_code_name",
)


def apply_markdown(
    text_widget: tk.Text,
    content: str,
    palette: dict | None = None,
    *,
    syntax_highlight: bool = True,
) -> None:
    palette = palette or {}
    _setup_tags(text_widget, palette)
    text_widget.configure(state=tk.NORMAL)
    text_widget.delete("1.0", "end")
    text_widget.insert("1.0", content or "")
    for tag in MARKDOWN_TAGS:
        text_widget.tag_remove(tag, "1.0", "end")
    _apply_block_tags(text_widget)
    _apply_inline_tags(text_widget)
    if syntax_highlight:
        _apply_code_highlight(text_widget)
    text_widget.configure(state="disabled")


def _setup_tags(text_widget: tk.Text, palette: dict) -> None:
    if getattr(text_widget, "_screenshot_md_ready", False):
        return
    try:
        base_font = tkfont.nametofont("TkDefaultFont")
        base_size = base_font.cget("size")
        bold_font = base_font.copy()
        bold_font.configure(weight="bold")
        italic_font = base_font.copy()
        italic_font.configure(slant="italic")
        h1_font = base_font.copy()
        h1_font.configure(size=max(base_size + 5, 15), weight="bold")
        h2_font = base_font.copy()
        h2_font.configure(size=max(base_size + 3, 13), weight="bold")
        h3_font = base_font.copy()
        h3_font.configure(size=max(base_size + 1, 12), weight="bold")
        code_font = tkfont.Font(family="Consolas", size=base_size)
    except Exception:
        bold_font = ("Segoe UI", 10, "bold")
        italic_font = ("Segoe UI", 10, "italic")
        h1_font = ("Segoe UI", 15, "bold")
        h2_font = ("Segoe UI", 13, "bold")
        h3_font = ("Segoe UI", 12, "bold")
        code_font = ("Consolas", 10)
    text_widget.tag_configure("md_h1", font=h1_font, foreground=palette.get("accent", "#d8dde8"), spacing1=6, spacing3=4)
    text_widget.tag_configure("md_h2", font=h2_font, foreground=palette.get("accent", "#d8dde8"), spacing1=5, spacing3=3)
    text_widget.tag_configure("md_h3", font=h3_font, foreground=palette.get("accent", "#d8dde8"), spacing1=4, spacing3=2)
    text_widget.tag_configure("md_bold", font=bold_font)
    text_widget.tag_configure("md_italic", font=italic_font)
    text_widget.tag_configure("md_inline_code", font=code_font, background=palette.get("panel_soft", "#3d404c"))
    text_widget.tag_configure("md_code_block", font=code_font, background="#1f222b", foreground="#e6e8ef", lmargin1=10, lmargin2=10, spacing1=5, spacing3=5)
    text_widget.tag_configure("md_quote", foreground=palette.get("muted", "#9ba0ad"), lmargin1=12, lmargin2=12)
    text_widget.tag_configure("md_list", lmargin1=12, lmargin2=24)
    text_widget.tag_configure("md_hr", foreground=palette.get("muted", "#9ba0ad"))
    text_widget.tag_configure("md_link", foreground="#7fb4ff", underline=True)
    text_widget.tag_configure("md_code_keyword", foreground="#c586c0")
    text_widget.tag_configure("md_code_string", foreground="#ce9178")
    text_widget.tag_configure("md_code_comment", foreground="#6a9955")
    text_widget.tag_configure("md_code_number", foreground="#b5cea8")
    text_widget.tag_configure("md_code_name", foreground="#9cdcfe")
    text_widget._screenshot_md_ready = True


def _apply_block_tags(text_widget: tk.Text) -> None:
    content = text_widget.get("1.0", "end-1c")
    in_code = False
    code_start = None
    for line_no, line in enumerate(content.splitlines(), start=1):
        start = f"{line_no}.0"
        end = f"{line_no}.end"
        stripped = line.strip()
        if stripped.startswith("```"):
            if not in_code:
                in_code = True
                code_start = start
            else:
                text_widget.tag_add("md_code_block", code_start, end)
                in_code = False
                code_start = None
            continue
        if in_code:
            continue
        if line.startswith("# "):
            text_widget.tag_add("md_h1", start, end)
        elif line.startswith("## "):
            text_widget.tag_add("md_h2", start, end)
        elif line.startswith("### "):
            text_widget.tag_add("md_h3", start, end)
        elif line.startswith(">"):
            text_widget.tag_add("md_quote", start, end)
        elif re.match(r"^\s*(?:[-*+]|\d+\.)\s+", line):
            text_widget.tag_add("md_list", start, end)
        elif re.match(r"^\s*---+\s*$", line):
            text_widget.tag_add("md_hr", start, end)
    if in_code and code_start:
        text_widget.tag_add("md_code_block", code_start, "end")


def _apply_inline_tags(text_widget: tk.Text) -> None:
    content = text_widget.get("1.0", "end-1c")
    for pattern, tag in (
        (r"\*\*[^*\n]+?\*\*", "md_bold"),
        (r"(?<!\*)\*[^*\n]+?\*(?!\*)", "md_italic"),
        (r"`[^`\n]+?`", "md_inline_code"),
        (r"\[[^\]\n]+?\]\([^)]+?\)", "md_link"),
    ):
        for match in re.finditer(pattern, content):
            start = f"1.0 + {match.start()} chars"
            end = f"1.0 + {match.end()} chars"
            if "md_code_block" not in text_widget.tag_names(start):
                text_widget.tag_add(tag, start, end)


def _apply_code_highlight(text_widget: tk.Text) -> None:
    if lex is None or get_lexer_by_name is None:
        return
    content = text_widget.get("1.0", "end-1c")
    for match in re.finditer(r"```([\w+-]*)\n(.*?)```", content, re.DOTALL):
        language = match.group(1) or "text"
        code = match.group(2)
        try:
            lexer = get_lexer_by_name(language)
        except Exception:
            continue
        offset = match.start(2)
        cursor = 0
        for token_type, token_text in lex(code, lexer):
            tag = _tag_for_token(token_type)
            if tag:
                start = f"1.0 + {offset + cursor} chars"
                end = f"1.0 + {offset + cursor + len(token_text)} chars"
                text_widget.tag_add(tag, start, end)
            cursor += len(token_text)


def _tag_for_token(token_type):
    if token_type in Keyword:
        return "md_code_keyword"
    if token_type in String:
        return "md_code_string"
    if token_type in Comment:
        return "md_code_comment"
    if token_type in Number:
        return "md_code_number"
    if token_type in Name:
        return "md_code_name"
    if token_type in Token:
        return None
    return None
