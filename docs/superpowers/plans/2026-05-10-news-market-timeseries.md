# News × Market Time-Series Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect the news pipeline with a macro market time-series layer (8 instruments via yfinance) so cycle digests carry a market snapshot, the `/tracker` has an MCP tool for ad-hoc time-series queries, and graph relations targeting tracked instruments get annotated with ±60m intraday (or daily-fallback) price reactions. Also: relations carry their source article GUIDs for exact reverse traversal.

**Architecture:** New `newsparser/market/` package (fetcher, store, snapshot, annotate) backed by `workspace/market.db` SQLite (WAL). A daily 07:30 KST cron pulls fresh daily bars. `run_cycle.py` prepends a snapshot block to each cycle input file. A new `market_query` MCP tool exposes the data to Claude. `apply_graph.py` gains a resolver step (article index `A001` → real GUID via `{slot}-guids.txt`) and an annotate step (write `impact_price_delta_pct` etc. onto `IMPACTS`/`INFLUENCES` relations whose target alias is tracked).

**Tech Stack:** Python 3.11+, `yfinance`, SQLite (stdlib + WAL), Neo4j (existing), FastMCP (stdio, existing).

---

## File Map

**Create:**

```
newsparser/market/__init__.py
newsparser/market/fetcher.py
newsparser/market/store.py
newsparser/market/snapshot.py
newsparser/market/annotate.py
newsparser/scripts/fetch_market_daily.py
scripts/smoke_market.py
tests/test_market_store.py
tests/test_market_fetcher.py
tests/test_market_snapshot.py
tests/test_market_annotate.py
tests/test_fetch_market_daily_script.py
tests/test_market_query_mcp.py
```

**Modify:**

```
pyproject.toml                       add yfinance dependency
newsparser/mcp_server.py             add @mcp.tool() market_query
newsparser/scripts/apply_graph.py    resolver step + call maybe_annotate_impacts
newsparser/scripts/run_cycle.py      prepend snapshot block to cycle input file
.claude/commands/cycle.md            snapshot instruction + canonical_name convention + src: extension
newsparser/bot/tracker.py            market_query usage paragraph in tracker prompt
newsparser/claude/input_builder.py   per-article [A001] index header + GUID line
newsparser/claude/output_parser.py   parse optional src: segment; RelationUpdate fields
newsparser/graph/writer.py           accumulate source_article_guids on relations (union, idempotent)
tests/test_run_cycle_script.py       snapshot-prepended assertion
tests/test_input_builder.py          article index headers + GUID line
tests/test_output_parser.py          src: parsing + back-compat
tests/test_graph_writer.py           source_article_guids union/dedup
tests/test_apply_graph.py            resolver + annotate-call assertions
/etc/cron.d/newsparser               add 07:30 KST daily fetch line (Task 16)
```

**Delete:** none.

---

## Task 1: Add yfinance dependency + market package skeleton

**Files:**
- Modify: `pyproject.toml`
- Create: `newsparser/market/__init__.py`

- [ ] **Step 1: Add yfinance to dependencies**

Edit `pyproject.toml` `dependencies` list. Final list:

```toml
dependencies = [
    "feedparser>=6.0",
    "trafilatura>=1.9",
    "python-telegram-bot>=20.0",
    "python-dotenv>=1.0",
    "apscheduler>=3.10,<4",
    "neo4j>=6.2.0",
    "fastmcp>=2.0,<4",
    "yfinance>=0.2.40",
]
```

- [ ] **Step 2: Install into the existing venv**

```bash
.venv/bin/pip install "yfinance>=0.2.40"
```

Expected: yfinance + transitive deps install successfully.

- [ ] **Step 3: Create the package init**

Create `newsparser/market/__init__.py` (empty file).

- [ ] **Step 4: Sanity-check the import**

```bash
.venv/bin/python -c "import newsparser.market; import yfinance; print('ok')"
```

Expected: prints `ok`.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml newsparser/market/__init__.py
git commit -m "$(cat <<'EOF'
feat(market): add yfinance dep and market package skeleton

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: `store.py` — SQLite layer for market_daily / market_intraday

**Files:**
- Create: `newsparser/market/store.py`
- Create: `tests/test_market_store.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_market_store.py`:

```python
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
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
.venv/bin/pytest tests/test_market_store.py -v
```

Expected: FAIL — `ModuleNotFoundError: newsparser.market.store`.

- [ ] **Step 3: Implement `store.py`**

Create `newsparser/market/store.py`:

```python
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
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
.venv/bin/pytest tests/test_market_store.py -v
```

Expected: all 6 tests pass.

- [ ] **Step 5: Commit**

```bash
git add newsparser/market/store.py tests/test_market_store.py
git commit -m "$(cat <<'EOF'
feat(market): add SQLite store for daily and intraday OHLCV

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: `fetcher.py` — yfinance wrapper with retry

**Files:**
- Create: `newsparser/market/fetcher.py`
- Create: `tests/test_market_fetcher.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_market_fetcher.py`:

```python
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from newsparser.market import fetcher


def _df_daily(rows):
    df = pd.DataFrame(rows)
    df.index = pd.DatetimeIndex(df.pop("date"))
    return df


def _df_intraday(rows):
    df = pd.DataFrame(rows)
    df.index = pd.DatetimeIndex(df.pop("ts"))
    return df


def test_tickers_dict_has_all_eight_aliases():
    expected = {"SPX", "NDX", "KOSPI", "USDKRW", "USDJPY", "DXY", "VIX", "TNX"}
    assert set(fetcher.TICKERS.keys()) == expected


def test_fetch_daily_calls_yfinance_and_converts():
    df = _df_daily([
        {"date": "2026-05-07", "Open": 1.0, "High": 2.0, "Low": 0.5, "Close": 1.5, "Volume": 100},
        {"date": "2026-05-08", "Open": 1.5, "High": 3.0, "Low": 1.0, "Close": 2.0, "Volume": 200},
    ])
    fake_ticker = MagicMock()
    fake_ticker.history.return_value = df
    with patch.object(fetcher.yf, "Ticker", return_value=fake_ticker) as ticker_cls:
        bars = fetcher.fetch_daily("SPX", date(2026, 5, 7), date(2026, 5, 8))
    ticker_cls.assert_called_once_with("^GSPC")
    fake_ticker.history.assert_called_once()
    assert [b["date"] for b in bars] == ["2026-05-07", "2026-05-08"]
    assert bars[0]["instrument"] == "SPX"
    assert bars[1]["close"] == 2.0


def test_fetch_daily_empty_dataframe_returns_empty_list():
    fake_ticker = MagicMock()
    fake_ticker.history.return_value = pd.DataFrame()
    with patch.object(fetcher.yf, "Ticker", return_value=fake_ticker):
        bars = fetcher.fetch_daily("SPX", date(2026, 5, 7), date(2026, 5, 8))
    assert bars == []


def test_fetch_daily_retries_then_returns_empty():
    fake_ticker = MagicMock()
    fake_ticker.history.side_effect = RuntimeError("yfinance down")
    with patch.object(fetcher.yf, "Ticker", return_value=fake_ticker), \
         patch.object(fetcher, "_sleep") as sleep_:
        bars = fetcher.fetch_daily("SPX", date(2026, 5, 7), date(2026, 5, 8))
    assert bars == []
    assert fake_ticker.history.call_count == 3
    assert sleep_.call_count >= 2


def test_fetch_intraday_hourly_converts_to_utc_iso():
    df = _df_intraday([
        {"ts": pd.Timestamp("2026-05-09 02:00:00", tz="UTC"),
         "Open": 5230.0, "High": 5231.0, "Low": 5229.0, "Close": 5230.5, "Volume": 1000},
        {"ts": pd.Timestamp("2026-05-09 03:00:00", tz="UTC"),
         "Open": 5230.5, "High": 5232.0, "Low": 5228.0, "Close": 5228.3, "Volume": 1100},
    ])
    fake_ticker = MagicMock()
    fake_ticker.history.return_value = df
    with patch.object(fetcher.yf, "Ticker", return_value=fake_ticker):
        bars = fetcher.fetch_intraday_hourly(
            "SPX",
            datetime(2026, 5, 9, 2, 0, tzinfo=timezone.utc),
            datetime(2026, 5, 9, 4, 0, tzinfo=timezone.utc),
        )
    assert bars[0]["ts"] == "2026-05-09T02:00:00+00:00"
    assert bars[1]["close"] == 5228.3
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
.venv/bin/pytest tests/test_market_fetcher.py -v
```

Expected: FAIL — `ModuleNotFoundError: newsparser.market.fetcher`.

- [ ] **Step 3: Implement `fetcher.py`**

Create `newsparser/market/fetcher.py`:

```python
import logging
import random
import time
from datetime import date, datetime, timezone
from typing import Any

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

