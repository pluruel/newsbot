import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from newsparser.claude.runner import run_claude

logger = logging.getLogger(__name__)

HISTORY_MAX_TURNS = 10

_MCP_CONFIG = Path(__file__).parent.parent / "mcp.json"


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


def run_tracker(chat_id: str, query: str) -> str:
    """Resolve a user query using Claude with MCP tools."""
    history = load_history(chat_id)

    prompt = (
        "You are a market intelligence assistant. Use the available tools "
        "to gather relevant context, then answer the user's query. "
        "Cite cycle reports by date. Lead with TL;DR if the answer is long.\n\n"
        f"User query: {query}\n"
        f"Chat ID (for history tool): {chat_id}"
    )

    answer = run_claude(prompt, mcp_config=str(_MCP_CONFIG))

    now = datetime.now(timezone.utc).isoformat()
    new_turns = history + [
        {"role": "user", "content": query, "ts": now},
        {"role": "assistant", "content": answer, "ts": now},
    ]
    save_history(chat_id, new_turns)
    return answer
