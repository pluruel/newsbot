import json
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from newsparser.graph.traversal import get_context, get_influence_chain, format_context_for_claude

mcp = FastMCP("newsparser", host="0.0.0.0", port=8766)


def _workspace() -> Path:
    return Path(os.environ.get("WORKSPACE_DIR", "workspace"))


def _log_interest_event(entity: str) -> None:
    event = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "type": "query",
        "entities": [entity],
        "themes": [entity],
        "depth": "shallow",
    }
    path = _workspace() / "me" / "interest-events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


@mcp.tool()
def graph_query(entity: str, days: int = 7) -> str:
    """Query the knowledge graph for context about an entity."""
    neighbors = get_context(entity, days)
    chains = get_influence_chain(entity)
    _log_interest_event(entity)
    return format_context_for_claude(entity, neighbors, chains)


@mcp.tool()
def read_cycle_reports(n: int = 4) -> str:
    """Read the N most recent cycle reports."""
    cycles_dir = _workspace() / "cycles"
    if not cycles_dir.exists():
        return "No cycle reports found."
    files = sorted(cycles_dir.glob("*.md"), reverse=True)[:n]
    if not files:
        return "No cycle reports found."
    return "\n\n---\n\n".join(f.read_text() for f in reversed(files))


@mcp.tool()
def read_conversation_history(chat_id: str, n: int = 10) -> str:
    """Read recent conversation turns for a given chat."""
    from newsparser.bot.tracker import load_history
    history = load_history(chat_id)[-n:]
    if not history:
        return "No conversation history."
    return "\n".join(f"{t['role'].upper()}: {t['content']}" for t in history)


@mcp.tool()
def get_interest_weights(days: int = 14) -> str:
    """Compare actual interest weights vs estimated weights from recent tracker queries."""
    interests_path = _workspace() / "me" / "interests.md"
    events_path = _workspace() / "me" / "interest-events.jsonl"

    # Parse actual weights from interests.md
    actual: dict[str, dict] = {}
    if interests_path.exists():
        for line in interests_path.read_text().splitlines():
            if not line.startswith("|"):
                continue
            parts = [p.strip() for p in line.split("|") if p.strip()]
            if len(parts) < 3 or parts[0] in ("Theme", "") or set(parts[0]) <= set("-"):
                continue
            try:
                actual[parts[0]] = {
                    "interest": float(parts[1]),
                    "familiarity": float(parts[2]),
                }
            except ValueError:
                continue

    # Estimate weights from recent events
    estimated: dict[str, float] = {}
    if events_path.exists():
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        counts: Counter = Counter()
        for line in events_path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                e = json.loads(line)
                ts = datetime.fromisoformat(e["ts"].replace("Z", "+00:00"))
                if ts < cutoff:
                    continue
                for theme in e.get("themes", []):
                    counts[theme] += 1
            except (json.JSONDecodeError, KeyError, ValueError):
                continue

        if counts:
            max_count = max(counts.values())
            for theme, count in counts.items():
                estimated[theme] = round(count / max_count, 2)

    if not actual and not estimated:
        return "No data found."

    all_themes = sorted(set(actual) | set(estimated))
    lines = [f"Interest weight comparison (estimated from last {days} days of queries)\n"]
    lines.append(f"{'Theme':<30} {'actual':>8} {'estimated':>10} {'diff':>6}")
    lines.append("-" * 58)
    for theme in all_themes:
        a = actual.get(theme, {}).get("interest", None)
        e = estimated.get(theme, None)
        a_str = f"{a:.2f}" if a is not None else "  —  "
        e_str = f"{e:.2f}" if e is not None else "  —  "
        if a is not None and e is not None:
            diff = e - a
            diff_str = f"{diff:+.2f}"
        else:
            diff_str = "  —  "
        lines.append(f"{theme:<30} {a_str:>8} {e_str:>10} {diff_str:>6}")

    return "\n".join(lines)


@mcp.tool()
def clear_interest_events() -> str:
    """Clear the interest-events.jsonl query log (resets weight estimation baseline)."""
    path = _workspace() / "me" / "interest-events.jsonl"
    if not path.exists():
        return "No interest events file found."
    path.write_text("")
    return "interest-events.jsonl cleared."


@mcp.tool()
def clear_conversation_history() -> str:
    """Clear all conversation history."""
    sessions_dir = _workspace() / "sessions"
    if not sessions_dir.exists():
        return "No sessions found."
    files = list(sessions_dir.glob("*.jsonl"))
    for f in files:
        f.write_text("")
    return f"Conversation history cleared ({len(files)} sessions)."


@mcp.tool()
def read_interests() -> str:
    """Read the user's interest profile."""
    path = _workspace() / "me" / "interests.md"
    if not path.exists():
        return "No interests file found."
    return path.read_text()


@mcp.tool()
def write_interests(content: str) -> str:
    """Overwrite the user's interests.md. Preserve ## User overrides section."""
    path = _workspace() / "me" / "interests.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, ensure_ascii=False)
    return "interests.md updated."


@mcp.tool()
def read_manifesto() -> str:
    """Read the user's manifesto (perspective/goals)."""
    path = _workspace() / "me" / "manifesto.md"
    if not path.exists():
        return "No manifesto found."
    return path.read_text()


@mcp.tool()
def write_manifesto(content: str) -> str:
    """Overwrite the user's manifesto.md."""
    path = _workspace() / "me" / "manifesto.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, ensure_ascii=False)
    return "manifesto.md updated."


if __name__ == "__main__":
    mcp.run(transport="sse")