TICKERS: dict[str, str] = {
    "SPX":    "^GSPC",
    "NDX":    "^IXIC",
    "KOSPI":  "^KS11",
    "USDKRW": "KRW=X",
    "USDJPY": "JPY=X",
    "DXY":    "DX-Y.NYB",
    "VIX":    "^VIX",
    "TNX":    "^TNX",
}

_RETRIES = 3
_BACKOFF_BASE = 1.0   # seconds


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _call_with_retry(fn, label: str) -> Any:
    last_exc: Exception | None = None
    for attempt in range(_RETRIES):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if attempt == _RETRIES - 1:
                break
            wait = _BACKOFF_BASE * (2 ** attempt) + random.uniform(0, 0.25)
            logger.warning("%s attempt %d failed (%s); sleeping %.2fs",
                           label, attempt + 1, exc, wait)
            _sleep(wait)
    logger.warning("%s gave up after %d attempts: %s", label, _RETRIES, last_exc)
    return None


def _df_to_daily_bars(alias: str, df: pd.DataFrame) -> list[dict]:
    if df is None or df.empty:
        return []
    bars: list[dict] = []
    for idx, row in df.iterrows():
        dt = idx.date() if hasattr(idx, "date") else idx
        bars.append({
            "instrument": alias,
            "date": dt.isoformat() if isinstance(dt, date) else str(dt),
            "open": round(float(row["Open"]), 4) if pd.notna(row["Open"]) else None,
            "high": round(float(row["High"]), 4) if pd.notna(row["High"]) else None,
            "low":  round(float(row["Low"]),  4) if pd.notna(row["Low"])  else None,
            "close": round(float(row["Close"]), 4) if pd.notna(row["Close"]) else None,
            "volume": int(row["Volume"]) if pd.notna(row["Volume"]) else 0,
        })
    return bars


def _df_to_intraday_bars(alias: str, df: pd.DataFrame) -> list[dict]:
    if df is None or df.empty:
        return []
    bars: list[dict] = []
    for idx, row in df.iterrows():
        ts = idx
        if not isinstance(ts, datetime):
            ts = pd.Timestamp(ts)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        else:
            ts = ts.tz_convert("UTC")
        bars.append({
            "instrument": alias,
            "ts": ts.isoformat(),
            "open": round(float(row["Open"]), 4) if pd.notna(row["Open"]) else None,
            "high": round(float(row["High"]), 4) if pd.notna(row["High"]) else None,
            "low":  round(float(row["Low"]),  4) if pd.notna(row["Low"])  else None,
            "close": round(float(row["Close"]), 4) if pd.notna(row["Close"]) else None,
            "volume": int(row["Volume"]) if pd.notna(row["Volume"]) else 0,
        })
    return bars


def fetch_daily(alias: str, start: date, end: date) -> list[dict]:
    symbol = TICKERS.get(alias)
    if symbol is None:
        logger.warning("Unknown alias: %s", alias)
        return []

    # yfinance's `end` is exclusive; bump by one day so `end` is included.
    from datetime import timedelta
    end_excl = end + timedelta(days=1)

    def call() -> pd.DataFrame:
        return yf.Ticker(symbol).history(
            start=start.isoformat(),
            end=end_excl.isoformat(),
            interval="1d",
            auto_adjust=False,
        )

    df = _call_with_retry(call, f"fetch_daily {alias}")
    return _df_to_daily_bars(alias, df)


def fetch_intraday_hourly(alias: str, start: datetime, end: datetime) -> list[dict]:
    symbol = TICKERS.get(alias)
    if symbol is None:
        logger.warning("Unknown alias: %s", alias)
        return []

    def call() -> pd.DataFrame:
        return yf.Ticker(symbol).history(
            start=start.isoformat(),
            end=end.isoformat(),
            interval="1h",
            auto_adjust=False,
        )

    df = _call_with_retry(call, f"fetch_intraday_hourly {alias}")
    return _df_to_intraday_bars(alias, df)
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
.venv/bin/pytest tests/test_market_fetcher.py -v
```

Expected: all 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add newsparser/market/fetcher.py tests/test_market_fetcher.py
git commit -m "$(cat <<'EOF'
feat(market): add yfinance fetcher with retry/backoff

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: `fetch_market_daily.py` — cron entrypoint

**Files:**
- Create: `newsparser/scripts/fetch_market_daily.py`
- Create: `tests/test_fetch_market_daily_script.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_fetch_market_daily_script.py`:

```python
from datetime import date
from unittest.mock import patch, MagicMock

import pytest

from newsparser.market import store
import newsparser.scripts.fetch_market_daily as script


