"""
SnapArc Radial Plugin Service
============================
Loads, hot-reloads, validates, and executes dynamic radial wheel plugins
configured via `plugins.yaml` or `config.yaml` ("radial_plugins"), plus automatic
discovery of standalone scripts in `plugins/*.py` (via `PLUGIN_META_JSON` AND
automatic AST inspection of `argparse` `add_argument("--flag")` definitions).

Key capabilities:
1. Dynamic Parameters (`RadialParamSpec`):
   - If a plugin has 0 params (`not spec.params`), it runs immediately on mouse release.
   - If a plugin has N params (`len(spec.params) > 0`), the UI opens `RadialParamPopup`
     with N inputs pre-populated from `RadialParamStore` (last-used values) or defaults.
2. Persistent Last-Used Parameter Store (`RadialParamStore` -> `data/radial_plugin_params.json`):
   - Remembers the user's most recent parameter values per plugin for instant 1-key (`Enter`) re-runs.
   - Dynamically resolves `{file_dir}` / `{file_path}` defaults to the root directory of
     any dragged/selected input file (e.g. `pdf2png` and `png_resize`).
3. Flexible Command & Script Execution:
   - Supports `uv run <script>.py`, `python <script>.py`, `.ps1`, `.mjs`, `.bat`, or arbitrary binaries.
   - Parses inline CLI flags from command strings (e.g. `uv run a.py --root 11235813x --host 0.0.0.0`,
     even handling glued flags like `11235813x--host`).
   - Only passes `--context-file` on the CLI if the script supports it (always sets
     `STC_PLUGIN_CONTEXT_FILE` and `STC_PLUGIN_PARAMS_JSON` in environment variables),
     so standard `argparse` scripts never fail with `unrecognized arguments`.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Dict, List, Optional

from src.utils.yaml_io import load_yaml_or_json


@dataclass
class RadialWheelStyle:
    hold_seconds: float = 0.18
    outer_radius: int = 158
    inner_radius: int = 56
    deadzone_radius: int = 46
    bg_color: str = "#0f172a"
    sector_color: str = "#1e293b"
    sector_hover_color: str = "#334155"
    ring_border_color: str = "#38bdf8"
    center_bg_color: str = "#090d16"
    center_border_color: str = "#06b6d4"
    text_color: str = "#f8fafc"
    subtext_color: str = "#38bdf8"


@dataclass
class RadialParamSpec:
    name: str
    flag: str = ""
    label: str = ""
    type: str = "string"  # "string" | "int" | "float" | "path" | "file" | "bool" | "choice"
    default: Any = ""
    min_val: Optional[float] = None
    max_val: Optional[float] = None
    choices: List[str] = field(default_factory=list)
    placeholder: str = ""
    description: str = ""
    required: bool = False

    def __post_init__(self) -> None:
        self.name = str(self.name or "").strip().lstrip("-").replace("-", "_")
        if not self.flag:
            self.flag = f"--{self.name.replace('_', '-')}"
        if not self.label:
            pretty = self.name.replace("_", " ").title()
            self.label = f"{pretty} ({self.flag})"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "flag": self.flag,
            "label": self.label,
            "type": self.type,
            "default": self.default,
            "min": self.min_val,
            "max": self.max_val,
            "choices": list(self.choices),
            "placeholder": self.placeholder,
            "description": self.description,
            "required": self.required,
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "RadialParamSpec":
        name = str(raw.get("name") or raw.get("flag") or "").strip().lstrip("-").replace("-", "_")
        flag = str(raw.get("flag") or (f"--{name.replace('_', '-')}" if name else "")).strip()
        p_type = str(raw.get("type") or "string").strip().lower()
        default = raw.get("default", "")
        min_v = raw.get("min", raw.get("min_val"))
        max_v = raw.get("max", raw.get("max_val"))
        choices_raw = raw.get("choices") or []
        choices = [str(c) for c in choices_raw] if isinstance(choices_raw, list) else []
        return cls(
            name=name,
            flag=flag,
            label=str(raw.get("label") or "").strip(),
            type=p_type,
            default=default,
            min_val=float(min_v) if min_v is not None and str(min_v) != "" else None,
            max_val=float(max_v) if max_v is not None and str(max_v) != "" else None,
            choices=choices,
            placeholder=str(raw.get("placeholder") or "").strip(),
            description=str(raw.get("description") or "").strip(),
            required=bool(raw.get("required", False)),
        )


@dataclass
class RadialPluginSpec:
    id: str
    name: str
    icon: str = "⚙️"
    badge: str = "APP"
    description: str = ""
    script: str = ""
    command: List[str] = field(default_factory=list)
    runner: str = "auto"  # "auto" | "uv" | "python" | "powershell" | "node" | "shell"
    execution_mode: str = "capture"  # "capture" | "terminal" | "detached"
    action: str = ""
    input_mode: str = "files_or_clipboard"  # "none" | "file" | "files_or_clipboard" | "clipboard"
    file_extensions: List[str] = field(default_factory=list)
    output_mode: str = "floating"
    timeout_sec: int = 30
    enabled: bool = True
    params: List[RadialParamSpec] = field(default_factory=list)

    @property
    def has_params(self) -> bool:
        return len(self.params) > 0

    @property
    def requires_file_input(self) -> bool:
        return self.input_mode in ("file", "files")

    def matches_file(self, file_path: str) -> bool:
        if not file_path:
            return False
        if not self.file_extensions:
            return True
        ext = Path(file_path).suffix.lower()
        return ext in {e.lower() if e.startswith(".") else f".{e.lower()}" for e in self.file_extensions}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "icon": self.icon,
            "badge": self.badge,
            "description": self.description,
            "script": self.script,
            "command": list(self.command),
            "runner": self.runner,
            "execution_mode": self.execution_mode,
            "action": self.action,
            "input_mode": self.input_mode,
            "file_extensions": list(self.file_extensions),
            "output_mode": self.output_mode,
            "timeout_sec": self.timeout_sec,
            "enabled": self.enabled,
            "params": [p.to_dict() for p in self.params],
        }


@dataclass
class RadialPluginResult:
    plugin_id: str
    plugin_name: str
    ok: bool
    title: str
    markdown: str
    output_mode: str = "floating"
    copy_to_clipboard: Optional[str] = None
    generated_files: List[str] = field(default_factory=list)
    action: str = ""
    elapsed_ms: float = 0.0
    error: Optional[str] = None
    command_executed: str = ""
    param_values: Dict[str, Any] = field(default_factory=dict)


class RadialParamStore:
    """Persists last-used plugin parameter values to `data/radial_plugin_params.json`."""

    DYNAMIC_FILE_DIR_TOKENS = {"{file_dir}", "{root_pdf}", "{root_file}", "{input_dir}"}

    def __init__(self, store_path: str | Path) -> None:
        self.store_path = Path(store_path)
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not self.store_path.is_file():
            self._cache = {}
            return
        try:
            data = json.loads(self.store_path.read_text(encoding="utf-8"))
            self._cache = data if isinstance(data, dict) else {}
        except Exception:
            self._cache = {}

    def save(self) -> None:
        try:
            self.store_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self.store_path.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(self._cache, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp_path.replace(self.store_path)
        except Exception as exc:
            print(f"[RadialParamStore] Warning saving param history: {exc}")

    def get_last_values(self, plugin_id: str) -> Dict[str, Any]:
        self._load()
        entry = self._cache.get(str(plugin_id))
        return dict(entry) if isinstance(entry, dict) else {}

    def record_values(self, plugin_id: str, values: Dict[str, Any]) -> None:
        if not plugin_id or not isinstance(values, dict):
            return
        self._load()
        clean: Dict[str, Any] = {}
        for k, v in values.items():
            if v is not None:
                clean[str(k)] = v
        self._cache[str(plugin_id)] = clean
        self.save()

    @classmethod
    def resolve_default_value(
        cls,
        param: RadialParamSpec,
        active_files: Optional[List[str]] = None,
    ) -> str:
        raw_def = "" if param.default is None else str(param.default).strip()
        first_file = None
        for f in active_files or []:
            if f and os.path.exists(str(f)):
                first_file = Path(str(f)).resolve()
                break

        if raw_def in cls.DYNAMIC_FILE_DIR_TOKENS:
            if first_file is not None:
                return str(first_file.parent if first_file.is_file() else first_file)
            return ""
        if raw_def == "{file_path}":
            return str(first_file) if first_file is not None else ""
        if raw_def == "{file_stem}":
            return first_file.stem if first_file is not None else ""
        return raw_def

    def get_prefilled_values(
        self,
        spec: RadialPluginSpec,
        active_files: Optional[List[str]] = None,
        use_last_saved: bool = True,
    ) -> Dict[str, str]:
        """Compute initial values for all parameters of `spec`, combining saved last-used
        values with dynamic file-root defaults (e.g. `{file_dir}`)."""
        saved = self.get_last_values(spec.id) if use_last_saved else {}
        prefilled: Dict[str, str] = {}
        has_active_file = any(f and os.path.exists(str(f)) for f in (active_files or []))

        for param in spec.params:
            raw_def = "" if param.default is None else str(param.default).strip()
            # When a file is dragged onto the wheel and the parameter default is `{file_dir}`
            # (e.g. `output_path` for pdf2png / png_resize), always default to that file's root folder!
            if raw_def in self.DYNAMIC_FILE_DIR_TOKENS and has_active_file:
                prefilled[param.name] = self.resolve_default_value(param, active_files)
                continue

            if use_last_saved and param.name in saved and str(saved[param.name]).strip() != "":
                prefilled[param.name] = str(saved[param.name])
            else:
                prefilled[param.name] = self.resolve_default_value(param, active_files)

        return prefilled


def parse_command_string_and_flags(raw_command: str | List[str]) -> tuple[List[str], List[RadialParamSpec]]:
    """
    Parse a CLI command string or token list (e.g.
    `uv run a.py --root 11235813x --host 0.0.0.0` or `uv run a.py --root 11235813x--host 0.0.0.0`)
    into `(base_command_tokens, extracted_param_specs)`.
    """
    if isinstance(raw_command, str):
        # Fix glued flags like `11235813x--host` -> `11235813x --host`
        normalized_str = re.sub(r"([^\s])(--[A-Za-z0-9_-]+)", r"\1 \2", raw_command.strip())
        try:
            tokens = shlex.split(normalized_str, posix=(os.name != "nt"))
        except Exception:
            tokens = normalized_str.split()
    elif isinstance(raw_command, list):
        tokens = []
        for item in raw_command:
            fixed = re.sub(r"([^\s])(--[A-Za-z0-9_-]+)", r"\1 \2", str(item))
            if " --" in fixed and not Path(fixed).exists():
                tokens.extend(fixed.split())
            else:
                tokens.append(str(item))
    else:
        return [], []

    base_tokens: List[str] = []
    extracted_params: List[RadialParamSpec] = []
    idx = 0
    while idx < len(tokens):
        tok = tokens[idx].strip().strip('"').strip("'")
        if tok.startswith("--") and len(tok) > 2:
            if "=" in tok:
                flag_part, val_part = tok.split("=", 1)
                pname = flag_part.lstrip("-").replace("-", "_")
                extracted_params.append(
                    RadialParamSpec(
                        name=pname,
                        flag=flag_part,
                        default=val_part,
                        type=_infer_param_type(pname, val_part),
                    )
                )
                idx += 1
                continue
            elif idx + 1 < len(tokens) and not tokens[idx + 1].startswith("-"):
                val_part = tokens[idx + 1].strip().strip('"').strip("'")
                pname = tok.lstrip("-").replace("-", "_")
                extracted_params.append(
                    RadialParamSpec(
                        name=pname,
                        flag=tok,
                        default=val_part,
                        type=_infer_param_type(pname, val_part),
                    )
                )
                idx += 2
                continue
        base_tokens.append(tok)
        idx += 1

    return base_tokens, extracted_params


def _infer_param_type(name: str, default_val: Any) -> str:
    low = name.lower()
    if "path" in low or "dir" in low or "folder" in low:
        return "path"
    if isinstance(default_val, bool):
        return "bool"
    if isinstance(default_val, int):
        return "int"
    if isinstance(default_val, float):
        return "float"
    s = str(default_val or "").strip()
    if s.isdigit():
        return "int"
    try:
        if "." in s and not s.count(".") > 1:
            float(s)
            return "float"
    except ValueError:
        pass
    return "string"


def extract_argparse_params_from_script(script_file: Path) -> List[RadialParamSpec]:
    """Inspect a Python script's AST to auto-discover `parser.add_argument('--flag', ...)` parameters."""
    if not script_file.is_file() or script_file.suffix.lower() != ".py":
        return []
    try:
        source = script_file.read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(source)
    except Exception:
        return []

    ignored_flags = {"--context-file", "--context_file", "--input", "-i", "-h", "--help"}
    discovered: List[RadialParamSpec] = []
    seen_names: set[str] = set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func_name = getattr(node.func, "attr", "") or getattr(node.func, "id", "")
        if func_name != "add_argument":
            continue

        flag_strs = []
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                flag_strs.append(arg.value)
        long_flags = [f for f in flag_strs if f.startswith("--")]
        if not long_flags:
            continue
        primary_flag = long_flags[0]
        if primary_flag in ignored_flags:
            continue

        kw_map: Dict[str, Any] = {}
        for kw in node.keywords:
            if not kw.arg:
                continue
            if isinstance(kw.value, ast.Constant):
                kw_map[kw.arg] = kw.value.value
            elif isinstance(kw.value, ast.Name):
                kw_map[kw.arg] = kw.value.id

        pname = str(kw_map.get("dest") or primary_flag.lstrip("-").replace("-", "_"))
        if pname in seen_names or pname in ("context_file", "input"):
            continue
        seen_names.add(pname)

        default_val = kw_map.get("default", "")
        arg_type_name = str(kw_map.get("type") or "")
        if arg_type_name == "int":
            p_type = "int"
        elif arg_type_name == "float":
            p_type = "float"
        elif kw_map.get("action") in ("store_true", "store_false"):
            p_type = "bool"
            default_val = kw_map.get("action") == "store_false"
        else:
            p_type = _infer_param_type(pname, default_val)

        help_txt = str(kw_map.get("help") or "").strip()
        label = f"{pname.replace('_', ' ').title()} ({primary_flag})"
        discovered.append(
            RadialParamSpec(
                name=pname,
                flag=primary_flag,
                label=label,
                type=p_type,
                default="" if default_val is None else default_val,
                description=help_txt,
                required=bool(kw_map.get("required", False)),
            )
        )
    return discovered


