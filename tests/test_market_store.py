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
