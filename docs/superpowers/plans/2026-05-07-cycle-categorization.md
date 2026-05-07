# Cycle Categorization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split each `/cycle` run into two parallel cognitive tracks — `tech` and `markets` — with category-aware storage, cycle reports, graph properties, interest profiles, and MCP tools.

**Architecture:** Articles get a `category` column tagged at ingestion (deterministic source-map) or lazily at cycle time (haiku). `_cycle_job` iterates two categories per slot, building per-category input files, prompts, reports, telegram messages, and graph updates. MCP tools accept an optional `category` param; `tracker` adds a haiku-derived hint to its prompt.

**Tech Stack:** Python 3.11, SQLite (built-in), Neo4j (cypher), Claude CLI (`claude -p`) headless, pytest, FastMCP.

**Spec:** `docs/superpowers/specs/2026-05-07-cycle-categorization-design.md`

---

## File Structure

**New files:**
- `newsparser/classifier.py` — `CATEGORIES`, `classify_article()`, `classify_query()` (haiku-backed)
- `tests/test_classifier.py`

**Modified files:**
- `newsparser/store/sqlite.py` — `init_db` ALTER, `insert_article(category=)`, `get_unprocessed(category=)`, `get_unclassified()`, `update_category()`
- `newsparser/collector/sources.py` — `Source.category`, header-based parser
- `newsparser/collector/poller.py` — pass `source.category` to `insert_article`
- `newsparser/scheduler/workspace.py` — per-category dirs + `interests_tech.md` / `interests_markets.md` templates
- `newsparser/scheduler/cycle.py` — `classify_pending()`, per-category loop, paths, `[TECH]` / `[MARKETS]` digest prefix
- `newsparser/claude/input_builder.py` — `build_input_file(slot, category)`
- `newsparser/graph/writer.py` — `apply_graph_updates(..., category=)`, sets `e.category` / `r.category`
- `newsparser/mcp_server.py` — `category` param on tools, new `classify_query` tool, per-category interests
- `newsparser/bot/tracker.py` — call `classify_query`, inject hint
- `prompts/cycle.md` — note that Python prepends a category block (no placeholder syntax — Python concatenates)
- `sources.md` — add `Category` column + 4 new tech sources
- `README.md` — document `workspace/newsparser.db` path and `DB_PATH` env var
- Tests: `test_store.py`, `test_sources.py`, `test_input_builder.py`, `test_graph_writer.py`, `test_cycle.py`, `test_mcp_server.py`, `test_tracker.py`

---

## Pre-flight

- [ ] **Step 0a: Confirm pytest is available**

```bash
.venv/bin/python -c "import pytest; print(pytest.__version__)"
```

If it errors with `ModuleNotFoundError`:

```bash
.venv/bin/pip install -e ".[dev]"
```

Re-run the version check. Expected: a version string like `8.x.x`.

- [ ] **Step 0b: Confirm clean git state**

```bash
git status
```

Expected: `nothing to commit, working tree clean` (or only the plan file untracked).

---

## Task 1: SQLite schema migration and category-aware access

**Files:**
- Modify: `newsparser/store/sqlite.py`
- Test: `tests/test_store.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_store.py`:

```python
def test_init_db_adds_category_column_idempotent():
    # init_db is idempotent — running twice should not raise
    init_db()
    init_db()
    import sqlite3, os
    with sqlite3.connect(os.environ["DB_PATH"]) as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(pending_articles)").fetchall()]
    assert "category" in cols


def test_insert_article_with_category():
    insert_article("g1", "Reuters", "Title", "https://x.com", None, "body", category="markets")
    rows = get_unprocessed()
    assert rows[0]["category"] == "markets"


def test_insert_article_default_category_is_null():
    insert_article("g1", "HN", "Title", "https://x.com", None, "body")
    rows = get_unprocessed()
    assert rows[0]["category"] is None


def test_get_unprocessed_filters_by_category():
    insert_article("g1", "S1", "T1", "https://x.com/1", None, "b", category="tech")
    insert_article("g2", "S2", "T2", "https://x.com/2", None, "b", category="markets")
    insert_article("g3", "S3", "T3", "https://x.com/3", None, "b")  # NULL
    tech = get_unprocessed(category="tech")
    assert len(tech) == 1
    assert tech[0]["guid"] == "g1"
    markets = get_unprocessed(category="markets")
    assert len(markets) == 1
    assert markets[0]["guid"] == "g2"


def test_get_unprocessed_no_filter_returns_all():
    insert_article("g1", "S1", "T1", "https://x.com/1", None, "b", category="tech")
    insert_article("g2", "S2", "T2", "https://x.com/2", None, "b")
    assert len(get_unprocessed()) == 2


def test_get_unclassified_returns_only_null_category():
    insert_article("g1", "S1", "T1", "https://x.com/1", None, "b", category="tech")
    insert_article("g2", "S2", "T2", "https://x.com/2", None, "b")  # NULL
    rows = get_unclassified()
    assert len(rows) == 1
    assert rows[0]["guid"] == "g2"


def test_update_category():
    insert_article("g1", "S1", "T1", "https://x.com/1", None, "b")
    update_category("g1", "tech")
    rows = get_unprocessed()
    assert rows[0]["category"] == "tech"
```

Add the imports to the top of the file:

```python
from newsparser.store.sqlite import (
    init_db, is_seen, mark_seen, insert_article, get_unprocessed,
    mark_processed, mark_alerted, get_unclassified, update_category,
)
```

- [ ] **Step 2: Run tests to verify failure**

```bash
.venv/bin/pytest tests/test_store.py -v
```

Expected: import errors / `TypeError` for unknown `category` kwarg / `AttributeError` for missing functions.

- [ ] **Step 3: Implement schema migration and new functions**

Replace `init_db` and `insert_article` and append new functions in `newsparser/store/sqlite.py`:

```python
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
        # Idempotent column addition. SQLite raises OperationalError if column exists.
        try:
            conn.execute("ALTER TABLE pending_articles ADD COLUMN category TEXT")
            conn.commit()
        except sqlite3.OperationalError:
            pass
    finally:
        conn.close()


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
             datetime.utcnow().isoformat(), category),
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
```

- [ ] **Step 4: Run tests to verify pass**

```bash
.venv/bin/pytest tests/test_store.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add newsparser/store/sqlite.py tests/test_store.py
git commit -m "feat(store): add category column and category-aware queries"
```

---

## Task 2: Header-based source loader with Category field

**Files:**
- Modify: `newsparser/collector/sources.py`
- Test: `tests/test_sources.py`

- [ ] **Step 1: Write failing tests**

Replace `tests/test_sources.py`:

```python
import textwrap
from pathlib import Path
from newsparser.collector.sources import load_sources, Source


def test_load_sources_parses_table(tmp_path):
    md = textwrap.dedent("""\
        # Sources

        | Name | RSS URL | Tier | Category | Paywall |
        |------|---------|------|----------|---------|
        | Reuters | https://feeds.reuters.com/reuters/topNews | international | markets | no |
        | Financial Times | https://www.ft.com/rss/home | international | markets | yes |
    """)
    p = tmp_path / "sources.md"
    p.write_text(md)
    sources = load_sources(str(p))
    assert len(sources) == 2
    assert sources[0].name == "Reuters"
    assert sources[0].rss_url == "https://feeds.reuters.com/reuters/topNews"
    assert sources[0].tier == "international"
    assert sources[0].category == "markets"
    assert sources[0].paywall is False
    assert sources[1].paywall is True


def test_load_sources_blank_category_is_none(tmp_path):
    md = textwrap.dedent("""\
        | Name | RSS URL | Tier | Category | Paywall |
        |------|---------|------|----------|---------|
        | Hacker News | https://news.ycombinator.com/rss | tech |  | no |
    """)
    p = tmp_path / "sources.md"
    p.write_text(md)
    sources = load_sources(str(p))
    assert len(sources) == 1
    assert sources[0].category is None


def test_load_sources_skips_separator_rows(tmp_path):
    md = textwrap.dedent("""\
        | Name | RSS URL | Tier | Category | Paywall |
        |------|---------|------|----------|---------|
        | Reuters | https://feeds.reuters.com/reuters/topNews | international | markets | no |
    """)
    p = tmp_path / "sources.md"
    p.write_text(md)
    sources = load_sources(str(p))
    assert len(sources) == 1


def test_load_sources_handles_legacy_layout_without_category(tmp_path):
    """If the Category column is absent, sources still load with category=None."""
    md = textwrap.dedent("""\
        | Name | RSS URL | Tier | Paywall |
        |------|---------|------|---------|
        | Reuters | https://feeds.reuters.com/reuters/topNews | international | no |
    """)
    p = tmp_path / "sources.md"
    p.write_text(md)
    sources = load_sources(str(p))
    assert len(sources) == 1
    assert sources[0].category is None
```