def _simple_yaml_parse(raw_text: str) -> Dict[str, Any]:
    """Parse plugins.yaml using unified `load_yaml_or_json` block-YAML/JSON reader."""
    try:
        parsed = load_yaml_or_json(raw_text)
        if isinstance(parsed, dict) and ("wheel" in parsed or "plugins" in parsed):
            return parsed
    except Exception:
        pass
    return {"wheel": {}, "plugins": []}


# Bundled scripts shipped with the source distribution. New local files
# are not auto-enabled until the user explicitly opts into custom plugins.
BUNDLED_PLUGIN_SCRIPTS = frozenset({
    "a.py", "ask_pi_agent.py", "clean_workspace.py", "extract_table.py",
    "format_json.py", "ocr_clipboard.py", "pdf2png.py", "png_resize.py",
    "summarize_doc.py", "translate_instant.py",
})


def plugin_child_environment() -> dict[str, str]:
    """Pass basic process requirements, never ambient provider/session credentials.

    Plugins remain trusted code with the user's filesystem privileges; this limits
    accidental credential inheritance, not intentional local secret access.
    """
    safe = {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP",
            "USERPROFILE", "APPDATA", "LOCALAPPDATA", "HOMEDRIVE", "HOMEPATH",
            "PYTHONPATH", "VIRTUAL_ENV", "UV_CACHE_DIR", "UV_PYTHON"}
    return {key: value for key, value in os.environ.items() if key.upper() in safe}


