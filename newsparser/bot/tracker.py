import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from newsparser.claude.runner import run_claude

logger = logging.getLogger(__name__)

HISTORY_MAX_TURNS = 10

_MCP_CONFIG = Path(__file__).parent.parent.parent / "mcp.json"


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


def _needs_history(query: str, last_user: str, last_assistant: str) -> bool:
    """Use haiku to quickly decide if the query requires prior context."""
    prompt = (
        "Does the new query require the previous exchange to answer correctly? "
        "Reply with only 'yes' or 'no'.\n\n"
        f"Previous exchange:\nUser: {last_user}\nAssistant: {last_assistant}\n\n"
        f"New query: {query}"
    )
    try:
        result = run_claude(prompt, timeout=30, model="claude-haiku-4-5-20251001")
        return result.strip().lower().startswith("yes")
    except Exception:
        return True


def run_tracker(chat_id: str, query: str) -> str:
    """Resolve a user query using Claude with MCP tools."""
    history = load_history(chat_id)

    prev_context = ""
    if len(history) >= 2:
        last_user = history[-2]
        last_assistant = history[-1]
        if last_user["role"] == "user" and last_assistant["role"] == "assistant":
            if _needs_history(query, last_user["content"], last_assistant["content"]):
                prev_context = (
                    "\n\nPrevious exchange:\n"
                    f"User: {last_user['content']}\n"
                    f"Assistant: {last_assistant['content']}\n"
                )

    prompt = (
        "You are a market intelligence assistant. Use the available tools "
        "to gather relevant context, then answer the user's query. "
        "Cite cycle reports by date. Lead with TL;DR if the answer is long."
        f"{prev_context}\n\n"
        f"User query: {query}"
    )

    answer = run_claude(prompt, mcp_config=str(_MCP_CONFIG))

    _ADMIN_MARKERS = ("interests.md updated", "manifesto.md updated", "cleared", "interest-events.jsonl")
    if not any(marker in answer for marker in _ADMIN_MARKERS):
        now = datetime.now(timezone.utc).isoformat()
        new_turns = history + [
            {"role": "user", "content": query, "ts": now},
            {"role": "assistant", "content": answer, "ts": now},
        ]
        save_history(chat_id, new_turns)
    return answer
