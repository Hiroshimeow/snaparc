from pathlib import Path



from src.utils.prompt_store import save_prompt_to_toml


def write_prompts(path: Path, prompt_value: str = "old value"):
    path.write_text(
        "[settings]\n"
        "follow_history_limit = 10\n\n"
        "[prompts]\n"
        f'prompt_fix_grammar = """\n{prompt_value}\n"""\n'
        'prompt_translate = """\ntranslate\n"""\n',
        encoding="utf-8",
    )


def test_save_prompt_to_toml_supports_triple_quotes_inside_prompt(tmp_path):
    prompts_path = tmp_path / "prompts.toml"
    write_prompts(prompts_path)
    new_prompt = 'Explain this code block:\n"""\nprint("hello")\n"""\nDone.'

    save_ok = save_prompt_to_toml(prompts_path, "prompt_fix_grammar", new_prompt)

    assert save_ok is True

    try:
        import tomllib
    except ImportError:
        import tomli as tomllib

    payload = tomllib.loads(prompts_path.read_text(encoding="utf-8"))
    assert payload["prompts"]["prompt_fix_grammar"] == new_prompt
    assert payload["prompts"]["prompt_translate"] == "translate\n"


def test_save_prompt_to_toml_returns_false_for_invalid_existing_toml(tmp_path):
    prompts_path = tmp_path / "prompts.toml"
    broken_content = (
        "[settings]\n"
        "follow_history_limit = 10\n\n"
        "[prompts]\n"
        'prompt_fix_grammar = """\nhello\n"""\n'
        "this is not valid toml\n"
    )
    prompts_path.write_text(broken_content, encoding="utf-8")

    save_ok = save_prompt_to_toml(prompts_path, "prompt_fix_grammar", "new value")

    assert save_ok is False
    assert prompts_path.read_text(encoding="utf-8") == broken_content
