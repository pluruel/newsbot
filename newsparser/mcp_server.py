import json
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from newsparser.graph.traversal import get_context, get_influence_chain, format_context_for_claude
from newsparser.classifier import classify_query as _classify_query_impl

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
def graph_query(entity: str, category: str | None = None, days: int = 7) -> str:
    """Query the knowledge graph for context about an entity.
    Pass `category='tech'` or `category='markets'` to restrict traversal."""
    neighbors = get_context(entity, days, category=category)
    chains = get_influence_chain(entity, category=category)
    _log_interest_event(entity)
    return format_context_for_claude(entity, neighbors, chains)


@mcp.tool()
def read_cycle_reports(category: str, n: int = 4) -> str:
    """Read the N most recent cycle reports for the given category ('tech' or 'markets')."""
    cycles_dir = _workspace() / "cycles" / category
    if not cycles_dir.exists():
        return f"No cycle reports found for category={category}."
    files = sorted(cycles_dir.glob("*.md"), reverse=True)[:n]
    if not files:
        return f"No cycle reports found for category={category}."
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
def get_interest_weights(category: str, days: int = 14) -> str:
    """Compare actual vs estimated weights for a category's interest profile."""
    interests_path = _workspace() / "me" / f"interests_{category}.md"
    events_path = _workspace() / "me" / "interest-events.jsonl"

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
        return f"No data found for category={category}."

    all_themes = sorted(set(actual) | set(estimated))
    lines = [f"Interest weight comparison for category={category} (last {days} days)\n"]
    lines.append(f"{'Theme':<30} {'actual':>8} {'estimated':>10} {'diff':>6}")
    lines.append("-" * 58)
    for theme in all_themes:
        a = actual.get(theme, {}).get("interest", None)
        e = estimated.get(theme, None)
        a_str = f"{a:.2f}" if a is not None else "  —  "
        e_str = f"{e:.2f}" if e is not None else "  —  "
        diff_str = f"{(e - a):+.2f}" if (a is not None and e is not None) else "  —  "
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
def read_interests(category: str) -> str:
    """Read the per-category interest profile."""
    path = _workspace() / "me" / f"interests_{category}.md"
    if not path.exists():
        return f"No interests file found for category={category}."
    return path.read_text()


@mcp.tool()
def write_interests(category: str, content: str) -> str:
    """Overwrite a per-category interests file."""
    path = _workspace() / "me" / f"interests_{category}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return f"interests_{category}.md updated."


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


@mcp.tool()
def classify_query(query: str) -> str:
    """Return the category the query is most likely about: 'tech', 'markets', or 'both'."""
    return _classify_query_impl(query)


if __name__ == "__main__":
    mcp.run(transport="sse")
