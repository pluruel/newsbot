import sqlite3
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator


def _db_path() -> str:
    return os.environ.get("DB_PATH", "workspace/newsparser.db")


def _connect() -> sqlite3.Connection:
    path = _db_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def _connection() -> Generator[sqlite3.Connection, None, None]:
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    conn = _connect()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS pending_articles (
                guid        TEXT PRIMARY KEY,
                source      TEXT NOT NULL,
                title       TEXT NOT NULL,
                url         TEXT NOT NULL,
                published   TEXT,
                body        TEXT,
                fetched_at  TEXT NOT NULL,
                alerted     INTEGER DEFAULT 0,
                processed   INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS seen_articles (
                guid    TEXT PRIMARY KEY,
                seen_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS feed_health (
                source               TEXT PRIMARY KEY,
                last_ok              TEXT,
                consecutive_failures INTEGER NOT NULL DEFAULT 0,
                last_error           TEXT
            );
        """)
        # Idempotent column addition. SQLite raises OperationalError if column exists.
        try:
            conn.execute("ALTER TABLE pending_articles ADD COLUMN category TEXT")
            conn.commit()
        except sqlite3.OperationalError:
            pass
    finally:
        conn.close()


def is_seen(guid: str) -> bool:
    with _connection() as conn:
        return conn.execute(
            "SELECT 1 FROM seen_articles WHERE guid = ?", (guid,)
        ).fetchone() is not None


def mark_seen(guid: str) -> None:
    with _connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO seen_articles (guid, seen_at) VALUES (?, ?)",
            (guid, datetime.now(timezone.utc).isoformat()),
        )


def record_feed_ok(source: str) -> None:
    with _connection() as conn:
        conn.execute(
            """INSERT INTO feed_health (source, last_ok, consecutive_failures, last_error)
               VALUES (?, ?, 0, NULL)
               ON CONFLICT(source) DO UPDATE SET
                   last_ok = excluded.last_ok,
                   consecutive_failures = 0,
                   last_error = NULL""",
            (source, datetime.now(timezone.utc).isoformat()),
        )


def record_feed_failure(source: str, error: str) -> None:
    with _connection() as conn:
        conn.execute(
            """INSERT INTO feed_health (source, last_ok, consecutive_failures, last_error)
               VALUES (?, NULL, 1, ?)
               ON CONFLICT(source) DO UPDATE SET
                   consecutive_failures = feed_health.consecutive_failures + 1,
                   last_error = excluded.last_error""",
            (source, error),
        )


def get_failing_feeds(min_consecutive: int) -> list[dict]:
    """Sources whose last min_consecutive polls all failed (fetch error or empty feed)."""
    with _connection() as conn:
        rows = conn.execute(
            """SELECT source, last_ok, consecutive_failures, last_error
               FROM feed_health WHERE consecutive_failures >= ?
               ORDER BY consecutive_failures DESC""",
            (min_consecutive,),
        ).fetchall()
        return [dict(r) for r in rows]


def insert_article(
    guid: str, source: str, title: str, url: str,
    published: str | None, body: str | None, category: str | None = None,
) -> None:
    with _connection() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO pending_articles
               (guid, source, title, url, published, body, fetched_at, category)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (guid, source, title, url, published, body,
             datetime.now(timezone.utc).isoformat(), category),
        )


def get_unprocessed(category: str | None = None) -> list[dict]:
    sql = "SELECT * FROM pending_articles WHERE processed = 0"
    params: tuple = ()
    if category is not None:
        sql += " AND category = ?"
        params = (category,)
    sql += " ORDER BY COALESCE(published, fetched_at)"
    with _connection() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def get_recent(minutes: int = 60) -> list[dict]:
    with _connection() as conn:
        rows = conn.execute(
            """SELECT * FROM pending_articles
               WHERE fetched_at >= strftime('%Y-%m-%dT%H:%M:%S', 'now', ?)
               ORDER BY fetched_at""",
            (f"-{minutes} minutes",),
        ).fetchall()
        return [dict(r) for r in rows]


def get_between(start: datetime, end: datetime, category: str | None = None,
                limit: int = 100) -> list[dict]:
    """Articles fetched within [start, end], oldest-first.

    Unlike get_unprocessed this ignores the `processed` flag: the cycle marks
    rows processed on its own 6-hourly schedule, and a volatility alert must see
    the same window regardless of whether a cycle happened to run in between.
    Both bounds are UTC; stored timestamps are ISO-8601 UTC (naive for
    pre-migration rows, which compare correctly against the same prefix).

    When the window holds more than `limit` rows the *newest* are kept: the
    caller is headlines.candidates during a burst, and the articles nearest the
    move are the ones worth keeping.
    """
    sql = "SELECT * FROM pending_articles WHERE fetched_at >= ? AND fetched_at <= ?"
    params: list = [start.isoformat(), end.isoformat()]
    if category is not None:
        sql += " AND category = ?"
        params.append(category)
    sql += " ORDER BY fetched_at DESC LIMIT ?"
    params.append(limit)
    with _connection() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in reversed(rows)]


def mark_processed(guids: list[str]) -> None:
    with _connection() as conn:
        conn.executemany(
            "UPDATE pending_articles SET processed = 1 WHERE guid = ?",
            [(g,) for g in guids],
        )


def mark_alerted(guid: str) -> None:
    with _connection() as conn:
        conn.execute(
            "UPDATE pending_articles SET alerted = 1 WHERE guid = ?", (guid,)
        )


def get_unclassified() -> list[dict]:
    """Return unprocessed rows with NULL category — input for haiku classification."""
    with _connection() as conn:
        rows = conn.execute(
            "SELECT * FROM pending_articles WHERE processed = 0 AND category IS NULL "
            "ORDER BY COALESCE(published, fetched_at)"
        ).fetchall()
        return [dict(r) for r in rows]


def update_category(guid: str, category: str) -> None:
    with _connection() as conn:
        conn.execute(
            "UPDATE pending_articles SET category = ? WHERE guid = ?",
            (category, guid),
        )


def search_articles(
    keyword: str,
    category: str | None = None,
    limit: int = 5,
) -> list[dict]:
    """Case-insensitive LIKE search over title and body across all ingested articles
    (regardless of `processed`). Returns rows newest-first."""
    if not keyword.strip():
        return []
    pattern = f"%{keyword.strip()}%"
    sql = (
        "SELECT guid, source, title, url, published, body, fetched_at, category "
        "FROM pending_articles "
        "WHERE (title LIKE ? OR body LIKE ?)"
    )
    params: list = [pattern, pattern]
    if category is not None:
        sql += " AND category = ?"
        params.append(category)
    sql += " ORDER BY COALESCE(published, fetched_at) DESC LIMIT ?"
    params.append(limit)
    with _connection() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