- [ ] **Step 2: Run tests to verify failure**

```bash
.venv/bin/pytest tests/test_sources.py -v
```

Expected: `AttributeError` for `Source.category`.

- [ ] **Step 3: Implement header-based parser**

Replace `newsparser/collector/sources.py`:

```python
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class Source:
    name: str
    rss_url: str
    tier: str
    paywall: bool = False
    category: str | None = None


def _split_row(line: str) -> list[str]:
    """Split a markdown table row into stripped cells."""
    # Drop the leading and trailing pipe before splitting so empty cells survive.
    inner = line.strip()
    if inner.startswith("|"):
        inner = inner[1:]
    if inner.endswith("|"):
        inner = inner[:-1]
    return [cell.strip() for cell in inner.split("|")]


def _is_separator(cells: list[str]) -> bool:
    return all(set(c) <= set("-:") and c for c in cells)


def load_sources(path: str = "sources.md") -> list[Source]:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        raise FileNotFoundError(f"sources file not found: {path!r}") from None

    header: list[str] | None = None
    sources: list[Source] = []
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = _split_row(line)
        if header is None:
            header = [h.lower() for h in cells]
            continue
        if _is_separator(cells):
            continue
        if len(cells) < len(header):
            cells += [""] * (len(header) - len(cells))

        row = dict(zip(header, cells))
        rss_url = row.get("rss url", "")
        if not rss_url.startswith("http"):
            logger.warning("Skipping source row with non-HTTP URL: %r", rss_url)
            continue

        category = row.get("category") or None  # blank cell -> None
        sources.append(Source(
            name=row.get("name", ""),
            rss_url=rss_url,
            tier=row.get("tier", ""),
            paywall=row.get("paywall", "").lower() == "yes",
            category=category,
        ))
    return sources
```

- [ ] **Step 4: Run tests to verify pass**

```bash
.venv/bin/pytest tests/test_sources.py -v
```

Expected: all four tests pass.

- [ ] **Step 5: Commit**

```bash
git add newsparser/collector/sources.py tests/test_sources.py
git commit -m "feat(sources): header-based parser with Category column"
```

---

## Task 3: Update `sources.md` with Category column and 4 new tech feeds

**Files:**
- Modify: `sources.md`

- [ ] **Step 1: Rewrite sources.md**

Replace the entire contents of `sources.md`:

```markdown
# Sources

| Name | RSS URL | Tier | Category | Paywall |
|------|---------|------|----------|---------|
| 매일경제 | https://www.mk.co.kr/rss/30000001/ | domestic | markets | no |
| 한국경제 | https://www.hankyung.com/feed/all-news | domestic | markets | no |
| 연합인포맥스 | https://news.einfomax.co.kr/rss/allnews.xml | domestic | markets | no |
| 중앙일보 | https://rss.joins.com/joins_news_list.xml | domestic | markets | no |
| 한겨레 | https://www.hani.co.kr/rss/ | domestic | markets | no |
| AP | https://feeds.apnews.com/rss/apf-business | international | markets | no |
| Financial Times | https://www.ft.com/rss/home | international | markets | yes |
| Federal Reserve | https://www.federalreserve.gov/feeds/press_all.xml | international | markets | no |
| Bloomberg Markets | https://feeds.bloomberg.com/markets/news.rss | international | markets | yes |
| OpenAI Blog | https://openai.com/blog/rss/ | international | tech | no |
| Anthropic News | https://www.anthropic.com/news/rss.xml | international | tech | no |
| Google DeepMind Blog | https://deepmind.google/blog/rss.xml | international | tech | no |
| TechCrunch AI | https://techcrunch.com/category/artificial-intelligence/feed/ | international | tech | no |
| VentureBeat AI | https://venturebeat.com/category/ai/feed | tech | tech | no |
| Hacker News | https://news.ycombinator.com/rss | tech |  | no |
| Ars Technica | https://feeds.arstechnica.com/arstechnica/index | tech |  | no |
| AP Technology | https://feeds.apnews.com/rss/apf-technology | tech |  | no |
| MIT Technology Review | https://feeds.technologyreview.com/technologyreview/ | tech |  | no |
| Bloomberg Technology | https://feeds.bloomberg.com/technology/news.rss | tech |  | yes |
```

- [ ] **Step 2: Verify load_sources reads it cleanly**

```bash
.venv/bin/python -c "
from newsparser.collector.sources import load_sources
srcs = load_sources('sources.md')
for s in srcs:
    print(f'{s.name:30} category={s.category!r:12} tier={s.tier} paywall={s.paywall}')
print(f'total: {len(srcs)}')
"
```

Expected output: 19 lines + "total: 19", with `category` set to `'markets'`, `'tech'`, or `None` matching the table.

- [ ] **Step 3: Commit**

```bash
git add sources.md
git commit -m "feat(sources): add Category column and 4 dedicated AI feeds"
```

---

## Task 4: Collector poller passes category to `insert_article`

**Files:**
- Modify: `newsparser/collector/poller.py`
- Test: `tests/test_scraper.py` is the closest existing — but the cleanest path is a new minimal test for poll_source's category passthrough. We'll instead test by patching `insert_article` and asserting the kwarg.
- Test: append to `tests/test_sources.py` (since it concerns the source→insert flow).

- [ ] **Step 1: Write failing test**

Append to `tests/test_sources.py`:

```python
from unittest.mock import patch, MagicMock
from newsparser.collector.poller import poll_source
from newsparser.collector.sources import Source


def test_poll_source_passes_category_to_insert(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    from newsparser.store.sqlite import init_db
    init_db()

    fake_entry = MagicMock()
    fake_entry.id = "guid-x"
    fake_entry.link = "https://example.com/x"
    fake_entry.title = "Hello"
    fake_entry.published = "2026-05-07T00:00:00"
    fake_entry.summary = "summary"

    fake_feed = MagicMock()
    fake_feed.entries = [fake_entry]

    src = Source(name="OpenAI Blog", rss_url="https://openai.com/rss",
                 tier="international", paywall=False, category="tech")

    with patch("newsparser.collector.poller.feedparser.parse", return_value=fake_feed), \
         patch("newsparser.collector.poller.fetch_body", return_value="body"), \
         patch("newsparser.collector.poller.insert_article") as mock_insert:
        poll_source(src)

    mock_insert.assert_called_once()
    _, kwargs = mock_insert.call_args
    # poll_source passes positional args; category is the last kwarg
    args = mock_insert.call_args[0]
    assert "category" in mock_insert.call_args.kwargs or len(args) >= 7
    # Accept either invocation form, but the value must be 'tech'
    if "category" in mock_insert.call_args.kwargs:
        assert mock_insert.call_args.kwargs["category"] == "tech"
    else:
        # If called positionally, category is the 7th positional arg (after body)
        assert args[6] == "tech"
```

- [ ] **Step 2: Run test to verify failure**

```bash
.venv/bin/pytest tests/test_sources.py::test_poll_source_passes_category_to_insert -v
```

Expected: assertion failure (category not passed) or older signature mismatch.

- [ ] **Step 3: Update poller**

Modify `newsparser/collector/poller.py`'s `poll_source` — replace the `insert_article(...)` call:

```python
        insert_article(
            guid, source.name, title, url, published, body,
            category=source.category,
        )
```

Full updated `poll_source` for clarity (rest of file unchanged):

```python
def poll_source(source: Source) -> list[dict]:
    """Fetch RSS feed and store new articles. Returns list of new article dicts."""
    try:
        feed = feedparser.parse(source.rss_url)
    except Exception as exc:
        logger.error("RSS fetch failed for %s: %s", source.name, exc)
        return []

    new_articles = []
    for entry in feed.entries:
        guid = getattr(entry, "id", None) or getattr(entry, "link", None)
        if not guid or is_seen(guid):
            continue

        title = getattr(entry, "title", "")
        url = getattr(entry, "link", "")
        published = getattr(entry, "published", datetime.utcnow().isoformat())
        summary = getattr(entry, "summary", "")

        if source.paywall:
            body = summary
        else:
            body = fetch_body(url) or summary

        insert_article(
            guid, source.name, title, url, published, body,
            category=source.category,
        )
        mark_seen(guid)
        new_articles.append({"guid": guid, "source": source.name, "title": title, "fetched_at": datetime.utcnow().isoformat()})
        logger.info("New article: [%s] %s", source.name, title)

    return new_articles
```

