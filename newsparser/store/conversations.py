"""Conversation storage — SQLite source of truth for Telegram chat turns.

Replaces the old per-chat ``workspace/sessions/{chat_id}.jsonl`` files. Each turn
is a row in ``messages`` with a stable ``id`` and a ``reply_to_id`` pointer to the
turn it answers/follows, so threading is a DAG (adjacency list) rather than line
order — messages need not arrive strictly user→assistant→user. A single INSERT per
turn is atomic, so concurrent messages can no longer lose turns the way the old
full-file rewrite could.

Kept in its own DB file (``workspace/conversations.db`` by default) separate from
the article store (``newsparser.db``) and the market store (``market.db``).
``backup.sh`` snapshots every ``workspace/*.db`` so no backup-script change is needed.
"""

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, Iterator

from newsparser.paths import workspace_dir

_DEFAULT_KINDS = ("chat",)

# How long a blocked writer waits for the WAL lock before giving up (also the
# sqlite3.connect timeout). Reprojection scans + concurrent tracker writes can
# briefly overlap; a few seconds of patience avoids losing a turn.
_BUSY_TIMEOUT_S = 5.0


def _db_path() -> str:
    # CONV_DB_PATH wins; otherwise derive from WORKSPACE_DIR so tests that set
    # WORKSPACE_DIR get an isolated DB without extra wiring.
    return os.environ.get("CONV_DB_PATH") or str(workspace_dir() / "conversations.db")


def _connect() -> sqlite3.Connection:
    path = _db_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=_BUSY_TIMEOUT_S)
    conn.row_factory = sqlite3.Row
    # WAL lets readers (the reproject/demand scans) and a writer (the tracker
    # thread) proceed concurrently instead of blocking each other; busy_timeout
    # makes a writer wait for a lock rather than raising "database is locked"
    # immediately. The article/market stores set the same pragmas — several
    # processes (dispatcher, MCP server, reflect/weekly) touch this file at once.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute(f"PRAGMA busy_timeout = {int(_BUSY_TIMEOUT_S * 1000)}")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def _connection() -> Generator[sqlite3.Connection, None, None]:
    _ensure()
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# Lazily create the schema the first time a given DB path is touched, so callers
# (tracker, MCP tools, tests) don't each have to remember to call init_conv_db.
# Keyed by path so a monkeypatched CONV_DB_PATH in tests re-initializes.
_initialized: set[str] = set()


def _ensure() -> None:
    path = _db_path()
    if path not in _initialized:
        init_conv_db()
        _initialized.add(path)


def init_conv_db() -> None:
    """Create the messages table, its trigram FTS index, and sync triggers.
    Idempotent — safe to call on every startup (mirrors store.sqlite.init_db)."""
    conn = _connect()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id          TEXT PRIMARY KEY,
                chat_id     TEXT NOT NULL,
                role        TEXT NOT NULL,
                content     TEXT NOT NULL,
                ts          TEXT NOT NULL,
                reply_to_id TEXT REFERENCES messages(id),
                kind        TEXT NOT NULL DEFAULT 'chat',
                meta        TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_messages_chat_ts
                ON messages(chat_id, ts);
            CREATE INDEX IF NOT EXISTS idx_messages_reply
                ON messages(reply_to_id);

            -- trigram tokenizer gives indexed substring search that works for
            -- mixed Korean/English content (queries of >= 3 characters).
            CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                content,
                content='messages',
                content_rowid='rowid',
                tokenize='trigram'
            );

            CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
                INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
            END;
            CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
                INSERT INTO messages_fts(messages_fts, rowid, content)
                    VALUES('delete', old.rowid, old.content);
            END;
            CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
                INSERT INTO messages_fts(messages_fts, rowid, content)
                    VALUES('delete', old.rowid, old.content);
                INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
            END;

            -- Conversation-derived interest signal: one row per theme the user
            -- queried about (logged when the chat agent calls graph_query).
            -- Replaces the old workspace/me/interest-events.jsonl so all
            -- conversation-derived data lives in one SQLite file; existing data is
            -- carried over by newsparser/scripts/migrate_conversations.py.
            CREATE TABLE IF NOT EXISTS interest_events (
                id    INTEGER PRIMARY KEY AUTOINCREMENT,
                ts    TEXT NOT NULL,
                theme TEXT NOT NULL,
                type  TEXT NOT NULL DEFAULT 'query',
                depth TEXT NOT NULL DEFAULT 'shallow'
            );
            CREATE INDEX IF NOT EXISTS idx_interest_events_ts
                ON interest_events(ts);
            """
        )
        conn.commit()
    finally:
        conn.close()


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    if d.get("meta"):
        try:
            d["meta"] = json.loads(d["meta"])
        except (json.JSONDecodeError, TypeError):
            pass
    return d


def add_message(
    chat_id: str,
    role: str,
    content: str,
    *,
    reply_to_id: str | None = None,
    kind: str = "chat",
    meta: dict | None = None,
    ts: str | None = None,
) -> str:
    """Append one turn and return its generated id.

    A single INSERT — atomic, so concurrent writers can't clobber each other the
    way the old read-modify-write JSONL rewrite did. ``reply_to_id`` records which
    message this one answers/follows (the DAG edge); ``kind`` separates real chat
    from admin/report turns.
    """
    msg_id = uuid.uuid4().hex
    ts = ts or datetime.now(timezone.utc).isoformat()
    with _connection() as conn:
        conn.execute(
            """INSERT INTO messages
               (id, chat_id, role, content, ts, reply_to_id, kind, meta)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                msg_id, chat_id, role, content, ts, reply_to_id, kind,
                json.dumps(meta, ensure_ascii=False) if meta is not None else None,
            ),
        )
    return msg_id


