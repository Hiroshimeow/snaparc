#!/usr/bin/env python3
# PLUGIN_META_JSON: {"id": "script_a", "name": "Run a.py", "icon": "🚀", "badge": "UV/PY", "description": "Run uv run a.py --root <val> --host <val> with saved last-used parameters", "input_mode": "none", "runner": "uv", "params": [{"name": "root", "flag": "--root", "label": "Root Token / Path (--root)", "type": "string", "default": "11235813x"}, {"name": "host", "flag": "--host", "label": "Bind Host (--host)", "type": "string", "default": "0.0.0.0"}, {"name": "port", "flag": "--port", "label": "Bind Port (--port)", "type": "int", "default": 8000}]}
"""
Sample Parameterized CLI Script (`plugins/a.py`) — v2.4
=======================================================
Demonstrates running a CLI script or app from the Radial Wheel (`Ctrl + Alt + Drag`)
equivalent to:
    uv run plugins/a.py --root 11235813x --host 0.0.0.0 --port 8000

Any script using `argparse.ArgumentParser` in `plugins/` is automatically inspected
by `RadialPluginService` so its `--flags` and defaults appear in the parameter popup
with last-used value persistence.
"""

from __future__ import annotations

import argparse
import json
import sys
import time


def main() -> int:
    parser = argparse.ArgumentParser(description="Parameterized app/script launcher (a.py)")
    parser.add_argument("--root", default="11235813x", help="Root key or directory")
    parser.add_argument("--host", default="0.0.0.0", help="Server host address")
    parser.add_argument("--port", type=int, default=8000, help="Server port")
    args, extra = parser.parse_known_args()

    cmd_display = f"uv run plugins/a.py --root {args.root} --host {args.host} --port {args.port}"
    if extra:
        cmd_display += " " + " ".join(extra)

    payload = {
        "ok": True,
        "title": f"a.py ({args.host}:{args.port})",
        "markdown": (
            "### 🚀 Script `a.py` Executed Successfully\n\n"
            f"- **Command**: `{cmd_display}`\n"
            f"- **`--root`**: `{args.root}`\n"
            f"- **`--host`**: `{args.host}`\n"
            f"- **`--port`**: `{args.port}`\n"
            f"- **Timestamp**: `{time.strftime('%Y-%m-%d %H:%M:%S')}`"
        ),
        "copy_to_clipboard": cmd_display,
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
