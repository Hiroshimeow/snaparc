from __future__ import annotations

import subprocess
from pathlib import PurePosixPath

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".ico"}
ALLOWED_PRODUCT_ASSET_ROOTS = ("src/animate/", "src/icons/")
FORBIDDEN_TRACKED_PREFIXES = (
    "screenshots/", "src/screenshots/", "captures/", "recordings/", "data/",
    "logs/", "tests_runtime/",
)
FORBIDDEN_TRACKED_NAMES = {
    ".env", "config.yaml", "oath_token_cache.json",
}
RUNTIME_MEDIA_MARKERS = (
    "screenshot_", "clipboard_", "thumb_", "recording_",
)


def tracked_files() -> list[str]:
    raw = subprocess.check_output(["git", "ls-files", "-z"])
    return [p.decode("utf-8") for p in raw.split(b"\0") if p]


def main() -> int:
    files = tracked_files()
    failures: list[str] = []
    product_assets: list[str] = []

    for raw in files:
        path = raw.replace("\\", "/")
        name = PurePosixPath(path).name.lower()
        lower = path.lower()

        if any(lower == p.rstrip("/") or lower.startswith(p) for p in FORBIDDEN_TRACKED_PREFIXES):
            failures.append(f"runtime/private path tracked: {path}")
        if name in FORBIDDEN_TRACKED_NAMES or name.endswith(".dpapi.json"):
            failures.append(f"private config/token file tracked: {path}")

        suffix = PurePosixPath(lower).suffix
        if suffix in IMAGE_SUFFIXES or lower == "src/icons/icon_pause":
            if any(lower.startswith(root) for root in ALLOWED_PRODUCT_ASSET_ROOTS):
                product_assets.append(path)
            elif any(marker in name for marker in RUNTIME_MEDIA_MARKERS):
                failures.append(f"runtime screenshot/media tracked: {path}")
            else:
                failures.append(f"unclassified image asset requires provenance: {path}")

    if not any(p.startswith("src/animate/") for p in product_assets):
        failures.append("no tray animation assets tracked")
    required_icons = {
        "src/icons/icon.png", "src/icons/icon_fix.png", "src/icons/icon_pause",
        "src/icons/spi.png", "src/icons/sum.png", "src/icons/trans.png",
        "src/icons/voice_off.png", "src/icons/voice_on.png",
    }
    missing_icons = sorted(required_icons - set(product_assets))
    if missing_icons:
        failures.append(f"missing product icons: {missing_icons}")

    print(f"tracked_files={len(files)} product_image_assets={len(product_assets)}")
    print("asset_policy=src/animate + src/icons only; user/runtime media forbidden")
    if failures:
        for issue in failures:
            print(f"ERROR: {issue}")
        return 1
    print("release asset audit passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