- [ ] **Step 4: Run test to verify pass**

```bash
.venv/bin/pytest tests/test_sources.py -v
```

Expected: the new test plus all existing tests pass.

- [ ] **Step 5: Commit**

```bash
git add newsparser/collector/poller.py tests/test_sources.py
git commit -m "feat(collector): pass source category through to insert_article"
```

---

## Task 5: Classifier module (`classify_article`, `classify_query`)

**Files:**
- Create: `newsparser/classifier.py`
- Create: `tests/test_classifier.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_classifier.py`:

```python
from unittest.mock import patch
import pytest

from newsparser.classifier import (
    classify_article, classify_query, CATEGORIES, _normalize_article_response, _normalize_query_response,
)


def test_categories_constant():
    assert CATEGORIES == ("tech", "markets")


def test_normalize_article_response_accepts_exact():
    assert _normalize_article_response("tech") == "tech"
    assert _normalize_article_response("markets") == "markets"


def test_normalize_article_response_strips_whitespace_and_punctuation():
    assert _normalize_article_response(" tech\n") == "tech"
    assert _normalize_article_response("tech.") == "tech"


def test_normalize_article_response_falls_back_to_markets_on_garbage():
    assert _normalize_article_response("maybe both?") == "markets"
    assert _normalize_article_response("") == "markets"


def test_normalize_query_response_accepts_three_values():
    assert _normalize_query_response("tech") == "tech"
    assert _normalize_query_response("markets") == "markets"
    assert _normalize_query_response("both") == "both"


def test_normalize_query_response_falls_back_to_both_on_garbage():
    assert _normalize_query_response("???") == "both"
    assert _normalize_query_response("") == "both"


def test_classify_article_calls_haiku_and_returns_tech():
    with patch("newsparser.classifier.run_claude", return_value="tech") as mock:
        result = classify_article("OpenAI launches GPT-X", "Body about model release")
    assert result == "tech"
    args, kwargs = mock.call_args
    assert "claude-haiku" in kwargs.get("model", "")
    assert kwargs.get("timeout") == 15
    prompt = args[0]
    assert "OpenAI launches GPT-X" in prompt
    assert "Body about model release" in prompt


def test_classify_article_falls_back_to_markets_on_subprocess_error():
    with patch("newsparser.classifier.run_claude", side_effect=RuntimeError("boom")):
        assert classify_article("x", "y") == "markets"


def test_classify_article_truncates_long_body():
    long_body = "x" * 5000
    captured = {}
    def fake(prompt, **kw):
        captured["prompt"] = prompt
        return "markets"
    with patch("newsparser.classifier.run_claude", side_effect=fake):
        classify_article("title", long_body)
    # Body excerpt should be capped — entire 5000-char body MUST NOT be in prompt
    assert "x" * 5000 not in captured["prompt"]


def test_classify_query_returns_both_for_cross_category():
    with patch("newsparser.classifier.run_claude", return_value="both"):
        assert classify_query("AI 발표가 NVDA 주가에 미친 영향") == "both"


def test_classify_query_falls_back_to_both_on_error():
    with patch("newsparser.classifier.run_claude", side_effect=RuntimeError("boom")):
        assert classify_query("hello") == "both"
```

- [ ] **Step 2: Run tests to verify failure**

```bash
.venv/bin/pytest tests/test_classifier.py -v
```

Expected: `ImportError` — module doesn't exist yet.

- [ ] **Step 3: Implement the classifier module**

Create `newsparser/classifier.py`:

```python
"""Haiku-backed classification for articles and tracker queries."""
import logging

from newsparser.claude.runner import run_claude, ClaudeError

logger = logging.getLogger(__name__)

CATEGORIES: tuple[str, str] = ("tech", "markets")

# Resolve at implementation time. We pin to the same haiku snapshot tracker.py
# uses so classifier behavior matches the rest of the system.
HAIKU_MODEL = "claude-haiku-4-5-20251001"

_BODY_EXCERPT_CHARS = 500

_ARTICLE_PROMPT = (
    "다음 기사가 어느 카테고리에 가까운지 한 단어로 답해.\n"
    "- `tech`: AI 활용·신규 AI 정보·일반 컴퓨터 기술\n"
    "- `markets`: 시장·매크로·정책·지정학·기타 산업. 애매하면 무조건 `markets`.\n\n"
    "응답은 정확히 'tech' 또는 'markets' 한 단어. 다른 설명·기호·문장부호 금지.\n\n"
    "제목: {title}\n"
    "본문 (앞 {n}자): {body}"
)

_QUERY_PROMPT = (
    "다음 사용자 쿼리가 어느 카테고리에 가까운지 한 단어로 답해.\n"
    "- `tech`: AI 활용·신규 AI 정보·일반 컴퓨터 기술 관련 질문\n"
    "- `markets`: 시장·매크로·정책·기타 산업 관련 질문\n"
    "- `both`: 두 카테고리를 모두 가로지르는 질문 (예: AI가 시장에 미치는 영향)\n\n"
    "응답은 정확히 'tech', 'markets', 또는 'both' 한 단어.\n\n"
    "쿼리: {query}"
)


def _normalize_article_response(raw: str) -> str:
    cleaned = (raw or "").strip().strip(".`'\" \t\n").lower()
    if cleaned == "tech":
        return "tech"
    if cleaned == "markets":
        return "markets"
    return "markets"  # fallback per the global tie-breaker rule


def _normalize_query_response(raw: str) -> str:
    cleaned = (raw or "").strip().strip(".`'\" \t\n").lower()
    if cleaned in ("tech", "markets", "both"):
        return cleaned
    return "both"


def classify_article(title: str, body: str | None) -> str:
    """Return 'tech' or 'markets' for a single article. Falls back to 'markets' on errors."""
    body_excerpt = (body or "")[:_BODY_EXCERPT_CHARS]
    prompt = _ARTICLE_PROMPT.format(title=title, n=_BODY_EXCERPT_CHARS, body=body_excerpt)
    try:
        raw = run_claude(prompt, timeout=15, model=HAIKU_MODEL)
    except (ClaudeError, RuntimeError, OSError) as exc:
        logger.warning("classify_article failed (%s); defaulting to 'markets'", exc)
        return "markets"
    return _normalize_article_response(raw)


def classify_query(query: str) -> str:
    """Return 'tech' / 'markets' / 'both' for a tracker query. Falls back to 'both' on errors."""
    prompt = _QUERY_PROMPT.format(query=query)
    try:
        raw = run_claude(prompt, timeout=15, model=HAIKU_MODEL)
    except (ClaudeError, RuntimeError, OSError) as exc:
        logger.warning("classify_query failed (%s); defaulting to 'both'", exc)
        return "both"
    return _normalize_query_response(raw)
```

- [ ] **Step 4: Run tests to verify pass**

```bash
.venv/bin/pytest tests/test_classifier.py -v
```

Expected: all 11 tests pass.

- [ ] **Step 5: Commit**

```bash
git add newsparser/classifier.py tests/test_classifier.py
git commit -m "feat(classifier): haiku classify_article and classify_query"
```

---

## Task 6: Workspace setup — per-category dirs and interest templates

**Files:**
- Modify: `newsparser/scheduler/workspace.py`
- Test: append to `tests/test_input_builder.py` (already exercises ensure_workspace via fixture indirectly) — better to add a direct test in a new `tests/test_workspace.py`.

- [ ] **Step 1: Write failing test**

Create `tests/test_workspace.py`:

```python
import os
from pathlib import Path
import pytest

from newsparser.scheduler.workspace import ensure_workspace


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "workspace"))


def test_ensure_workspace_creates_per_category_dirs():
    root = ensure_workspace()
    assert (root / "input" / "tech").is_dir()
    assert (root / "input" / "markets").is_dir()
    assert (root / "cycles" / "tech").is_dir()
    assert (root / "cycles" / "markets").is_dir()


