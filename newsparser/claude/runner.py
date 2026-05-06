import subprocess


class ClaudeError(RuntimeError):
    pass


def run_claude(prompt: str, timeout: int = 1500) -> str:
    """Invoke claude CLI headless and return stdout. Raises ClaudeError on failure."""
    result = subprocess.run(
        ["claude", "-p", prompt, "--output-format", "text"],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise ClaudeError(f"claude exited {result.returncode}: {result.stderr[:500]}")
    return result.stdout
