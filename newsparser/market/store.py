import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
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


def _migrate_intraday_interval(conn: sqlite3.Connection) -> None:
    """Fold `interval` into market_intraday's primary key.

    The table originally keyed on (instrument, ts) alone. That was fine while 1h
    was the only resolution, but mixing a second one silently corrupts reads:
    annotate.py's ±60m before/after lookup would pick a 15m bar as "the previous
    hour", and market_query would interleave resolutions in one table. Every
    pre-existing row predates the 15m feed, so they are all 1h.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(market_intraday)")}
    if not cols or "interval" in cols:
        return
    conn.executescript("""
        DROP INDEX IF EXISTS idx_market_intraday_ts;
        ALTER TABLE market_intraday RENAME TO market_intraday_legacy;
        CREATE TABLE market_intraday (
            instrument TEXT NOT NULL,
            interval   TEXT NOT NULL,
            ts         TEXT NOT NULL,
            open       REAL,
            high       REAL,
            low        REAL,
            close      REAL,
            volume     INTEGER,
            PRIMARY KEY (instrument, interval, ts)
        );
        INSERT INTO market_intraday
            (instrument, interval, ts, open, high, low, close, volume)
            SELECT instrument, '1h', ts, open, high, low, close, volume
            FROM market_intraday_legacy;
        DROP TABLE market_intraday_legacy;
    """)


def init_market_db() -> None:
    with _connection() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        _migrate_intraday_interval(conn)
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
                interval   TEXT NOT NULL,
                ts         TEXT NOT NULL,
                open       REAL,
                high       REAL,
                low        REAL,
                close      REAL,
                volume     INTEGER,
                PRIMARY KEY (instrument, interval, ts)
            );
            CREATE INDEX IF NOT EXISTS idx_market_intraday_ts
                ON market_intraday(interval, ts);

            -- One row per fired volatility alert. Outlives the bars it was
            -- derived from: yfinance only serves 15m history for ~60 days, so
            -- this is the only durable record of what was flagged and which
            -- headlines were attached to it.
            CREATE TABLE IF NOT EXISTS market_pulse (
                instrument TEXT NOT NULL,
                interval   TEXT NOT NULL,
                ts         TEXT NOT NULL,
                delta_pct  REAL NOT NULL,
                z_score    REAL,
                floor_pct  REAL,
                guids      TEXT,
                created_at TEXT NOT NULL,
                PRIMARY KEY (instrument, interval, ts)
            );
            CREATE INDEX IF NOT EXISTS idx_market_pulse_ts
                ON market_pulse(ts);
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


def upsert_intraday(rows: Iterable[dict], interval: str = "1h") -> int:
    """Upsert intraday bars. `interval` is the per-row default — a row that
    carries its own "interval" key wins, so batch fetches of mixed resolutions
    round-trip unchanged."""
    rows = [{**r, "interval": r.get("interval", interval)} for r in rows]
    if not rows:
        return 0
    with _connection() as conn:
        conn.executemany(
            """INSERT INTO market_intraday
               (instrument, interval, ts, open, high, low, close, volume)
               VALUES (:instrument, :interval, :ts, :open, :high, :low, :close, :volume)
               ON CONFLICT(instrument, interval, ts) DO UPDATE SET
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


def get_intraday(alias: str, start: datetime, end: datetime,
                 interval: str = "1h") -> list[dict]:
    with _connection() as conn:
        cur = conn.execute(
            "SELECT * FROM market_intraday "
            "WHERE instrument=? AND interval=? AND ts>=? AND ts<=? ORDER BY ts",
            (alias, interval, start.isoformat(), end.isoformat()),
        )
        return [dict(r) for r in cur.fetchall()]


def get_intraday_tail(alias: str, interval: str, limit: int) -> list[dict]:
    """The newest `limit` bars for (alias, interval), oldest-first."""
    with _connection() as conn:
        cur = conn.execute(
            "SELECT * FROM market_intraday WHERE instrument=? AND interval=? "
            "ORDER BY ts DESC LIMIT ?",
            (alias, interval, limit),
        )
        return [dict(r) for r in reversed(cur.fetchall())]


def count_intraday(alias: str, interval: str) -> int:
    with _connection() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM market_intraday WHERE instrument=? AND interval=?",
            (alias, interval),
        ).fetchone()[0]


def latest_intraday_ts(alias: str, interval: str) -> str | None:
    with _connection() as conn:
        row = conn.execute(
            "SELECT MAX(ts) AS t FROM market_intraday WHERE instrument=? AND interval=?",
            (alias, interval),
        ).fetchone()
    return row["t"] if row is not None else None


def pulse_exists(instrument: str, interval: str, ts: str) -> bool:
    """True once a bar has fired an alert — the dedup gate that lets the pulse
    check run far more often than bars close without re-alerting on one."""
    with _connection() as conn:
        return conn.execute(
            "SELECT 1 FROM market_pulse WHERE instrument=? AND interval=? AND ts=?",
            (instrument, interval, ts),
        ).fetchone() is not None


def record_pulse(*, instrument: str, interval: str, ts: str, delta_pct: float,
                 z_score: float | None, floor_pct: float | None,
                 guids: Iterable[str] = ()) -> None:
    with _connection() as conn:
        conn.execute(
            """INSERT INTO market_pulse
               (instrument, interval, ts, delta_pct, z_score, floor_pct, guids, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(instrument, interval, ts) DO UPDATE SET
                   delta_pct=excluded.delta_pct, z_score=excluded.z_score,
                   floor_pct=excluded.floor_pct, guids=excluded.guids""",
            (instrument, interval, ts, delta_pct, z_score, floor_pct,
             "\n".join(guids), datetime.now(timezone.utc).isoformat()),
        )


def latest_daily_date(alias: str) -> date | None:
    with _connection() as conn:
        row = conn.execute(
            "SELECT MAX(date) AS d FROM market_daily WHERE instrument=?",
            (alias,),
        ).fetchone()
    if row is None or row["d"] is None:
        return None
    return date.fromisoformat(row["d"])
