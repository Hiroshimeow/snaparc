#!/usr/bin/env python3
# PLUGIN_META_JSON: {"id": "ocr_clipboard", "name": "Instant OCR", "icon": "🔍", "badge": "OCR", "description": "Extract structured text from clipboard image or files"}
"""
External Radial Wheel Plugin: Instant OCR
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--context-file", default=os.environ.get("STC_PLUGIN_CONTEXT_FILE", ""))
    args = parser.parse_args()

    ctx = {}
    if args.context_file and os.path.exists(args.context_file):
        with open(args.context_file, "r", encoding="utf-8") as f:
            ctx = json.load(f)

    repo_root = ctx.get("repo_root") or str(Path(__file__).resolve().parents[1])
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    files = list(ctx.get("files") or [])
    clip_text = str(ctx.get("clipboard_text") or "").strip()

    if files:
        try:
            from PIL import Image
            from src.core.ocr_pipeline import HybridOcrEngine

            engine = HybridOcrEngine()
            out_blocks = ["### 🔍 Instant OCR Result\n"]
            copied = []
            for fpath in files:
                if os.path.exists(fpath):
                    with Image.open(fpath) as im:
                        res = engine.extract(im)
                    text = res.full_text.strip() or "[No text detected]"
                    out_blocks.append(f"**{os.path.basename(fpath)}** (`{res.engine_used}`, `{res.elapsed_ms:.1f}ms`):\n```text\n{text}\n```")
                    copied.append(text)
            print(
                json.dumps(
                    {
                        "ok": True,
                        "title": "Instant OCR",
                        "markdown": "\n\n".join(out_blocks),
                        "copy_to_clipboard": "\n\n".join(copied),
                    },
                    ensure_ascii=False,
                )
            )
            return 0
        except Exception as exc:
            print(json.dumps({"ok": False, "title": "Instant OCR Error", "markdown": f"❌ `{exc}`"}))
            return 0

    if clip_text:
        print(
            json.dumps(
                {
                    "ok": True,
                    "title": "Instant OCR (Clipboard Text)",
                    "markdown": f"### 🔍 Clipboard Text Ready\n\n```text\n{clip_text}\n```",
                    "copy_to_clipboard": clip_text,
                },
                ensure_ascii=False,
            )
        )
        return 0

    print(
        json.dumps(
            {
                "ok": True,
                "title": "Instant OCR",
                "markdown": "### 🔍 Instant OCR\n\nCopy an image or snap a region first to extract text.",
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