@pytest.fixture(autouse=True)
def market_db(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKET_DB_PATH", str(tmp_path / "market.db"))


def _bar(alias: str, d: str, close: float) -> dict:
    return {"instrument": alias, "date": d,
            "open": close, "high": close, "low": close, "close": close, "volume": 1}


def test_backfill_when_empty_db_calls_fetch_daily_for_every_alias():
    calls: list[tuple[str, date, date]] = []

    def fake_fetch(alias, start, end):
        calls.append((alias, start, end))
        return [_bar(alias, "2026-05-08", 100.0)]

    with patch("newsparser.scripts.fetch_market_daily.fetcher.fetch_daily",
               side_effect=fake_fetch):
        script.main()

    aliases = sorted({c[0] for c in calls})
    assert aliases == sorted(["SPX", "NDX", "KOSPI", "USDKRW", "USDJPY", "DXY", "VIX", "TNX"])
    # Backfill window should be 5 years
    for _, start, _ in calls:
        assert (date.today() - start).days >= 365 * 5 - 1


def test_incremental_when_existing_uses_last_date_plus_one():
    store.init_market_db()
    store.upsert_daily([_bar("SPX", "2026-05-08", 5230.0)])

    captured = {}

    def fake_fetch(alias, start, end):
        if alias == "SPX":
            captured["start"] = start
        return []

    with patch("newsparser.scripts.fetch_market_daily.fetcher.fetch_daily",
               side_effect=fake_fetch):
        script.main()

    assert captured["start"] == date(2026, 5, 9)


def test_one_alias_failure_doesnt_stop_others():
    counts: dict[str, int] = {}

    def fake_fetch(alias, start, end):
        counts[alias] = counts.get(alias, 0) + 1
        if alias == "SPX":
            raise RuntimeError("boom")
        return []

    with patch("newsparser.scripts.fetch_market_daily.fetcher.fetch_daily",
               side_effect=fake_fetch):
        script.main()

    # All 8 aliases were attempted, even though SPX raised
    assert set(counts.keys()) >= {"SPX", "NDX", "KOSPI", "USDKRW", "USDJPY", "DXY", "VIX", "TNX"}
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
.venv/bin/pytest tests/test_fetch_market_daily_script.py -v
```

Expected: FAIL — `ModuleNotFoundError: newsparser.scripts.fetch_market_daily`.

- [ ] **Step 3: Implement `fetch_market_daily.py`**

Create `newsparser/scripts/fetch_market_daily.py`:

```python
import logging
from datetime import date, timedelta

from dotenv import load_dotenv
load_dotenv()

from newsparser.market import fetcher, store

logger = logging.getLogger(__name__)
_BACKFILL_DAYS = 365 * 5


def main() -> None:
    store.init_market_db()
    today = date.today()

    for alias in fetcher.TICKERS:
        try:
            last = store.latest_daily_date(alias)
            start = last + timedelta(days=1) if last else today - timedelta(days=_BACKFILL_DAYS)
            if start > today:
                logger.info("%s: up to date", alias)
                continue
            bars = fetcher.fetch_daily(alias, start, today)
            store.upsert_daily(bars)
            logger.info("%s: +%d rows", alias, len(bars))
        except Exception as exc:
            logger.error("%s: failed (%s)", alias, exc)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    main()
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
.venv/bin/pytest tests/test_fetch_market_daily_script.py -v
```

Expected: all 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add newsparser/scripts/fetch_market_daily.py tests/test_fetch_market_daily_script.py
git commit -m "$(cat <<'EOF'
feat(market): add daily-fetch cron entrypoint with backfill + incremental

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: `snapshot.py` — markdown snapshot block builder

**Files:**
- Create: `newsparser/market/snapshot.py`
- Create: `tests/test_market_snapshot.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_market_snapshot.py`:

```python
from datetime import date

import pytest

from newsparser.market import store, snapshot


@pytest.fixture(autouse=True)
def market_db(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKET_DB_PATH", str(tmp_path / "market.db"))
    store.init_market_db()


def _bar(alias, d, close):
    return {"instrument": alias, "date": d,
            "open": close, "high": close, "low": close, "close": close, "volume": 1}


def test_snapshot_has_header_and_table():
    store.upsert_daily([
        _bar("SPX", "2026-05-07", 5208.0),
        _bar("SPX", "2026-05-08", 5230.0),
    ])
    text = snapshot.build_snapshot_block(date(2026, 5, 9))
    assert "## 시장 스냅샷" in text
    assert "S&P 500" in text
    assert "5,230" in text or "5230" in text


def test_snapshot_computes_pct_change():
    store.upsert_daily([
        _bar("USDKRW", "2026-05-07", 1370.0),
        _bar("USDKRW", "2026-05-08", 1369.20),
    ])
    text = snapshot.build_snapshot_block(date(2026, 5, 9))
    # (1369.20 - 1370.0) / 1370.0 * 100 ≈ -0.06%
    # Allow the renderer to round; just assert a negative sign and "USD/KRW"
    assert "USD/KRW" in text
    line = next(l for l in text.splitlines() if "USD/KRW" in l)
    assert "-0.0" in line or "-0.1" in line


def test_snapshot_handles_missing_instrument():
    # Only SPX has data; the rest should render as 결측
    store.upsert_daily([
        _bar("SPX", "2026-05-07", 100.0),
        _bar("SPX", "2026-05-08", 102.0),
    ])
    text = snapshot.build_snapshot_block(date(2026, 5, 9))
    assert "결측" in text  # at least one missing row rendered
    assert "S&P 500" in text  # SPX row still present


def test_snapshot_uses_most_recent_trading_day_le_at():
    # at=2026-05-15, but latest data is 2026-05-08; should still render
    store.upsert_daily([
        _bar("SPX", "2026-05-07", 100.0),
        _bar("SPX", "2026-05-08", 102.0),
    ])
    text = snapshot.build_snapshot_block(date(2026, 5, 15))
    assert "2026-05-08" in text
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
.venv/bin/pytest tests/test_market_snapshot.py -v
```

Expected: FAIL — `ModuleNotFoundError: newsparser.market.snapshot`.

- [ ] **Step 3: Implement `snapshot.py`**

Create `newsparser/market/snapshot.py`:

```python
from datetime import date, timedelta

from newsparser.market import store
from newsparser.market.fetcher import TICKERS

DISPLAY = {
    "SPX":    "S&P 500",
    "NDX":    "NASDAQ",
    "KOSPI":  "KOSPI",
    "USDKRW": "USD/KRW",
    "USDJPY": "USD/JPY",
    "DXY":    "달러인덱스",
    "VIX":    "VIX",
    "TNX":    "미 10Y",
}

# Display order for the snapshot table
ORDER = ["SPX", "NDX", "KOSPI", "USDKRW", "USDJPY", "DXY", "VIX", "TNX"]


def _fmt_close(alias: str, close: float) -> str:
    if alias == "TNX":
        return f"{close:.2f}%"
    if alias in ("USDKRW", "USDJPY", "DXY", "VIX"):
        return f"{close:,.2f}"
    return f"{close:,.2f}"


def _fmt_pct(prev: float, cur: float, alias: str) -> str:
    if prev == 0 or prev is None or cur is None:
        return "—"
    if alias == "TNX":
        # bps change in absolute terms
        diff = cur - prev
        sign = "+" if diff >= 0 else ""
        return f"{sign}{diff:.2f}"
    pct = (cur - prev) / prev * 100
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.2f}%"


def build_snapshot_block(at: date) -> str:
    # Fetch up to 10 trading days back per instrument so we can pick the latest two
    lookback = at - timedelta(days=14)
    latest_date: str | None = None
    rows_out: list[str] = []

    for alias in ORDER:
        bars = store.get_daily(alias, lookback, at)
        if len(bars) < 1:
            rows_out.append(f"| {DISPLAY[alias]} | — | — (결측) |")
            continue
        if len(bars) == 1:
            cur = bars[-1]
            rows_out.append(f"| {DISPLAY[alias]} | {_fmt_close(alias, cur['close'])} | — (결측) |")
            latest_date = latest_date or cur["date"]
            continue
        prev, cur = bars[-2], bars[-1]
        rows_out.append(
            f"| {DISPLAY[alias]} | {_fmt_close(alias, cur['close'])} "
            f"| {_fmt_pct(prev['close'], cur['close'], alias)} |"
        )
        if latest_date is None or cur["date"] > latest_date:
            latest_date = cur["date"]

    header_date = latest_date or at.isoformat()
    return "\n".join([
        f"## 시장 스냅샷 ({header_date} 기준 종가)",
        "",
        "| 종목 | 종가 | 일변동 |",
        "|---|---|---|",
        *rows_out,
    ])
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
.venv/bin/pytest tests/test_market_snapshot.py -v
```

Expected: all 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add newsparser/market/snapshot.py tests/test_market_snapshot.py
git commit -m "$(cat <<'EOF'
feat(market): add snapshot markdown block builder

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: `run_cycle.py` prepends snapshot block to input file

**Files:**
- Modify: `newsparser/scripts/run_cycle.py`
- Modify: `tests/test_run_cycle_script.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_run_cycle_script.py`:

```python
def test_snapshot_block_prepended_to_input(tmp_path, monkeypatch):
    """run_cycle should prepend a ## 시장 스냅샷 block above the article list."""
    from datetime import date
    from unittest.mock import patch

    monkeypatch.setenv("MARKET_DB_PATH", str(tmp_path / "market.db"))
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "workspace"))
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))

    from newsparser.store.sqlite import init_db, insert_article
    from newsparser.market import store as market_store
    init_db()
    market_store.init_market_db()
    market_store.upsert_daily([
        {"instrument": "SPX", "date": "2026-05-07",
         "open": 100, "high": 100, "low": 100, "close": 100, "volume": 1},
        {"instrument": "SPX", "date": "2026-05-08",
         "open": 100, "high": 100, "low": 100, "close": 102, "volume": 1},
    ])

    insert_article("g1", "Bloomberg", "T", "https://x.com/1", None, "body", category="markets")

    seen_input: list[str] = []

    def fake_run_claude(prompt, **kw):
        ws = tmp_path / "workspace"
        slot, cat = prompt.strip().split()[1:3]
        seen_input.append((ws / "input" / cat / f"{slot}-input.md").read_text())
        report_dir = ws / "cycles" / cat
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / f"{slot}.md").write_text("사이클 OK\n## Graph updates\n")
        return ""

    import newsparser.scripts.run_cycle as run_cycle
    with patch.object(run_cycle, "run_claude", side_effect=fake_run_claude), \
         patch.object(run_cycle, "send_long_message"):
        run_cycle.main("2026-05-09-12")

    assert any("## 시장 스냅샷" in t for t in seen_input)
    # Snapshot must precede article list
    text = next(t for t in seen_input if "## 시장 스냅샷" in t)
    snap_idx = text.find("## 시장 스냅샷")
    art_idx = text.find("Collected Articles")
    assert 0 <= snap_idx < art_idx
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
.venv/bin/pytest tests/test_run_cycle_script.py::test_snapshot_block_prepended_to_input -v
```

Expected: FAIL — `## 시장 스냅샷` is not found in the input file.

- [ ] **Step 3: Modify `run_cycle.py`**

In `newsparser/scripts/run_cycle.py`, add imports near the top:

```python
from datetime import date
from newsparser.market import snapshot as market_snapshot
from newsparser.market import store as market_store
```

In `_run_for_category`, between the existing `build_input_file(slot, category)` line and `run_claude(f"/cycle {slot} {category}")`, insert:

```python
    # Prepend a market snapshot block to the input file so Claude sees it first.
    input_path = workspace / "input" / category / f"{slot}-input.md"
    try:
        market_store.init_market_db()
        slot_date = date.fromisoformat(slot[:10])
        snapshot_block = market_snapshot.build_snapshot_block(slot_date)
    except Exception as exc:
        logger.warning("[%s] market snapshot failed: %s", category, exc)
        snapshot_block = ""
    if snapshot_block and input_path.exists():
        existing = input_path.read_text(encoding="utf-8")
        input_path.write_text(snapshot_block + "\n\n" + existing, encoding="utf-8")
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
.venv/bin/pytest tests/test_run_cycle_script.py -v
```

Expected: all existing tests still pass, new one passes.

- [ ] **Step 5: Commit**

```bash
git add newsparser/scripts/run_cycle.py tests/test_run_cycle_script.py
git commit -m "$(cat <<'EOF'
feat(cycle): prepend market snapshot block to cycle input file

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: `cycle.md` — snapshot instruction + canonical_name + src:

**Files:**
- Modify: `.claude/commands/cycle.md`

No automated test — prompt review only.

- [ ] **Step 1: Edit `.claude/commands/cycle.md`**

Insert this section *after* "## 사용자 관심사" and *before* "## Task":

```markdown
## 시장 스냅샷

