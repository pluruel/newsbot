import json
import os
from datetime import datetime, timezone
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from newsparser.graph.traversal import get_context, get_influence_chain, format_context_for_claude

mcp = FastMCP("newsparser")


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
def read_interests() -> str:
    """Read the user's interest profile."""
    path = _workspace() / "me" / "interests.md"
    if not path.exists():
        return "No interests file found."
    return path.read_text()


if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=8766)