def import_message(
    id: str,
    chat_id: str,
    role: str,
    content: str,
    ts: str,
    *,
    reply_to_id: str | None = None,
    kind: str = "chat",
) -> bool:
    """Insert a message with a caller-supplied id (``INSERT OR IGNORE``), for one-time
    data migration only — normal writes use ``add_message``. Idempotent: returns True
    if a new row was written, False if the id already existed."""
    with _connection() as conn:
        cur = conn.execute(
            """INSERT OR IGNORE INTO messages
               (id, chat_id, role, content, ts, reply_to_id, kind, meta)
               VALUES (?, ?, ?, ?, ?, ?, ?, NULL)""",
            (id, chat_id, role, content, ts, reply_to_id, kind),
        )
        return cur.rowcount > 0


def get_recent_messages(
    chat_id: str,
    n: int = 10,
    kinds: tuple[str, ...] = _DEFAULT_KINDS,
) -> list[dict]:
    """Return the most recent ``n`` turns for a chat, oldest-first.

    Defaults to ``kind='chat'`` so admin/report turns are excluded from the
    conversational context (they are still stored for audit)."""
    placeholders = ",".join("?" for _ in kinds)
    with _connection() as conn:
        rows = conn.execute(
            f"""SELECT * FROM messages
                WHERE chat_id = ? AND kind IN ({placeholders})
                ORDER BY ts DESC, rowid DESC LIMIT ?""",
            (chat_id, *kinds, n),
        ).fetchall()
    return [_row_to_dict(r) for r in reversed(rows)]


