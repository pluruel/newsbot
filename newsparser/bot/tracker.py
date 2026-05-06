import json
import logging
import os
from datetime import datetime
from pathlib import Path

from newsparser.claude.runner import run_claude
from newsparser.graph.traversal import get_context, get_influence_chain, format_context_for_claude

logger = logging.getLogger(__name__)

HISTORY_MAX_TURNS = 10


def _workspace() -> Path:
    return Path(os.environ.get("WORKSPACE_DIR", "workspace"))


def load_history(chat_id: str) -> list[dict]:
    path = _workspace() / "sessions" / f"{chat_id}.jsonl"
    if not path.exists():
        return []
    turns = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return turns[-HISTORY_MAX_TURNS:]


def save_history(chat_id: str, turns: list[dict]) -> None:
    path = _workspace() / "sessions" / f"{chat_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(t, ensure_ascii=False) for t in turns))


def _log_interest_event(query: str, entities: list[str]) -> None:
    event = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "type": "query",
        "entities": entities,
        "themes": [query[:50]],
        "depth": "shallow",
    }
    path = _workspace() / "me" / "interest-events.jsonl"
    with path.open("a") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def run_tracker(chat_id: str, query: str) -> str:
    """Resolve a user query using graph context + history + Claude."""
    history = load_history(chat_id)

    # Graph traversal — use first noun-like word as entity hint
    entity_hint = query.split()[0] if query.split() else query
    neighbors = []
    try:
        neighbors = get_context(entity_hint, days=7)
        chains = get_influence_chain(entity_hint)
        graph_ctx = format_context_for_claude(entity_hint, neighbors, chains)
    except Exception:
        logger.warning("Graph traversal failed for %r — proceeding without context", entity_hint)
        graph_ctx = ""

    # Build history block
    history_block = ""
    if history:
        history_block = "\n## Conversation history\n" + "\n".join(
            f"{t['role'].upper()}: {t['content']}" for t in history
        )

    prompt = (
        f"/tracker\n\n"
        f"## User query\n{query}\n\n"
        f"{graph_ctx}"
        f"{history_block}"
    )

    answer = run_claude(prompt)

    # Persist history
    now = datetime.utcnow().isoformat()
    new_turns = history + [
        {"role": "user", "content": query, "ts": now},
        {"role": "assistant", "content": answer, "ts": now},
    ]
    save_history(chat_id, new_turns)

    hit_entities = [n["name"] for n in neighbors]
    _log_interest_event(query, hit_entities)
    return answer
