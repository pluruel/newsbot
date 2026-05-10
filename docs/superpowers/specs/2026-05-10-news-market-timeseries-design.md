# News × Market Time-Series Integration

**Date:** 2026-05-10
**Goal:** Connect the existing news pipeline with macro market time-series (indices + FX) so cycle digests carry market context, the knowledge graph can be annotated with price reactions, and the tracker can answer time-series questions on demand.

---

## Motivation

The current pipeline ingests news, classifies it (tech / markets), and produces 6-hour cycle digests plus a Neo4j knowledge graph. The graph has `Indicator` and `Market` entity labels and `IMPACTS` / `INFLUENCES` relations, but no actual price data exists anywhere — every market claim is text-only.

This spec adds a thin time-series layer covering 8 macro instruments (S&P 500, NASDAQ Composite, KOSPI, USD/KRW, USD/JPY, DXY, VIX, US 10Y) so:

1. Every cycle digest opens with a one-line market snapshot derived from real numbers, not Claude's recollection.
2. `IMPACTS` / `INFLUENCES` graph relations whose target is one of those instruments get annotated with the ±60-minute intraday price reaction, turning the graph into a verifiable causal record over time.
3. The `/tracker` flow can answer ad-hoc questions like "한 달 SPX 흐름 보여줘" or "FOMC 직후 환율" via a single MCP tool.

The work is sized so the staged value (digest context → causal verification → tracker) lands on the same schema without rework.

---

## Architecture Overview

```
cron 07:30 KST
  → fetch_market_daily.py
    → fetcher.fetch_daily (yfinance)
    → store.upsert_daily   (workspace/market.db)

cron 00/06/12/18 KST
  → run_cycle.py
    → snapshot.build_snapshot_block(today)
    → prepend "## 시장 스냅샷" to workspace/input/{cat}/{slot}-input.md
    → run_claude("/cycle {slot} {cat}")
        ├── Claude reads input (snapshot included)
        ├── Claude may call market_query MCP for extra data
        └── Claude writes workspace/cycles/{cat}/{slot}.md
    → apply_graph.py
        → apply_graph_updates(entities, relations, cycle_id, category)   (existing)
        → annotate.maybe_annotate_impacts(relations, slot, category)     (new)
            → fetcher.fetch_intraday_hourly (yfinance)
            → store.upsert_intraday
            → Neo4j SET r.impact_price_delta_pct, r.impact_price_delta_window, ...

tracker free-text
  → run_claude (tracker prompt)
    → Claude calls market_query MCP tool
```

Storage: a separate `workspace/market.db` SQLite file (WAL mode) — chosen over Neo4j (awkward for OHLCV bars), DuckDB (no scale benefit at < 100K rows), and TimescaleDB (operational overhead unjustified for a single-host personal project). The macro instrument set tops out at roughly 30K daily rows across a decade plus selective hourly bars; SQLite handles this with index lookups in microseconds.

---

## Section 1 — Module Layout

**Create:**

```
newsparser/market/__init__.py
newsparser/market/fetcher.py        # yfinance calls + retry/backoff
newsparser/market/store.py          # SQLite r/w for market_daily and market_intraday
newsparser/market/snapshot.py       # snapshot markdown block builder
newsparser/market/annotate.py       # maybe_annotate_impacts: graph relation enrichment

newsparser/scripts/fetch_market_daily.py   # cron entrypoint for daily incremental fetch

tests/test_market_fetcher.py
tests/test_market_store.py
tests/test_market_snapshot.py
tests/test_market_annotate.py
tests/test_fetch_market_daily_script.py
tests/test_market_query_mcp.py
```

**Modify:**

```
newsparser/mcp_server.py            # add @mcp.tool() market_query
newsparser/scripts/apply_graph.py   # call maybe_annotate_impacts after apply_graph_updates
newsparser/scripts/run_cycle.py     # prepend snapshot block to cycle input file
.claude/commands/cycle.md           # one-paragraph instruction about the snapshot block + canonical_name convention
newsparser/bot/tracker.py           # tracker prompt guidance for market_query usage
/etc/cron.d/newsparser              # add 07:30 KST daily fetch line
tests/test_run_cycle_script.py      # one new case: snapshot block is prepended
```

