from __future__ import annotations

import os
from pathlib import Path
import subprocess


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "setup_pi_sdk.ps1"


def _write_cmd(path: Path, lines: list[str]) -> Path:
    path.write_bytes(("@echo off\r\n" + "\r\n".join(lines) + "\r\n").encode("utf-8"))
    return path


def _run(tmp_path: Path, *, node_version: str = "v22.22.2", npm_exit: int = 0, development: bool = False):
    import shutil, unittest
    if shutil.which("powershell.exe") is None or not (SCRIPT.parents[1] / "sidecars" / "pi-sdk").exists():
        raise unittest.SkipTest("Native Pi-RPC v2.1 build does not use sidecars/pi-sdk or powershell.exe unavailable")
    tools = tmp_path / "tool wrappers with spaces"
    tools.mkdir()
    node = _write_cmd(tools / "node fake.cmd", [f"echo {node_version}", "exit /b 0"])
    log = tmp_path / "npm-call.txt"
    npm = _write_cmd(
        tools / "npm fake.cmd",
        [f'echo %CD%^|%* > "{log}"', f"exit /b {npm_exit}"],
    )
    command = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(SCRIPT),
        "-NodeCommand",
        str(node),
        "-NpmCommand",
        str(npm),
    ]
    if development:
        command.append("-Development")
    result = subprocess.run(command, text=True, capture_output=True, timeout=30)
    return result, log


def test_setup_uses_sidecar_working_directory_and_runtime_npm_ci(tmp_path):
    result, log = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    call = log.read_text(encoding="utf-8").strip()
    cwd, args = call.split("|", 1)
    assert Path(cwd).resolve() == (SCRIPT.parents[1] / "sidecars" / "pi-sdk").resolve()
    assert args.strip() == "ci --omit=dev"
    assert "pi_sdk_health.py --human" in result.stdout


def test_setup_development_uses_plain_npm_ci(tmp_path):
    result, log = _run(tmp_path, development=True)
    assert result.returncode == 0, result.stderr
    assert log.read_text(encoding="utf-8").strip().split("|", 1)[1].strip() == "ci"


def test_setup_rejects_node_older_than_22(tmp_path):
    result, log = _run(tmp_path, node_version="v20.19.0")
    assert result.returncode == 2
    assert not log.exists()
    assert "Node.js 22+" in (result.stderr + result.stdout)


def test_setup_propagates_npm_failure(tmp_path):
    result, _ = _run(tmp_path, npm_exit=7)
    assert result.returncode == 7