def test_ensure_workspace_creates_interest_templates():
    root = ensure_workspace()
    assert (root / "me" / "interests_tech.md").exists()
    assert (root / "me" / "interests_markets.md").exists()
    assert (root / "me" / "manifesto.md").exists()


def test_ensure_workspace_does_not_overwrite_existing_interests(tmp_path):
    root = ensure_workspace()
    custom = "# Custom tech profile\n\n## Themes\n"
    (root / "me" / "interests_tech.md").write_text(custom)
    ensure_workspace()  # second call must not overwrite
    assert (root / "me" / "interests_tech.md").read_text() == custom


def test_ensure_workspace_idempotent_on_repeated_calls():
    ensure_workspace()
    ensure_workspace()  # must not raise
```

- [ ] **Step 2: Run test to verify failure**

```bash
.venv/bin/pytest tests/test_workspace.py -v
```

Expected: failure on per-category dirs not existing.

- [ ] **Step 3: Update `ensure_workspace`**

Replace `newsparser/scheduler/workspace.py`:

```python
import os
from pathlib import Path

CATEGORIES = ("tech", "markets")


def ensure_workspace() -> Path:
    """Create all required workspace directories and template files. Returns workspace root."""
    root = Path(os.environ.get("WORKSPACE_DIR", "workspace"))

    for subdir in ["input", "cycles", "me", "state", "logs", "sessions", "briefs"]:
        (root / subdir).mkdir(parents=True, exist_ok=True)

    for category in CATEGORIES:
        (root / "input" / category).mkdir(parents=True, exist_ok=True)
        (root / "cycles" / category).mkdir(parents=True, exist_ok=True)

    # Per-category interest templates. Created only if missing.
    for category in CATEGORIES:
        path = root / "me" / f"interests_{category}.md"
        if not path.exists():
            path.write_text(_interests_template(category), encoding="utf-8")

    manifesto = root / "me" / "manifesto.md"
    if not manifesto.exists():
        manifesto.write_text("", encoding="utf-8")

    return root


def _interests_template(category: str) -> str:
    label = {"tech": "Tech", "markets": "Markets"}[category]
    return (
        f"# Interests Profile — {label}\n"
        f"Last updated: (manual)\n\n"
        f"## Themes\n\n"
        f"| Theme | interest_weight | familiarity_weight | Notes |\n"
        f"|---|---|---|---|\n\n"
        f"## User overrides\n"
    )
```

- [ ] **Step 4: Run test to verify pass**

```bash
.venv/bin/pytest tests/test_workspace.py -v
```

Expected: 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add newsparser/scheduler/workspace.py tests/test_workspace.py
git commit -m "feat(workspace): per-category dirs and interest templates"
```

---

## Task 7: Input builder per-category

**Files:**
- Modify: `newsparser/claude/input_builder.py`
- Test: `tests/test_input_builder.py`

- [ ] **Step 1: Update tests for new signature**

Replace `tests/test_input_builder.py`:

```python
import os
import pytest
from newsparser.store.sqlite import init_db, insert_article
from newsparser.claude.input_builder import build_input_file


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "workspace"))
    init_db()


def test_build_input_file_writes_under_category_subfolder(tmp_path):
    insert_article("g1", "TechCrunch AI", "Model release", "https://x.com/1", None, "body", category="tech")
    path = build_input_file("2026-05-05-00", "tech")
    assert path.parent.name == "tech"
    assert path.name == "2026-05-05-00-input.md"
    assert path.exists()


def test_build_input_file_only_includes_matching_category(tmp_path):
    insert_article("g1", "TechCrunch AI", "Model release", "https://x.com/1", None, "tech body", category="tech")
    insert_article("g2", "FT", "Fed cuts", "https://x.com/2", None, "markets body", category="markets")
    tech_path = build_input_file("2026-05-05-00", "tech")
    markets_path = build_input_file("2026-05-05-00", "markets")
    assert "tech body" in tech_path.read_text()
    assert "markets body" not in tech_path.read_text()
    assert "markets body" in markets_path.read_text()
    assert "tech body" not in markets_path.read_text()


def test_build_input_file_marks_category_in_header(tmp_path):
    insert_article("g1", "TechCrunch AI", "T", "https://x.com/1", None, "b", category="tech")
    path = build_input_file("2026-05-05-00", "tech")
    content = path.read_text()
    assert "# Input 2026-05-05-00 KST [tech]" in content


def test_build_input_file_zero_articles_for_category(tmp_path):
    path = build_input_file("2026-05-05-00", "tech")
    assert "0 total" in path.read_text()
```

- [ ] **Step 2: Run tests to verify failure**

```bash
.venv/bin/pytest tests/test_input_builder.py -v
```

Expected: signature errors / missing category folder.

- [ ] **Step 3: Update `build_input_file`**

Replace `newsparser/claude/input_builder.py`:

```python
import os
from pathlib import Path

from newsparser.store.sqlite import get_unprocessed


def build_input_file(slot: str, category: str) -> Path:
    """Read unprocessed articles for `category` and write input.md for Claude.
    Returns the file path."""
    workspace = Path(os.environ.get("WORKSPACE_DIR", "workspace"))
    articles = get_unprocessed(category=category)

    lines = [
        f"# Input {slot} KST [{category}]",
        f"## Collected Articles ({len(articles)} total)",
    ]
    for a in articles:
        body = (a["body"] or "").replace("\n", "\n  ")
        lines += [
            f"\n### [{a['source']}] {a['title']}",
            f"- URL: {a['url']}",
            f"- Published: {a['published'] or 'unknown'}",
            f"- Body:\n  {body}",
        ]

    path = workspace / "input" / category / f"{slot}-input.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
```

- [ ] **Step 4: Run tests to verify pass**

```bash
.venv/bin/pytest tests/test_input_builder.py -v
```

Expected: 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add newsparser/claude/input_builder.py tests/test_input_builder.py
git commit -m "feat(input): per-category input file with category in header"
```

---

## Task 8: Graph writer category property

**Files:**
- Modify: `newsparser/graph/writer.py`
- Test: `tests/test_graph_writer.py`

> **Note:** `test_graph_writer.py` requires a live Neo4j. If `NEO4J_PASSWORD` and a running Neo4j container are not available, those tests will fail with a connection error. The plan assumes the executor can run them; if not, mark this task to be verified manually after deploy.

- [ ] **Step 1: Add failing tests for category property**

Append to `tests/test_graph_writer.py`:

```python
def test_upsert_entity_sets_category():
    entity = EntityUpdate(op="NEW", label="Company", name="OpenAI", aliases=[])
    apply_graph_updates([entity], [], "tech-2026-05-07-12", category="tech")
    with get_driver().session() as s:
        row = s.run("MATCH (e:Company {canonical_name: 'OpenAI'}) RETURN e.category AS c").single()
    assert row["c"] == "tech"


def test_upsert_entity_does_not_overwrite_existing_category():
    entity = EntityUpdate(op="NEW", label="Company", name="OpenAI", aliases=[])
    apply_graph_updates([entity], [], "tech-2026-05-07-12", category="tech")
    apply_graph_updates([entity], [], "markets-2026-05-07-12", category="markets")
    with get_driver().session() as s:
        row = s.run("MATCH (e:Company {canonical_name: 'OpenAI'}) RETURN e.category AS c").single()
    # First-set wins (coalesce semantics)
    assert row["c"] == "tech"


def test_upsert_relation_sets_category():
    entities = [
        EntityUpdate(op="NEW", label="Company", name="OpenAI", aliases=[]),
        EntityUpdate(op="NEW", label="Company", name="Microsoft", aliases=[]),
    ]
    rel = RelationUpdate(op="NEW", subject="OpenAI", predicate="INFLUENCES",
                         obj="Microsoft", confidence=0.7, impact_score=0.6)
    apply_graph_updates(entities, [rel], "tech-2026-05-07-12", category="tech")
    with get_driver().session() as s:
        row = s.run(
            "MATCH ()-[r:INFLUENCES]->() RETURN r.category AS c"
        ).single()
    assert row["c"] == "tech"
```

- [ ] **Step 2: Run tests to verify failure**

```bash
.venv/bin/pytest tests/test_graph_writer.py -v
```

Expected: `TypeError` for unknown `category` kwarg, or assertion failures (property not present).

- [ ] **Step 3: Update graph writer**

Replace `newsparser/graph/writer.py`:

```python
from datetime import datetime

