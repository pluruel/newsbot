"""System prompts for the scheduled jobs that call the API directly (no tools).

The analysis rules live in ``{cycle,weekly,reflect}.md`` next to this file; the
Style section is read from the repo-root CLAUDE.md rather than copied, so the
tracker path and the scheduled path stay on the same wording.
"""

from pathlib import Path

_CLAUDE_MD = Path(__file__).resolve().parents[2] / "CLAUDE.md"


def style_rules() -> str:
    """Body of CLAUDE.md's ``## Style`` section (header excluded)."""
    lines = _CLAUDE_MD.read_text(encoding="utf-8").splitlines()
    body: list[str] = []
    in_style = False
    for line in lines:
        if line.startswith("## "):
            if in_style:
                break
            in_style = line.strip() == "## Style"
            continue
        if in_style:
            body.append(line)
    return "\n".join(body).strip()


def system_prompt(name: str) -> str:
    """Style rules followed by the ``name`` job spec (cycle / weekly / reflect)."""
    spec = (Path(__file__).parent / f"{name}.md").read_text(encoding="utf-8")
    return style_rules() + "\n\n" + spec
