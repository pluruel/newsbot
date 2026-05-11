import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Generator, Iterable


def _db_path() -> str:
    return os.environ.get("MARKET_DB_PATH", "workspace/market.db")


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


def init_market_db() -> None:
    with _connection() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS market_daily (
                instrument TEXT NOT NULL,
                date       TEXT NOT NULL,
                open       REAL,
                high       REAL,
                low        REAL,
                close      REAL,
                volume     INTEGER,
                PRIMARY KEY (instrument, date)
            );
            CREATE INDEX IF NOT EXISTS idx_market_daily_date
                ON market_daily(date);

            CREATE TABLE IF NOT EXISTS market_intraday (
                instrument TEXT NOT NULL,
                ts         TEXT NOT NULL,
                open       REAL,
                high       REAL,
                low        REAL,
                close      REAL,
                volume     INTEGER,
                PRIMARY KEY (instrument, ts)
            );
            CREATE INDEX IF NOT EXISTS idx_market_intraday_ts
                ON market_intraday(ts);
        """)


def upsert_daily(rows: Iterable[dict]) -> int:
    rows = list(rows)
    if not rows:
        return 0
    with _connection() as conn:
        conn.executemany(
            """INSERT INTO market_daily
               (instrument, date, open, high, low, close, volume)
               VALUES (:instrument, :date, :open, :high, :low, :close, :volume)
               ON CONFLICT(instrument, date) DO UPDATE SET
                   open=excluded.open, high=excluded.high, low=excluded.low,
                   close=excluded.close, volume=excluded.volume""",
            rows,
        )
    return len(rows)


def upsert_intraday(rows: Iterable[dict]) -> int:
    rows = list(rows)
    if not rows:
        return 0
    with _connection() as conn:
        conn.executemany(
            """INSERT INTO market_intraday
               (instrument, ts, open, high, low, close, volume)
               VALUES (:instrument, :ts, :open, :high, :low, :close, :volume)
               ON CONFLICT(instrument, ts) DO UPDATE SET
                   open=excluded.open, high=excluded.high, low=excluded.low,
                   close=excluded.close, volume=excluded.volume""",
            rows,
        )
    return len(rows)


def get_daily(alias: str, start: date, end: date) -> list[dict]:
    with _connection() as conn:
        cur = conn.execute(
            "SELECT * FROM market_daily WHERE instrument=? AND date>=? AND date<=? "
            "ORDER BY date",
            (alias, start.isoformat(), end.isoformat()),
        )
        return [dict(r) for r in cur.fetchall()]


def get_intraday(alias: str, start: datetime, end: datetime) -> list[dict]:
    with _connection() as conn:
        cur = conn.execute(
            "SELECT * FROM market_intraday WHERE instrument=? AND ts>=? AND ts<=? "
            "ORDER BY ts",
            (alias, start.isoformat(), end.isoformat()),
        )
        return [dict(r) for r in cur.fetchall()]


def latest_daily_date(alias: str) -> date | None:
    with _connection() as conn:
        row = conn.execute(
            "SELECT MAX(date) AS d FROM market_daily WHERE instrument=?",
            (alias,),
        ).fetchone()
    if row is None or row["d"] is None:
        return None
    return date.fromisoformat(row["d"])
