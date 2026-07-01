import os
import subprocess
import json
from pathlib import Path

# Project root: two levels above this file (newsparser/claude/runner.py → project root)
_PROJECT_ROOT = Path(__file__).parent.parent.parent


class ClaudeError(RuntimeError):
    pass


# Resolve at call time. Override with `CLAUDE_BIN` env var when PATH lookup fails
# (e.g. systemd services with minimal PATH).
def _claude_bin() -> str:
    return os.environ.get("CLAUDE_BIN", "claude")


def _extra_perm_args(
    allowed_tools: list[str] | None,
    permission_mode: str | None,
) -> list[str]:
    args: list[str] = []
    if allowed_tools:
        args += ["--allowedTools", ",".join(allowed_tools)]
    if permission_mode is not None:
        args += ["--permission-mode", permission_mode]
    return args


def run_claude(
    prompt: str,
    timeout: int = 1500,
    mcp_config: str | None = None,
    model: str = "claude-sonnet-5",
    system_prompt: str | None = None,
    allowed_tools: list[str] | None = None,
    permission_mode: str | None = None,
) -> str:
    """Invoke claude CLI headless and return stdout. Raises ClaudeError on failure."""
    cmd = [_claude_bin(), "-p", prompt, "--output-format", "text", "--model", model]
    if mcp_config is not None:
        cmd += ["--mcp-config", mcp_config]
    if system_prompt is not None:
        cmd += ["--system-prompt", system_prompt]
    cmd += _extra_perm_args(allowed_tools, permission_mode)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=_PROJECT_ROOT)
    if result.returncode != 0:
        raise ClaudeError(f"claude exited {result.returncode}: stderr={result.stderr[:500]} stdout={result.stdout[:500]}")
    return result.stdout


def run_claude_json(
    prompt: str,
    timeout: int = 1500,
    mcp_config: str | None = None,
    model: str = "claude-sonnet-5",
    system_prompt: str | None = None,
    allowed_tools: list[str] | None = None,
    permission_mode: str | None = None,
) -> tuple[str, dict]:
    """Like run_claude() but uses --output-format json. Returns (text, meta)."""
    cmd = [_claude_bin(), "-p", prompt, "--output-format", "json", "--model", model]
    if mcp_config is not None:
        cmd += ["--mcp-config", mcp_config]
    if system_prompt is not None:
        cmd += ["--system-prompt", system_prompt]
    cmd += _extra_perm_args(allowed_tools, permission_mode)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=_PROJECT_ROOT)
    if result.returncode != 0:
        raise ClaudeError(f"claude exited {result.returncode}: stderr={result.stderr[:500]}")
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ClaudeError(f"claude returned non-JSON output: {result.stdout[:200]}") from exc
    text = data.get("result", "")
    meta = {
        "duration_ms": data.get("duration_ms"),
        "input_tokens": (data.get("usage") or {}).get("input_tokens"),
        "output_tokens": (data.get("usage") or {}).get("output_tokens"),
        "cost_usd": data.get("cost_usd"),
    }
    return text, meta