from newsparser.claude.output_parser import EntityUpdate, RelationUpdate
from newsparser.graph.neo4j_client import get_driver


def upsert_entity(entity: EntityUpdate, cycle_id: str, category: str | None = None) -> None:
    with get_driver().session() as session:
        session.run(
            f"MERGE (e:{entity.label} {{canonical_name: $name}}) "
            "ON CREATE SET e.first_seen = $now, e.mention_count = 1, "
            "  e.aliases = $aliases, e.category = $category "
            "ON MATCH SET e.mention_count = e.mention_count + 1, "
            "  e.category = coalesce(e.category, $category) "
            "SET e.last_seen = $now",
            name=entity.name,
            now=datetime.utcnow().isoformat(),
            aliases=entity.aliases,
            category=category,
        )


def upsert_relation(rel: RelationUpdate, cycle_id: str, category: str | None = None) -> None:
    with get_driver().session() as session:
        session.run(
            "MATCH (a {canonical_name: $subject}) "
            "MATCH (b {canonical_name: $obj}) "
            f"MERGE (a)-[r:{rel.predicate}]->(b) "
            "ON CREATE SET r.first_seen = $now, r.confidence = $conf, "
            "  r.impact_score = $impact, r.source_cycles = [$cycle_id], "
            "  r.predicate_text = $text, r.category = $category "
            "ON MATCH SET r.impact_score = 0.85 * r.impact_score + 0.15 * $impact, "
            "  r.source_cycles = r.source_cycles + [$cycle_id], "
            "  r.category = coalesce(r.category, $category) "
            "SET r.last_seen = $now",
            subject=rel.subject, obj=rel.obj,
            now=datetime.utcnow().isoformat(),
            conf=rel.confidence, impact=rel.impact_score,
            cycle_id=cycle_id, text=rel.predicate_text,
            category=category,
        )


def apply_graph_updates(
    entities: list[EntityUpdate],
    relations: list[RelationUpdate],
    cycle_id: str,
    category: str | None = None,
) -> None:
    for entity in entities:
        upsert_entity(entity, cycle_id, category)
    for relation in relations:
        upsert_relation(relation, cycle_id, category)
```

- [ ] **Step 4: Run tests to verify pass**

```bash
.venv/bin/pytest tests/test_graph_writer.py -v
```

Expected: existing tests pass + 3 new ones pass.

- [ ] **Step 5: Commit**

```bash
git add newsparser/graph/writer.py tests/test_graph_writer.py
git commit -m "feat(graph): category property on entities and relations"
```

---

## Task 9: Cycle scheduler — `classify_pending` and per-category dispatch

**Files:**
- Modify: `newsparser/scheduler/cycle.py`
- Modify: `prompts/cycle.md` (note in the markdown that Python prepends a category block — pure documentation update)
- Test: `tests/test_cycle.py`

- [ ] **Step 1: Update `prompts/cycle.md` to note the prepended category block**

At the very top of `prompts/cycle.md`, before the `## /cycle task` heading, add:

```markdown
> Note: Python prepends a `## 카테고리` block to this prompt at runtime, declaring the current category and its scope. Treat that block as the source of truth for which category you're processing.

```

(One blank line then the existing `## /cycle task` content continues.)

- [ ] **Step 2: Replace `tests/test_cycle.py`**

```python
import os
from pathlib import Path
import pytest
from unittest.mock import patch, call

from newsparser.store.sqlite import init_db, insert_article, get_unprocessed
from newsparser.scheduler.cycle import run_cycle


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "workspace"))
    monkeypatch.setenv("NEO4J_PASSWORD", "testpass")
    init_db()


SAMPLE_TECH_DIGEST = """사이클 2026-05-07 12:00 KST [tech]

새 소식
• (중요도 0.8) OpenAI 신모델 발표.

오픈 스레드
• 없음"""

SAMPLE_TECH_REPORT = SAMPLE_TECH_DIGEST + """

## Graph updates
### Entities
- NEW | Company | OpenAI | aliases: []

### Relations
"""

SAMPLE_MARKETS_DIGEST = """사이클 2026-05-07 12:00 KST [markets]

새 소식
• (중요도 0.7) Fed 50bp 인하.

오픈 스레드
• 없음"""

SAMPLE_MARKETS_REPORT = SAMPLE_MARKETS_DIGEST + """

## Graph updates
### Entities
- NEW | Institution | Fed | aliases: [연준]

### Relations
"""


def test_run_cycle_classifies_null_articles_then_dispatches_per_category(tmp_path):
    insert_article("g1", "OpenAI Blog", "Model X", "https://x.com/1", None, "release", category="tech")
    insert_article("g2", "FT", "Fed cuts", "https://x.com/2", None, "rate cut", category="markets")
    insert_article("g3", "HN", "Mixed", "https://x.com/3", None, "ambiguous")  # NULL

    def fake_classify(title, body):
        return "tech" if "Mixed" in title else "markets"

    fake_run_claude_calls: list[str] = []

    def fake_run_claude(prompt, **kw):
        fake_run_claude_calls.append(prompt)
        if "[tech]" in prompt or "tech" in prompt[:200]:
            return SAMPLE_TECH_REPORT
        return SAMPLE_MARKETS_REPORT

    with patch("newsparser.scheduler.cycle.classify_article", side_effect=fake_classify), \
         patch("newsparser.scheduler.cycle.run_claude", side_effect=fake_run_claude), \
         patch("newsparser.scheduler.cycle.apply_graph_updates"), \
         patch("newsparser.scheduler.cycle.send_long_message"):
        run_cycle("2026-05-07-12")

    # 2 cycles dispatched (tech + markets), each got their own claude call
    assert len(fake_run_claude_calls) == 2
    # All articles processed
    assert get_unprocessed() == []


def test_run_cycle_skips_empty_category(tmp_path):
    insert_article("g1", "OpenAI Blog", "Model X", "https://x.com/1", None, "release", category="tech")
    # No markets articles

    fake_run_claude_calls: list[str] = []

    def fake_run_claude(prompt, **kw):
        fake_run_claude_calls.append(prompt)
        return SAMPLE_TECH_REPORT

    with patch("newsparser.scheduler.cycle.classify_article"), \
         patch("newsparser.scheduler.cycle.run_claude", side_effect=fake_run_claude), \
         patch("newsparser.scheduler.cycle.apply_graph_updates"), \
         patch("newsparser.scheduler.cycle.send_long_message"):
        run_cycle("2026-05-07-12")

    assert len(fake_run_claude_calls) == 1


def test_run_cycle_writes_per_category_report(tmp_path):
    insert_article("g1", "OpenAI Blog", "Model X", "https://x.com/1", None, "release", category="tech")
    workspace = Path(os.environ["WORKSPACE_DIR"])

    with patch("newsparser.scheduler.cycle.run_claude", return_value=SAMPLE_TECH_REPORT), \
         patch("newsparser.scheduler.cycle.apply_graph_updates"), \
         patch("newsparser.scheduler.cycle.send_long_message"):
        run_cycle("2026-05-07-12")

    report = workspace / "cycles" / "tech" / "2026-05-07-12.md"
    assert report.exists()
    assert "OpenAI" in report.read_text()


def test_run_cycle_telegram_prefix_marks_category(tmp_path):
    insert_article("g1", "OpenAI Blog", "Model X", "https://x.com/1", None, "release", category="tech")

    sent: list[str] = []

    with patch("newsparser.scheduler.cycle.run_claude", return_value=SAMPLE_TECH_REPORT), \
         patch("newsparser.scheduler.cycle.apply_graph_updates"), \
         patch("newsparser.scheduler.cycle.send_long_message",
               side_effect=lambda msg: sent.append(msg)):
        run_cycle("2026-05-07-12")

    assert len(sent) == 1
    assert sent[0].startswith("[TECH]")
    assert "## Graph updates" not in sent[0]


def test_run_cycle_passes_category_to_apply_graph_updates(tmp_path):
    insert_article("g1", "OpenAI Blog", "Model X", "https://x.com/1", None, "release", category="tech")

    with patch("newsparser.scheduler.cycle.run_claude", return_value=SAMPLE_TECH_REPORT), \
         patch("newsparser.scheduler.cycle.apply_graph_updates") as mock_apply, \
         patch("newsparser.scheduler.cycle.send_long_message"):
        run_cycle("2026-05-07-12")

    mock_apply.assert_called_once()
    kwargs = mock_apply.call_args.kwargs
    assert kwargs["cycle_id"] == "tech-2026-05-07-12"
    assert kwargs["category"] == "tech"


def test_run_cycle_marks_processed_even_if_telegram_fails(tmp_path):
    insert_article("g1", "OpenAI Blog", "Model X", "https://x.com/1", None, "release", category="tech")

    with patch("newsparser.scheduler.cycle.run_claude", return_value=SAMPLE_TECH_REPORT), \
         patch("newsparser.scheduler.cycle.apply_graph_updates"), \
         patch("newsparser.scheduler.cycle.send_long_message", side_effect=RuntimeError("boom")):
        run_cycle("2026-05-07-12")

    assert get_unprocessed() == []
```