The rationale for putting all new code under `newsparser/market/` (parallel to `collector/`, `graph/`, `claude/`) is to keep the time-series concern isolated. Each file has one responsibility and can be reasoned about independently.

---

## Section 2 — Data Model

### Ticker dictionary

`newsparser/market/fetcher.py` exposes a single mapping. The left column is the alias used everywhere in the codebase, in Neo4j `Indicator.canonical_name` values, and in MCP tool arguments. The right column is the yfinance symbol.

| Alias    | yfinance symbol | Korean display |
|----------|-----------------|----------------|
| SPX      | `^GSPC`         | S&P 500        |
| NDX      | `^IXIC`         | NASDAQ         |
| KOSPI    | `^KS11`         | KOSPI          |
| USDKRW   | `KRW=X`         | USD/KRW        |
| USDJPY   | `JPY=X`         | USD/JPY        |
| DXY      | `DX-Y.NYB`      | 달러인덱스     |
| VIX      | `^VIX`          | VIX            |
| TNX      | `^TNX`          | 미 10Y         |

The Korean display strings are used only for snapshot rendering. All inter-module APIs (store, MCP, annotate) use aliases.

### SQLite schema (`workspace/market.db`)

```sql
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS market_daily (
    instrument TEXT NOT NULL,
    date       TEXT NOT NULL,        -- YYYY-MM-DD, exchange-local trading date
    open       REAL,
    high       REAL,
    low        REAL,
    close      REAL,
    volume     INTEGER,
    PRIMARY KEY (instrument, date)
);
CREATE INDEX IF NOT EXISTS idx_market_daily_date ON market_daily(date);

CREATE TABLE IF NOT EXISTS market_intraday (
    instrument TEXT NOT NULL,
    ts         TEXT NOT NULL,        -- ISO8601 UTC, 1-hour bucket start
    open       REAL,
    high       REAL,
    low        REAL,
    close      REAL,
    volume     INTEGER,
    PRIMARY KEY (instrument, ts)
);
CREATE INDEX IF NOT EXISTS idx_market_intraday_ts ON market_intraday(ts);
```

`market_daily.date` is the exchange-local trading date as returned by yfinance (NY date for US/global instruments, KST for KOSPI). Mixing time zones in a single column is acceptable because `date` is only used as a trading-day key, and snapshots / queries are formulated in terms of "the most recent N trading days for instrument X."

`market_intraday.ts` is normalized to UTC ISO8601 so that aggregation across instruments around a single news event is well-defined.

### Neo4j additions

No new node labels. Existing `Indicator` and `Market` labels keep working. The convention added: when the report's graph block names one of the eight macro instruments, the `canonical_name` must be the alias from the table above. This is the only contract between the graph and the price DB.

New properties applied to `IMPACTS` and `INFLUENCES` relations whose `object` is a tracked alias (Section 4):

| Property                       | Type     | Meaning                                      |
|--------------------------------|----------|----------------------------------------------|
| `impact_price_delta_pct`       | float    | `(close_after - close_before) / close_before * 100`, computed over the window |
| `impact_price_delta_window`    | string   | `"[-60m, +60m]"` (literal label for now)     |
| `impact_target_instrument`     | string   | The alias whose price was measured (SPX, etc.) |
| `annotated_at`                 | datetime | When `annotate.py` wrote these properties    |

Absent properties = not annotated (either target was non-macro, or fetcher returned no data — both treated as benign).

---

## Section 3 — Fetcher and Daily Cron

### `newsparser/market/fetcher.py`

```python
TICKERS: dict[str, str] = {...}   # the table above

def fetch_daily(alias: str, start: date, end: date) -> list[Bar]: ...
def fetch_intraday_hourly(alias: str, start: datetime, end: datetime) -> list[Bar]: ...
```

Both functions:

- Use `yfinance.Ticker(symbol).history(...)` internally.
- Retry up to 3 times with exponential backoff (1s, 2s, 4s) plus small jitter.
- Treat empty results (holidays, market closed, transient block) as a normal "no data" return — never raise.
- Convert intraday timestamps to UTC ISO8601 before returning.
- Round numeric values to a reasonable precision (price 4 dp, volume integer).

