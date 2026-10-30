#!/usr/bin/env python3
# PLUGIN_META_JSON: {"id": "summarize_doc", "name": "File Q&A", "icon": "🗂️", "badge": "POPUP", "description": "Open Unified Text/File Drag-and-Drop Quick Prompt Popup", "action": "open_quick_popup", "output_mode": "popup"}
"""
External Radial Wheel Plugin: Open Unified Quick Prompt Popup (Text & File Drag-and-Drop)
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

    print(
        json.dumps(
            {
                "ok": True,
                "title": "File & Text Quick Q&A Popup",
                "action": "open_quick_popup",
                "output_mode": "popup",
                "markdown": "🗂️ Opened Unified Text/File Quick Prompt Popup.",
                "files": ctx.get("files", []),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