- [ ] **Step 3: Run tests to verify failure**

```bash
.venv/bin/pytest tests/test_cycle.py -v
```

Expected: failures around `classify_article` import / per-category dispatch / cycle_id format / telegram prefix.

- [ ] **Step 4: Replace `newsparser/scheduler/cycle.py`**

```python
import logging
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_KST = ZoneInfo("Asia/Seoul")

from newsparser.bot.sender import send_long_message
from newsparser.claude.input_builder import build_input_file
from newsparser.claude.output_parser import parse_graph_updates
from newsparser.claude.runner import run_claude
from newsparser.classifier import classify_article, CATEGORIES
from newsparser.graph.writer import apply_graph_updates
from newsparser.store.sqlite import (
    get_unclassified, get_unprocessed, mark_processed, update_category,
)
from newsparser.scheduler.lock import acquire_lock, release_lock, LockError
from newsparser.scheduler.workspace import ensure_workspace

logger = logging.getLogger(__name__)

_CYCLE_PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "cycle.md"

_SCOPE_TEXT = {
    "tech": (
        "AI 활용·신규 AI 정보·일반 컴퓨터 기술. "
        "시장 영향 일반 산업 뉴스는 markets 사이클에서 처리하므로 다루지 마."
    ),
    "markets": (
        "시장·매크로·정책·지정학·일반 산업. "
        "AI 회사 실적·주가 영향처럼 시장 관점이면 여기서 다뤄도 됨."
    ),
}


def _classify_pending() -> int:
    """Tag any unprocessed articles with NULL category via haiku. Returns count tagged."""
    rows = get_unclassified()
    if not rows:
        return 0
    logger.info("Classifying %d untagged articles via haiku", len(rows))
    for r in rows:
        try:
            cat = classify_article(r["title"], r["body"])
        except Exception as exc:  # defense in depth — classifier already catches its own errors
            logger.warning("Unexpected classifier error on %s: %s — defaulting to 'markets'", r["guid"], exc)
            cat = "markets"
        update_category(r["guid"], cat)
    return len(rows)


def _interests_text(workspace: Path, category: str) -> str:
    path = workspace / "me" / f"interests_{category}.md"
    if not path.exists():
        return "(no interests file yet)"
    return path.read_text(encoding="utf-8")


def _build_prompt(spec: str, category: str, workspace: Path, input_path: Path) -> str:
    header = (
        "## 카테고리\n"
        f"현재 사이클: {category}\n"
        f"범위: {_SCOPE_TEXT[category]}\n\n"
        "## 사용자 관심사\n"
        f"{_interests_text(workspace, category)}\n"
    )
    return f"{header}\n{spec}\n\nInput file: {input_path}"


def _run_for_category(slot: str, category: str, workspace: Path) -> None:
    unprocessed = get_unprocessed(category=category)
    if not unprocessed:
        logger.info("No unprocessed articles for category=%s slot=%s", category, slot)
        return

    input_path = build_input_file(slot, category)
    logger.info("[%s] Built input file: %s (%d articles)", category, input_path, len(unprocessed))

    spec = _CYCLE_PROMPT_PATH.read_text(encoding="utf-8")
    prompt = _build_prompt(spec, category, workspace, input_path)
    report = run_claude(prompt)

    report_path = workspace / "cycles" / category / f"{slot}.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    logger.info("[%s] Cycle report written: %s", category, report_path)

    entities, relations = parse_graph_updates(report)
    cycle_id = f"{category}-{slot}"
    apply_graph_updates(entities, relations, cycle_id=cycle_id, category=category)
    logger.info("[%s] Graph updated: %d entities, %d relations", category, len(entities), len(relations))

    digest = report.split("## Graph updates", 1)[0].rstrip()
    message = f"[{category.upper()}] {digest}" if digest else f"[{category.upper()}] (empty digest)"
    try:
        send_long_message(message)
    except Exception as e:
        logger.error("Telegram send failed for cycle %s/%s: %s", category, slot, e)

    mark_processed([a["guid"] for a in unprocessed])

    log_path = workspace / "logs" / f"{slot[:10]}.log"
    with log_path.open("a") as f:
        f.write(
            f"{datetime.now(_KST).isoformat()} cycle {cycle_id} OK "
            f"articles={len(unprocessed)} entities={len(entities)} relations={len(relations)}\n"
        )


def run_cycle(slot: str) -> None:
    """Full /cycle flow per slot — classifies pending then runs once per category."""
    workspace = ensure_workspace()
    lock_path = workspace / "state" / "lockfile"

    try:
        acquire_lock(lock_path)
    except LockError as e:
        logger.warning("Cycle aborted: %s", e)
        return

    try:
        try:
            _classify_pending()
        except Exception as exc:
            logger.warning("classify_pending failed (%s); proceeding with already-tagged rows", exc)

        for category in CATEGORIES:
            _run_for_category(slot, category, workspace)
    finally:
        release_lock(lock_path)
```

- [ ] **Step 5: Run tests to verify pass**

```bash
.venv/bin/pytest tests/test_cycle.py -v
```

Expected: 6 tests pass.

- [ ] **Step 6: Commit**

```bash
git add newsparser/scheduler/cycle.py prompts/cycle.md tests/test_cycle.py
git commit -m "feat(cycle): per-category dispatch with lazy haiku classification"
```

---

## Task 10: MCP — `graph_query`, `read_cycle_reports`, and per-category interests

**Files:**
- Modify: `newsparser/mcp_server.py`
- Modify: `newsparser/graph/traversal.py` if `get_context` needs a category filter — inspect first.
- Test: `tests/test_mcp_server.py`

- [ ] **Step 1: Inspect `traversal.py`**

```bash
grep -n "def " newsparser/graph/traversal.py
```

Expected: shows `get_context` and `get_influence_chain` signatures. We will pass `category` through to filter Cypher matches.

- [ ] **Step 2: Update existing MCP tests for new signatures**

Existing tests `test_read_cycle_reports_returns_n_most_recent`, `test_read_cycle_reports_empty_dir`, `test_read_interests_returns_content`, and `test_read_interests_missing_file` call the old signatures. Replace them in `tests/test_mcp_server.py`:

```python
def test_read_cycle_reports_returns_n_most_recent(tmp_path):
    cycles = Path(tmp_path / "workspace" / "cycles" / "markets")
    cycles.mkdir(parents=True, exist_ok=True)
    (cycles / "2026-05-04-10.md").write_text("cycle A")
    (cycles / "2026-05-05-10.md").write_text("cycle B")
    (cycles / "2026-05-06-10.md").write_text("cycle C")

    result = read_cycle_reports(category="markets", n=2)
    assert "cycle B" in result
    assert "cycle C" in result
    assert "cycle A" not in result


def test_read_cycle_reports_empty_dir():
    result = read_cycle_reports(category="markets")
    assert "No cycle reports found" in result


def test_read_interests_returns_content(tmp_path):
    me = Path(tmp_path / "workspace" / "me")
    me.mkdir(parents=True, exist_ok=True)
    (me / "interests_tech.md").write_text("# Tech profile\n")
    result = read_interests(category="tech")
    assert "Tech profile" in result


def test_read_interests_missing_file():
    result = read_interests(category="tech")
    assert "No interests file found" in result
```

- [ ] **Step 3: Append new tests for per-category behaviors**

Append to `tests/test_mcp_server.py`:

```python
from newsparser.mcp_server import (
    graph_query, read_cycle_reports, read_conversation_history, read_interests,
    write_interests, get_interest_weights, classify_query as mcp_classify_query,
)


def test_read_cycle_reports_reads_per_category_subfolder(tmp_path):
    base = Path(tmp_path / "workspace" / "cycles")
    (base / "tech").mkdir(parents=True, exist_ok=True)
    (base / "markets").mkdir(parents=True, exist_ok=True)
    (base / "tech" / "2026-05-07-12.md").write_text("tech cycle X")
    (base / "markets" / "2026-05-07-12.md").write_text("markets cycle Y")

    tech = read_cycle_reports(category="tech", n=2)
    assert "tech cycle X" in tech
    assert "markets cycle Y" not in tech


def test_graph_query_passes_category_filter():
    with patch("newsparser.mcp_server.get_context", return_value=[]) as mock_ctx, \
         patch("newsparser.mcp_server.get_influence_chain", return_value=[]):
        graph_query("OpenAI", category="tech")
    _, kwargs = mock_ctx.call_args
    args = mock_ctx.call_args[0]
    # accept either calling convention
    assert kwargs.get("category") == "tech" or "tech" in args


def test_read_interests_reads_per_category_file(tmp_path):
    me = Path(tmp_path / "workspace" / "me")
    me.mkdir(parents=True, exist_ok=True)
    (me / "interests_tech.md").write_text("# Tech profile\n")
    (me / "interests_markets.md").write_text("# Markets profile\n")

    assert "Tech profile" in read_interests(category="tech")
    assert "Markets profile" in read_interests(category="markets")


def test_write_interests_writes_to_per_category_file(tmp_path):
    me = Path(tmp_path / "workspace" / "me")
    me.mkdir(parents=True, exist_ok=True)

    write_interests(category="tech", content="# new tech profile\n")
    assert (me / "interests_tech.md").read_text() == "# new tech profile\n"


def test_get_interest_weights_uses_per_category_file(tmp_path):
    me = Path(tmp_path / "workspace" / "me")
    me.mkdir(parents=True, exist_ok=True)
    (me / "interests_tech.md").write_text(
        "| Theme | interest_weight | familiarity_weight | Notes |\n"
        "|---|---|---|---|\n"
        "| AI | 0.95 | 0.5 | |\n"
    )
    (me / "interest-events.jsonl").write_text("")
    out = get_interest_weights(category="tech", days=14)
    assert "AI" in out
    assert "0.95" in out


def test_classify_query_tool_returns_label():
    with patch("newsparser.mcp_server.classify_query", return_value="tech"):
        # The MCP-exported function delegates to the same name in `classifier`
        result = mcp_classify_query("OpenAI 신모델 동향")
    assert result == "tech"
```

- [ ] **Step 4: Run tests to verify failure**

```bash
.venv/bin/pytest tests/test_mcp_server.py -v
```

Expected: signature errors for the new `category` kwargs.

- [ ] **Step 5: Update MCP server**

Modify the relevant tool definitions in `newsparser/mcp_server.py`. Replace each tool one-for-one (keep all other tools and the imports intact). Add the new `classify_query` tool and the per-category file helpers.

Add to the imports near the top:

```python
from newsparser.classifier import classify_query as _classify_query_impl
```

Replace `graph_query`:

```python
@mcp.tool()
def graph_query(entity: str, category: str | None = None, days: int = 7) -> str:
    """Query the knowledge graph for context about an entity.
    Pass `category='tech'` or `category='markets'` to restrict traversal."""
    neighbors = get_context(entity, days, category=category)
    chains = get_influence_chain(entity, category=category)
    _log_interest_event(entity)
    return format_context_for_claude(entity, neighbors, chains)
```

Replace `read_cycle_reports`:

```python
@mcp.tool()
def read_cycle_reports(category: str, n: int = 4) -> str:
    """Read the N most recent cycle reports for the given category ('tech' or 'markets')."""
    cycles_dir = _workspace() / "cycles" / category
    if not cycles_dir.exists():
        return f"No cycle reports found for category={category}."
    files = sorted(cycles_dir.glob("*.md"), reverse=True)[:n]
    if not files:
        return f"No cycle reports found for category={category}."
    return "\n\n---\n\n".join(f.read_text() for f in reversed(files))
```

Replace `get_interest_weights`:

```python
@mcp.tool()
def get_interest_weights(category: str, days: int = 14) -> str:
    """Compare actual vs estimated weights for a category's interest profile."""
    interests_path = _workspace() / "me" / f"interests_{category}.md"
    events_path = _workspace() / "me" / "interest-events.jsonl"

    actual: dict[str, dict] = {}
    if interests_path.exists():
        for line in interests_path.read_text().splitlines():
            if not line.startswith("|"):
                continue
            parts = [p.strip() for p in line.split("|") if p.strip()]
            if len(parts) < 3 or parts[0] in ("Theme", "") or set(parts[0]) <= set("-"):
                continue
            try:
                actual[parts[0]] = {
                    "interest": float(parts[1]),
                    "familiarity": float(parts[2]),
                }
            except ValueError:
                continue

    estimated: dict[str, float] = {}
    if events_path.exists():
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        counts: Counter = Counter()
        for line in events_path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                e = json.loads(line)
                ts = datetime.fromisoformat(e["ts"].replace("Z", "+00:00"))
                if ts < cutoff:
                    continue
                for theme in e.get("themes", []):
                    counts[theme] += 1
            except (json.JSONDecodeError, KeyError, ValueError):
                continue
        if counts:
            max_count = max(counts.values())
            for theme, count in counts.items():
                estimated[theme] = round(count / max_count, 2)

    if not actual and not estimated:
        return f"No data found for category={category}."

    all_themes = sorted(set(actual) | set(estimated))
    lines = [f"Interest weight comparison for category={category} (last {days} days)\n"]
    lines.append(f"{'Theme':<30} {'actual':>8} {'estimated':>10} {'diff':>6}")
    lines.append("-" * 58)
    for theme in all_themes:
        a = actual.get(theme, {}).get("interest", None)
        e = estimated.get(theme, None)
        a_str = f"{a:.2f}" if a is not None else "  —  "
        e_str = f"{e:.2f}" if e is not None else "  —  "
        diff_str = f"{(e - a):+.2f}" if (a is not None and e is not None) else "  —  "
        lines.append(f"{theme:<30} {a_str:>8} {e_str:>10} {diff_str:>6}")
    return "\n".join(lines)
```

Replace `read_interests` and `write_interests`:

```python
@mcp.tool()
def read_interests(category: str) -> str:
    """Read the per-category interest profile."""
    path = _workspace() / "me" / f"interests_{category}.md"
    if not path.exists():
        return f"No interests file found for category={category}."
    return path.read_text()


@mcp.tool()
def write_interests(category: str, content: str) -> str:
    """Overwrite a per-category interests file."""
    path = _workspace() / "me" / f"interests_{category}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return f"interests_{category}.md updated."
```

Add the new `classify_query` MCP tool (anywhere with the other tools):

```python
@mcp.tool()
def classify_query(query: str) -> str:
    """Return the category the query is most likely about: 'tech', 'markets', or 'both'."""
    return _classify_query_impl(query)
```

- [ ] **Step 6: Update `newsparser/graph/traversal.py` to accept `category`**

Replace the bodies of `get_context` and `get_influence_chain` (leave `get_high_impact_recent` and `format_context_for_claude` alone):

```python
def get_context(entity_name: str, days: int = 7, category: str | None = None) -> list[dict]:
    """Return 3-hop neighbors updated within last N days. If category is set, only neighbors in that category."""
    cypher = (
        "MATCH (e {canonical_name: $name})-[*1..3]-(related) "
        "WHERE related.last_seen >= datetime() - duration({days: $days}) "
    )
    params: dict = {"name": entity_name, "days": days}
    if category is not None:
        cypher += "AND related.category = $category "
        params["category"] = category
    cypher += (
        "RETURN DISTINCT related.canonical_name AS name, "
        "  labels(related)[0] AS label, related.mention_count AS mentions "
        "ORDER BY related.mention_count DESC LIMIT 40"
    )
    with get_driver().session() as session:
        result = session.run(cypher, **params)
        return [dict(r) for r in result]


def get_influence_chain(entity_name: str, category: str | None = None) -> list[dict]:
    """Return influence chain up to 3 hops. If category is set, every hop must match."""
    cypher = (
        "MATCH path = (e {canonical_name: $name})"
        "-[:IMPACTS|INFLUENCES*1..3]->(target) "
    )
    params: dict = {"name": entity_name}
    if category is not None:
        cypher += "WHERE all(n IN nodes(path) WHERE n.category = $category) "
        params["category"] = category
    cypher += (
        "RETURN [n IN nodes(path) | n.canonical_name] AS chain, length(path) AS depth "
        "ORDER BY depth LIMIT 10"
    )
    with get_driver().session() as session:
        result = session.run(cypher, **params)
        return [dict(r) for r in result]
```

