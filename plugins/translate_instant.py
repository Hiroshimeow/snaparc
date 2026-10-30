#!/usr/bin/env python3
# PLUGIN_META_JSON: {"id": "translate_instant", "name": "Quick Trans", "icon": "🌐", "badge": "TRANS", "description": "Instant bilingual translation helper"}
"""
External Radial Wheel Plugin: Quick Bilingual Translation
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
    print(
        json.dumps(
            {
                "ok": True,
                "title": "Quick Translate",
                "action": "translate_clipboard",
                "output_mode": "floating",
                "markdown": (
                    f"### 🌐 Quick Translation Queued\n\n```text\n{raw[:1200] or '[Clipboard empty]'}\n```"
                ),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
