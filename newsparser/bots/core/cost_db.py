import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def _db_path() -> Path:
    return Path(os.environ.get("WORKSPACE_DIR", "workspace")) / "state" / "claude_runs.db"


def _init(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS runs (
            ts            TEXT,
            bot           TEXT,
            model         TEXT,
            duration_ms   INTEGER,
            input_tokens  INTEGER,
            output_tokens INTEGER,
            cost_usd      REAL,
            ok            INTEGER,
            error         TEXT
        )
    """)
    conn.commit()


def record_run(
    bot: str,
    meta: dict,
    model: str = "claude-sonnet-4-6",
    ok: bool = True,
    error: str | None = None,
) -> None:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        _init(conn)
        conn.execute(
            "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?)",
            (
                datetime.now(timezone.utc).isoformat(),
                bot, model,
                meta.get("duration_ms"),
                meta.get("input_tokens"),
                meta.get("output_tokens"),
                meta.get("cost_usd"),
                1 if ok else 0,
                error,
            ),
        )
        conn.commit()