입력파일 상단에 `## 시장 스냅샷` 블록이 있다. 보고서의 "새 소식" 첫 단락 또는 lead-in 한 줄에 그 날 시장 상태를 짧게 요약·반영하라. Indicator 엔티티를 라벨링할 때 `canonical_name`은 반드시 다음 별칭 중 하나로 쓴다: `SPX`, `NDX`, `KOSPI`, `USDKRW`, `USDJPY`, `DXY`, `VIX`, `TNX`. 그래프와 가격 DB는 이 별칭으로 연결된다.
```

In the existing "## Task" section, modify step 3 (analysis) to add one more bullet at the end:

```markdown
   - 각 관계에 대해 그 주장을 뒷받침하는 입력파일 내 기사 인덱스(`A001`, `A002` 등)를 `src:` 세그먼트로 표기한다. 예: `[conf:0.85, impact:0.7, src:A001,A007]`.
```

Update the "Report file format" Relations example to show the new `src:` segment:

```
### Relations
- NEW | {subject} --{PREDICATE}[conf:{0.NN}, impact:{0.NN}, src:A001,A007]--> {object} | {predicate_text}
- UPDATE | {subject} --{PREDICATE}[conf:{0.NN}, impact:{0.NN}, src:A003]--> {object}
```

- [ ] **Step 2: Eyeball check**

```bash
grep -n "시장 스냅샷\|src:" .claude/commands/cycle.md
```

Expected: matches for both phrases.

- [ ] **Step 3: Commit**

```bash
git add .claude/commands/cycle.md
git commit -m "$(cat <<'EOF'
feat(prompt): add market snapshot directive + src: graph block syntax

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: `market_query` MCP tool

**Files:**
- Modify: `newsparser/mcp_server.py`
- Create: `tests/test_market_query_mcp.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_market_query_mcp.py`:

```python
from datetime import datetime, timezone
import pytest

from newsparser.market import store


@pytest.fixture(autouse=True)
def market_db(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKET_DB_PATH", str(tmp_path / "market.db"))
    store.init_market_db()


def _bar(alias, d, close):
    return {"instrument": alias, "date": d,
            "open": close, "high": close, "low": close, "close": close, "volume": 1}


def test_market_query_returns_table_for_daily():
    store.upsert_daily([
        _bar("SPX", "2026-05-07", 5208.0),
        _bar("SPX", "2026-05-08", 5230.0),
    ])
    from newsparser.mcp_server import market_query
    out = market_query.fn(instruments=["SPX"], start="2026-05-07", end="2026-05-08", freq="1d")
    assert "SPX" in out
    assert "5230" in out or "5,230" in out
    assert "2026-05-07" in out and "2026-05-08" in out


def test_market_query_handles_no_data():
    from newsparser.mcp_server import market_query
    out = market_query.fn(instruments=["SPX"], start="2026-05-07", end="2026-05-08", freq="1d")
    assert "no data" in out.lower()


def test_market_query_unknown_alias():
    from newsparser.mcp_server import market_query
    out = market_query.fn(instruments=["XYZ"], start="2026-05-07", end="2026-05-08", freq="1d")
    assert "unknown" in out.lower() or "XYZ" in out


def test_market_query_hourly_uses_intraday_table():
    ts = datetime(2026, 5, 9, 3, 0, tzinfo=timezone.utc).isoformat()
    store.upsert_intraday([{"instrument": "SPX", "ts": ts,
                            "open": 5230.0, "high": 5232.0, "low": 5228.0,
                            "close": 5230.5, "volume": 1000}])
    from newsparser.mcp_server import market_query
    out = market_query.fn(instruments=["SPX"], start="2026-05-09", end="2026-05-09", freq="1h")
    assert "5230.5" in out or "5,230.5" in out
```

Note: `market_query.fn` accesses the underlying function on a FastMCP-decorated tool. If FastMCP's API differs in the installed version, fall back to importing the wrapped function directly.

- [ ] **Step 2: Run test to confirm it fails**

```bash
.venv/bin/pytest tests/test_market_query_mcp.py -v
```

Expected: FAIL — `market_query` not exported.

- [ ] **Step 3: Add the tool to `mcp_server.py`**

At the top of `newsparser/mcp_server.py`, add imports near the other ones:

```python
from datetime import date as _date, datetime as _datetime, time as _time, timezone as _tz

from newsparser.market import store as _market_store
from newsparser.market.fetcher import TICKERS as _MARKET_TICKERS
```

Append the new tool near the bottom of the file, before the `if __name__ == "__main__":` block:

```python
@mcp.tool()
def market_query(
    instruments: list[str],
    start: str,
    end: str,
    freq: str = "1d",
) -> str:
    """Return OHLCV rows for the given macro instruments as compact markdown tables.

    Valid instruments: SPX, NDX, KOSPI, USDKRW, USDJPY, DXY, VIX, TNX.
    Dates must be absolute (YYYY-MM-DD). The caller is expected to resolve
    relative expressions ("최근 30일") against the current date before invoking.
    """
    _market_store.init_market_db()
    start_d = _date.fromisoformat(start)
    end_d = _date.fromisoformat(end)
    out: list[str] = []
    for alias in instruments:
        if alias not in _MARKET_TICKERS:
            out.append(f"## {alias}\n\nunknown instrument\n")
            continue
        if freq == "1d":
            rows = _market_store.get_daily(alias, start_d, end_d)
            ts_key = "date"
        elif freq == "1h":
            start_dt = _datetime.combine(start_d, _time.min, tzinfo=_tz.utc)
            end_dt = _datetime.combine(end_d, _time.max, tzinfo=_tz.utc)
            rows = _market_store.get_intraday(alias, start_dt, end_dt)
            ts_key = "ts"
        else:
            out.append(f"## {alias}\n\nunsupported freq: {freq}\n")
            continue
        if not rows:
            out.append(f"## {alias} ({freq})\n\nno data for {alias} in {start}..{end}\n")
            continue
        out.append(f"## {alias} ({freq})")
        out.append("| " + ts_key + " | open | high | low | close | volume |")
        out.append("|---|---|---|---|---|---|")
        for r in rows:
            out.append(f"| {r[ts_key]} | {r['open']} | {r['high']} | {r['low']} | {r['close']} | {r['volume']} |")
        out.append("")
    return "\n".join(out)
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
.venv/bin/pytest tests/test_market_query_mcp.py tests/test_mcp_server.py -v
```

Expected: new tests pass; existing mcp server tests still pass.

- [ ] **Step 5: Commit**

```bash
git add newsparser/mcp_server.py tests/test_market_query_mcp.py
git commit -m "$(cat <<'EOF'
feat(mcp): add market_query tool for daily/hourly OHLCV lookups

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Tracker prompt mentions `market_query`

**Files:**
- Modify: `newsparser/bot/tracker.py`

- [ ] **Step 1: Locate the tracker prompt**

```bash
grep -n "TOOLS:\|역할\|graph_query" newsparser/bot/tracker.py | head -20
```

Note the line where the tool list / instructions live (the tracker prompt template).

- [ ] **Step 2: Add the market_query paragraph**

Append the following paragraph to the tracker's system/instructions block (the exact location depends on tracker.py layout — insert it adjacent to the existing tool-usage guidance):

```
시계열·가격·환율 질문이 들어오면 `market_query` 도구를 쓴다. `start`/`end`는 항상 절대 날짜(YYYY-MM-DD). 사용자가 "최근 한 달" 같이 말하면 오늘 날짜 기준으로 직접 변환해서 넣는다. 유효 instruments: SPX, NDX, KOSPI, USDKRW, USDJPY, DXY, VIX, TNX.
```

- [ ] **Step 3: Verify**

```bash
grep -n "market_query" newsparser/bot/tracker.py
```

Expected: at least one match in the prompt block.

- [ ] **Step 4: Run tracker tests to ensure nothing broke**

```bash
.venv/bin/pytest tests/test_tracker.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add newsparser/bot/tracker.py
git commit -m "$(cat <<'EOF'
feat(tracker): instruct tracker on market_query usage and instrument set

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: `input_builder.py` — `[A001]` indices + GUID line

**Files:**
- Modify: `newsparser/claude/input_builder.py`
- Modify: `tests/test_input_builder.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_input_builder.py`:

```python
def test_build_input_file_assigns_A_indices(tmp_path):
    insert_article("g1", "Bloomberg", "T1", "https://x.com/1", None, "b1", category="markets")
    insert_article("g2", "FT", "T2", "https://x.com/2", None, "b2", category="markets")
    insert_article("g3", "AP", "T3", "https://x.com/3", None, "b3", category="markets")
    path = build_input_file("2026-05-09-12", "markets")
    text = path.read_text()
    assert "[A001]" in text
    assert "[A002]" in text
    assert "[A003]" in text


def test_build_input_file_emits_guid_lines(tmp_path):
    insert_article("guid-abc", "Bloomberg", "T", "https://x.com/1", None, "b", category="markets")
    path = build_input_file("2026-05-09-12", "markets")
    text = path.read_text()
    assert "- GUID: guid-abc" in text


def test_index_order_matches_db_order(tmp_path):
    # The index order in the input file must match the order get_unprocessed returns,
    # which is what {slot}-guids.txt is written from in run_cycle.py.
    insert_article("g-first", "Bloomberg", "T1", "u1", "2026-05-09T01:00:00Z", "b", category="markets")
    insert_article("g-second", "FT", "T2", "u2", "2026-05-09T02:00:00Z", "b", category="markets")
    path = build_input_file("2026-05-09-12", "markets")
    text = path.read_text()
    a001 = text.index("[A001]")
    a002 = text.index("[A002]")
    g_first = text.index("g-first")
    g_second = text.index("g-second")
    assert a001 < g_first < a002 < g_second
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
.venv/bin/pytest tests/test_input_builder.py -v
```

Expected: 3 new tests FAIL (no `[A001]`, no GUID line).

- [ ] **Step 3: Modify `input_builder.py`**

Replace the file contents with:

```python
import os
from pathlib import Path

from newsparser.store.sqlite import get_unprocessed


def build_input_file(slot: str, category: str) -> Path:
    """Read unprocessed articles for `category` and write input.md for Claude.
    Returns the file path. Each article gets an [A001]-style index and an
    explicit GUID line so Claude can cite source articles via `src:A001,A007`
    in graph block relations.
    """
    workspace = Path(os.environ.get("WORKSPACE_DIR", "workspace"))
    articles = get_unprocessed(category=category)

    lines = [
        f"# Input {slot} KST [{category}]",
        f"## Collected Articles ({len(articles)} total)",
    ]
    for i, a in enumerate(articles, start=1):
        index = f"A{i:03d}"
        body = (a["body"] or "").replace("\n", "\n  ")
        lines += [
            f"\n### [{index}] [{a['source']}] {a['title']}",
            f"- URL: {a['url']}",
            f"- GUID: {a['guid']}",
            f"- Published: {a['published'] or 'unknown'}",
            f"- Body:\n  {body}",
        ]

    path = workspace / "input" / category / f"{slot}-input.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
.venv/bin/pytest tests/test_input_builder.py -v
```

Expected: all pass (old + new).

- [ ] **Step 5: Commit**

```bash
git add newsparser/claude/input_builder.py tests/test_input_builder.py
git commit -m "$(cat <<'EOF'
feat(input): emit [A001] indices and GUID line per article

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: `output_parser.py` — capture `src:` indices

**Files:**
- Modify: `newsparser/claude/output_parser.py`
- Modify: `tests/test_output_parser.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_output_parser.py`:

```python
def test_relation_with_src_captures_indices():
    report = (
        "## Graph updates\n"
        "### Relations\n"
        "- NEW | Fed --IMPACTS[conf:0.85, impact:0.7, src:A001,A007]--> SPX | rate decision\n"
    )
    entities, relations = parse_graph_updates(report)
    assert len(relations) == 1
    r = relations[0]
    assert r.subject == "Fed"
    assert r.obj == "SPX"
    assert r.predicate == "IMPACTS"
    assert r.source_indices == ["A001", "A007"]


def test_relation_without_src_keeps_empty_indices():
    report = (
        "## Graph updates\n"
        "### Relations\n"
        "- NEW | Fed --IMPACTS[conf:0.85, impact:0.7]--> SPX | rate decision\n"
    )
    entities, relations = parse_graph_updates(report)
    assert relations[0].source_indices == []


def test_relation_with_single_src_index():
    report = (
        "## Graph updates\n"
        "### Relations\n"
        "- NEW | OpenAI --ANNOUNCED[conf:0.95, impact:0.6, src:A003]--> GPT-5 | release\n"
    )
    entities, relations = parse_graph_updates(report)
    assert relations[0].source_indices == ["A003"]
```

Note: `parse_graph_updates` and `EntityUpdate`/`RelationUpdate` are likely already imported at the top of `test_output_parser.py`. If not, add the import.

- [ ] **Step 2: Run test to confirm it fails**

```bash
.venv/bin/pytest tests/test_output_parser.py -v
```

Expected: new tests FAIL — `source_indices` attribute does not exist.

- [ ] **Step 3: Modify `output_parser.py`**

Update `RELATION_RE` (note the new optional `(?:,\s*src:([A-Z0-9,]+))?` group):

```python
RELATION_RE = re.compile(
    r"^-\s+(NEW|UPDATE)\s+\|\s+(.+?)\s+--(\w+)"
    r"\[conf:([\d.]+),\s*impact:([\d.]+)(?:,\s*src:([A-Z0-9,]+))?\]-->\s+(.+?)"
    r"(?:\s+\|\s+(.+))?$"
)
```

Extend `RelationUpdate`:

```python
@dataclass
class RelationUpdate:
    op: str
    subject: str
    predicate: str
    obj: str
    confidence: float
    impact_score: float
    predicate_text: str = ""
    source_indices: list[str] = field(default_factory=list)
    source_article_guids: list[str] = field(default_factory=list)
```

Update the relation-parsing block inside `parse_graph_updates` (note the new group + cleanup):

```python
        m = RELATION_RE.match(stripped)
        if m:
            op, subject, predicate, conf, impact, src, obj, text = m.groups()
            indices = [s.strip() for s in (src or "").split(",") if s.strip()]
            relations.append(RelationUpdate(
                op=op, subject=subject.strip(), predicate=predicate,
                obj=obj.strip(), confidence=float(conf), impact_score=float(impact),
                predicate_text=(text or "").strip(),
                source_indices=indices,
            ))
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
.venv/bin/pytest tests/test_output_parser.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add newsparser/claude/output_parser.py tests/test_output_parser.py
git commit -m "$(cat <<'EOF'
feat(parser): capture src: indices and add source_article_guids field

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 12: `graph/writer.py` — accumulate `source_article_guids`

**Files:**
- Modify: `newsparser/graph/writer.py`
- Modify: `tests/test_graph_writer.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_graph_writer.py`:

```python
def test_upsert_relation_sets_source_article_guids_on_create():
    entities = [
        EntityUpdate(op="NEW", label="Institution", name="Fed", aliases=[]),
        EntityUpdate(op="NEW", label="Indicator", name="SPX", aliases=[]),
    ]
    rel = RelationUpdate(
        op="NEW", subject="Fed", predicate="IMPACTS",
        obj="SPX", confidence=0.85, impact_score=0.7,
        source_article_guids=["guid-a", "guid-b"],
    )
    apply_graph_updates(entities, [rel], "markets-2026-05-09-12")
    with get_driver().session() as s:
        row = s.run(
            "MATCH ()-[r:IMPACTS]->() RETURN r.source_article_guids AS guids"
        ).single()
    assert sorted(row["guids"]) == ["guid-a", "guid-b"]


def test_upsert_relation_unions_source_article_guids_on_match():
    entities = [
        EntityUpdate(op="NEW", label="Institution", name="Fed", aliases=[]),
        EntityUpdate(op="NEW", label="Indicator", name="SPX", aliases=[]),
    ]
    rel1 = RelationUpdate(op="NEW", subject="Fed", predicate="IMPACTS",
                          obj="SPX", confidence=0.85, impact_score=0.7,
                          source_article_guids=["guid-a", "guid-b"])
    rel2 = RelationUpdate(op="UPDATE", subject="Fed", predicate="IMPACTS",
                          obj="SPX", confidence=0.9, impact_score=0.8,
                          source_article_guids=["guid-b", "guid-c"])
    apply_graph_updates(entities, [rel1], "markets-2026-05-09-12")
    apply_graph_updates([], [rel2], "markets-2026-05-09-18")
    with get_driver().session() as s:
        row = s.run(
            "MATCH ()-[r:IMPACTS]->() RETURN r.source_article_guids AS guids"
        ).single()
    assert sorted(row["guids"]) == ["guid-a", "guid-b", "guid-c"]


def test_upsert_relation_handles_empty_source_article_guids():
    entities = [
        EntityUpdate(op="NEW", label="Company", name="OpenAI", aliases=[]),
        EntityUpdate(op="NEW", label="Company", name="Microsoft", aliases=[]),
    ]
    rel = RelationUpdate(op="NEW", subject="OpenAI", predicate="COMPETES_WITH",
                         obj="Microsoft", confidence=0.7, impact_score=0.5)
    apply_graph_updates(entities, [rel], "tech-2026-05-09-12")
    with get_driver().session() as s:
        row = s.run(
            "MATCH ()-[r:COMPETES_WITH]->() RETURN r.source_article_guids AS guids"
        ).single()
    # Either [] or null is acceptable; we just want this not to crash.
    assert row["guids"] in ([], None)
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
NEWSPARSER_TEST_NEO4J=1 NEO4J_URI=bolt://localhost:7687 NEO4J_PASSWORD=testpass \
  .venv/bin/pytest tests/test_graph_writer.py -v
```

