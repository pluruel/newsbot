import os
import sqlite3
from datetime import date, datetime, timezone

import pytest

from newsparser.market import store


@pytest.fixture(autouse=True)
def market_db(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKET_DB_PATH", str(tmp_path / "market.db"))
    store.init_market_db()


def test_init_creates_tables_and_wal():
    path = os.environ["MARKET_DB_PATH"]
    with sqlite3.connect(path) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert "market_daily" in tables
    assert "market_intraday" in tables
    assert mode.lower() == "wal"


def test_upsert_daily_inserts_and_idempotent():
    row = {
        "instrument": "SPX", "date": "2026-05-08",
        "open": 5200.0, "high": 5240.0, "low": 5195.0,
        "close": 5230.0, "volume": 1_000_000,
    }
    assert store.upsert_daily([row]) == 1
    assert store.upsert_daily([row]) == 1
    rows = store.get_daily("SPX", date(2026, 5, 8), date(2026, 5, 8))
    assert len(rows) == 1
    assert rows[0]["close"] == 5230.0


def test_upsert_daily_updates_on_conflict():
    base = {"instrument": "SPX", "date": "2026-05-08",
            "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1}
    store.upsert_daily([base])
    store.upsert_daily([{**base, "close": 5230.0}])
    rows = store.get_daily("SPX", date(2026, 5, 8), date(2026, 5, 8))
    assert rows[0]["close"] == 5230.0


def test_get_daily_filters_alias_and_range():
    rows_in = [
        {"instrument": "SPX", "date": "2026-05-07", "open": 0, "high": 0, "low": 0, "close": 1, "volume": 0},
        {"instrument": "SPX", "date": "2026-05-08", "open": 0, "high": 0, "low": 0, "close": 2, "volume": 0},
        {"instrument": "SPX", "date": "2026-05-09", "open": 0, "high": 0, "low": 0, "close": 3, "volume": 0},
        {"instrument": "NDX", "date": "2026-05-08", "open": 0, "high": 0, "low": 0, "close": 99, "volume": 0},
    ]
    store.upsert_daily(rows_in)
    rows = store.get_daily("SPX", date(2026, 5, 7), date(2026, 5, 8))
    closes = [r["close"] for r in rows]
    assert closes == [1, 2]


def test_latest_daily_date_returns_max_or_none():
    assert store.latest_daily_date("SPX") is None
    store.upsert_daily([
        {"instrument": "SPX", "date": "2026-05-07", "open": 0, "high": 0, "low": 0, "close": 1, "volume": 0},
        {"instrument": "SPX", "date": "2026-05-09", "open": 0, "high": 0, "low": 0, "close": 3, "volume": 0},
    ])
    assert store.latest_daily_date("SPX") == date(2026, 5, 9)


def test_upsert_and_get_intraday():
    ts = datetime(2026, 5, 9, 3, 0, tzinfo=timezone.utc).isoformat()
    row = {"instrument": "SPX", "ts": ts,
           "open": 5230.0, "high": 5235.0, "low": 5228.0, "close": 5233.0, "volume": 50_000}
    store.upsert_intraday([row])
    rows = store.get_intraday(
        "SPX",
        datetime(2026, 5, 9, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 5, 9, 23, 59, tzinfo=timezone.utc),
    )
    assert len(rows) == 1
    assert rows[0]["close"] == 5233.0


def _bar(ts: str, close: float) -> dict:
    return {"instrument": "SPX", "ts": ts, "open": close, "high": close,
            "low": close, "close": close, "volume": 0}


def test_intraday_resolutions_do_not_collide():
    """Same instrument, same timestamp, two resolutions: both must survive.
    Before `interval` joined the PK these overwrote each other, and annotate.py's
    ±60m lookup would read a 15m bar as the previous hour."""
    ts = datetime(2026, 5, 9, 3, 0, tzinfo=timezone.utc).isoformat()
    store.upsert_intraday([_bar(ts, 100.0)], interval="1h")
    store.upsert_intraday([_bar(ts, 200.0)], interval="15m")
    lo = datetime(2026, 5, 9, 0, 0, tzinfo=timezone.utc)
    hi = datetime(2026, 5, 9, 23, 59, tzinfo=timezone.utc)
    assert [r["close"] for r in store.get_intraday("SPX", lo, hi, "1h")] == [100.0]
    assert [r["close"] for r in store.get_intraday("SPX", lo, hi, "15m")] == [200.0]


def test_get_intraday_defaults_to_hourly():
    """annotate.py and market_query call these without an interval — they must
    keep seeing exactly the 1h series."""
    ts = datetime(2026, 5, 9, 3, 0, tzinfo=timezone.utc).isoformat()
    store.upsert_intraday([_bar(ts, 100.0)])
    store.upsert_intraday([_bar(ts, 200.0)], interval="15m")
    rows = store.get_intraday("SPX",
                              datetime(2026, 5, 9, 0, 0, tzinfo=timezone.utc),
                              datetime(2026, 5, 9, 23, 59, tzinfo=timezone.utc))
    assert [r["close"] for r in rows] == [100.0]


def test_upsert_intraday_honours_per_row_interval():
    ts = datetime(2026, 5, 9, 3, 0, tzinfo=timezone.utc).isoformat()
    store.upsert_intraday([{**_bar(ts, 42.0), "interval": "15m"}], interval="1h")
    assert store.get_intraday_tail("SPX", "15m", 5)[0]["close"] == 42.0
    assert store.get_intraday_tail("SPX", "1h", 5) == []


def test_get_intraday_tail_returns_newest_oldest_first():
    rows = [_bar(datetime(2026, 5, 9, h, tzinfo=timezone.utc).isoformat(), float(h))
            for h in range(10)]
    store.upsert_intraday(rows, interval="15m")
    tail = store.get_intraday_tail("SPX", "15m", 3)
    assert [r["close"] for r in tail] == [7.0, 8.0, 9.0]


def test_latest_intraday_ts_is_per_interval():
    store.upsert_intraday([_bar("2026-05-09T03:00:00+00:00", 1.0)], interval="1h")
    store.upsert_intraday([_bar("2026-05-09T09:00:00+00:00", 2.0)], interval="15m")
    assert store.latest_intraday_ts("SPX", "1h") == "2026-05-09T03:00:00+00:00"
    assert store.latest_intraday_ts("SPX", "15m") == "2026-05-09T09:00:00+00:00"
    assert store.latest_intraday_ts("NDX", "15m") is None


def test_record_pulse_roundtrip_and_dedup():
    ts = "2026-07-28T05:45:00+00:00"
    assert store.pulse_exists("KOSPI", "15m", ts) is False
    store.record_pulse(instrument="KOSPI", interval="15m", ts=ts, delta_pct=-1.3,
                       z_score=3.4, floor_pct=1.5, guids=["a", "b"])
    assert store.pulse_exists("KOSPI", "15m", ts) is True
    # Re-recording the same bar updates rather than raising.
    store.record_pulse(instrument="KOSPI", interval="15m", ts=ts, delta_pct=-1.4,
                       z_score=3.5, floor_pct=1.5, guids=["c"])
    with sqlite3.connect(os.environ["MARKET_DB_PATH"]) as conn:
        rows = conn.execute("SELECT delta_pct, guids FROM market_pulse").fetchall()
    assert rows == [(-1.4, "c")]


def test_pulse_is_scoped_by_interval():
    ts = "2026-07-28T05:45:00+00:00"
    store.record_pulse(instrument="KOSPI", interval="15m", ts=ts, delta_pct=-1.3,
                       z_score=3.4, floor_pct=1.5)
    assert store.pulse_exists("KOSPI", "1h", ts) is False


def test_migration_tags_legacy_rows_as_hourly(tmp_path, monkeypatch):
    """The pre-interval table keyed on (instrument, ts); every row in it predates
    the 15m feed."""
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE market_intraday (
                instrument TEXT NOT NULL, ts TEXT NOT NULL,
                open REAL, high REAL, low REAL, close REAL, volume INTEGER,
                PRIMARY KEY (instrument, ts)
            );
            CREATE INDEX idx_market_intraday_ts ON market_intraday(ts);
            INSERT INTO market_intraday VALUES ('SPX','2026-05-09T03:00:00+00:00',1,1,1,1,7);
        """)
    monkeypatch.setenv("MARKET_DB_PATH", str(path))
    store.init_market_db()
    store.init_market_db()  # idempotent

    with sqlite3.connect(path) as conn:
        rows = conn.execute(
            "SELECT instrument, interval, volume FROM market_intraday").fetchall()
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    assert rows == [("SPX", "1h", 7)]
    assert "market_intraday_legacy" not in tables
