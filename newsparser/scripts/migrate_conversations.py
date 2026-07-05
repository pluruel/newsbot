"""One-time migration of the pre-SQLite conversation state into ``conversations.db``.

Two legacy sources under ``workspace/`` are folded into the new store:

  * ``sessions/{chat_id}.jsonl`` — chat turns ``{"role","content","ts"}`` → ``messages``
  * ``me/interest-events.jsonl`` — ``{"ts","themes",...}`` → ``interest_events``

Idempotent and re-runnable:
  * message rows get a deterministic id (uuid5 of chat_id + position + role + ts +
    content) and are inserted ``OR IGNORE``, so a re-run never duplicates a turn even
    if a previous run died mid-file;
  * each processed source file is renamed to ``<name>.migrated`` so a re-run skips it.

Turns are inserted in file order; the old writer stamped a user turn and its answer
with the same ts, so ``(ts, rowid)`` ordering (what ``iter_all_messages`` uses) only
stays correct if we preserve arrival order — hence the file-order insert. Each
assistant turn's ``reply_to_id`` is inferred positionally (the immediately preceding
user turn), matching the old ``_extract_pairs`` pairing; older files never stored
admin turns, so everything is ``kind='chat'``.

Run on the deploy host after ``uv sync`` and before serving traffic:

    .venv/bin/python -m newsparser.scripts.migrate_conversations
"""

import json
import logging
import sys
import uuid
from pathlib import Path

from newsparser.paths import workspace_dir as _workspace
from newsparser.store import conversations as conv

logger = logging.getLogger(__name__)

# Fixed namespace so ids are stable across runs (uuid5 is deterministic).
_NS = uuid.UUID("6f9b1e2a-0000-4000-8000-000000000001")


def _msg_id(chat_id: str, index: int, role: str, ts: str, content: str) -> str:
    return uuid.uuid5(_NS, f"{chat_id}\x1f{index}\x1f{role}\x1f{ts}\x1f{content}").hex


def _migrate_session_file(path: Path) -> tuple[int, int]:
    """Migrate one ``{chat_id}.jsonl`` file. Returns (turns_seen, rows_inserted)."""
    chat_id = path.stem
    turns: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            turns.append(json.loads(line))
        except json.JSONDecodeError:
            logger.warning("skipping malformed line in %s", path.name)

    ids: list[str] = []
    inserted = 0
    for i, t in enumerate(turns):
        role = t.get("role") or "user"
        content = t.get("content") or ""
        ts = t.get("ts") or ""
        mid = _msg_id(chat_id, i, role, ts, content)
        # Positional pairing: an assistant turn answers the immediately preceding
        # user turn (the old _extract_pairs rule).
        reply_to = None
        if role == "assistant" and i > 0 and turns[i - 1].get("role") == "user":
            reply_to = ids[i - 1]
        if conv.import_message(mid, chat_id, role, content, ts,
                               reply_to_id=reply_to, kind="chat"):
            inserted += 1
        ids.append(mid)

    path.rename(path.with_suffix(path.suffix + ".migrated"))
    return len(turns), inserted


def migrate_sessions(workspace: Path) -> tuple[int, int]:
    sessions_dir = workspace / "sessions"
    if not sessions_dir.is_dir():
        return 0, 0
    total_turns = total_inserted = 0
    for path in sorted(sessions_dir.glob("*.jsonl")):
        turns, inserted = _migrate_session_file(path)
        total_turns += turns
        total_inserted += inserted
        logger.info("session %s: %d turns, %d new rows", path.stem, turns, inserted)
    return total_turns, total_inserted


def migrate_interest_events(workspace: Path) -> int:
    path = workspace / "me" / "interest-events.jsonl"
    if not path.exists():
        return 0
    inserted = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("skipping malformed interest event line")
            continue
        themes = ev.get("themes") or ev.get("entities") or []
        if not themes:
            continue
        conv.log_interest_event(
            themes[0],
            themes=themes,
            type=ev.get("type", "query"),
            depth=ev.get("depth", "shallow"),
            ts=ev.get("ts"),
        )
        inserted += len(themes)
    # Commit-then-rename: the rows are already persisted above, so renaming last
    # means a re-run skips this file (its rows are non-idempotent, unlike messages).
    path.rename(path.with_suffix(path.suffix + ".migrated"))
    return inserted


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ws = _workspace()
    conv.init_conv_db()
    turns, msg_rows = migrate_sessions(ws)
    event_rows = migrate_interest_events(ws)
    logger.info(
        "migration done: %d session turns (%d new message rows), %d interest-event rows",
        turns, msg_rows, event_rows,
    )
    logger.info(
        "Neo4j is a derived projection — run "
        "`from newsparser.graph.conversation_projector import reproject_all; reproject_all()` "
        "once Neo4j is up to build the graph from the migrated store."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