Expected: 3 new tests FAIL — property `source_article_guids` is not set.

- [ ] **Step 3: Modify `upsert_relation` in `writer.py`**

Replace the body of `upsert_relation`:

```python
def upsert_relation(rel: RelationUpdate, cycle_id: str, category: str | None = None) -> None:
    with get_driver().session() as session:
        session.run(
            "MATCH (a {canonical_name: $subject}) "
            "MATCH (b {canonical_name: $obj}) "
            f"MERGE (a)-[r:{rel.predicate}]->(b) "
            "ON CREATE SET r.first_seen = datetime(), r.confidence = $conf, "
            "  r.impact_score = $impact, r.source_cycles = [$cycle_id], "
            "  r.predicate_text = $text, r.category = $category, "
            "  r.source_article_guids = $guids "
            "ON MATCH SET r.impact_score = 0.85 * r.impact_score + 0.15 * $impact, "
            "  r.source_cycles = r.source_cycles + [$cycle_id], "
            "  r.category = coalesce(r.category, $category), "
            "  r.source_article_guids = coalesce(r.source_article_guids, []) + "
            "    [g IN $guids WHERE NOT g IN coalesce(r.source_article_guids, [])] "
            "SET r.last_seen = datetime()",
            subject=rel.subject, obj=rel.obj,
            conf=rel.confidence, impact=rel.impact_score,
            cycle_id=cycle_id, text=rel.predicate_text,
            category=category,
            guids=list(rel.source_article_guids or []),
        )
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
NEWSPARSER_TEST_NEO4J=1 NEO4J_URI=bolt://localhost:7687 NEO4J_PASSWORD=testpass \
  .venv/bin/pytest tests/test_graph_writer.py -v
```

Expected: all pass (existing + new).

- [ ] **Step 5: Commit**

```bash
git add newsparser/graph/writer.py tests/test_graph_writer.py
git commit -m "$(cat <<'EOF'
feat(graph): accumulate source_article_guids on relations (idempotent union)

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 13: `apply_graph.py` — resolver step (indices → GUIDs)

**Files:**
- Modify: `newsparser/scripts/apply_graph.py`
- Modify: `tests/test_apply_graph.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_apply_graph.py`:

```python
def test_resolver_maps_A_indices_to_real_guids(tmp_path, monkeypatch):
    import os
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "workspace"))
    ws = tmp_path / "workspace"
    (ws / "input" / "markets").mkdir(parents=True)
    (ws / "input" / "markets" / "2026-05-09-12-guids.txt").write_text("g-first\ng-second\ng-third\n")
    (ws / "cycles" / "markets").mkdir(parents=True)
    (ws / "cycles" / "markets" / "2026-05-09-12.md").write_text(
        "## Graph updates\n"
        "### Relations\n"
        "- NEW | Fed --IMPACTS[conf:0.85, impact:0.7, src:A001,A003]--> SPX | rate\n"
    )

    captured = {}

    def fake_apply(entities, relations, cycle_id, category=None):
        captured["relations"] = relations

    from unittest.mock import patch
    import newsparser.scripts.apply_graph as script
    with patch.object(script, "apply_graph_updates", side_effect=fake_apply):
        script.main(["apply_graph.py", "markets", "2026-05-09-12"])

    rels = captured["relations"]
    assert len(rels) == 1
    assert sorted(rels[0].source_article_guids) == ["g-first", "g-third"]


def test_resolver_drops_out_of_range_indices(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "workspace"))
    ws = tmp_path / "workspace"
    (ws / "input" / "markets").mkdir(parents=True)
    (ws / "input" / "markets" / "2026-05-09-12-guids.txt").write_text("g-only\n")
    (ws / "cycles" / "markets").mkdir(parents=True)
    (ws / "cycles" / "markets" / "2026-05-09-12.md").write_text(
        "## Graph updates\n"
        "### Relations\n"
        "- NEW | Fed --IMPACTS[conf:0.85, impact:0.7, src:A001,A099]--> SPX | rate\n"
    )

    captured = {}

    def fake_apply(entities, relations, cycle_id, category=None):
        captured["relations"] = relations

    from unittest.mock import patch
    import newsparser.scripts.apply_graph as script
    with patch.object(script, "apply_graph_updates", side_effect=fake_apply):
        script.main(["apply_graph.py", "markets", "2026-05-09-12"])

    rels = captured["relations"]
    # A099 is out of range; only A001 → "g-only" survives
    assert rels[0].source_article_guids == ["g-only"]


def test_resolver_handles_missing_guids_file(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "workspace"))
    ws = tmp_path / "workspace"
    (ws / "cycles" / "markets").mkdir(parents=True)
    (ws / "cycles" / "markets" / "2026-05-09-12.md").write_text(
        "## Graph updates\n"
        "### Relations\n"
        "- NEW | Fed --IMPACTS[conf:0.85, impact:0.7, src:A001]--> SPX | rate\n"
    )

    captured = {}

    def fake_apply(entities, relations, cycle_id, category=None):
        captured["relations"] = relations

    from unittest.mock import patch
    import newsparser.scripts.apply_graph as script
    with patch.object(script, "apply_graph_updates", side_effect=fake_apply):
        script.main(["apply_graph.py", "markets", "2026-05-09-12"])

    # No guids file → no resolution; source_article_guids stays empty.
    assert captured["relations"][0].source_article_guids == []
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
.venv/bin/pytest tests/test_apply_graph.py -v
```

Expected: the 3 new tests FAIL — `source_article_guids` not populated.

- [ ] **Step 3: Modify `apply_graph.py`**

Replace the body of `main` in `newsparser/scripts/apply_graph.py`:

```python
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from newsparser.claude.output_parser import parse_graph_updates
from newsparser.graph.writer import apply_graph_updates

logger = logging.getLogger(__name__)


def _resolve_source_indices(relations, guids: list[str]) -> None:
    """Mutate each relation's source_article_guids based on its source_indices."""
    for r in relations:
        resolved: list[str] = []
        for idx in r.source_indices:
            if not (len(idx) >= 2 and idx[0] == "A" and idx[1:].isdigit()):
                logger.warning("invalid src index %r — dropped", idx)
                continue
            n = int(idx[1:]) - 1
            if 0 <= n < len(guids):
                resolved.append(guids[n])
            else:
                logger.warning("out-of-range src index %r (have %d guids) — dropped",
                               idx, len(guids))
        r.source_article_guids = resolved


def main(argv: list[str] | None = None) -> None:
    args = argv if argv is not None else sys.argv
    if len(args) != 3:
        name = args[0] if args else "apply_graph.py"
        print(f"Usage: {name} <category> <slot>", file=sys.stderr)
        sys.exit(1)

    category, slot = args[1], args[2]
    workspace = Path(os.environ.get("WORKSPACE_DIR", "workspace"))
    report_path = workspace / "cycles" / category / f"{slot}.md"

    if not report_path.exists():
        print(f"Report not found: {report_path}", file=sys.stderr)
        sys.exit(1)

    report = report_path.read_text(encoding="utf-8")
    entities, relations = parse_graph_updates(report)

    guids_path = workspace / "input" / category / f"{slot}-guids.txt"
    guids = (guids_path.read_text().splitlines()
             if guids_path.exists() else [])
    guids = [g.strip() for g in guids if g.strip()]
    _resolve_source_indices(relations, guids)

    cycle_id = f"{category}-{slot}"
    apply_graph_updates(entities, relations, cycle_id=cycle_id, category=category)
    print(f"Graph updated: {len(entities)} entities, {len(relations)} relations")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    main()
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
.venv/bin/pytest tests/test_apply_graph.py -v
```

Expected: existing + new tests pass.

- [ ] **Step 5: Commit**

```bash
git add newsparser/scripts/apply_graph.py tests/test_apply_graph.py
git commit -m "$(cat <<'EOF'
feat(apply_graph): resolve src: indices to article GUIDs before upsert

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 14: `annotate.py` — intraday + daily-fallback annotation

**Files:**
- Create: `newsparser/market/annotate.py`
- Create: `tests/test_market_annotate.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_market_annotate.py`:

```python
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

import pytest

from newsparser.claude.output_parser import RelationUpdate
from newsparser.market import store


@pytest.fixture(autouse=True)
def market_db(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKET_DB_PATH", str(tmp_path / "market.db"))
    store.init_market_db()


def _rel(predicate="IMPACTS", obj="SPX"):
    return RelationUpdate(op="NEW", subject="Fed", predicate=predicate,
                          obj=obj, confidence=0.85, impact_score=0.7)


def test_annotate_skips_non_tracked_alias():
    from newsparser.market import annotate
    with patch.object(annotate, "_apply_annotation_cypher") as cy:
        n = annotate.maybe_annotate_impacts([_rel(obj="OpenAI")], "2026-05-09-12", "markets")
    assert n == 0
    assert cy.call_count == 0


def test_annotate_skips_non_impacts_predicate():
    from newsparser.market import annotate
    with patch.object(annotate, "_apply_annotation_cypher") as cy:
        n = annotate.maybe_annotate_impacts([_rel(predicate="ANNOUNCED")], "2026-05-09-12", "markets")
    assert n == 0
    assert cy.call_count == 0


def test_annotate_uses_intraday_when_available():
    """Slot 12:00 KST = 03:00 UTC. Bars at 02:00 UTC and 03:00 UTC."""
    from newsparser.market import annotate
    before_ts = datetime(2026, 5, 9, 2, 0, tzinfo=timezone.utc).isoformat()
    after_ts  = datetime(2026, 5, 9, 3, 0, tzinfo=timezone.utc).isoformat()
    bars = [
        {"instrument": "SPX", "ts": before_ts, "open": 0, "high": 0, "low": 0, "close": 100.0, "volume": 0},
        {"instrument": "SPX", "ts": after_ts,  "open": 0, "high": 0, "low": 0, "close": 99.0,  "volume": 0},
    ]

    with patch("newsparser.market.annotate.fetcher.fetch_intraday_hourly", return_value=bars), \
         patch.object(annotate, "_apply_annotation_cypher") as cy:
        n = annotate.maybe_annotate_impacts([_rel()], "2026-05-09-12", "markets")

    assert n == 1
    call_kwargs = cy.call_args.kwargs
    assert call_kwargs["window_literal"] == "[-60m, +60m]"
    assert abs(call_kwargs["delta_pct"] - (-1.0)) < 1e-6  # (99-100)/100*100


def test_annotate_falls_back_to_daily_when_intraday_empty():
    from newsparser.market import annotate
    # No intraday data; seed daily values
    store.upsert_daily([
        {"instrument": "SPX", "date": "2026-05-08",
         "open": 0, "high": 0, "low": 0, "close": 100.0, "volume": 0},
        {"instrument": "SPX", "date": "2026-05-09",
         "open": 0, "high": 0, "low": 0, "close": 101.5, "volume": 0},
    ])

    with patch("newsparser.market.annotate.fetcher.fetch_intraday_hourly", return_value=[]), \
         patch.object(annotate, "_apply_annotation_cypher") as cy:
        n = annotate.maybe_annotate_impacts([_rel()], "2026-05-09-12", "markets")

    assert n == 1
    call_kwargs = cy.call_args.kwargs
    assert call_kwargs["window_literal"] == "daily"
    assert abs(call_kwargs["delta_pct"] - 1.5) < 1e-6


def test_annotate_skips_when_both_intraday_and_daily_empty():
    from newsparser.market import annotate
    with patch("newsparser.market.annotate.fetcher.fetch_intraday_hourly", return_value=[]), \
         patch.object(annotate, "_apply_annotation_cypher") as cy:
        n = annotate.maybe_annotate_impacts([_rel()], "2026-05-09-12", "markets")
    assert n == 0
    assert cy.call_count == 0


def test_annotate_never_raises():
    from newsparser.market import annotate
    def boom(*a, **kw):
        raise RuntimeError("kaboom")
    with patch("newsparser.market.annotate.fetcher.fetch_intraday_hourly", side_effect=boom):
        # Should swallow the exception per-relation; never propagate
        n = annotate.maybe_annotate_impacts([_rel()], "2026-05-09-12", "markets")
    assert n == 0
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
.venv/bin/pytest tests/test_market_annotate.py -v
```

Expected: FAIL — `ModuleNotFoundError: newsparser.market.annotate`.

- [ ] **Step 3: Implement `annotate.py`**

Create `newsparser/market/annotate.py`:

```python
import logging
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from newsparser.claude.output_parser import RelationUpdate
from newsparser.market import fetcher, store
from newsparser.graph.neo4j_client import get_driver

logger = logging.getLogger(__name__)

_TRACKED = {"IMPACTS", "INFLUENCES"}
_KST = ZoneInfo("Asia/Seoul")
_DAILY_CUSHION_DAYS = 3


def _slot_to_utc(slot: str) -> datetime:
    dt = datetime.strptime(slot, "%Y-%m-%d-%H").replace(tzinfo=_KST)
    return dt.astimezone(timezone.utc)


def _apply_annotation_cypher(
    *,
    subject: str,
    predicate: str,
    obj: str,
    delta_pct: float,
    window_literal: str,
) -> None:
    with get_driver().session() as session:
        session.run(
            "MATCH (a {canonical_name: $subject})-[r:" + predicate + "]->(b {canonical_name: $obj}) "
            "SET r.impact_price_delta_pct = $delta, "
            "    r.impact_price_delta_window = $window, "
            "    r.impact_target_instrument = $obj, "
            "    r.annotated_at = datetime()",
            subject=subject, obj=obj, delta=delta_pct, window=window_literal,
        )


def _intraday_delta(alias: str, slot_utc: datetime) -> float | None:
    bars = fetcher.fetch_intraday_hourly(
        alias,
        slot_utc - timedelta(minutes=60),
        slot_utc + timedelta(minutes=60),
    )
    if not bars:
        return None
    store.upsert_intraday(bars)
    before = [b for b in bars if b["ts"] < slot_utc.isoformat()]
    after = [b for b in bars if b["ts"] >= slot_utc.isoformat()]
    if not before or not after:
        return None
    return (after[0]["close"] - before[-1]["close"]) / before[-1]["close"] * 100


def _daily_delta(alias: str, slot_utc: datetime) -> float | None:
    slot_date = slot_utc.date()
    bars = store.get_daily(
        alias,
        slot_date - timedelta(days=_DAILY_CUSHION_DAYS),
        slot_date + timedelta(days=_DAILY_CUSHION_DAYS),
    )
    prev = [b for b in bars if b["date"] < slot_date.isoformat()]
    event = [b for b in bars if b["date"] >= slot_date.isoformat()]
    if not prev or not event:
        return None
    return (event[0]["close"] - prev[-1]["close"]) / prev[-1]["close"] * 100


def maybe_annotate_impacts(relations: list[RelationUpdate], slot: str, category: str) -> int:
    annotated = 0
    try:
        slot_utc = _slot_to_utc(slot)
    except Exception as exc:
        logger.warning("annotate: invalid slot %r (%s)", slot, exc)
        return 0

    for r in relations:
        try:
            if r.predicate not in _TRACKED:
                continue
            if r.obj not in fetcher.TICKERS:
                continue

            delta = _intraday_delta(r.obj, slot_utc)
            window_literal = "[-60m, +60m]"
            if delta is None:
                delta = _daily_delta(r.obj, slot_utc)
                window_literal = "daily"
            if delta is None:
                continue

            _apply_annotation_cypher(
                subject=r.subject,
                predicate=r.predicate,
                obj=r.obj,
                delta_pct=delta,
                window_literal=window_literal,
            )
            annotated += 1
        except Exception as exc:
            logger.warning("annotate failed for %s --%s--> %s: %s",
                           r.subject, r.predicate, r.obj, exc)
    return annotated
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
.venv/bin/pytest tests/test_market_annotate.py -v
```

Expected: all 6 tests pass.

- [ ] **Step 5: Commit**

```bash
git add newsparser/market/annotate.py tests/test_market_annotate.py
git commit -m "$(cat <<'EOF'
feat(market): add causal annotation with intraday + daily fallback

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 15: `apply_graph.py` — call `maybe_annotate_impacts`

**Files:**
- Modify: `newsparser/scripts/apply_graph.py`
- Modify: `tests/test_apply_graph.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_apply_graph.py`:

```python
def test_apply_graph_calls_annotate_after_apply(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "workspace"))
    ws = tmp_path / "workspace"
    (ws / "cycles" / "markets").mkdir(parents=True)
    (ws / "cycles" / "markets" / "2026-05-09-12.md").write_text(
        "## Graph updates\n"
        "### Relations\n"
        "- NEW | Fed --IMPACTS[conf:0.85, impact:0.7]--> SPX | rate\n"
    )

    order: list[str] = []

    def fake_apply(*a, **kw):
        order.append("apply")

    def fake_annotate(relations, slot, category):
        order.append("annotate")
        assert slot == "2026-05-09-12"
        assert category == "markets"
        return 1

    from unittest.mock import patch
    import newsparser.scripts.apply_graph as script
    with patch.object(script, "apply_graph_updates", side_effect=fake_apply), \
         patch.object(script, "maybe_annotate_impacts", side_effect=fake_annotate):
        script.main(["apply_graph.py", "markets", "2026-05-09-12"])

    assert order == ["apply", "annotate"]