def get_message(message_id: str) -> dict | None:
    with _connection() as conn:
        row = conn.execute(
            "SELECT * FROM messages WHERE id = ?", (message_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def get_messages(ids: list[str]) -> list[dict]:
    """Fetch several messages by id in one query, returned in ``ids`` order
    (missing ids are skipped). One round-trip for the projector's exchange fetch."""
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    with _connection() as conn:
        rows = conn.execute(
            f"SELECT * FROM messages WHERE id IN ({placeholders})", ids
        ).fetchall()
    by_id = {r["id"]: _row_to_dict(r) for r in rows}
    return [by_id[i] for i in ids if i in by_id]


def get_thread(message_id: str) -> list[dict]:
    """Walk the reply_to_id chain from ``message_id`` up to the thread root and
    return it root-first. Uses a recursive CTE so a long thread is one query."""
    with _connection() as conn:
        rows = conn.execute(
            """
            WITH RECURSIVE chain(id) AS (
                SELECT id FROM messages WHERE id = ?
                UNION
                SELECT m.reply_to_id FROM messages m
                JOIN chain c ON m.id = c.id
                WHERE m.reply_to_id IS NOT NULL
            )
            SELECT m.* FROM messages m JOIN chain c ON m.id = c.id
            ORDER BY m.ts, m.rowid
            """,
            (message_id,),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def search_messages(
    keyword: str,
    chat_id: str | None = None,
    since: str | None = None,
    limit: int = 10,
) -> list[dict]:
    """Full-text search over message content (trigram FTS5), newest-first.

    ``since`` is an ISO date/datetime lower bound on ``ts``. Trigram matching
    needs queries of >= 3 characters; shorter keywords fall back to a LIKE scan."""
    keyword = keyword.strip()
    if not keyword:
        return []
    params: list = []
    if len(keyword) >= 3:
        sql = (
            "SELECT m.* FROM messages m "
            "JOIN messages_fts f ON f.rowid = m.rowid "
            "WHERE messages_fts MATCH ? "
        )
        params.append('"' + keyword.replace('"', '""') + '"')
    else:
        # Escape LIKE wildcards so a keyword of "%" or "_" is matched literally
        # instead of returning every stored message (admin turns included).
        escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        sql = "SELECT m.* FROM messages m WHERE m.content LIKE ? ESCAPE '\\' "
        params.append(f"%{escaped}%")
    if chat_id is not None:
        sql += "AND m.chat_id = ? "
        params.append(chat_id)
    if since is not None:
        sql += "AND m.ts >= ? "
        params.append(since)
    sql += "ORDER BY m.ts DESC, m.rowid DESC LIMIT ?"
    params.append(limit)
    with _connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_dict(r) for r in rows]


def clear_chat(chat_id: str | None = None) -> int:
    """Delete stored messages. ``chat_id=None`` clears every chat. Returns the
    number of rows removed."""
    with _connection() as conn:
        if chat_id is None:
            cur = conn.execute("DELETE FROM messages")
        else:
            cur = conn.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
        return cur.rowcount


def iter_all_messages() -> Iterator[dict]:
    """Yield every message ordered by (ts, rowid) — the input for Neo4j reprojection."""
    with _connection() as conn:
        for row in conn.execute(
            "SELECT * FROM messages ORDER BY ts, rowid"
        ):
            yield _row_to_dict(row)


def recent_user_queries(
    since: str | None = None, limit: int = 200, chat_id: str | None = None
) -> list[dict]:
    """Return the user's own questions (role='user', kind='chat'), newest-first.
    The demand-side interest signal for reflect/weekly. ``since`` is an ISO lower
    bound on ``ts``."""
    sql = "SELECT * FROM messages WHERE role = 'user' AND kind = 'chat'"
    params: list = []
    if since is not None:
        sql += " AND ts >= ?"
        params.append(since)
    if chat_id is not None:
        sql += " AND chat_id = ?"
        params.append(chat_id)
    sql += " ORDER BY ts DESC, rowid DESC LIMIT ?"
    params.append(limit)
    with _connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_dict(r) for r in rows]


# --- interest events (conversation-derived interest signal) ------------------


def log_interest_event(
    entity: str,
    *,
    themes: list[str] | None = None,
    type: str = "query",
    depth: str = "shallow",
    ts: str | None = None,
) -> None:
    """Record that the user showed interest in one or more themes. One row per
    theme (defaults to ``[entity]``)."""
    themes = themes or [entity]
    ts = ts or datetime.now(timezone.utc).isoformat()
    with _connection() as conn:
        conn.executemany(
            "INSERT INTO interest_events (ts, theme, type, depth) VALUES (?, ?, ?, ?)",
            [(ts, theme, type, depth) for theme in themes],
        )


def interest_theme_counts(since: str | None = None) -> list[tuple[str, int]]:
    """Theme → query-count over events at/after ``since``, most-queried first."""
    sql = "SELECT theme, COUNT(*) AS c FROM interest_events"
    params: list = []
    if since is not None:
        sql += " WHERE ts >= ?"
        params.append(since)
    sql += " GROUP BY theme ORDER BY c DESC, theme"
    with _connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [(r["theme"], r["c"]) for r in rows]


def clear_interest_events() -> int:
    """Delete all interest events (resets the estimation baseline). Returns count."""
    with _connection() as conn:
        return conn.execute("DELETE FROM interest_events").rowcount
