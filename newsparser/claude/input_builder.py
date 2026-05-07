import os
from pathlib import Path

from newsparser.store.sqlite import get_unprocessed


def build_input_file(slot: str, category: str) -> Path:
    """Read unprocessed articles for `category` and write input.md for Claude.
    Returns the file path."""
    workspace = Path(os.environ.get("WORKSPACE_DIR", "workspace"))
    articles = get_unprocessed(category=category)

    lines = [
        f"# Input {slot} KST [{category}]",
        f"## Collected Articles ({len(articles)} total)",
    ]
    for a in articles:
        body = (a["body"] or "").replace("\n", "\n  ")
        lines += [
            f"\n### [{a['source']}] {a['title']}",
            f"- URL: {a['url']}",
            f"- Published: {a['published'] or 'unknown'}",
            f"- Body:\n  {body}",
        ]

    path = workspace / "input" / category / f"{slot}-input.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
