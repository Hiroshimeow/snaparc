from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import urllib.request

from PIL import Image


EXPECTED_ICONS = {
    "icon.png", "icon_fix.png", "icon_pause", "spi.png",
    "sum.png", "trans.png", "voice_off.png", "voice_on.png",
}


def _runtime_root() -> Path:
    if getattr(sys, "frozen", False) and getattr(sys, "_MEIPASS", None):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parents[1]


def _check_assets(root: Path) -> dict:
    icons_dir = root / "src" / "icons"
    animate_dir = root / "src" / "animate"
    missing = sorted(EXPECTED_ICONS - {p.name for p in icons_dir.iterdir() if p.is_file()})
    if missing:
        raise RuntimeError(f"missing product icons: {missing}")
    icon_count = 0
    for path in icons_dir.iterdir():
        if path.is_file():
            with Image.open(path) as image:
                image.verify()
            icon_count += 1
    frames = list(animate_dir.rglob("*.png"))
    if len(frames) < 164:
        raise RuntimeError(f"animation frame count too small: {len(frames)}")
    for path in frames:
        with Image.open(path) as image:
            image.verify()
    return {"icons": icon_count, "animation_frames": len(frames)}


def _check_tk() -> str:
    import tkinter as tk
    root = tk.Tk()
    try:
        root.withdraw()
        root.update_idletasks()
        return str(root.tk.call("info", "patchlevel"))
    finally:
        root.destroy()


def _check_plugins(root: Path) -> dict:
    from src.services.radial_plugin_service import RadialPluginService
    with tempfile.TemporaryDirectory(prefix="snaparc-smoke-") as tmp:
        service = RadialPluginService(
            repo_root=root,
            yaml_path=root / "plugins.yaml",
            stc_json_path=Path(tmp) / "missing-config.yaml",
            param_store_path=Path(tmp) / "plugin-params.json",
        )
        plugins = service.list_plugins(reload_if_changed=False)
    ids = sorted({p.id for p in plugins})
    if len(ids) < 8:
        raise RuntimeError(f"too few bundled plugins: {ids}")
    return {"count": len(ids), "ids": ids}


def _check_web_assets_and_server(root: Path) -> dict:
    for name in ("index.html", "app.js", "app.css"):
        path = root / "src" / "gui" / "screenshot_chat" / "web_assets" / name
        if not path.is_file():
            raise RuntimeError(f"missing web asset: {name}")

    from src.gui.screenshot_chat.server import ScreenshotChatServer
    server = ScreenshotChatServer(object(), host="127.0.0.1", port=0)
    server.start()
    try:
        with urllib.request.urlopen(f"{server.base_url}/health", timeout=3) as response:
            health = json.loads(response.read().decode("utf-8"))
        if health.get("ok") is not True:
            raise RuntimeError("ScreenshotChat health endpoint failed")
        with urllib.request.urlopen(f"{server.base_url}/?t={server.token}", timeout=3) as response:
            page = response.read().decode("utf-8")
        if "SnapArc" not in page:
            raise RuntimeError("ScreenshotChat page identity missing")
        return {"base_host": "127.0.0.1", "page_bytes": len(page)}
    finally:
        server.stop()


def run_release_smoke(report_path: str | None = None) -> int:
    root = _runtime_root()
    report = {
        "product": "SnapArc",
        "version": "0.0.1",
        "frozen": bool(getattr(sys, "frozen", False)),
        "python": sys.version.split()[0],
        "runtime_root": str(root),
        "checks": {},
    }
    try:
        from src.platform.win32_tray import Icon  # noqa: F401
        import webview  # noqa: F401
        import soundcard  # noqa: F401
        import soundfile  # noqa: F401
        report["checks"]["imports"] = "ok"
        report["checks"]["tcl_tk"] = _check_tk()
        report["checks"]["assets"] = _check_assets(root)
        report["checks"]["plugins"] = _check_plugins(root)
        report["checks"]["screenshot_chat"] = _check_web_assets_and_server(root)
        report["status"] = "pass"
        code = 0
    except Exception as exc:
        report["status"] = "fail"
        report["error_type"] = type(exc).__name__
        report["error"] = str(exc)[:500]
        code = 1

    rendered = json.dumps(report, indent=2, ensure_ascii=False)
    if report_path:
        Path(report_path).write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return code


if __name__ == "__main__":
    raise SystemExit(run_release_smoke(os.environ.get("SNAPARC_RELEASE_SMOKE_REPORT")))
