"""Native `pi --mode rpc` & OpenAI-Completions Agentic Bridge (`src/agents/pi_rpc_bridge.py`) — v2.4.

Provides the primary Pi Sidecar Agent runtime for SnapArc v0.0.1:
1. Powered directly by the single configured OpenAI-compatible Chat Completions API
   (`openai-completions` in `config.yaml`) with full multi-step tool calling (`read`, `write`, `edit`,
   `bash`, `grep`, `find`, `ls`), image attachments, streaming thinking/text deltas,
   and tool approval gating.
2. Also supports delegating to an external `pi --mode rpc` CLI binary when `STC_USE_EXTERNAL_PI_CLI=1`
   using the provider/baseUrl/apiKey values resolved from `config.yaml`.
3. Includes `PiTerminalToolset` so `read`, `write`, `edit`, `bash`, `grep`, `find`, and `ls`
   execute safely within `trusted_roots`.
"""
from __future__ import annotations

import fnmatch
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
from typing import Any, Optional
from uuid import uuid4


PI_TERMINAL_TOOLS = ("read", "write", "edit", "bash", "grep", "find", "ls")
READ_ONLY_TOOLS = frozenset({"read", "grep", "find", "ls", "inspect_attachments"})
MUTATING_TOOLS = frozenset({"write", "edit", "bash"})

PI_OPENAI_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "read",
            "description": "Read lines from a file in the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative or absolute file path"},
                    "offset": {"type": "integer", "description": "1-indexed start line", "default": 1},
                    "limit": {"type": "integer", "description": "Max lines to read", "default": 200},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ls",
            "description": "List files and directories at the given path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory path (default '.')", "default": "."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find",
            "description": "Find files matching a glob pattern inside a directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Filename glob pattern, e.g. '*.py'"},
                    "path": {"type": "string", "description": "Base directory path", "default": "."},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Search file contents using a regular expression.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regex pattern to search for"},
                    "path": {"type": "string", "description": "File or directory to search", "default": "."},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write",
            "description": "Write text content to a file inside trusted roots.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Target file path"},
                    "content": {"type": "string", "description": "Full text content to write"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit",
            "description": "Replace oldText with newText in a file inside trusted roots.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Target file path"},
                    "oldText": {"type": "string", "description": "Exact text to replace"},
                    "newText": {"type": "string", "description": "Replacement text"},
                },
                "required": ["path", "oldText", "newText"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Execute a shell command in the workspace directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to run"},
                    "timeout": {"type": "number", "description": "Timeout in seconds", "default": 30},
                },
                "required": ["command"],
            },
        },
    },
]


def resolve_pi_cli(explicit_bin: str = "") -> str:
    """Locate the `pi` CLI binary across POSIX and Windows (npm, Volta, NVM, Scoop)."""
    candidates: list[str] = []
    if explicit_bin:
        candidates.append(explicit_bin)
    env_bin = os.environ.get("STC_PI_BIN", "").strip()
    if env_bin:
        candidates.append(env_bin)

    which_pi = shutil.which("pi")
    if which_pi:
        candidates.append(which_pi)

    home = Path.home()
    if os.name == "nt":
        appdata = os.environ.get("APPDATA", "")
        localappdata = os.environ.get("LOCALAPPDATA", "")
        if appdata:
            candidates.extend([
                str(Path(appdata) / "npm" / "pi.cmd"),
                str(Path(appdata) / "npm" / "pi"),
            ])
        if localappdata:
            candidates.extend([
                str(Path(localappdata) / "Volta" / "bin" / "pi.cmd"),
                str(Path(localappdata) / "Programs" / "nodejs" / "pi.cmd"),
            ])
        candidates.extend([
            str(home / "scoop" / "shims" / "pi.cmd"),
            str(home / ".local" / "bin" / "pi.exe"),
        ])
    else:
        candidates.extend([
            str(home / ".local" / "bin" / "pi"),
            str(home / ".volta" / "bin" / "pi"),
            "/usr/local/bin/pi",
        ])

    for cand in candidates:
        if cand and Path(cand).is_file():
            return str(Path(cand).resolve())
    return ""


def unwrap_windows_cmd_shim(shim_path: Path) -> Optional[list[str]]:
    """Unwrap an npm `.cmd` shim on Windows into `[node_exe, cli_js]` to avoid `cmd.exe` pipe hangs."""
    if os.name != "nt" or shim_path.suffix.lower() not in (".cmd", ".bat"):
        return None
    try:
        text = shim_path.read_text(encoding="utf-8", errors="ignore")
        m = re.search(r'"%_prog%"\s+"%~dp0\\([^"]+)"', text) or re.search(r'"%~dp0\\([^"]+\.(?:js|mjs|cjs))"', text)
        if not m:
            return None
        rel_js = m.group(1).replace("\\", "/")
        cli_js = (shim_path.parent / rel_js).resolve()
        if not cli_js.is_file():
            return None
        node_exe = shutil.which("node") or "node"
        return [node_exe, str(cli_js)]
    except Exception:
        return None


