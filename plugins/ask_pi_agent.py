#!/usr/bin/env python3
# PLUGIN_META_JSON: {"id": "ask_pi_agent", "name": "Pi Sidecar", "icon": "🤖", "badge": "AGENT", "description": "Open Pi Sidecar Multi-Turn Chat Screen with current selection or files", "action": "open_pi_chat", "output_mode": "chat_screen"}
"""
External Radial Wheel Plugin: Escalate to Pi Sidecar Multi-Turn Chat Screen
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
                "title": "Pi Sidecar Agent",
                "action": "open_pi_chat",
                "output_mode": "chat_screen",
                "markdown": "🤖 Opening Pi Sidecar Chat Screen...",
                "clipboard_text": ctx.get("clipboard_text", ""),
                "files": ctx.get("files", []),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