The `Bar` shape is a `TypedDict` or `dataclass` with `instrument, date_or_ts, open, high, low, close, volume`. The daily and intraday variants share the same fields; the store layer routes them to the right table.

### `newsparser/market/store.py`

```python
def init_market_db(path: Path | None = None) -> None: ...
def upsert_daily(rows: list[Bar]) -> int: ...
def upsert_intraday(rows: list[Bar]) -> int: ...
def get_daily(alias: str, start: date, end: date) -> list[Bar]: ...
def get_intraday(alias: str, start: datetime, end: datetime) -> list[Bar]: ...
def latest_daily_date(alias: str) -> date | None: ...
```

`init_market_db` runs the schema DDL and `PRAGMA journal_mode=WAL`. Path defaults to `workspace/market.db` (override via `MARKET_DB_PATH` env for tests).

Upserts use `INSERT ... ON CONFLICT(instrument, date) DO UPDATE SET ...` so re-fetching the same range is idempotent and resilient to upstream corrections.

### `newsparser/scripts/fetch_market_daily.py`

Cron entrypoint. Behavior:

1. `init_market_db()`.
2. For each alias in `TICKERS`:
   a. `last = latest_daily_date(alias)`.
   b. If `last is None`: fetch from `today - 5y` to `today` (backfill).
   c. Else: fetch from `last + 1d` to `today` (incremental).
   d. `upsert_daily(rows)`.
   e. Log `f"{alias}: +{len(rows)} rows"`.
3. Each alias wrapped in `try/except`; one failure does not stop the rest.
4. Exit 0 always (cron-friendly).

### Cron line

Added to `/etc/cron.d/newsparser`:

```
30 7 * * *  ubuntu  flock -n /home/ubuntu/newsparser/workspace/state/locks/market_daily \
  /home/ubuntu/newsparser/.venv/bin/python -m newsparser.scripts.fetch_market_daily \
  >> /home/ubuntu/newsparser/workspace/logs/market.log 2>&1
```

KST 07:30 is chosen to clear both US daylight saving regimes: NY 16:00 EDT = KST 05:00 next day, NY 16:00 EST = KST 06:00 next day. 07:30 leaves at least 1.5 hours for yfinance to publish the daily close, and finishes well before the 12:00 KST cycle. If any instrument is still slow, that day's row stays missing and the next daily run picks it up via the `latest_daily_date` check.

---

## Section 4 — Cycle Integration

### Snapshot block

`newsparser/market/snapshot.py`:

```python
def build_snapshot_block(at: date) -> str:
    """Return the markdown block to prepend to a cycle input file.

    Pulls the most recent trading day with data <= `at` for each alias,
    plus the previous trading day, and renders one row per alias.
    """
```

Rendered output (example):

```
## 시장 스냅샷 (2026-05-09 기준 종가)

| 종목       | 종가      | 일변동  |
|-----------|-----------|---------|
| S&P 500   | 5,231.42  | +0.41%  |
| NASDAQ    | 16,418.10 | -0.12%  |
| KOSPI     | 2,712.55  | +0.83%  |
| USD/KRW   | 1,369.20  | -0.18%  |
| USD/JPY   | 154.82    | +0.34%  |
| 달러인덱스 | 104.55    | -0.10%  |
| VIX       | 13.21     | -2.07%  |
| 미 10Y    | 4.47%     | +0.03   |
```

Missing data renders the cell as `—` and a `(결측)` suffix on the row; the rest of the table is unaffected.

### `run_cycle.py` change

After `build_input_file(slot, category)`, prepend the snapshot block to the file:

```python
input_path = workspace / "input" / category / f"{slot}-input.md"
existing = input_path.read_text(encoding="utf-8") if input_path.exists() else ""
snapshot = snapshot.build_snapshot_block(date.today())
input_path.write_text(snapshot + "\n\n" + existing, encoding="utf-8")
```

The snapshot block must appear before the article list so Claude reads it first.

### `cycle.md` prompt change

Add the following paragraph after the existing "## 카테고리 컨텍스트" section, before "## Task":

