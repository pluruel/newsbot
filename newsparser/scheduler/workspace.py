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

    ignore = root / "me" / "ignore.md"
    if not ignore.exists():
        ignore.write_text(_ignore_template(), encoding="utf-8")

    return root


def _ignore_template() -> str:
    return (
        "# 무시 목록 (ignore list)\n\n"
        "봇이 이 목록의 대상을 사이클 분석·다이제스트·그래프·텔레그램에서 제외한다.\n"
        '"무시: <대상>" 추가 · "무시 해제: <대상>" 삭제 · "차단 리스트" 조회.\n\n'
        "| 종류 | 대상 | 추가일 | 메모 |\n"
        "|------|------|--------|------|\n"
    )


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
