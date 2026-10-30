#!/usr/bin/env python3
# PLUGIN_META_JSON: {"id": "png_resize", "name": "Image Resize", "icon": "🖼️", "badge": "RESIZE", "description": "Resize PNG/JPG/WebP image by scale factor (--size 0.1 to 3.0 = 10% to 300%)", "input_mode": "file", "file_extensions": [".png", ".jpg", ".jpeg", ".webp", ".bmp"], "params": [{"name": "size", "flag": "--size", "label": "Scale Factor (--size: 0.1=10% -> 3.0=300%)", "type": "float", "default": 0.5, "min": 0.1, "max": 3.0, "choices": ["0.1", "0.25", "0.5", "0.75", "1.0", "1.5", "2.0", "3.0"]}, {"name": "output_path", "flag": "--output-path", "label": "Output Directory (default = root image)", "type": "path", "default": "{file_dir}"}]}
"""
External Radial Wheel Plugin: Image Resizer (`plugins/png_resize.py`) — v2.4
============================================================================
Resizes dragged/selected image files (`.png`, `.jpg`, `.jpeg`, `.webp`, `.bmp`)
by `--size` factor (`0.1` => 10% up to `3.0` => 300%) and writes the output to
`--output-path` (defaulting to the source image's parent directory).
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from PIL import Image

SUPPORTED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}


def parse_scale_factor(raw_val: str | float | int) -> float:
    """Parse scale factor (`0.1` -> `3.0`, or percentage string like `50%` -> `0.5`)."""
    if isinstance(raw_val, (int, float)):
        val = float(raw_val)
    else:
        s = str(raw_val or "0.5").strip().replace(",", ".")
        if s.endswith("%"):
            try:
                val = float(s[:-1].strip()) / 100.0
            except ValueError:
                val = 0.5
        else:
            try:
                val = float(s)
                # If user typed e.g. 50 or 150 meaning percentage, convert if > 3.0
                if val > 3.0 and val <= 300.0:
                    val = val / 100.0
            except ValueError:
                val = 0.5
    return max(0.1, min(3.0, val))


def resize_image_file(img_path: Path, scale: float = 0.5, output_path: str = "") -> dict:
    scale = parse_scale_factor(scale)
    pct = int(round(scale * 100))

    clean_out = str(output_path or "").strip().strip('"').strip("'")
    if clean_out and clean_out not in ("{file_dir}", "."):
        out_target = Path(clean_out)
        if out_target.suffix.lower() in SUPPORTED_IMAGE_EXTS:
            out_Target_file = out_target
            out_Target_file.parent.mkdir(parents=True, exist_ok=True)
        else:
            out_target.mkdir(parents=True, exist_ok=True)
            out_Target_file = out_target / f"{img_path.stem}_resized_{pct}pct{img_path.suffix}"
    else:
        out_Target_file = img_path.parent / f"{img_path.stem}_resized_{pct}pct{img_path.suffix}"

    try:
        with Image.open(img_path) as im:
            orig_w, orig_h = im.size
            new_w = max(1, int(round(orig_w * scale)))
            new_h = max(1, int(round(orig_h * scale)))
            resample = getattr(Image, "Resampling", Image).LANCZOS
            resized = im.resize((new_w, new_h), resample=resample)
            resized.save(out_Target_file)
            resized.close()
        return {
            "ok": True,
            "input": str(img_path),
            "output": str(out_Target_file),
            "orig_size": f"{orig_w}x{orig_h}",
            "new_size": f"{new_w}x{new_h}",
            "scale": scale,
            "percent": pct,
        }
    except Exception as exc:
        return {"ok": False, "input": str(img_path), "error": str(exc)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Resize PNG/image files by scale factor (0.1 to 3.0).")
    parser.add_argument("--input", "-i", default="", help="Input image file path")
    parser.add_argument(
        "--size",
        "-s",
        default="0.5",
        help="Resize multiplier from 0.1 (10%%) to 3.0 (300%%), default 0.5",
    )
    parser.add_argument(
        "--output-path",
        "--output_path",
        dest="output_path",
        default="",
        help="Output folder or file path (default = root directory of input image)",
    )
    parser.add_argument("--context-file", default=os.environ.get("STC_PLUGIN_CONTEXT_FILE", ""))
    parser.add_argument("files", nargs="*", help="Optional positional image file paths")
    args, _unknown = parser.parse_known_args()

    ctx: dict = {}
    if args.context_file and os.path.exists(args.context_file):
        try:
            with open(args.context_file, "r", encoding="utf-8") as f:
                ctx = json.load(f)
        except Exception:
            ctx = {}

    params = ctx.get("params") if isinstance(ctx.get("params"), dict) else {}
    raw_size = params.get("size") if params.get("size") not in (None, "") else args.size
    scale = parse_scale_factor(raw_size)
    output_path_val = str(params.get("output_path") or args.output_path or "").strip()

    candidates: list[str] = []
    if args.input:
        candidates.append(args.input)
    for pos_file in args.files or []:
        candidates.append(pos_file)
    for cf in ctx.get("files") or []:
        candidates.append(str(cf))
    if params.get("input"):
        candidates.append(str(params["input"]))

    clip = str(ctx.get("clipboard_text") or "").strip().strip('"').strip("'")
    if clip and Path(clip).suffix.lower() in SUPPORTED_IMAGE_EXTS and os.path.exists(clip):
        candidates.append(clip)

    seen = set()
    img_files: list[Path] = []
    for raw_p in candidates:
        clean_p = str(raw_p or "").strip().strip('"').strip("'")
        if clean_p and Path(clean_p).suffix.lower() in SUPPORTED_IMAGE_EXTS and os.path.exists(clean_p):
            resolved = str(Path(clean_p).resolve())
            if resolved not in seen:
                seen.add(resolved)
                img_files.append(Path(resolved))

    if not img_files:
        payload = {
            "ok": False,
            "title": "Image Resize (png_resize)",
            "markdown": (
                "### 🖼️ Image Resize (`png_resize`)\n\n"
                "Chưa có file ảnh (`.png`, `.jpg`, `.webp`) đầu vào. Hãy kéo thả file ảnh vào "
                "bánh xe (**Image Resize**) hoặc chọn file trong hộp thoại tham số (`--size 0.1 -> 3.0`)."
            ),
        }
        print(json.dumps(payload, ensure_ascii=False))
        return 0

    generated: list[str] = []
    pct = int(round(scale * 100))
    lines = [f"### 🖼️ Image Resize Complete (`--size {scale:.2f}` = `{pct}%`)\n"]
    for img_path in img_files:
        res = resize_image_file(img_path, scale=scale, output_path=output_path_val)
        if res.get("ok"):
            out_file = res["output"]
            generated.append(out_file)
            lines.append(
                f"- **{img_path.name}**: `{res['orig_size']}` → **`{res['new_size']}`** (`{res['percent']}%`) saved to `{out_file}`"
            )
        else:
            lines.append(f"- **{img_path.name}**: ❌ Lỗi (`{res.get('error')}`)")

    print(
        json.dumps(
            {
                "ok": True,
                "title": f"Image Resize ({pct}%)",
                "markdown": "\n".join(lines),
                "generated_files": generated,
                "copy_to_clipboard": generated[0] if generated else None,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
