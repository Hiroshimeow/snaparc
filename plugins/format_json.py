#!/usr/bin/env python3
# PLUGIN_META_JSON: {"id": "format_json", "name": "Format JSON", "icon": "🧩", "badge": "JSON", "description": "Validate, repair, and pretty-print JSON/YAML from clipboard"}
"""
External Radial Wheel Plugin: Format & Validate JSON / Config
"""

from __future__ import annotations

import argparse
import json
import os
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--context-file", default=os.environ.get("STC_PLUGIN_CONTEXT_FILE", ""))
    args = parser.parse_args()

    ctx = {}
    if args.context_file and os.path.exists(args.context_file):
        with open(args.context_file, "r", encoding="utf-8") as f:
            ctx = json.load(f)

    raw = str(ctx.get("clipboard_text") or "").strip()
    if not raw:
        print(
            json.dumps(
                {
                    "ok": True,
                    "title": "Format JSON",
                    "markdown": "### 🧩 Format JSON\n\nClipboard is empty. Copy a JSON snippet and run again.",
                },
                ensure_ascii=False,
            )
        )
        return 0

    try:
        parsed = json.loads(raw)
        pretty = json.dumps(parsed, ensure_ascii=False, indent=2, sort_keys=True)
        print(
            json.dumps(
                {
                    "ok": True,
                    "title": "Formatted JSON",
                    "markdown": f"### 🧩 Valid JSON (Formatted & Copied)\n\n```json\n{pretty}\n```",
                    "copy_to_clipboard": pretty,
                },
                ensure_ascii=False,
            )
        )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "title": "Invalid JSON",
                    "markdown": f"### ⚠️ JSON Parse Error\n\n- **Error**: `{exc}`\n\n```text\n{raw[:1000]}\n```",
                },
                ensure_ascii=False,
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
