import os
from pathlib import Path


def ensure_workspace() -> Path:
    """Create all required workspace directories if missing. Returns workspace root."""
    root = Path(os.environ.get("WORKSPACE_DIR", "workspace"))
    for subdir in ["cycles", "briefs", "input", "me", "state", "logs", "sessions"]:
        (root / subdir).mkdir(parents=True, exist_ok=True)
    for template in ["me/interests.md", "me/manifesto.md"]:
        p = root / template
        if not p.exists():
            p.write_text("")
    return root
