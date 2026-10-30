from __future__ import annotations

import html
import os
import re
import time


MERMAID_FENCE_RE = re.compile(r"```mermaid\s*\n(.*?)```", re.IGNORECASE | re.DOTALL)


def extract_mermaid_blocks(text: str) -> list[str]:
    return [match.group(1).strip() for match in MERMAID_FENCE_RE.finditer(text or "") if match.group(1).strip()]


def write_mermaid_preview(project_root: str, diagram: str) -> str:
    preview_dir = os.path.join(project_root, "data", "mermaid_preview")
    os.makedirs(preview_dir, exist_ok=True)
    filename = f"mermaid_{time.strftime('%Y%m%d_%H%M%S')}_{int(time.time() * 1000) % 1000:03d}.html"
    path = os.path.join(preview_dir, filename)
    escaped = html.escape(diagram or "")
    document = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Mermaid preview</title>
  <script type="module">
    import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs';
    mermaid.initialize({{ startOnLoad: true, theme: 'dark' }});
  </script>
  <style>
    body {{ margin: 0; padding: 24px; background: #1f222b; color: #f2f3f7; font-family: Segoe UI, sans-serif; }}
    .mermaid {{ background: #282b36; border-radius: 12px; padding: 18px; }}
  </style>
</head>
<body>
  <pre class="mermaid">{escaped}</pre>
</body>
</html>
"""
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(document)
    return path