def test_apply_graph_annotate_failure_doesnt_break_cycle(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "workspace"))
    ws = tmp_path / "workspace"
    (ws / "cycles" / "markets").mkdir(parents=True)
    (ws / "cycles" / "markets" / "2026-05-09-12.md").write_text(
        "## Graph updates\n"
        "### Relations\n"
        "- NEW | Fed --IMPACTS[conf:0.85, impact:0.7]--> SPX | rate\n"
    )

    from unittest.mock import patch
    import newsparser.scripts.apply_graph as script
    with patch.object(script, "apply_graph_updates"), \
         patch.object(script, "maybe_annotate_impacts", side_effect=RuntimeError("boom")):
        # Must not raise
        script.main(["apply_graph.py", "markets", "2026-05-09-12"])
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
.venv/bin/pytest tests/test_apply_graph.py -v
```

Expected: new tests FAIL — `maybe_annotate_impacts` not imported by `apply_graph`.

- [ ] **Step 3: Modify `apply_graph.py`**

Add the import near the top (with the other imports from newsparser):

```python
from newsparser.market.annotate import maybe_annotate_impacts
```

In `main`, after the `apply_graph_updates(...)` call and the existing `print(...)` line, append:

```python
    try:
        annotated = maybe_annotate_impacts(relations, slot, category)
        if annotated:
            print(f"Annotated {annotated} relations with price reactions.")
    except Exception as exc:
        logger.warning("annotation pass failed: %s", exc)
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
.venv/bin/pytest tests/test_apply_graph.py -v
```

Expected: existing + new tests pass.

- [ ] **Step 5: Commit**

```bash
git add newsparser/scripts/apply_graph.py tests/test_apply_graph.py
git commit -m "$(cat <<'EOF'
feat(apply_graph): call maybe_annotate_impacts after graph updates

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 16: Cron registration + smoke script

**Files:**
- Modify: `/etc/cron.d/newsparser` (system file — requires sudo)
- Create: `scripts/smoke_market.py`

No automated tests for either. Manual verification only.

- [ ] **Step 1: Create the smoke script**

Create `scripts/smoke_market.py`:

```python
"""Manual smoke test for newsparser/market/fetcher.py.

Run after fetcher changes to confirm yfinance still returns sensible data
for each tracked alias. Not exercised by CI.

Usage:
    .venv/bin/python scripts/smoke_market.py
"""
from datetime import date, datetime, timedelta, timezone
import sys

from newsparser.market import fetcher


def main() -> int:
    today = date.today()
    yesterday = today - timedelta(days=1)
    rc = 0
    for alias in fetcher.TICKERS:
        bars = fetcher.fetch_daily(alias, yesterday - timedelta(days=5), today)
        if bars:
            print(f"  ✓ {alias}: {len(bars)} daily bars, latest close={bars[-1]['close']}")
        else:
            print(f"  ✗ {alias}: no daily bars returned")
            rc = 1

    print("Intraday smoke for SPX (last 2 hours UTC):")
    end_utc = datetime.now(timezone.utc)
    start_utc = end_utc - timedelta(hours=2)
    bars = fetcher.fetch_intraday_hourly("SPX", start_utc, end_utc)
    print(f"  SPX intraday: {len(bars)} bars")

    return rc


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run the smoke script (network-dependent — skip if offline)**

```bash
.venv/bin/python scripts/smoke_market.py
```

Expected: at least 6 of 8 aliases return non-empty daily bars (KRW=X / DXY can occasionally be flaky).

- [ ] **Step 3: Bootstrap the daily DB (one-shot)**

```bash
.venv/bin/python -m newsparser.scripts.fetch_market_daily
```

Expected: `workspace/market.db` is created and back-filled five years for each alias. Log lines like `SPX: +1250 rows` appear.

- [ ] **Step 4: Verify with snapshot**

```bash
.venv/bin/python -c "from datetime import date; from newsparser.market import snapshot, store; store.init_market_db(); print(snapshot.build_snapshot_block(date.today()))"
```

Expected: a `## 시장 스냅샷` block with 8 rows and concrete numbers.

- [ ] **Step 5: Add cron line**

```bash
sudo tee -a /etc/cron.d/newsparser > /dev/null << 'EOF'

# Market daily fetch — KST 07:30, ~1.5h after NY close (EDT) / ~30m after (EST)
30 7 * * *  ubuntu  flock -n /home/ubuntu/newsparser/workspace/state/locks/market_daily /home/ubuntu/newsparser/.venv/bin/python -m newsparser.scripts.fetch_market_daily >> /home/ubuntu/newsparser/workspace/logs/market.log 2>&1
EOF
sudo chmod 644 /etc/cron.d/newsparser
```

- [ ] **Step 6: Verify cron line loaded**

```bash
sudo systemctl status cron
grep market /etc/cron.d/newsparser
```

Expected: cron running, the new line visible.

- [ ] **Step 7: Wait for first scheduled run (or trigger manually)**

```bash
tail -f workspace/logs/market.log
```

Expected at next 07:30 KST: `SPX: +N rows`, `NDX: +N rows`, etc.

- [ ] **Step 8: Commit**

```bash
git add scripts/smoke_market.py
git commit -m "$(cat <<'EOF'
feat(market): add manual yfinance smoke script (not run in CI)

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

Note: the cron file is a system file outside the repo; commit only documents the smoke script.

---

## Self-Review

### Spec coverage

| Spec section | Task |
|---|---|
| 1. Module Layout | Tasks 1–16 collectively |
| 2. Data Model (ticker dict + market_daily + market_intraday + Neo4j props) | Tasks 2, 3, 12 |
| 3. Fetcher + Daily Cron | Tasks 3, 4, 16 |
| 4. Cycle Integration (snapshot + cycle.md + MCP tool + tracker) | Tasks 5, 6, 7, 8, 9 |
| 5. Causal Annotation (intraday + daily fallback) | Task 14 + Task 15 hook-up |
| 6. Article-Relation Source Linkage | Tasks 10, 11, 12, 13 |
| 7. Operations & Error Handling | Task 4 (per-alias try/except), Task 6 (snapshot soft-fail), Task 14 (annotate soft-fail), Task 15 (annotate-error swallow), Task 16 (cron + lock) |
| 8. Testing | Each Task has its TDD loop; integration assertions added to test_run_cycle_script and test_apply_graph |

All spec sections covered.

### Placeholder scan

- No "TBD" / "TODO" / "fill in later".
- Every code step shows complete code.
- Every test step shows complete test code (imports + asserts).
- Every command shows exact invocation.

### Type & signature consistency

- `Bar` shape: store + fetcher + snapshot + annotate all use `dict` with keys `instrument, date|ts, open, high, low, close, volume`. ✓
- `RelationUpdate` gains `source_indices` and `source_article_guids`. Parser (Task 11) sets `source_indices`; apply_graph (Task 13) sets `source_article_guids`; writer (Task 12) reads `source_article_guids`. ✓
- `maybe_annotate_impacts(relations, slot, category) -> int` — same signature in Task 14 implementation and Task 15 caller. ✓
- `market_query(instruments, start, end, freq) -> str` — same signature in MCP tool and tracker prompt. ✓
- `latest_daily_date(alias) -> date | None` — used identically in Task 4. ✓

---

## Files to Create

```
newsparser/market/__init__.py
newsparser/market/fetcher.py
newsparser/market/store.py
newsparser/market/snapshot.py
newsparser/market/annotate.py
newsparser/scripts/fetch_market_daily.py
scripts/smoke_market.py
tests/test_market_store.py
tests/test_market_fetcher.py
tests/test_market_snapshot.py
tests/test_market_annotate.py
tests/test_fetch_market_daily_script.py
tests/test_market_query_mcp.py
```

## Files to Modify

```
pyproject.toml
newsparser/mcp_server.py
newsparser/scripts/apply_graph.py
newsparser/scripts/run_cycle.py
.claude/commands/cycle.md
newsparser/bot/tracker.py
newsparser/claude/input_builder.py
newsparser/claude/output_parser.py
newsparser/graph/writer.py
tests/test_run_cycle_script.py
tests/test_input_builder.py
tests/test_output_parser.py
tests/test_graph_writer.py
tests/test_apply_graph.py
/etc/cron.d/newsparser
```