> ## 시장 스냅샷
>
> 입력파일 상단에 `## 시장 스냅샷` 블록이 있다. 보고서의 "새 소식" 첫 단락 또는 lead-in 한 줄에 그 날 시장 상태를 짧게 요약·반영하라. Indicator 엔티티를 라벨링할 때 `canonical_name`은 반드시 다음 별칭 중 하나로 쓴다: `SPX`, `NDX`, `KOSPI`, `USDKRW`, `USDJPY`, `DXY`, `VIX`, `TNX`. 그래프와 가격 DB는 이 별칭으로 연결된다.

### MCP tool `market_query`

Added to `newsparser/mcp_server.py`:

```python
@mcp.tool()
def market_query(
    instruments: list[str],
    start: str,             # "YYYY-MM-DD" — absolute date, no relative expressions
    end: str,               # "YYYY-MM-DD" — inclusive
    freq: str = "1d",       # "1d" or "1h"
) -> str:
    """Return OHLCV rows for the given macro instruments as a compact markdown table.

    Valid instruments: SPX, NDX, KOSPI, USDKRW, USDJPY, DXY, VIX, TNX.
    Dates must be absolute (YYYY-MM-DD). The caller is expected to resolve
    relative expressions ("최근 30일") against the current date before invoking.
    """
```

Implementation reads from `store` (daily) or the intraday table; if the requested daily/intraday range has gaps within a tracked instrument's history, the gaps are returned as missing rows in the table — no auto-fetch on read. Returning markdown rather than JSON keeps the response compact in Claude's context and matches the rest of the MCP surface.

Tracker prompt guidance (one short paragraph added to `newsparser/bot/tracker.py`):

> 시계열·가격·환율 질문이 들어오면 `market_query` 도구를 쓴다. `start`/`end`는 항상 절대 날짜(YYYY-MM-DD). 사용자가 "최근 한 달" 같이 말하면 오늘 날짜 기준으로 직접 변환해서 넣는다. 유효 instruments: SPX, NDX, KOSPI, USDKRW, USDJPY, DXY, VIX, TNX.

---

## Section 5 — Causal Annotation (Stage 2)

### `newsparser/market/annotate.py`

```python
def maybe_annotate_impacts(
    relations: list[RelationUpdate],
    slot: str,                # "YYYY-MM-DD-HH"
    category: str,            # "tech" | "markets"
) -> int:
    """For each IMPACTS or INFLUENCES relation whose object is a tracked alias,
    fetch ±60 minutes of hourly bars around the slot time, compute the price delta,
    and write annotation properties onto the Neo4j relation.

    Returns the number of relations actually annotated. Never raises."""
```

Algorithm:

1. Parse `slot` (`YYYY-MM-DD-HH`, KST) into a UTC datetime `slot_utc`.
2. For each relation:
   a. If `relation.predicate not in {"IMPACTS", "INFLUENCES"}`: skip.
   b. If `relation.obj not in TICKERS`: skip.
   c. `bars = fetcher.fetch_intraday_hourly(relation.obj, start=slot_utc - timedelta(minutes=60), end=slot_utc + timedelta(minutes=60))`.
   d. `store.upsert_intraday(bars)` so the data is persisted for future queries.
   e. Split bars into `before = [b for b in bars if b.ts < slot_utc]` and `after = [b for b in bars if b.ts >= slot_utc]`. If either is empty: skip silently.
   f. `delta_pct = (after[0].close - before[-1].close) / before[-1].close * 100`.
   g. Cypher: `MATCH (a {canonical_name:$s})-[r:{predicate}]->(b {canonical_name:$o}) SET r.impact_price_delta_pct=$d, r.impact_price_delta_window='[-60m, +60m]', r.impact_target_instrument=$o, r.annotated_at=datetime()`.
3. Each relation wrapped in `try/except`; one failure does not block the rest. All errors logged at WARNING level.

