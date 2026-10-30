"""Zero-Dependency YAML + JSON Reader/Writer (`src/utils/yaml_io.py`) — v2.1.

Uses `PyYAML` (`yaml.safe_load` / `yaml.safe_dump`) when installed, and includes a
complete pure-Python block-YAML parser & emitter fallback so `config.yaml` works
out-of-the-box in every Python environment (including `uv run` with locked wheels).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore
except ImportError:
    yaml = None


def load_yaml_or_json(text: str | Path) -> dict[str, Any]:
    if isinstance(text, Path):
        text = text.read_text(encoding="utf-8")
    stripped = (text or "").strip()
    if not stripped:
        return {}
    if stripped.startswith("{"):
        data = json.loads(stripped)
        return data if isinstance(data, dict) else {}
    if yaml is not None:
        try:
            data = yaml.safe_load(stripped)
            return data if isinstance(data, dict) else {}
        except Exception:
            pass
    return _parse_block_yaml(stripped)


def dump_yaml_or_json(data: dict[str, Any], as_json: bool = False) -> str:
    if as_json:
        return json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if yaml is not None:
        try:
            return yaml.safe_dump(
                data,
                allow_unicode=True,
                sort_keys=False,
                default_flow_style=False,
                width=120,
            )
        except Exception:
            pass
    return _emit_block_yaml(data, indent=0) + "\n"


def _emit_scalar(val: Any) -> str:
    if val is None:
        return "null"
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, (int, float)):
        return str(val)
    s = str(val)
    if (
        not s
        or "\n" in s
        or ":" in s
        or "#" in s
        or s.startswith(("<", "[", "{", "*", "&", "!", "%", "@", "`", " ", "-", "?"))
        or s.lower() in ("true", "false", "null", "yes", "no", "on", "off")
    ):
        return json.dumps(s, ensure_ascii=False)
    return s


def _emit_block_yaml(obj: Any, indent: int = 0) -> str:
    pad = "  " * indent
    if isinstance(obj, dict):
        if not obj:
            return "{}"
        lines: list[str] = []
        for k, v in obj.items():
            key_str = str(k)
            if isinstance(v, dict):
                if not v:
                    lines.append(f"{pad}{key_str}: {{}}")
                else:
                    lines.append(f"{pad}{key_str}:")
                    lines.append(_emit_block_yaml(v, indent + 1))
            elif isinstance(v, list):
                if not v:
                    lines.append(f"{pad}{key_str}: []")
                else:
                    lines.append(f"{pad}{key_str}:")
                    lines.append(_emit_block_yaml(v, indent + 1))
            else:
                lines.append(f"{pad}{key_str}: {_emit_scalar(v)}")
        return "\n".join(lines)
    if isinstance(obj, list):
        if not obj:
            return "[]"
        lines = []
        for item in obj:
            if isinstance(item, dict):
                if not item:
                    lines.append(f"{pad}- {{}}")
                else:
                    first = True
                    for ik, iv in item.items():
                        prefix = f"{pad}- " if first else f"{pad}  "
                        first = False
                        if isinstance(iv, (dict, list)) and iv:
                            lines.append(f"{prefix}{ik}:")
                            lines.append(_emit_block_yaml(iv, indent + 2))
                        elif isinstance(iv, dict):
                            lines.append(f"{prefix}{ik}: {{}}")
                        elif isinstance(iv, list):
                            lines.append(f"{prefix}{ik}: []")
                        else:
                            lines.append(f"{prefix}{ik}: {_emit_scalar(iv)}")
            else:
                lines.append(f"{pad}- {_emit_scalar(item)}")
        return "\n".join(lines)
    return f"{pad}{_emit_scalar(obj)}"


def _parse_scalar(raw: str) -> Any:
    s = raw.strip()
    if not s:
        return ""
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("{") and s.endswith("}")) or (s.startswith("[") and s.endswith("]")):
        try:
            return json.loads(s)
        except Exception:
            pass
    if s.startswith("'") and s.endswith("'") and len(s) >= 2:
        return s[1:-1].replace("''", "'")
    low = s.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low in ("null", "~"):
        return None
    if s.isdigit() or (s.startswith("-") and s[1:].isdigit()):
        try:
            return int(s)
        except ValueError:
            pass
    try:
        if "." in s:
            return float(s)
    except ValueError:
        pass
    return s


def _strip_inline_comment(line: str) -> str:
    in_single = False
    in_double = False
    escaped = False
    for idx, ch in enumerate(line):
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            if idx == 0 or line[idx - 1].isspace():
                return line[:idx].rstrip()
    return line.rstrip()


def _parse_block_yaml(text: str) -> dict[str, Any]:
    raw_lines = text.splitlines()
    tokens: list[tuple[int, str]] = []
    for rl in raw_lines:
        cleaned = _strip_inline_comment(rl)
        if not cleaned.strip():
            continue
        indent = len(cleaned) - len(cleaned.lstrip(" "))
        tokens.append((indent, cleaned.strip()))

    if not tokens:
        return {}

    def _parse_node(idx: int, min_indent: int) -> tuple[Any, int]:
        if idx >= len(tokens):
            return {}, idx
        cur_indent, cur_text = tokens[idx]
        if cur_indent < min_indent:
            return {}, idx

        if cur_text.startswith("- ") or cur_text == "-":
            arr: list[Any] = []
            while idx < len(tokens):
                ind, txt = tokens[idx]
                if ind < cur_indent or not (txt.startswith("- ") or txt == "-"):
                    break
                if ind > cur_indent:
                    break
                rest = txt[1:].strip()
                idx += 1
                if not rest:
                    sub, idx = _parse_node(idx, ind + 1)
                    arr.append(sub)
                elif ":" in rest and not rest.startswith(('"', "'")):
                    k, v = _split_kv(rest)
                    item_dict: dict[str, Any] = {}
                    if v == "":
                        sub, idx = _parse_node(idx, ind + 1)
                        item_dict[k] = sub
                    else:
                        item_dict[k] = _parse_scalar(v)
                    while idx < len(tokens):
                        n_ind, n_txt = tokens[idx]
                        if n_ind <= ind or n_txt.startswith("- "):
                            break
                        nk, nv = _split_kv(n_txt)
                        idx += 1
                        if nv == "":
                            sub, idx = _parse_node(idx, n_ind + 1)
                            item_dict[nk] = sub
                        else:
                            item_dict[nk] = _parse_scalar(nv)
                    arr.append(item_dict)
                else:
                    arr.append(_parse_scalar(rest))
            return arr, idx

        mapping: dict[str, Any] = {}
        while idx < len(tokens):
            ind, txt = tokens[idx]
            if ind < cur_indent or txt.startswith("- "):
                break
            if ind > cur_indent:
                idx += 1
                continue
            k, v = _split_kv(txt)
            idx += 1
            if v == "":
                if idx < len(tokens) and tokens[idx][0] > ind:
                    sub, idx = _parse_node(idx, ind + 1)
                    mapping[k] = sub
                else:
                    mapping[k] = {}
            else:
                mapping[k] = _parse_scalar(v)
        return mapping, idx

    parsed, _ = _parse_node(0, 0)
    return parsed if isinstance(parsed, dict) else {}


def _split_kv(line: str) -> tuple[str, str]:
    in_single = False
    in_double = False
    for i, ch in enumerate(line):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == ":" and not in_single and not in_double:
            if i + 1 == len(line) or line[i + 1].isspace():
                return line[:i].strip().strip('"').strip("'"), line[i + 1 :].strip()
    return line.strip(), ""
