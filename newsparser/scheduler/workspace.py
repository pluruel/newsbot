import os
from pathlib import Path

CATEGORIES = ("tech", "markets")


def ensure_workspace() -> Path:
    """Create all required workspace directories and template files. Returns workspace root."""
    root = Path(os.environ.get("WORKSPACE_DIR", "workspace"))

    for subdir in ["input", "cycles", "me", "state", "state/locks", "logs", "sessions", "briefs"]:
        (root / subdir).mkdir(parents=True, exist_ok=True)

    for category in CATEGORIES:
        (root / "input" / category).mkdir(parents=True, exist_ok=True)
        (root / "cycles" / category).mkdir(parents=True, exist_ok=True)

    # Per-category interest templates. Created only if missing.
    for category in CATEGORIES:
        path = root / "me" / f"interests_{category}.md"
        if not path.exists():
            path.write_text(_interests_template(category), encoding="utf-8")

    manifesto = root / "me" / "manifesto.md"
    if not manifesto.exists():
        manifesto.write_text("", encoding="utf-8")

    return root


def _interests_template(category: str) -> str:
    label = {"tech": "Tech", "markets": "Markets"}[category]
    return (
        f"# Interests Profile — {label}\n"
        f"Last updated: (manual)\n\n"
        f"## Themes\n\n"
        f"| Theme | interest_weight | familiarity_weight | Notes |\n"
        f"|---|---|---|---|\n\n"
        f"## User overrides\n"
    )