class RadialPluginService:
    """Manages configuration, hot-reload, parameter persistence, and execution of Radial Wheel plugins."""

    def __init__(
        self,
        repo_root: Optional[str | Path] = None,
        yaml_path: Optional[str | Path] = None,
        stc_json_path: Optional[str | Path] = None,
        param_store_path: Optional[str | Path] = None,
    ) -> None:
        self.repo_root = Path(repo_root or Path(__file__).resolve().parents[2])
        self.yaml_path = Path(yaml_path) if yaml_path else self.repo_root / "plugins.yaml"
        self.stc_json_path = Path(stc_json_path) if stc_json_path else self.repo_root / "config.yaml"
        default_store = self.repo_root / "data" / "radial_plugin_params.json"
        self.param_store = RadialParamStore(param_store_path or default_store)
        self.style = RadialWheelStyle()
        self._plugins: List[RadialPluginSpec] = []
        self._last_yaml_mtime: float = -1.0
        self._last_json_mtime: float = -1.0
        self._builtin_handlers: Dict[str, Callable[[Dict[str, Any], RadialPluginSpec], RadialPluginResult]] = {}
        self._allow_user_plugins = False
        self.reload(force=True)

    def register_builtin_action(
        self,
        action_name: str,
        handler: Callable[[Dict[str, Any], RadialPluginSpec], RadialPluginResult],
    ) -> None:
        self._builtin_handlers[str(action_name).strip()] = handler

    def reload(self, force: bool = False) -> List[RadialPluginSpec]:
        yaml_mtime = self.yaml_path.stat().st_mtime if self.yaml_path.exists() else 0.0
        json_mtime = self.stc_json_path.stat().st_mtime if self.stc_json_path.exists() else 0.0
        if not force and yaml_mtime == self._last_yaml_mtime and json_mtime == self._last_json_mtime and self._plugins:
            return list(self._plugins)

        self._last_yaml_mtime = yaml_mtime
        self._last_json_mtime = json_mtime

        raw_wheel: Dict[str, Any] = {}
        raw_plugins: List[Dict[str, Any]] = []

        if self.yaml_path.exists():
            try:
                parsed = _simple_yaml_parse(self.yaml_path.read_text(encoding="utf-8"))
                if isinstance(parsed.get("wheel"), dict):
                    raw_wheel.update(parsed["wheel"])
                if isinstance(parsed.get("plugins"), list):
                    raw_plugins.extend(p for p in parsed["plugins"] if isinstance(p, dict))
            except Exception as exc:
                print(f"[RadialPluginService] Warning reading plugins.yaml: {exc}")

        if self.stc_json_path.exists():
            try:
                stc_data = load_yaml_or_json(self.stc_json_path.read_text(encoding="utf-8"))
                radial_cfg = stc_data.get("radial_plugins") if isinstance(stc_data, dict) else None
                if isinstance(radial_cfg, dict):
                    self._allow_user_plugins = radial_cfg.get("allow_user_plugins") is True
                    if isinstance(radial_cfg.get("wheel"), dict):
                        raw_wheel.update(radial_cfg["wheel"])
                    if isinstance(radial_cfg.get("plugins"), list):
                        raw_plugins = [p for p in radial_cfg["plugins"] if isinstance(p, dict)]
                elif isinstance(radial_cfg, list):
                    raw_plugins = [p for p in radial_cfg if isinstance(p, dict)]
            except Exception:
                pass

        self.style = RadialWheelStyle(
            hold_seconds=float(raw_wheel.get("hold_seconds", 0.18)),
            outer_radius=int(raw_wheel.get("outer_radius", 158)),
            inner_radius=int(raw_wheel.get("inner_radius", 56)),
            deadzone_radius=int(raw_wheel.get("deadzone_radius", 46)),
            bg_color=str(raw_wheel.get("bg_color", "#0f172a")),
            sector_color=str(raw_wheel.get("sector_color", "#1e293b")),
            sector_hover_color=str(raw_wheel.get("sector_hover_color", "#334155")),
            ring_border_color=str(raw_wheel.get("ring_border_color", "#38bdf8")),
            center_bg_color=str(raw_wheel.get("center_bg_color", "#090d16")),
            center_border_color=str(raw_wheel.get("center_border_color", "#06b6d4")),
            text_color=str(raw_wheel.get("text_color", "#f8fafc")),
            subtext_color=str(raw_wheel.get("subtext_color", "#38bdf8")),
        )

        specs: List[RadialPluginSpec] = []
        seen_ids = set()
        for item in raw_plugins:
            spec = self._build_spec_from_dict(item)
            if spec is not None and spec.id not in seen_ids:
                specs.append(spec)
                seen_ids.add(spec.id)

        # Auto-discover standalone plugins/*.py files
        plugins_dir = self.repo_root / "plugins"
        if plugins_dir.is_dir():
            for script_file in sorted(plugins_dir.glob("*.py")):
                if not self._allow_user_plugins and script_file.name not in BUNDLED_PLUGIN_SCRIPTS:
                    print(f"[RadialPluginService] Skipping unapproved local plugin: {script_file.name}")
                    continue
                stem = script_file.stem
                if stem.startswith("_") or stem in seen_ids:
                    continue
                meta = self._extract_script_meta(script_file) or {"id": stem, "name": stem}
                pid = str(meta.get("id") or stem).strip()
                if pid in seen_ids or not bool(meta.get("enabled", True)):
                    continue
                meta.setdefault("script", f"plugins/{script_file.name}")
                spec = self._build_spec_from_dict(meta, script_file_override=script_file)
                if spec is not None:
                    specs.append(spec)
                    seen_ids.add(spec.id)

        if not specs:
            specs = self._default_specs()
        self._plugins = specs
        return list(self._plugins)

    def _build_spec_from_dict(
        self,
        item: Dict[str, Any],
        script_file_override: Optional[Path] = None,
    ) -> Optional[RadialPluginSpec]:
        pid = str(item.get("id") or "").strip()
        name = str(item.get("name") or pid).strip()
        if not pid or not bool(item.get("enabled", True)):
            return None

        raw_cmd = item.get("command")
        command_list: List[str] = []
        inline_cmd_params: List[RadialParamSpec] = []
        if isinstance(raw_cmd, (str, list)) and raw_cmd:
            command_list, inline_cmd_params = parse_command_string_and_flags(raw_cmd)

        script_rel = str(item.get("script") or "").strip()
        script_path: Optional[Path] = script_file_override
        if script_path is None and script_rel:
            cand = Path(script_rel)
            script_path = cand if cand.is_absolute() else (self.repo_root / cand)

        # Collect parameters from: 1) explicit item["params"], 2) script PLUGIN_META_JSON, 3) inline command flags, 4) script argparse AST
        params: List[RadialParamSpec] = []
        seen_param_names: set[str] = set()

        def _add_params(param_iterable: List[Any]) -> None:
            for raw_p in param_iterable:
                p_spec = raw_p if isinstance(raw_p, RadialParamSpec) else (
                    RadialParamSpec.from_dict(raw_p) if isinstance(raw_p, dict) else None
                )
                if p_spec and p_spec.name and p_spec.name not in seen_param_names:
                    seen_param_names.add(p_spec.name)
                    params.append(p_spec)

        if isinstance(item.get("params"), list):
            _add_params(item["params"])

        if script_path and script_path.is_file():
            script_meta = self._extract_script_meta(script_path)
            if isinstance(script_meta, dict) and isinstance(script_meta.get("params"), list):
                _add_params(script_meta["params"])

        if inline_cmd_params:
            _add_params(inline_cmd_params)

        if not params and script_path and script_path.is_file() and not item.get("action"):
            _add_params(extract_argparse_params_from_script(script_path))

        raw_exts = item.get("file_extensions") or []
        if isinstance(raw_exts, str):
            raw_exts = [x.strip() for x in raw_exts.split(",") if x.strip()]
        file_exts = [str(e) for e in raw_exts if str(e).strip()]

        return RadialPluginSpec(
            id=pid,
            name=name,
            icon=str(item.get("icon") or "⚙️").strip(),
            badge=str(item.get("badge") or pid[:4].upper()).strip(),
            description=str(item.get("description") or "").strip(),
            script=script_rel,
            command=command_list,
            runner=str(item.get("runner") or "auto").strip().lower(),
            execution_mode=str(item.get("execution_mode") or "capture").strip().lower(),
            action=str(item.get("action") or "").strip(),
            input_mode=str(item.get("input_mode") or "files_or_clipboard").strip(),
            file_extensions=file_exts,
            output_mode=str(item.get("output_mode") or "floating").strip(),
            timeout_sec=int(item.get("timeout_sec") or 30),
            enabled=bool(item.get("enabled", True)),
            params=params,
        )

    @staticmethod
    def _extract_script_meta(script_file: Path) -> Optional[Dict[str, Any]]:
        try:
            head = script_file.read_text(encoding="utf-8", errors="ignore")[:4096]
            marker = "PLUGIN_META_JSON:"
            for line in head.splitlines():
                if marker in line:
                    raw_json = line.split(marker, 1)[1].strip()
                    loaded = json.loads(raw_json)
                    if isinstance(loaded, dict):
                        return loaded
        except Exception:
            pass
        return None

    @staticmethod
    def _default_specs() -> List[RadialPluginSpec]:
        return [
            RadialPluginSpec(
                id="pdf2png",
                name="PDF -> PNG",
                icon="📄",
                badge="PDF",
                description="Convert PDF pages to PNG images",
                script="plugins/pdf2png.py",
                input_mode="file",
                file_extensions=[".pdf"],
                params=[
                    RadialParamSpec(name="dpi", flag="--dpi", label="Resolution DPI (--dpi)", type="int", default=150),
                    RadialParamSpec(name="output_path", flag="--output-path", label="Output Path (default = root PDF)", type="path", default="{file_dir}"),
                ],
            ),
            RadialPluginSpec(
                id="png_resize",
                name="Image Resize",
                icon="🖼️",
                badge="RESIZE",
                description="Resize PNG/JPG image (--size 0.1 to 3.0)",
                script="plugins/png_resize.py",
                input_mode="file",
                file_extensions=[".png", ".jpg", ".jpeg", ".webp", ".bmp"],
                params=[
                    RadialParamSpec(name="size", flag="--size", label="Scale Factor (--size: 0.1 -> 3.0)", type="float", default=0.5, min_val=0.1, max_val=3.0),
                    RadialParamSpec(name="output_path", flag="--output-path", label="Output Path (default = root image)", type="path", default="{file_dir}"),
                ],
            ),
            RadialPluginSpec(
                id="script_a",
                name="Run a.py",
                icon="🚀",
                badge="UV/PY",
                description="Run uv run plugins/a.py --root 11235813x --host 0.0.0.0",
                script="plugins/a.py",
                runner="uv",
                input_mode="none",
                params=[
                    RadialParamSpec(name="root", flag="--root", label="Root Token (--root)", type="string", default="11235813x"),
                    RadialParamSpec(name="host", flag="--host", label="Bind Host (--host)", type="string", default="0.0.0.0"),
                    RadialParamSpec(name="port", flag="--port", label="Bind Port (--port)", type="int", default=8000),
                ],
            ),
            RadialPluginSpec(
                id="ask_pi_agent",
                name="Pi Sidecar",
                icon="🤖",
                badge="AGENT",
                description="Send context to Pi Sidecar Chat Screen",
                script="plugins/ask_pi_agent.py",
                action="open_pi_chat",
                output_mode="chat_screen",
            ),
            RadialPluginSpec(
                id="ocr_clipboard",
                name="Instant OCR",
                icon="🔍",
                badge="OCR",
                description="Extract text from clipboard image",
                script="plugins/ocr_clipboard.py",
            ),
            RadialPluginSpec(
                id="extract_table",
                name="Table -> MD",
                icon="📊",
                badge="TABLE",
                description="Convert table text into Markdown & CSV",
                script="plugins/extract_table.py",
            ),
        ]

    def list_plugins(self, reload_if_changed: bool = True) -> List[RadialPluginSpec]:
        if reload_if_changed:
            self.reload(force=False)
        return list(self._plugins)

    def get_plugin(self, plugin_id: str) -> Optional[RadialPluginSpec]:
        for spec in self.list_plugins(reload_if_changed=True):
            if spec.id == plugin_id:
                return spec
        return None

    def get_prefilled_params(
        self,
        plugin_id: str,
        active_files: Optional[List[str]] = None,
        use_last_saved: bool = True,
    ) -> Dict[str, str]:
        spec = self.get_plugin(plugin_id)
        if spec is None:
            return {}
        return self.param_store.get_prefilled_values(spec, active_files=active_files, use_last_saved=use_last_saved)

    def build_command_preview(
        self,
        spec: RadialPluginSpec,
        param_values: Optional[Dict[str, Any]] = None,
        active_files: Optional[List[str]] = None,
    ) -> str:
        """Build human-readable CLI command preview for UI display."""
        cmd, _script_path = self._resolve_base_command(spec, for_preview=True)
        cli_args = self._build_cli_param_args(spec, param_values or {}, active_files or [], script_path=None, for_preview=True)
        full = [*cmd, *cli_args]
        return " ".join(f'"{t}"' if (" " in str(t) and not str(t).startswith('"')) else str(t) for t in full)

    def _resolve_base_command(
        self,
        spec: RadialPluginSpec,
        for_preview: bool = False,
    ) -> tuple[List[str], Optional[Path]]:
        script_path: Optional[Path] = None
        if spec.script:
            cand = Path(spec.script)
            script_path = cand if cand.is_absolute() else (self.repo_root / cand)

        if spec.command:
            cmd = list(spec.command)
            # If command starts with `uv run` and we are executing in a sandbox without `uv`, fallback to sys.executable
            if not for_preview and len(cmd) >= 2 and cmd[0].lower() == "uv" and cmd[1].lower() == "run":
                if not shutil.which("uv"):
                    cmd = [sys.executable, *cmd[2:]]
            return cmd, script_path

        if script_path is not None:
            suffix = script_path.suffix.lower()
            rel_or_abs = spec.script if for_preview else str(script_path)
            if suffix == ".py":
                if spec.runner == "uv":
                    if for_preview:
                        return ["uv", "run", rel_or_abs], script_path
                    uv_bin = shutil.which("uv")
                    if uv_bin and os.environ.get("STC_PREFER_UV_SUBPROCESS") == "1":
                        return [uv_bin, "run", str(script_path)], script_path
                return (["python", rel_or_abs] if for_preview else [sys.executable, str(script_path)]), script_path
            if suffix == ".ps1":
                return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", rel_or_abs], script_path
            if suffix in (".mjs", ".js"):
                return ["node", rel_or_abs], script_path
            return [rel_or_abs], script_path

        return [], None

    @staticmethod
    def _script_supports_flag(script_path: Optional[Path], flag_substring: str) -> bool:
        if script_path is None or not script_path.is_file():
            return False
        try:
            content = script_path.read_text(encoding="utf-8", errors="ignore")
            return flag_substring in content
        except Exception:
            return False

    def _build_cli_param_args(
        self,
        spec: RadialPluginSpec,
        param_values: Dict[str, Any],
        active_files: List[str],
        script_path: Optional[Path],
        for_preview: bool = False,
    ) -> List[str]:
        cli_args: List[str] = []
        # 1. Input file argument if dragged/selected
        valid_files = [str(f) for f in (active_files or []) if f and (for_preview or os.path.exists(str(f)))]
        if valid_files and spec.input_mode in ("file", "files", "files_or_clipboard"):
            if for_preview or self._script_supports_flag(script_path, "--input"):
                cli_args.extend(["--input", valid_files[0]])

        # 2. User / default parameters
        for param in spec.params:
            raw_val = param_values.get(param.name)
            if raw_val is None or str(raw_val).strip() == "":
                raw_val = self.param_store.resolve_default_value(param, valid_files)
            val_str = str(raw_val).strip() if raw_val is not None else ""
            if not val_str:
                continue
            if param.type == "bool":
                if val_str.lower() in ("true", "1", "yes", "on"):
                    cli_args.append(param.flag)
            else:
                cli_args.extend([param.flag, val_str])

        return cli_args

    def execute_plugin(
        self,
        plugin_id: str,
        context: Optional[Dict[str, Any]] = None,
        param_values: Optional[Dict[str, Any]] = None,
        param_overrides: Optional[Dict[str, Any]] = None,
        execution_mode: Optional[str] = None,
    ) -> RadialPluginResult:
        t0 = time.perf_counter()
        spec = self.get_plugin(plugin_id)
        if spec is None:
            return RadialPluginResult(
                plugin_id=plugin_id,
                plugin_name=plugin_id,
                ok=False,
                title="Plugin Not Found",
                markdown=f"❌ Radial plugin `{plugin_id}` is not registered in `plugins.yaml`.",
                error="plugin_not_found",
            )

        ctx = dict(context or {})
        active_files = [str(f) for f in (ctx.get("files") or []) if f and os.path.exists(str(f))]
        ctx["files"] = active_files

        # Resolve final parameter values (merging prefilled/last-used with explicitly submitted param_values)
        resolved_params = self.param_store.get_prefilled_values(spec, active_files=active_files, use_last_saved=True)
        if isinstance(ctx.get("params"), dict):
            for k, v in ctx["params"].items():
                if v is not None:
                    resolved_params[str(k)] = str(v)
        for override_dict in (param_values, param_overrides):
            if isinstance(override_dict, dict):
                for k, v in override_dict.items():
                    if v is not None:
                        resolved_params[str(k)] = str(v)

        # Save user-submitted parameter values for quick reuse next time
        if spec.params and (param_values is not None or param_overrides is not None or resolved_params):
            to_record = dict(resolved_params)
            # Don't overwrite dynamic `{file_dir}` with a hardcoded folder unless user edited it away from file_dir
            for p in spec.params:
                if str(p.default).strip() in RadialParamStore.DYNAMIC_FILE_DIR_TOKENS and active_files:
                    auto_dir = RadialParamStore.resolve_default_value(p, active_files)
                    if to_record.get(p.name) == auto_dir:
                        to_record.pop(p.name, None)
            if to_record:
                self.param_store.record_values(spec.id, to_record)

        ctx["params"] = resolved_params
        ctx.setdefault("plugin_id", spec.id)
        ctx.setdefault("plugin_name", spec.name)
        ctx.setdefault("repo_root", str(self.repo_root))

        # Check if a built-in action handler is registered and should run
        if spec.action and spec.action in self._builtin_handlers:
            try:
                res = self._builtin_handlers[spec.action](ctx, spec)
                res.elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
                res.param_values = resolved_params
                return res
            except Exception as exc:
                return RadialPluginResult(
                    plugin_id=spec.id,
                    plugin_name=spec.name,
                    ok=False,
                    title=f"{spec.name} Error",
                    markdown=f"❌ Builtin action `{spec.action}` failed: `{exc}`",
                    output_mode=spec.output_mode,
                    action=spec.action,
                    elapsed_ms=round((time.perf_counter() - t0) * 1000.0, 2),
                    error=str(exc),
                )

        cmd, script_path = self._resolve_base_command(spec, for_preview=False)
        if script_path is not None and not script_path.exists() and not spec.command:
            return RadialPluginResult(
                plugin_id=spec.id,
                plugin_name=spec.name,
                ok=False,
                title=f"{spec.name} Missing Script",
                markdown=f"❌ Script file not found: `{script_path}`",
                output_mode=spec.output_mode,
                action=spec.action,
                elapsed_ms=round((time.perf_counter() - t0) * 1000.0, 2),
                error="script_not_found",
            )

        if not cmd:
            return RadialPluginResult(
                plugin_id=spec.id,
                plugin_name=spec.name,
                ok=True,
                title=spec.name,
                markdown=f"✅ Triggered `{spec.name}` ({spec.action or 'no-op'}).",
                output_mode=spec.output_mode,
                action=spec.action,
                elapsed_ms=round((time.perf_counter() - t0) * 1000.0, 2),
                param_values=resolved_params,
            )

        cli_param_args = self._build_cli_param_args(
            spec,
            resolved_params,
            active_files,
            script_path=script_path,
            for_preview=False,
        )
        full_cmd = [*cmd, *cli_param_args]
        cmd_preview = self.build_command_preview(spec, resolved_params, active_files)

        eff_mode = (execution_mode or spec.execution_mode or "capture").strip().lower()
        if eff_mode in ("terminal", "detached"):
            try:
                env = plugin_child_environment()
                env["STC_PLUGIN_PARAMS_JSON"] = json.dumps(resolved_params, ensure_ascii=False)
                env["PYTHONIOENCODING"] = "utf-8"
                if os.name == "nt" and eff_mode == "terminal":
                    creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)
                    subprocess.Popen(full_cmd, cwd=str(self.repo_root), env=env, creationflags=creationflags)
                else:
                    subprocess.Popen(
                        full_cmd,
                        cwd=str(self.repo_root),
                        env=env,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                return RadialPluginResult(
                    plugin_id=spec.id,
                    plugin_name=spec.name,
                    ok=True,
                    title=f"{spec.name} Launched",
                    markdown=(
                        f"### 🚀 Launched `{spec.name}` ({eff_mode})\n\n"
                        f"- **Command**: `{cmd_preview}`\n"
                        + (f"- **Files**: `{', '.join(Path(f).name for f in active_files)}`\n" if active_files else "")
                    ),
                    output_mode=spec.output_mode,
                    action=spec.action,
                    elapsed_ms=round((time.perf_counter() - t0) * 1000.0, 2),
                    command_executed=cmd_preview,
                    param_values=resolved_params,
                )
            except Exception as exc:
                return RadialPluginResult(
                    plugin_id=spec.id,
                    plugin_name=spec.name,
                    ok=False,
                    title=f"{spec.name} Launch Error",
                    markdown=f"❌ Could not launch `{cmd_preview}`: `{exc}`",
                    output_mode=spec.output_mode,
                    action=spec.action,
                    elapsed_ms=round((time.perf_counter() - t0) * 1000.0, 2),
                    error=str(exc),
                    command_executed=cmd_preview,
                    param_values=resolved_params,
                )

        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".stc_plugin_ctx.json",
            delete=False,
            encoding="utf-8",
        ) as tmp:
            json.dump(ctx, tmp, ensure_ascii=False, indent=2)
            ctx_path = tmp.name

        try:
            env = plugin_child_environment()
            env["STC_PLUGIN_CONTEXT_FILE"] = ctx_path
            env["STC_PLUGIN_PARAMS_JSON"] = json.dumps(resolved_params, ensure_ascii=False)
            env["PYTHONIOENCODING"] = "utf-8"

            # Only append `--context-file` on CLI if the target script explicitly accepts it
            run_argv = list(full_cmd)
            if self._script_supports_flag(script_path, "--context-file"):
                run_argv.extend(["--context-file", ctx_path])

            proc = subprocess.run(
                run_argv,
                cwd=str(self.repo_root),
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=max(2, int(spec.timeout_sec)),
            )
            elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            stdout = (proc.stdout or "").strip()
            stderr = (proc.stderr or "").strip()

            if proc.returncode != 0:
                return RadialPluginResult(
                    plugin_id=spec.id,
                    plugin_name=spec.name,
                    ok=False,
                    title=f"{spec.name} Failed",
                    markdown=(
                        f"❌ Plugin `{spec.name}` exited with code {proc.returncode}.\n"
                        f"- **Command**: `{cmd_preview}`\n\n```\n{stderr or stdout}\n```"
                    ),
                    output_mode=spec.output_mode,
                    action=spec.action,
                    elapsed_ms=elapsed_ms,
                    error=stderr or f"exit_{proc.returncode}",
                    command_executed=cmd_preview,
                    param_values=resolved_params,
                )

            # Parse structured JSON output if the plugin emitted JSON
            if stdout.startswith("{") and stdout.endswith("}"):
                try:
                    payload = json.loads(stdout)
                    if isinstance(payload, dict):
                        return RadialPluginResult(
                            plugin_id=spec.id,
                            plugin_name=spec.name,
                            ok=bool(payload.get("ok", True)),
                            title=str(payload.get("title") or spec.name),
                            markdown=str(payload.get("markdown") or payload.get("text") or ""),
                            output_mode=str(payload.get("output_mode") or spec.output_mode),
                            copy_to_clipboard=payload.get("copy_to_clipboard"),
                            generated_files=[str(x) for x in (payload.get("generated_files") or [])],
                            action=str(payload.get("action") or spec.action),
                            elapsed_ms=elapsed_ms,
                            error=payload.get("error"),
                            command_executed=cmd_preview,
                            param_values=resolved_params,
                        )
                except Exception:
                    pass

            return RadialPluginResult(
                plugin_id=spec.id,
                plugin_name=spec.name,
                ok=True,
                title=spec.name,
                markdown=stdout or f"✅ `{spec.name}` completed.\n\n- **Command**: `{cmd_preview}`",
                output_mode=spec.output_mode,
                action=spec.action,
                elapsed_ms=elapsed_ms,
                command_executed=cmd_preview,
                param_values=resolved_params,
            )
        except subprocess.TimeoutExpired:
            return RadialPluginResult(
                plugin_id=spec.id,
                plugin_name=spec.name,
                ok=False,
                title=f"{spec.name} Timeout",
                markdown=f"⏱️ Plugin `{spec.name}` timed out after {spec.timeout_sec}s (`{cmd_preview}`).",
                output_mode=spec.output_mode,
                action=spec.action,
                elapsed_ms=round((time.perf_counter() - t0) * 1000.0, 2),
                error="timeout",
                command_executed=cmd_preview,
                param_values=resolved_params,
            )
        finally:
            try:
                os.unlink(ctx_path)
            except OSError:
                pass
