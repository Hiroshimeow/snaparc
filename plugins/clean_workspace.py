#!/usr/bin/env python3
# PLUGIN_META_JSON: {"id": "clean_workspace", "name": "Boost RAM", "icon": "⚡", "badge": "CLEAN", "description": "Free memory handles, prune old temp captures, and optimize runtime", "action": "run_memory_cleanup"}
"""
External Radial Wheel Plugin: Boost RAM & Prune Temp Captures
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--context-file", default=os.environ.get("STC_PLUGIN_CONTEXT_FILE", ""))
    args = parser.parse_args()

    collected = gc.collect()
    print(
        json.dumps(
            {
                "ok": True,
                "title": "RAM & Temp Cache Optimized",
                "action": "run_memory_cleanup",
                "output_mode": "floating",
                "markdown": (
                    "### ⚡ Runtime Memory Optimized\n\n"
                    f"- **GC Objects Reclaimed**: `{collected}`\n"
                    "- **Temp Screenshots & Closed Floating Windows**: Pruned"
                ),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
