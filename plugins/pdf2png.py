#!/usr/bin/env python3
# PLUGIN_META_JSON: {"id": "pdf2png", "name": "PDF -> PNG", "icon": "📄", "badge": "PDF", "description": "Convert PDF file(s) into high-DPI PNG pages with custom --dpi and --output-path", "input_mode": "file", "file_extensions": [".pdf"], "params": [{"name": "dpi", "flag": "--dpi", "label": "Resolution DPI (--dpi)", "type": "int", "default": 150, "min": 72, "max": 600, "choices": ["72", "150", "200", "300", "600"]}, {"name": "output_path", "flag": "--output-path", "label": "Output Directory (default = root PDF)", "type": "path", "default": "{file_dir}"}]}
"""
External Radial Wheel Plugin: PDF -> PNG Converter (v2.4)
=========================================================
Supports both Mode 1 (click-drag slice -> pick/confirm PDF & params) and Mode 2
(hold Ctrl+Alt, drag a PDF file onto the PDF -> PNG slice, confirm --dpi and
--output-path in the popup, and convert).
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


def convert_pdf_to_png(pdf_path: Path, dpi: int = 150, output_path: str = "") -> dict:
    dpi = max(36, min(1200, int(dpi or 150)))
    scale = max(0.5, float(dpi) / 72.0)

    clean_out = str(output_path or "").strip().strip('"').strip("'")
    if clean_out and clean_out not in ("{file_dir}", "{root_pdf}", "{input_dir}", "."):
        out_dir = Path(clean_out)
    else:
        out_dir = pdf_path.parent

    generated: list[str] = []
    pdf = None

    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        import pypdfium2 as pdfium  # type: ignore

        pdf = pdfium.PdfDocument(str(pdf_path))
        for idx in range(len(pdf)):
            page = pdf[idx]
            try:
                bitmap = page.render(scale=scale)
                try:
                    pil_image = bitmap.to_pil()
                    try:
                        out_file = out_dir / f"{pdf_path.stem}_page_{idx + 1:03d}.png"
                        pil_image.save(out_file, format="PNG", dpi=(dpi, dpi))
                        generated.append(str(out_file))
                    finally:
                        pil_image.close()
                finally:
                    bitmap.close()
            finally:
                page.close()
        if not generated:
            raise ValueError("PDF contains no renderable pages")
        return {
            "ok": True,
            "out_dir": str(out_dir),
            "files": generated,
            "dpi": dpi,
            "engine": "pypdfium2",
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "out_dir": str(out_dir),
            "files": generated,
            "dpi": dpi,
        }
    finally:
        if pdf is not None:
            pdf.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert PDF file(s) into high-DPI PNG images.")
    parser.add_argument("--input", "-i", default="", help="Input PDF file path")
    parser.add_argument("--dpi", type=int, default=150, help="Render resolution in DPI (default: 150)")
    parser.add_argument(
        "--output-path",
        "--output_path",
        dest="output_path",
        default="",
        help="Output folder path (default = root directory of input PDF)",
    )
    parser.add_argument("--context-file", default=os.environ.get("STC_PLUGIN_CONTEXT_FILE", ""))
    parser.add_argument("files", nargs="*", help="Optional positional PDF file paths")
    args, _unknown = parser.parse_known_args()

    ctx: dict = {}
    if args.context_file and os.path.exists(args.context_file):
        try:
            with open(args.context_file, "r", encoding="utf-8") as f:
                ctx = json.load(f)
        except Exception:
            ctx = {}

    params = ctx.get("params") if isinstance(ctx.get("params"), dict) else {}
    dpi_val = int(params.get("dpi") or args.dpi or 150)
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
    if clip.lower().endswith(".pdf") and os.path.exists(clip):
        candidates.append(clip)

    seen = set()
    pdf_files: list[Path] = []
    for raw_p in candidates:
        clean_p = str(raw_p or "").strip().strip('"').strip("'")
        if clean_p and clean_p.lower().endswith(".pdf") and os.path.exists(clean_p):
            resolved = str(Path(clean_p).resolve())
            if resolved not in seen:
                seen.add(resolved)
                pdf_files.append(Path(resolved))

    if not pdf_files:
        payload = {
            "ok": False,
            "title": "PDF -> PNG Converter",
            "markdown": (
                "### 📄 PDF -> PNG Plugin\n\n"
                "Chưa có file `.pdf` đầu vào. Hãy kéo thả file `.pdf` vào bánh xe (**PDF -> PNG**) "
                "hoặc chọn file `.pdf` trong hộp thoại tham số (`--dpi`, `--output-path`)."
            ),
        }
        print(json.dumps(payload, ensure_ascii=False))
        return 0

    all_generated: list[str] = []
    all_ok = True
    lines = [f"### 📄 PDF -> PNG Conversion (`{dpi_val} DPI`)\n"]
    for pdf_path in pdf_files:
        res = convert_pdf_to_png(pdf_path, dpi=dpi_val, output_path=output_path_val)
        if res.get("ok"):
            files = res.get("files", [])
            all_generated.extend(files)
            lines.append(
                f"- **{pdf_path.name}**: `{len(files)}` trang PNG (`{res.get('dpi')} DPI`) -> `{res['out_dir']}` ({res.get('engine')})"
            )
        else:
            all_ok = False
            all_generated.extend(res.get("files", []))
            lines.append(f"- **{pdf_path.name}**: ❌ Lỗi (`{res.get('error')}`)")

    print(
        json.dumps(
            {
                "ok": all_ok,
                "title": f"PDF -> PNG ({dpi_val} DPI)",
                "markdown": "\n".join(lines),
                "generated_files": all_generated,
                "copy_to_clipboard": all_generated[0] if all_generated else None,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
