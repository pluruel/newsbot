import sqlite3
import os
from contextlib import contextmanager
from datetime import datetime
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
        """)
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
            (guid, datetime.utcnow().isoformat()),
        )


def insert_article(
    guid: str, source: str, title: str, url: str, published: str | None, body: str | None
) -> None:
    with _connection() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO pending_articles
               (guid, source, title, url, published, body, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (guid, source, title, url, published, body, datetime.utcnow().isoformat()),
        )


def get_unprocessed() -> list[dict]:
    with _connection() as conn:
        rows = conn.execute(
            "SELECT * FROM pending_articles WHERE processed = 0 ORDER BY COALESCE(published, fetched_at)"
        ).fetchall()
        return [dict(r) for r in rows]


def get_recent(minutes: int = 60) -> list[dict]:
    with _connection() as conn:
        rows = conn.execute(
            """SELECT * FROM pending_articles
               WHERE fetched_at >= datetime('now', ?)
               ORDER BY fetched_at""",
            (f"-{minutes} minutes",),
        ).fetchall()
        return [dict(r) for r in rows]


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
