#!/usr/bin/env python3
# PLUGIN_META_JSON: {"id": "extract_table", "name": "Table -> MD/CSV", "icon": "📊", "badge": "TABLE", "description": "Convert TSV/CSV or aligned text into Markdown & CSV tables"}
"""
External Radial Wheel Plugin: Table -> Markdown / CSV Converter
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys


def text_to_markdown_table(raw: str) -> tuple[str, str]:
    lines = [ln.strip() for ln in raw.strip().splitlines() if ln.strip()]
    if not lines:
        return "", ""

    rows = []
    for ln in lines:
        if "\t" in ln:
            cols = [c.strip() for c in ln.split("\t")]
        elif "|" in ln:
            cols = [c.strip() for c in ln.strip("|").split("|")]
        elif "," in ln and len(ln.split(",")) > 1:
            cols = next(csv.reader([ln]))
        else:
            cols = [c.strip() for c in re.split(r"\s{2,}", ln) if c.strip()]
        if cols:
            rows.append(cols)

    if not rows:
        return "", ""

    width = max(len(r) for r in rows)
    norm_rows = [r + [""] * (width - len(r)) for r in rows]

    header = norm_rows[0]
    sep = ["---"] * width
    md_lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(sep) + " |",
    ]
    for r in norm_rows[1:]:
        md_lines.append("| " + " | ".join(r) + " |")

    csv_buf = io.StringIO()
    writer = csv.writer(csv_buf)
    writer.writerows(norm_rows)
    return "\n".join(md_lines), csv_buf.getvalue().strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--context-file", default=os.environ.get("STC_PLUGIN_CONTEXT_FILE", ""))
    args = parser.parse_args()

    ctx = {}
    if args.context_file and os.path.exists(args.context_file):
        with open(args.context_file, "r", encoding="utf-8") as f:
            ctx = json.load(f)

    raw = str(ctx.get("clipboard_text") or "").strip()
    md_table, csv_text = text_to_markdown_table(raw)
    if not md_table:
        print(
            json.dumps(
                {
                    "ok": True,
                    "title": "Table -> MD/CSV",
                    "markdown": "### 📊 Table -> MD/CSV\n\nCopy tabular lines (TSV, CSV, or space-aligned columns) to clipboard first.",
                },
                ensure_ascii=False,
            )
        )
        return 0

    print(
        json.dumps(
            {
                "ok": True,
                "title": "Table -> Markdown & CSV",
                "markdown": f"### 📊 Markdown Table\n\n{md_table}\n\n### CSV Output\n```csv\n{csv_text}\n```",
                "copy_to_clipboard": md_table,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