`get_high_impact_recent` and `format_context_for_claude` are unchanged.

- [ ] **Step 7: Run tests to verify pass**

```bash
.venv/bin/pytest tests/test_mcp_server.py -v
```

Expected: previously-existing tests still pass + 6 new tests pass.

- [ ] **Step 8: Commit**

```bash
git add newsparser/mcp_server.py newsparser/graph/traversal.py tests/test_mcp_server.py
git commit -m "feat(mcp): category-aware tools and classify_query"
```

---

## Task 11: Tracker — inject classify_query hint

**Files:**
- Modify: `newsparser/bot/tracker.py`
- Test: `tests/test_tracker.py`

- [ ] **Step 1: Update existing tracker tests to patch classify_query**

The existing `test_run_tracker_calls_claude_with_mcp_config` uses `assert_called_once()`. After this task, `run_tracker` will call `run_claude` twice (once for classify, once for the real answer), so the assertion must change. Patch `classify_query` separately so the existing tests continue to count only the real-answer call.

In `tests/test_tracker.py`, replace `test_run_tracker_calls_claude_with_mcp_config`:

```python
def test_run_tracker_calls_claude_with_mcp_config():
    with patch("newsparser.bot.tracker.classify_query", return_value="both"), \
         patch("newsparser.bot.tracker.run_claude", return_value="Claude answer") as mock_claude:
        answer = run_tracker(chat_id="chat123", query="FOMC 어떻게 됐어?")
    mock_claude.assert_called_once()
    args, kwargs = mock_claude.call_args
    prompt = args[0]
    assert "FOMC" in prompt
    assert kwargs.get("mcp_config") is not None
    assert answer == "Claude answer"
```

And replace `test_run_tracker_appends_to_history`:

```python
def test_run_tracker_appends_to_history():
    with patch("newsparser.bot.tracker.classify_query", return_value="both"), \
         patch("newsparser.bot.tracker.run_claude", return_value="답변"):
        run_tracker(chat_id="chat123", query="질문")
    history = load_history("chat123")
    assert len(history) == 2
    assert history[0]["role"] == "user"
    assert history[1]["role"] == "assistant"
```

- [ ] **Step 2: Append the new tests**

Append to `tests/test_tracker.py`:

```python
from unittest.mock import patch
from newsparser.bot.tracker import run_tracker


def test_run_tracker_injects_category_hint(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "workspace"))

    captured: dict = {}

    def fake_run_claude(prompt, **kw):
        captured["prompt"] = prompt
        return "answer"

    with patch("newsparser.bot.tracker.classify_query", return_value="tech") as mock_classify, \
         patch("newsparser.bot.tracker.run_claude", side_effect=fake_run_claude):
        run_tracker(chat_id="t1", query="OpenAI 새 모델 어때?")

    mock_classify.assert_called_once_with("OpenAI 새 모델 어때?")
    assert "category hint" in captured["prompt"].lower()
    assert "tech" in captured["prompt"]


def test_run_tracker_continues_if_classify_query_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "workspace"))

    def fake_run_claude(prompt, **kw):
        return "answer"

    with patch("newsparser.bot.tracker.classify_query", side_effect=RuntimeError("boom")), \
         patch("newsparser.bot.tracker.run_claude", side_effect=fake_run_claude):
        # must not raise — the tracker should treat classification as best-effort
        result = run_tracker(chat_id="t1", query="anything")
    assert result == "answer"
```

- [ ] **Step 3: Run tests to verify failure**

```bash
.venv/bin/pytest tests/test_tracker.py -v
```

Expected: tests fail because `classify_query` is not yet imported in `tracker.py`.

- [ ] **Step 4: Update `tracker.py`**

In `newsparser/bot/tracker.py`, add the import near the top (after the existing `run_claude` import):

```python
from newsparser.classifier import classify_query
```

Then update `run_tracker` — replace the `prompt = (...)` block:

```python
    try:
        category_hint = classify_query(query)
    except Exception:
        category_hint = "both"

    prompt = (
        f"User query category hint: {category_hint}. "
        "Use this as a default filter when calling graph/cycle/interests tools, "
        "but pass category=None or 'both' if the question genuinely spans both.\n\n"
        "You are a market intelligence assistant. Use the available tools "
        "to gather relevant context, then answer the user's query. "
        "Cite cycle reports by date. Lead with TL;DR if the answer is long."
        f"{prev_context}\n\n"
        f"User query: {query}"
    )
```

- [ ] **Step 5: Run tests to verify pass**

```bash
.venv/bin/pytest tests/test_tracker.py -v
```

Expected: existing tests pass + 2 new tests pass.

- [ ] **Step 6: Commit**

```bash
git add newsparser/bot/tracker.py tests/test_tracker.py
git commit -m "feat(tracker): inject classify_query hint into prompt"
```

---

## Task 12: README DB path documentation

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update README**

Add an "Architecture" subsection or extend the existing one with the DB path. Insert before the `## Running` section:

```markdown
## Storage

| Layer | Path | Override |
|---|---|---|
| Articles + cycle queue | `workspace/newsparser.db` | `DB_PATH` env var |
| Cycle reports | `workspace/cycles/{tech,markets}/{slot}.md` | `WORKSPACE_DIR` env var |
| Interest profiles | `workspace/me/interests_{tech,markets}.md` | `WORKSPACE_DIR` env var |
| Knowledge graph | Neo4j (configured via `NEO4J_PASSWORD`) | — |

```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: document DB and workspace paths in README"
```

---

## Task 13: Final verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

```bash
.venv/bin/pytest tests/ -v
```

Expected: every test passes. If `test_graph_writer.py` fails on Neo4j connection, that is environmental — note it but don't block on it.

- [ ] **Step 2: Smoke check imports and prompt path**

```bash
.venv/bin/python -c "
from newsparser.scheduler.cycle import run_cycle, _CYCLE_PROMPT_PATH, _SCOPE_TEXT
from newsparser.classifier import classify_article, classify_query, CATEGORIES
from newsparser.bot.tracker import run_tracker
from newsparser.mcp_server import graph_query, read_cycle_reports, read_interests, write_interests, classify_query as mcp_q
from newsparser.collector.sources import load_sources
from newsparser.scheduler.workspace import ensure_workspace
print('imports ok')
print('CATEGORIES:', CATEGORIES)
print('SCOPE_TEXT keys:', list(_SCOPE_TEXT.keys()))
print('cycle prompt exists:', _CYCLE_PROMPT_PATH.exists())
print('sources count:', len(load_sources('sources.md')))
"
```

Expected:
```
imports ok
CATEGORIES: ('tech', 'markets')
SCOPE_TEXT keys: ['tech', 'markets']
cycle prompt exists: True
sources count: 19
```

- [ ] **Step 3: Verify ensure_workspace creates the new layout**

```bash
.venv/bin/python -c "
import os, tempfile
with tempfile.TemporaryDirectory() as d:
    os.environ['WORKSPACE_DIR'] = d
    from newsparser.scheduler.workspace import ensure_workspace
    root = ensure_workspace()
    for p in ['input/tech', 'input/markets', 'cycles/tech', 'cycles/markets',
              'me/interests_tech.md', 'me/interests_markets.md']:
        assert (root/p).exists(), p
    print('ok')
"
```

Expected: `ok`.

- [ ] **Step 4: Push**

```bash
git push
```

---

## Operator follow-up (not part of automated execution)

After merge / deploy, the operator must:

1. Move themes from any pre-existing `workspace/me/interests.md` into `interests_tech.md` and `interests_markets.md`. Delete or rename the old file. (Skip if no pre-existing file.)
2. Optionally backfill historical Neo4j nodes:
   ```cypher
   MATCH (n) WHERE n.category IS NULL SET n.category = 'markets';
   MATCH ()-[r]->() WHERE r.category IS NULL SET r.category = 'markets';
   ```
3. First cycle slot will lazily classify the existing 207 untagged articles via haiku — expect a one-time ~$0.05 burst. Subsequent slots only classify new ambiguous-source articles.
