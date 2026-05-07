import os
import subprocess


class ClaudeError(RuntimeError):
    pass


# Resolve at call time. Override with `CLAUDE_BIN` env var when PATH lookup fails
# (e.g. systemd services with minimal PATH).
def _claude_bin() -> str:
    return os.environ.get("CLAUDE_BIN", "claude")


def run_claude(prompt: str, timeout: int = 1500, mcp_config: str | None = None, model: str = "claude-sonnet-4-6") -> str:
    """Invoke claude CLI headless and return stdout. Raises ClaudeError on failure."""
    cmd = [_claude_bin(), "-p", prompt, "--output-format", "text", "--model", model]
    if mcp_config is not None:
        cmd += ["--mcp-config", mcp_config]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise ClaudeError(f"claude exited {result.returncode}: stderr={result.stderr[:500]} stdout={result.stdout[:500]}")
    return result.stdout