def build_native_pi_rpc_argv(
    session_dir: Path,
    explicit_bin: str = "",
    model_ref: str = "",
) -> Optional[list[str]]:
    pi_bin = resolve_pi_cli(explicit_bin)
    if not pi_bin:
        return None
    shim_argv = unwrap_windows_cmd_shim(Path(pi_bin))
    base = shim_argv if shim_argv else [pi_bin]
    argv = [*base, "--mode", "rpc", "--session-dir", str(session_dir)]
    if model_ref and "/" in model_ref and not model_ref.startswith("pi://"):
        provider, model_id = model_ref.split("/", 1)
        if provider and model_id:
            argv.extend(["--provider", provider, "--model", model_id])
    return argv


class PiTerminalToolset:
    """Executes Pi's 7 standard terminal tools (`read`, `write`, `edit`, `bash`, `grep`, `find`, `ls`)
    with workspace root sandboxing and optional approval gating."""

    def __init__(self, cwd: str | Path, trusted_roots: list[str] | None = None) -> None:
        self.cwd = Path(cwd).resolve()
        roots = trusted_roots if trusted_roots else ["."]
        self.trusted_roots = [
            (self.cwd / r).resolve() if not Path(r).is_absolute() else Path(r).resolve()
            for r in roots
        ]

    def _resolve_safe_path(self, rel_or_abs: str, require_trusted: bool = False) -> Path:
        target = Path(rel_or_abs)
        resolved = target.resolve() if target.is_absolute() else (self.cwd / target).resolve()
        if require_trusted and self.trusted_roots:
            allowed = any(
                resolved == root or root in resolved.parents
                for root in self.trusted_roots
            )
            if not allowed:
                raise PermissionError(f"Path '{resolved}' is outside trusted_roots")
        return resolved

    def execute(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        name = str(tool_name or "").strip().lower()
        if name == "read":
            path = self._resolve_safe_path(str(args.get("path") or ""))
            offset = max(1, int(args.get("offset", 1) or 1))
            limit = max(1, min(2000, int(args.get("limit", 200) or 200)))
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            sliced = lines[offset - 1 : offset - 1 + limit]
            numbered = "\n".join(f"{idx}: {line}" for idx, line in enumerate(sliced, start=offset))
            return {"ok": True, "output": numbered, "totalLines": len(lines)}

        if name == "write":
            path = self._resolve_safe_path(str(args.get("path") or ""), require_trusted=True)
            content = str(args.get("content") or "")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return {"ok": True, "output": f"Wrote {len(content)} chars to {path.name}"}

        if name == "edit":
            path = self._resolve_safe_path(str(args.get("path") or ""), require_trusted=True)
            old_text = str(args.get("oldText") or args.get("old_text") or "")
            new_text = str(args.get("newText") or args.get("new_text") or "")
            text = path.read_text(encoding="utf-8")
            if old_text not in text:
                raise ValueError(f"oldText not found in {path.name}")
            updated = text.replace(old_text, new_text, 1)
            path.write_text(updated, encoding="utf-8")
            return {"ok": True, "output": f"Updated {path.name}"}

        if name == "bash":
            if not self.trusted_roots:
                raise PermissionError("bash tool requires at least one trusted_root")
            cmd = str(args.get("command") or "")
            timeout_s = max(1.0, min(120.0, float(args.get("timeout", 30) or 30)))
            completed = subprocess.run(
                cmd,
                shell=True,
                cwd=str(self.cwd),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_s,
            )
            out = (completed.stdout or "") + ("\n" + completed.stderr if completed.stderr else "")
            return {
                "ok": completed.returncode == 0,
                "exitCode": completed.returncode,
                "output": out.strip()[:8000],
            }

        if name == "ls":
            path = self._resolve_safe_path(str(args.get("path") or "."))
            entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
            formatted = [f"{p.name}/" if p.is_dir() else f"{p.name} ({p.stat().st_size}B)" for p in entries[:200]]
            return {"ok": True, "output": "\n".join(formatted)}

        if name == "find":
            pattern = str(args.get("pattern") or "*")
            base = self._resolve_safe_path(str(args.get("path") or "."))
            matches: list[str] = []
            for root, dirs, files in os.walk(base):
                dirs[:] = [d for d in dirs if d not in {".git", ".venv", "node_modules", "__pycache__"}]
                for fname in files:
                    if fnmatch.fnmatch(fname, pattern):
                        matches.append(str(Path(root, fname).relative_to(base)))
                        if len(matches) >= 200:
                            break
                if len(matches) >= 200:
                    break
            return {"ok": True, "output": "\n".join(matches)}

        if name == "grep":
            pattern = str(args.get("pattern") or "")
            base = self._resolve_safe_path(str(args.get("path") or "."))
            regex = re.compile(pattern, re.IGNORECASE)
            hits: list[str] = []
            targets = [base] if base.is_file() else base.rglob("*")
            for item in targets:
                if not item.is_file() or any(p in item.parts for p in (".git", ".venv", "node_modules", "__pycache__")):
                    continue
                try:
                    for lineno, line in enumerate(item.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                        if regex.search(line):
                            rel = item.name if base.is_file() else str(item.relative_to(base))
                            hits.append(f"{rel}:{lineno}: {line.strip()[:160]}")
                            if len(hits) >= 100:
                                return {"ok": True, "output": "\n".join(hits)}
                except Exception:
                    continue
            return {"ok": True, "output": "\n".join(hits)}

        raise ValueError(f"Unsupported Pi tool: {tool_name}")


class NativePiRpcServer:
    """Stdio JSONL bridge speaking the `PiSdkBridgeTransport` v1 protocol.
    Executes agent turns using the single OpenAI-compatible Chat Completions API
    configured in `config.yaml` with full Pi terminal tools (`read`, `write`, `edit`,
    `bash`, `grep`, `find`, `ls`)."""

    def __init__(self) -> None:
        self.write_lock = threading.Lock()
        self.state_lock = threading.RLock()
        self.project_root = Path(__file__).resolve().parents[2]
        self.session_dir = self.project_root / "data" / "pi-rpc-sessions"
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.canceled_jobs: set[str] = set()
        self.steered_jobs: dict[str, list[str]] = {}
        self.followup_jobs: dict[str, list[str]] = {}
        self.active_procs: dict[str, subprocess.Popen[str]] = {}
        self.pending_approvals: dict[str, dict[str, Any]] = {}
        self.sessions: dict[str, list[dict[str, str]]] = {}

    def _emit(self, frame: dict[str, Any]) -> None:
        raw = json.dumps(frame, ensure_ascii=False, separators=(",", ":"))
        with self.write_lock:
            sys.stdout.write(raw + "\n")
            sys.stdout.flush()

    def _emit_job_event(self, job_id: str, event: dict[str, Any]) -> None:
        self._emit({
            "v": 1,
            "type": "event",
            "jobId": job_id,
            "event": event,
        })

    def _request_tool_approval(
        self,
        job_id: str,
        tool_call_id: str,
        tool_name: str,
        summary: str,
        timeout_ms: int = 120000,
    ) -> bool:
        approval_id = f"apr-{uuid4().hex[:10]}"
        gate = threading.Event()
        holder: dict[str, Any] = {"event": gate, "approved": False}
        with self.state_lock:
            self.pending_approvals[approval_id] = holder
        self._emit_job_event(
            job_id,
            {
                "kind": "approval_request",
                "approvalId": approval_id,
                "toolCallId": tool_call_id,
                "toolName": tool_name,
                "summary": summary,
                "uiType": "tk_modal",
                "payload": {"toolName": tool_name, "summary": summary},
            },
        )
        gate.wait(timeout=max(1.0, timeout_ms / 1000.0))
        with self.state_lock:
            self.pending_approvals.pop(approval_id, None)
        return bool(holder.get("approved", False))

    def _check_and_record_tool_policy(
        self,
        job_id: str,
        tool_call_id: str,
        tool_name: str,
        tool_policy: str,
        summary: str,
        approval_timeout_ms: int,
    ) -> tuple[bool, bool]:
        """Returns `(allowed, is_mutating)` according to `tool_policy`."""
        norm_tool = tool_name.strip().lower()
        is_mutating = norm_tool in MUTATING_TOOLS or norm_tool not in READ_ONLY_TOOLS
        if tool_policy == "none":
            return False, is_mutating
        if not is_mutating:
            return True, False
        if tool_policy == "read_only":
            return False, True
        if tool_policy == "approve_mutations":
            approved = self._request_tool_approval(
                job_id=job_id,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                summary=summary,
                timeout_ms=approval_timeout_ms,
            )
            if not approved:
                return False, True
        self._emit_job_event(
            job_id,
            {"kind": "runtime_notice", "name": "side_effect_started", "toolName": tool_name},
        )
        return True, True

    def _resolve_openai_endpoint_for_model(self, model_ref: str, config_path=None) -> Optional[dict[str, Any]]:
        """Resolve `{base_url, api_key, model_id, provider_name, extra_headers, timeout}` from `config.yaml`."""
        try:
            if str(self.project_root) not in sys.path:
                sys.path.insert(0, str(self.project_root))
            from src.core.secret_store import SecretStore
            from src.utils.ConfigManager import ConfigManager

            cfg_path = Path(config_path) if config_path is not None else self.project_root / "config.yaml"
            if config_path is not None and not cfg_path.is_file():
                raise ValueError("Explicit external configuration must exist")
            cm = ConfigManager(str(cfg_path))
            if config_path is not None and Path(cm.config_path).resolve() != cfg_path.resolve():
                raise ValueError("Configuration singleton conflicts with explicit selection")
            cm.reload_if_changed()
            # The Pi-RPC chat uses its own OpenAI client, outside LLMService.
            # Enforce the same explicit local cloud permission before resolving
            # any network-capable endpoint (including fallback providers).
            from src.core.network_policy import is_loopback_provider

            clean_ref = str(model_ref or "").strip()
            if not clean_ref or clean_ref.startswith("pi://"):
                clean_ref = (
                    str(cm.get("screenshot_chat.default_model") or "").strip()
                    or str(cm.get("agents.defaults.model.primary") or "").strip()
                )
            all_models = cm.get_all_models()
            if not clean_ref or "/" not in clean_ref or clean_ref not in all_models:
                return None

            provider_name, model_id = clean_ref.split("/", 1)
            p_cfg = cm.get_provider_config(provider_name) or {}
            if p_cfg.get("api") != "openai-completions":
                return None
            base_url = str(p_cfg.get("baseUrl") or "").strip()
            raw_key = str(p_cfg.get("apiKey") or "").strip()
            resolved_key = SecretStore().resolve_config_secret(raw_key, provider_name=provider_name) or raw_key

            if not base_url:
                return None
            if not is_loopback_provider(base_url) and cm.get("privacy.allow_cloud_upload", False) is not True:
                return None

            return {
                "provider_name": provider_name,
                "model_id": model_id,
                "full_model": f"{provider_name}/{model_id}",
                "base_url": base_url,
                "api_key": resolved_key,
                "is_placeholder_key": bool(resolved_key.startswith("<") and resolved_key.endswith(">")),
                "timeout": float(p_cfg.get("timeout", 90.0) or 90.0),
                "trust_env": bool(p_cfg.get("trustEnv", True)),
                "extra_headers": p_cfg.get("extraHeaders") if isinstance(p_cfg.get("extraHeaders"), dict) else None,
            }
        except Exception:
            return None

    def _run_via_openai_completions_agent(
        self,
        endpoint: dict[str, Any],
        prompt: str,
        system_prompt: str,
        history: list[dict[str, Any]],
        attachments: list[dict[str, Any]],
        job_id: str,
        cwd: str,
        thinking_level: str,
        tool_policy: str,
        trusted_roots: list[str],
        approval_timeout_ms: int,
        max_output_tokens=None,
        timeout_seconds=None,
    ) -> tuple[Optional[str], bool]:
        """Execute a multi-step Pi Sidecar agent loop over the OpenAI-compatible Chat Completions API."""
        from src.llm.providers.completions import ChatCompletionsProvider
        from src.llm.request_bounds import completion_options

        options = completion_options(max_output_tokens, timeout_seconds)
        request_timeout = options.pop("timeout", None)
        side_effect_started = False
        timeout_s = min(90.0, float(endpoint.get("timeout") or 60.0))
        client = ChatCompletionsProvider(
            api_key=endpoint["api_key"],
            base_url=endpoint["base_url"],
            model=endpoint["model_id"],
            provider_name=endpoint.get("provider_name", ""),
            timeout=timeout_s,
            trust_env=endpoint.get("trust_env", True),
            extra_headers=endpoint.get("extra_headers"),
        )
        toolset = PiTerminalToolset(cwd=cwd, trusted_roots=trusted_roots)

        sys_instructions = (
            "You are the optional SnapArc workspace chat assistant.\n"
            f"Workspace root: {cwd}\n"
            "Respond clearly in Markdown. When inspecting files or running commands, use the provided workspace tools."
        )
        if system_prompt and system_prompt.strip():
            sys_instructions = f"{system_prompt.strip()}\n\n{sys_instructions}"

        messages: list[dict[str, Any]] = [{"role": "system", "content": sys_instructions}]

        # Convert conversation history
        for item in history or []:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "user")
            if role == "model":
                role = "assistant"
            if role not in ("user", "assistant"):
                continue
            text_val = ""
            if isinstance(item.get("content"), str):
                text_val = item["content"]
            elif isinstance(item.get("parts"), list):
                text_val = "\n".join(str(p) for p in item["parts"] if isinstance(p, str))
            if text_val.strip():
                messages.append({"role": role, "content": text_val.strip()})

        # Build user message with optional image attachments
        if attachments:
            content_blocks: list[dict[str, Any]] = [{"type": "text", "text": prompt or "Analyze the attached image(s)."}]
            for att in attachments:
                if isinstance(att, dict) and att.get("data") and att.get("mimeType"):
                    content_blocks.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{att['mimeType']};base64,{att['data']}"},
                    })
            messages.append({"role": "user", "content": content_blocks})
        else:
            messages.append({"role": "user", "content": prompt})

        use_tools = tool_policy != "none" and not attachments
        max_turns = 4 if use_tools else 1
        collected_final_text: list[str] = []

        for _turn in range(max_turns):
            if job_id in self.canceled_jobs:
                break

            req_kwargs: dict[str, Any] = {
                "model": endpoint["model_id"],
                "messages": messages,
                "stream": True,
                **options,
            }
            if use_tools:
                req_kwargs["tools"] = PI_OPENAI_TOOL_SCHEMAS

            try:
                response = client.open_stream(req_kwargs, timeout_seconds=request_timeout)
            except Exception:
                if use_tools:
                    # Some compatible backends do not implement tool calling.
                    use_tools = False
                    req_kwargs.pop("tools", None)
                    response = client.open_stream(req_kwargs, timeout_seconds=request_timeout)
                else:
                    raise

            turn_text_chunks: list[str] = []
            tool_calls_acc: dict[int, dict[str, str]] = {}

            for chunk in client.iter_sse(response):
                if job_id in self.canceled_jobs:
                    break
                choices = chunk.get("choices") if isinstance(chunk, dict) else None
                if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                    continue
                delta = choices[0].get("delta")
                if not isinstance(delta, dict):
                    continue

                reasoning = delta.get("reasoning_content") or delta.get("thinking")
                if reasoning:
                    self._emit_job_event(
                        job_id,
                        {"kind": "thinking_delta", "text": str(reasoning), "partial": True},
                    )

                content_piece = delta.get("content")
                if content_piece:
                    turn_text_chunks.append(str(content_piece))
                    self._emit_job_event(
                        job_id,
                        {"kind": "message_delta", "text": str(content_piece), "partial": True},
                    )

                delta_tcs = delta.get("tool_calls")
                if isinstance(delta_tcs, list):
                    for tc in delta_tcs:
                        if not isinstance(tc, dict):
                            continue
                        idx = int(tc.get("index", 0) or 0)
                        entry = tool_calls_acc.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                        tc_id = tc.get("id")
                        if tc_id:
                            entry["id"] = str(tc_id)
                        fn = tc.get("function")
                        if isinstance(fn, dict):
                            fn_name = fn.get("name")
                            if fn_name:
                                entry["name"] += str(fn_name)
                            fn_args = fn.get("arguments")
                            if fn_args:
                                entry["arguments"] += str(fn_args)

            turn_text = "".join(turn_text_chunks)
            if turn_text:
                collected_final_text.append(turn_text)

            if not tool_calls_acc:
                break

            # Execute requested tool calls and feed results back to the model
            assistant_tool_calls = []
            for idx in sorted(tool_calls_acc.keys()):
                tc_info = tool_calls_acc[idx]
                tc_id = tc_info["id"] or f"call_{uuid4().hex[:8]}"
                assistant_tool_calls.append({
                    "id": tc_id,
                    "type": "function",
                    "function": {
                        "name": tc_info["name"] or "ls",
                        "arguments": tc_info["arguments"] or "{}",
                    },
                })
            messages.append({
                "role": "assistant",
                "content": turn_text or None,
                "tool_calls": assistant_tool_calls,
            })

            for tc_obj in assistant_tool_calls:
                tc_id = tc_obj["id"]
                tname = tc_obj["function"]["name"]
                raw_args = tc_obj["function"]["arguments"]
                try:
                    parsed_args = json.loads(raw_args) if raw_args else {}
                except Exception:
                    parsed_args = {}
                summary = f"{tname}({json.dumps(parsed_args, ensure_ascii=False)[:120]})"

                allowed, is_mutating = self._check_and_record_tool_policy(
                    job_id=job_id,
                    tool_call_id=tc_id,
                    tool_name=tname,
                    tool_policy=tool_policy,
                    summary=summary,
                    approval_timeout_ms=approval_timeout_ms,
                )
                if not allowed:
                    tool_output_str = f"Error: Tool '{tname}' was blocked by tool_policy='{tool_policy}'."
                    self._emit_job_event(
                        job_id,
                        {
                            "kind": "tool_event",
                            "toolCallId": tc_id,
                            "toolName": tname,
                            "title": f"Blocked {tname} ({tool_policy})",
                            "status": "denied",
                            "text": summary,
                        },
                    )
                else:
                    if is_mutating:
                        side_effect_started = True
                    self._emit_job_event(
                        job_id,
                        {
                            "kind": "tool_event",
                            "toolCallId": tc_id,
                            "toolName": tname,
                            "title": f"Running {tname}",
                            "status": "running",
                            "text": summary,
                            "data": {"tool_call_id": tc_id, "args": parsed_args},
                        },
                    )
                    try:
                        res = toolset.execute(tname, parsed_args)
                        tool_output_str = str(res.get("output") or "(ok)")
                        self._emit_job_event(
                            job_id,
                            {
                                "kind": "tool_output",
                                "toolCallId": tc_id,
                                "toolName": tname,
                                "title": f"Finished {tname}",
                                "status": "done",
                                "text": tool_output_str[:2000],
                                "data": {"tool_call_id": tc_id, "args": parsed_args, "output": tool_output_str},
                            },
                        )
                    except Exception as exc:
                        tool_output_str = f"Tool error ({tname}): {exc}"
                        self._emit_job_event(
                            job_id,
                            {
                                "kind": "tool_error",
                                "toolCallId": tc_id,
                                "toolName": tname,
                                "title": f"Error in {tname}",
                                "status": "error",
                                "text": str(exc),
                                "data": {"tool_call_id": tc_id, "args": parsed_args, "is_error": True},
                            },
                        )

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": tool_output_str,
                })

        final_str = "\n".join(s for s in collected_final_text if s).strip()
        return (final_str if final_str else None), side_effect_started

    def _handle_run(self, req_id: str, job_id: str, session_key: str, payload: dict[str, Any]) -> None:
        prompt = str(payload.get("prompt") or "").strip()
        system_prompt = str(payload.get("systemPrompt") or "").strip()
        history = payload.get("history") if isinstance(payload.get("history"), list) else []
        thinking_level = str(payload.get("thinkingLevel") or "medium").strip()
        attachments = payload.get("attachments") or []
        cwd = str(payload.get("cwd") or self.project_root)
        tool_policy = str(payload.get("toolPolicy") or "approve_mutations").strip().lower()
        trusted_roots = payload.get("trustedRoots") if isinstance(payload.get("trustedRoots"), list) else ["."]
        approval_timeout_ms = int(payload.get("approvalTimeoutMs", 120000) or 120000)
        model_ref = str(payload.get("model") or "")

        self._emit_job_event(
            job_id,
            {"kind": "timeline_event", "title": f"Pi Sidecar ({model_ref or 'default'})", "status": "running"},
        )

        if job_id in self.canceled_jobs:
            self._emit({
                "v": 1,
                "type": "response",
                "requestId": req_id,
                "jobId": job_id,
                "ok": False,
                "error": {"code": "CANCELED", "message": "[Canceled]"},
            })
            return

        side_effect_started = False
        final_text: Optional[str] = None
        completion_error = {"code": "MODEL_UNAVAILABLE", "message": "No usable completion endpoint is configured or agent execution is disabled."}
        tool_outputs: list[str] = []
        toolset = PiTerminalToolset(cwd=cwd, trusted_roots=trusted_roots)

        # 1. Support direct `!tool` commands (`!ls`, `!read <path>`, `!grep <pattern>`, `!find <glob>`, `!bash <cmd>`)
        if prompt.startswith("!"):
            parts = prompt[1:].strip().split(maxsplit=1)
            tname = parts[0].lower() if parts else ""
            targ_raw = parts[1] if len(parts) > 1 else ""
            if tname in PI_TERMINAL_TOOLS:
                tcid = f"tc-{uuid4().hex[:6]}"
                allowed, is_mutating = self._check_and_record_tool_policy(
                    job_id=job_id,
                    tool_call_id=tcid,
                    tool_name=tname,
                    tool_policy=tool_policy,
                    summary=f"{tname}: {targ_raw[:120]}",
                    approval_timeout_ms=approval_timeout_ms,
                )
                if is_mutating and allowed:
                    side_effect_started = True
                if allowed:
                    self._emit_job_event(
                        job_id,
                        {"kind": "tool_event", "toolCallId": tcid, "toolName": tname, "title": f"Running {tname}", "status": "running"},
                    )
                    try:
                        args_map: dict[str, Any] = {}
                        if tname in ("read", "ls"):
                            args_map["path"] = targ_raw or "."
                        elif tname in ("grep", "find"):
                            args_map["pattern"] = targ_raw or "*"
                        elif tname == "bash":
                            args_map["command"] = targ_raw
                        res = toolset.execute(tname, args_map)
                        out_txt = str(res.get("output") or "")
                        tool_outputs.append(out_txt)
                        self._emit_job_event(
                            job_id,
                            {"kind": "tool_output", "toolCallId": tcid, "toolName": tname, "title": f"Finished {tname}", "status": "done", "text": out_txt},
                        )
                        final_text = f"### 🛠️ Pi Sidecar Tool `{tname}`\n\n```text\n{out_txt or '(empty)'}\n```"
                    except Exception as exc:
                        tool_outputs.append(f"Tool error ({tname}): {exc}")
                        self._emit_job_event(
                            job_id,
                            {"kind": "tool_error", "toolCallId": tcid, "toolName": tname, "title": f"Error {tname}", "status": "error", "text": str(exc)},
                        )
                        final_text = f"❌ Tool `{tname}` failed: `{exc}`"
                else:
                    final_text = f"⚠️ Tool `{tname}` blocked by `tool_policy='{tool_policy}'`."
                self._emit_job_event(
                    job_id,
                    {"kind": "message_delta", "text": final_text, "partial": True},
                )

        # 2. Primary Path: OpenAI-Compatible Chat Completions Agent Loop (`config.yaml` provider/model)
        if not final_text and os.environ.get("STC_DISABLE_OPENAI_PI_AGENT") != "1":
            endpoint = self._resolve_openai_endpoint_for_model(model_ref)
            # Only make live network calls when a real API key is configured or explicitly enabled
            if endpoint and (not endpoint["is_placeholder_key"] or os.environ.get("STC_FORCE_LIVE_PI_OPENAI") == "1"):
                try:
                    final_text, oai_side_effect = self._run_via_openai_completions_agent(
                        endpoint=endpoint,
                        prompt=prompt,
                        system_prompt=system_prompt,
                        history=history,
                        attachments=attachments if isinstance(attachments, list) else [],
                        job_id=job_id,
                        cwd=cwd,
                        thinking_level=thinking_level,
                        tool_policy=tool_policy,
                        trusted_roots=trusted_roots,
                        approval_timeout_ms=approval_timeout_ms,
                    )
                    if oai_side_effect:
                        side_effect_started = True
                except Exception as exc:
                    completion_error = {"code": "PROVIDER_ERROR", "message": "The completion provider failed."}
                    self._emit_job_event(
                        job_id,
                        {
                            "kind": "runtime_notice",
                            "title": "OpenAI Completion Endpoint Notice",
                            "text": str(exc),
                            "status": "error",
                        },
                    )

        # Missing/failed completions are failures, never simulated assistant replies.
        if job_id in self.canceled_jobs or not str(final_text or "").strip():
            if job_id in self.canceled_jobs:
                completion_error = {"code": "CANCELED", "message": "[Canceled]"}
            with self.state_lock:
                self.steered_jobs.pop(job_id, None)
                self.followup_jobs.pop(job_id, None)
            self._emit({
                "v": 1,
                "type": "response",
                "requestId": req_id,
                "jobId": job_id,
                "ok": False,
                "error": completion_error,
            })
            return

        self._emit({
            "v": 1,
            "type": "response",
            "requestId": req_id,
            "jobId": job_id,
            "ok": True,
            "data": {
                "text": final_text,
                "sessionKey": session_key,
                "piSessionAction": "opened",
                "sideEffectStarted": side_effect_started,
            },
        })

    def handle_frame(self, frame: dict[str, Any]) -> bool:
        cmd = str(frame.get("type") or "")
        req_id = str(frame.get("requestId") or "")
        job_id = str(frame.get("jobId") or "")
        session_key = str(frame.get("sessionKey") or "default")
        payload = frame.get("payload") if isinstance(frame.get("payload"), dict) else {}

        if cmd == "shutdown":
            if req_id:
                self._emit({"v": 1, "type": "response", "requestId": req_id, "ok": True, "data": {}})
            return False

        if cmd == "hello":
            self._emit({
                "v": 1,
                "type": "response",
                "requestId": req_id,
                "ok": True,
                "data": {
                    "protocols": [1],
                    "pid": os.getpid(),
                    "bridge": {"version": "2.4.0-pi-rpc"},
                    "sdk": {
                        "package": "@earendil-works/pi-coding-agent",
                        "version": "0.84.2",
                    },
                    "capabilities": {
                        "streaming": True,
                        "thinking": True,
                        "steer": True,
                        "followUp": True,
                        "nativeRpc": True,
                        "tools": list(PI_TERMINAL_TOOLS),
                    },
                    "limits": {
                        "maxFrameBytes": 1024 * 1024,
                        "maxRunFrameBytes": 28 * 1024 * 1024,
                        "maxImageBytes": 20 * 1024 * 1024,
                    },
                },
            })
            return True

        if cmd == "health":
            pi_cli = resolve_pi_cli()
            self._emit({
                "v": 1,
                "type": "response",
                "requestId": req_id,
                "ok": True,
                "data": {
                    "ok": True,
                    "mode": "pi-rpc",
                    "nativeCliInstalled": bool(pi_cli),
                    "nativeCliPath": pi_cli,
                    "tools": list(PI_TERMINAL_TOOLS),
                },
            })
            return True

        if cmd == "approval_response":
            approval_id = str(payload.get("approvalId") or "")
            approved = bool(payload.get("approved", False))
            with self.state_lock:
                holder = self.pending_approvals.get(approval_id)
            accepted = False
            if holder is not None:
                holder["approved"] = approved
                holder["event"].set()
                accepted = True
            if req_id:
                self._emit({
                    "v": 1,
                    "type": "response",
                    "requestId": req_id,
                    "jobId": job_id,
                    "ok": True,
                    "data": {"accepted": accepted, "approvalId": approval_id},
                })
            return True

        if cmd in ("reset_session", "close_session"):
            with self.state_lock:
                self.sessions.pop(session_key, None)
            self._emit({
                "v": 1,
                "type": "response",
                "requestId": req_id,
                "ok": True,
                "data": {"reset": True, "closed": True, "sessionKey": session_key},
            })
            return True

        if cmd == "cancel":
            target_job = str(payload.get("jobId") or job_id or "")
            if target_job:
                self.canceled_jobs.add(target_job)
                with self.state_lock:
                    proc = self.active_procs.get(target_job)
                if proc is not None and proc.stdin is not None:
                    try:
                        proc.stdin.write(json.dumps({"id": "abort1", "type": "abort"}) + "\n")
                        proc.stdin.flush()
                    except Exception:
                        pass
            if req_id:
                self._emit({
                    "v": 1,
                    "type": "response",
                    "requestId": req_id,
                    "jobId": target_job,
                    "ok": True,
                    "data": {"canceled": True, "jobId": target_job},
                })
            return True

        if cmd == "steer":
            target_job = str(payload.get("jobId") or job_id or "")
            text = str(payload.get("text") or payload.get("instruction") or "")
            with self.state_lock:
                self.steered_jobs.setdefault(target_job, []).append(text)
                proc = self.active_procs.get(target_job)
            if proc is not None and proc.stdin is not None and text:
                try:
                    proc.stdin.write(json.dumps({"id": "st1", "type": "steer", "message": text}) + "\n")
                    proc.stdin.flush()
                except Exception:
                    pass
            if req_id:
                self._emit({
                    "v": 1,
                    "type": "response",
                    "requestId": req_id,
                    "ok": True,
                    "data": {"steered": True},
                })
            return True

        if cmd == "follow_up":
            target_job = str(payload.get("jobId") or job_id or "")
            text = str(payload.get("text") or payload.get("instruction") or "")
            with self.state_lock:
                self.followup_jobs.setdefault(target_job, []).append(text)
                proc = self.active_procs.get(target_job)
            if proc is not None and proc.stdin is not None and text:
                try:
                    proc.stdin.write(json.dumps({"id": "fu1", "type": "follow_up", "message": text}) + "\n")
                    proc.stdin.flush()
                except Exception:
                    pass
            if req_id:
                self._emit({
                    "v": 1,
                    "type": "response",
                    "requestId": req_id,
                    "ok": True,
                    "data": {"queued": True},
                })
            return True

        if cmd == "run":
            threading.Thread(
                target=self._handle_run,
                args=(req_id, job_id, session_key, payload),
                daemon=True,
            ).start()
            return True

        if req_id:
            self._emit({
                "v": 1,
                "type": "response",
                "requestId": req_id,
                "ok": True,
                "data": {},
            })
        return True

    def serve_stdio(self) -> None:
        for raw_line in sys.stdin:
            line = raw_line.strip()
            if not line:
                continue
            try:
                frame = json.loads(line)
            except Exception:
                continue
            if isinstance(frame, dict):
                if not self.handle_frame(frame):
                    break


if __name__ == "__main__":
    NativePiRpcServer().serve_stdio()