The slot time is used as the temporal anchor for "the moment of impact". This is an approximation — a news event may have appeared anywhere in the preceding 6 hours — but it is sufficient for first-pass causal labeling and refinable later (e.g., using each article's `published` timestamp).

### `apply_graph.py` change

After `apply_graph_updates(entities, relations, cycle_id, category)`:

```python
try:
    annotated = annotate.maybe_annotate_impacts(relations, slot, category)
    if annotated:
        print(f"Annotated {annotated} relations with price reactions.")
except Exception as exc:
    logger.warning("Annotation pass failed: %s", exc)
```

Annotation is strictly additive — its failure must never poison the cycle's primary graph update.

---

## Section 6 — Operations and Error Handling

### Workspace and locks

```
workspace/market.db
workspace/logs/market.log
workspace/state/locks/market_daily
```

The `flock` pattern matches existing cycle/weekly/reflect cron lines: `flock -n` silently skips if a prior run is still alive.

### Failure modes

| Where                          | What can fail                          | What happens                                      |
|--------------------------------|----------------------------------------|---------------------------------------------------|
| `fetcher.fetch_daily`          | yfinance throttled / down              | 3 retries with backoff, then empty result         |
| `fetch_market_daily.py`        | one alias errors                       | logged, others continue, script exits 0           |
| `snapshot.build_snapshot_block`| no rows for an instrument              | row shows `— (결측)`, table still rendered        |
| `run_cycle.py` snapshot prepend| `market.db` missing entirely           | empty snapshot block prepended, warning logged    |
| `annotate.maybe_annotate_impacts` | any per-relation failure            | warning, continue; cycle's apply_graph unaffected |
| `market_query` MCP tool        | no data in range                       | returns text "no data for {alias} in {start}..{end}" — not an exception |

The principle: the price layer is additive context. Its absence degrades cycle quality but never breaks the cycle.

### Bootstrap

One-shot first run:

```
.venv/bin/python -m newsparser.scripts.fetch_market_daily
```

The script auto-detects empty DB and back-fills five years. Subsequent invocations are incremental.

---

## Section 7 — Testing

| Component                       | Test approach |
|---------------------------------|---------------|
| `fetcher.py`                    | yfinance monkeypatched to fixed returns; verify retry counts, backoff, empty-data handling |
| `store.py`                      | tmp_path SQLite; upsert idempotence, PK conflict, get range, WAL pragma applied |
| `snapshot.py`                   | seed store with sample rows; assert markdown output matches expected; missing-row case |
| `annotate.py`                   | fetcher mocked; assert Cypher invocations with correct args; skip cases (non-tracked alias, non-IMPACTS predicate, empty bar windows) |
| `fetch_market_daily.py`         | module-level mocks; smoke `main()` with empty DB (backfill) and seeded DB (incremental) |
| `market_query` MCP tool         | call the `@mcp.tool()` function directly; normal range, partial gap, unknown alias |
| `cycle.md`                      | no automated test (prompt review only) |
| `run_cycle.py` integration      | one new case in `tests/test_run_cycle_script.py`: snapshot block is prepended to input file before `run_claude` is called |

Live yfinance is **not** exercised in CI. A separate `scripts/smoke_market.py` (not under `tests/`) makes one real call per instrument and is run manually after fetcher changes.

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
tests/test_market_fetcher.py
tests/test_market_store.py
tests/test_market_snapshot.py
tests/test_market_annotate.py
tests/test_fetch_market_daily_script.py
tests/test_market_query_mcp.py
```

## Files to Modify

```
newsparser/mcp_server.py             add @mcp.tool() market_query
newsparser/scripts/apply_graph.py    call maybe_annotate_impacts after apply_graph_updates
newsparser/scripts/run_cycle.py      prepend snapshot block to cycle input file
.claude/commands/cycle.md            add "## 시장 스냅샷" instruction + canonical_name convention
newsparser/bot/tracker.py            add market_query usage paragraph to tracker prompt
/etc/cron.d/newsparser               add 07:30 KST daily fetch line
tests/test_run_cycle_script.py       add snapshot-prepended assertion
```

## Files to Delete

None.

---

## Out of Scope

- Individual equities (Apple, Samsung, etc.). The dictionary is fixed at 8 macro instruments.
- Real-time / streaming data. All fetches are end-of-day batch or on-demand intraday around a known event time.
- Chart rendering. Output is always tabular text — Claude reads it; the user sees it as part of digests or tracker replies.
- Auto-fetch on read in `market_query`. Reads only return what `fetch_market_daily.py` or `annotate.py` already wrote.
- Migration tooling. The first `fetch_market_daily.py` run on an empty DB is the migration.
