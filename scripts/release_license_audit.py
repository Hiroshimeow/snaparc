from __future__ import annotations

from importlib import metadata
from pathlib import Path
import re
import tomllib

REQUIRED_NOTICE_PACKAGES = {"tomli"}
CONDITIONAL_LICENSES = {"tomli": "MIT"}


def dep_name(requirement: str) -> str:
    return re.split(r"[<>=!~;\[]", requirement, maxsplit=1)[0].strip()


def main() -> int:
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    failures: list[str] = []

    lock_text = Path("uv.lock").read_text(encoding="utf-8")
    allowed_sources = {
        'source = { registry = "https://pypi.org/simple" }',
        'source = { virtual = "." }',
    }
    source_lines = {
        line.strip() for line in lock_text.splitlines() if line.strip().startswith("source = ")
    }
    unexpected_sources = sorted(source_lines - allowed_sources)
    if unexpected_sources:
        failures.append(f"uv.lock contains non-public/unexpected package sources: {unexpected_sources}")
    if project["project"].get("license") != "MIT":
        failures.append("pyproject project.license must be MIT")
    if not Path("LICENSE").is_file() or "MIT License" not in Path("LICENSE").read_text(encoding="utf-8-sig"):
        failures.append("MIT LICENSE file missing")
    for required in ("TRADEMARKS.md", "ASSET_POLICY.md", "THIRD_PARTY_NOTICES.md"):
        if not Path(required).is_file():
            failures.append(f"missing release policy: {required}")

    notices = Path("THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    direct = [dep_name(x) for x in project["project"]["dependencies"]]
    unresolved: list[str] = []
    detected: list[tuple[str, str, str]] = []
    for name in direct:
        try:
            dist = metadata.distribution(name)
            expression = (dist.metadata.get("License-Expression") or dist.metadata.get("License") or "").strip()
            classifiers = [
                item.rsplit("::", 1)[-1].strip()
                for item in dist.metadata.get_all("Classifier", [])
                if item.startswith("License ::")
            ]
            license_text = expression or ", ".join(classifiers)
            version = dist.version
        except metadata.PackageNotFoundError:
            license_text = CONDITIONAL_LICENSES.get(name, "")
            version = "conditional/not-installed"
        if not license_text:
            unresolved.append(name)
        detected.append((name, version, license_text))
        if name in REQUIRED_NOTICE_PACKAGES and name not in notices:
            failures.append(f"THIRD_PARTY_NOTICES.md missing direct dependency {name}")

    if unresolved:
        failures.append(f"direct dependencies with unresolved license metadata: {sorted(unresolved)}")

    print(f"direct_dependencies={len(direct)} dependency_sources=pypi.org+local-project")
    for name, version, license_text in detected:
        if any(tag in license_text.upper() for tag in ("GPL", "AGPL", "LGPL")):
            print(f"COPYLEFT-REVIEW: {name} {version} -> {license_text}")
    if failures:
        for issue in failures:
            print(f"ERROR: {issue}")
        return 1
    print("source-release license audit passed (binary redistribution remains separate)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
