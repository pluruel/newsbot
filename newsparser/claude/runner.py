import subprocess


class ClaudeError(RuntimeError):
    pass


def run_claude(prompt: str, timeout: int = 1500, mcp_config: str | None = None) -> str:
    """Invoke claude CLI headless and return stdout. Raises ClaudeError on failure."""
    cmd = ["claude", "-p", prompt, "--output-format", "text", "--model", "claude-sonnet-4-6"]
    if mcp_config:
        cmd += ["--mcp-config", mcp_config]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise ClaudeError(f"claude exited {result.returncode}: {result.stderr[:500]}")
    return result.stdout
