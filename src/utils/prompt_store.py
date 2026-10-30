import json
from pathlib import Path
from src.utils.yaml_io import dump_yaml_or_json, load_yaml_or_json

try:
    import tomllib
except ImportError:  # pragma: no cover
    import tomli as tomllib


DEFAULT_PROMPTS = {
    "prompt_translate": (
        "Dịch chính xác nội dung trong ảnh hoặc văn bản sang tiếng Việt tự nhiên, rõ ràng. "
        "Nếu nội dung gốc là tiếng Việt, hãy dịch sang tiếng Anh chuẩn xác. Chỉ trả về kết quả dịch."
    ),
    "prompt_summarize": "Tóm tắt ngắn gọn, súc tích các ý chính của nội dung theo dạng gạch đầu dòng cấu trúc.",
    "prompt_fix_grammar": "Sửa lỗi ngữ pháp, chính tả và tối ưu hóa câu chữ thành văn phong tự nhiên, chuyên nghiệp hoặc prompt rõ ràng.",
    "prompt_extract_text": "Trích xuất chính xác toàn bộ văn bản (OCR) trong ảnh, giữ nguyên định dạng gốc và bảng biểu Markdown nếu có.",
    "prompt_expert": "Phân tích kỹ thuật chuyên sâu nội dung, đoạn code hoặc thông báo lỗi và đề xuất giải pháp cụ thể.",
}


def _resolve_target_path(config_path):
    path = Path(config_path)
    if not path.exists() and path.name == "prompts.toml":
        yaml_candidate = path.with_name("config.yaml")
        if yaml_candidate.exists():
            return yaml_candidate
    return path


def load_prompt_payload(config_path="config.yaml"):
    path = _resolve_target_path(config_path)
    if not path.exists():
        return {"prompts": dict(DEFAULT_PROMPTS), "settings": {"follow_history_limit": 10}}

    if path.suffix.lower() in (".yaml", ".yml", ".json"):
        payload = load_yaml_or_json(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return {"prompts": dict(DEFAULT_PROMPTS), "settings": {"follow_history_limit": 10}}
        if "prompts" not in payload or not isinstance(payload.get("prompts"), dict):
            payload["prompts"] = dict(DEFAULT_PROMPTS)
        return payload

    with path.open("rb") as handle:
        payload = tomllib.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("Prompt configuration must contain a top-level table.")
    return payload


def save_prompt_to_toml(config_path, prompt_key, prompt_text):
    path = _resolve_target_path(config_path)
    if path.suffix.lower() in (".yaml", ".yml", ".json") or not path.exists() and path.name == "config.yaml":
        try:
            payload = load_yaml_or_json(path.read_text(encoding="utf-8")) if path.exists() else {}
            if not isinstance(payload, dict):
                payload = {}
            prompts = payload.get("prompts")
            if not isinstance(prompts, dict):
                prompts = dict(DEFAULT_PROMPTS)
                payload["prompts"] = prompts
            prompts[prompt_key] = prompt_text
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                dump_yaml_or_json(payload, as_json=path.suffix.lower() == ".json"),
                encoding="utf-8",
            )
            return True
        except Exception:
            return False

    try:
        payload = load_prompt_payload(path)
    except Exception:
        return False

    prompts = payload.get("prompts")
    if not isinstance(prompts, dict):
        return False

    prompts[prompt_key] = prompt_text

    try:
        path.write_text(_dump_toml(payload), encoding="utf-8")
        return True
    except Exception:
        return False


def _dump_toml(payload):
    scalar_lines = []
    section_lines = []

    for key, value in payload.items():
        if isinstance(value, dict):
            if section_lines:
                section_lines.append("")
            section_lines.extend(_dump_table(key, value))
        else:
            scalar_lines.append(f"{key} = {_format_value(value)}")

    lines = list(scalar_lines)
    if scalar_lines and section_lines:
        lines.append("")
    lines.extend(section_lines)
    return "\n".join(lines) + "\n"


def _dump_table(table_name, table_value):
    lines = [f"[{table_name}]"]
    nested_tables = []

    for key, value in table_value.items():
        if isinstance(value, dict):
            nested_tables.append((key, value))
            continue
        lines.append(f"{key} = {_format_value(value)}")

    for key, value in nested_tables:
        lines.append("")
        lines.extend(_dump_table(f"{table_name}.{key}", value))

    return lines


def _format_value(value):
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(_format_value(item) for item in value) + "]"
    raise TypeError(f"Unsupported TOML value type: {type(value).__name__}")
