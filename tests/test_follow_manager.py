from pathlib import Path
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.utils.FollowManager import FollowManager  # noqa: E402


def write_prompts(path: Path, limit):
    path.write_text(
        "[settings]\n"
        f"follow_history_limit = {limit}\n\n"
        "[prompts]\n"
        'prompt_translate = """translate"""\n',
        encoding="utf-8",
    )


def test_follow_manager_reads_limit_and_builds_history(tmp_path):
    prompts_path = tmp_path / "prompts.toml"
    write_prompts(prompts_path, 2)

    manager = FollowManager(prompts_path)
    manager.add_exchange("u1", "a1")
    manager.add_exchange("u2", "a2")
    manager.add_exchange("u3", "a3")

    assert manager.get_state()["history_count"] == 0
    assert manager.build_history() == []

    state = manager.toggle()
    assert state["enabled"] is True
    assert state["history_count"] == 2

    assert manager.build_history() == [
        {"role": "user", "parts": ["u2"]},
        {"role": "model", "parts": ["a2"]},
        {"role": "user", "parts": ["u3"]},
        {"role": "model", "parts": ["a3"]},
    ]


def test_follow_manager_reload_settings_applies_new_limit(tmp_path):
    prompts_path = tmp_path / "prompts.toml"
    write_prompts(prompts_path, 1)

    manager = FollowManager(prompts_path)
    manager.toggle()
    manager.add_exchange("u1", "a1")
    manager.add_exchange("u2", "a2")

    assert manager.build_history() == [
        {"role": "user", "parts": ["u2"]},
        {"role": "model", "parts": ["a2"]},
    ]

    write_prompts(prompts_path, 2)
    manager.reload_settings(force=True)

    assert manager.get_state()["history_count"] == 2
    assert manager.build_history() == [
        {"role": "user", "parts": ["u1"]},
        {"role": "model", "parts": ["a1"]},
        {"role": "user", "parts": ["u2"]},
        {"role": "model", "parts": ["a2"]},
    ]
