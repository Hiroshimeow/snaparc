from pathlib import Path


def test_legacy_pi_rpc_sidecar_dependency_is_absent_from_project_and_lock():
    root = Path(__file__).resolve().parents[1]
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    lock = (root / "uv.lock").read_text(encoding="utf-8")

    assert "pi-rpc-sidecar" not in pyproject
    assert "pi_rpc_sidecar" not in pyproject
    assert "pi-rpc-sidecar" not in lock
    assert "pi_rpc_sidecar" not in lock
