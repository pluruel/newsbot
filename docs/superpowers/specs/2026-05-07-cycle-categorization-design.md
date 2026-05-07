# Cycle Categorization Design

**Date:** 2026-05-07
**Status:** Approved (brainstorming complete, awaiting spec review → writing-plans)

## 1. Goal

Split each `/cycle` run into two parallel cognitive tracks — `tech` and `markets` — so the user can consume AI/computing news independently from finance/macro/general news. Storage, cycle reports, graph data, interest profiles, and tracker MCP tools all become category-aware.

## 2. Categories

Two top-level categories. Every article and every graph node/relation belongs to exactly one.

| Category | Scope |
|---|---|
| `tech` | AI 활용, 신규 AI 정보 (모델·연구·하드웨어·거버넌스), 일반 컴퓨터 기술. AI를 좁게 잡되, 일반 컴퓨터 기술도 함께. |
| `markets` | 시장·매크로·통화정책·정책·지정학·일반 산업·기타. **애매하면 무조건 `markets`** (fallback default). |

Tie-breaker for ambiguous content: lean to `markets`. The `tech` bucket is reserved for content that is *primarily* about AI utilization / new AI information / computer technology, not content that merely mentions them in a financial-impact context.

## 3. Architecture Overview

```
[Collector]    insert article → category from sources.md mapping (tech / markets / NULL if ambiguous source)
[Scheduler]    cycle slot fires
   ├ classify_pending() — haiku batch-tags any rows with category IS NULL
   ├ for cat in [tech, markets]:
   │    if no unprocessed rows for cat: skip
   │    build per-category input file
   │    run /cycle with category-scoped prompt
   │    save report to workspace/cycles/{cat}/{slot}.md
   │    apply graph updates with category property
   │    push digest to Telegram with [TECH]/[MARKETS] prefix
   │    mark articles processed
   └ release slot lock
[Tracker]      query → haiku classify_query() → category hint in prompt
                     → MCP tools accept category param (filter or pass-through)
```

## 4. Data Layer

### 4.1 SQLite (`workspace/newsparser.db` — DB path is documented in README, overridable via `DB_PATH` env var)

Add nullable `category` column to `pending_articles`:

```sql
ALTER TABLE pending_articles ADD COLUMN category TEXT;
```

Migration is idempotent — `init_db()` wraps the ALTER in `try / except sqlite3.OperationalError` so re-runs are safe.

`get_unprocessed()` gains a `category: str | None` parameter. With a value it filters; without, it returns all unprocessed (used by the classifier batch step to find NULL rows).

### 4.2 `sources.md`

Add `Category` column. Loader switches from positional parsing to **header-based parsing** so future column changes don't silently break.

Initial mapping:

| Source | Category |
|---|---|
| 매일경제, 한국경제, 연합인포맥스, 중앙일보, 한겨레 | `markets` |
| AP, Financial Times, Federal Reserve, Bloomberg Markets | `markets` |
| VentureBeat AI | `tech` |
| Hacker News, Ars Technica, AP Technology, MIT Technology Review, Bloomberg Technology | (blank — needs haiku) |

**New sources to add** (boost `tech` signal — first-pass low-noise feeds):

| Source | RSS | Category |
|---|---|---|
| OpenAI Blog | https://openai.com/blog/rss/ | `tech` |
| Anthropic News | https://www.anthropic.com/news/rss.xml | `tech` |
| Google DeepMind Blog | https://deepmind.google/blog/rss.xml | `tech` |
| TechCrunch AI | https://techcrunch.com/category/artificial-intelligence/feed/ | `tech` |

The header-based loader treats blank `Category` cells as `None` (→ ambiguous → haiku).

### 4.3 Neo4j

Add `category` property to entity and relation upserts.

```cypher
MERGE (e:Label {canonical_name: $name})
ON CREATE SET e.category = $category, ...
ON MATCH  SET e.category = coalesce(e.category, $category), ...
```

`coalesce` ensures pre-existing nodes get backfilled the first time a new cycle re-touches them, but never get overwritten if already set. Same pattern for relations.

`cycle_id` format changes from `{slot}` (e.g. `2026-05-07-12`) to `{category}-{slot}` (e.g. `tech-2026-05-07-12`) so `source_cycles` array preserves provenance.

No bulk backfill of existing graph data — natural ~14-day lookback churn refreshes active nodes. If consistency proves problematic, a one-shot `MATCH (n) WHERE n.category IS NULL SET n.category = 'markets'` can be run manually.

### 4.4 Workspace directory layout

```
workspace/
├── input/
│   ├── tech/{slot}-input.md
│   └── markets/{slot}-input.md
├── cycles/
│   ├── tech/{slot}.md
│   └── markets/{slot}.md
├── me/
│   ├── interests_tech.md
│   ├── interests_markets.md
│   └── manifesto.md          # single file — perspective is category-independent
└── newsparser.db
```

`ensure_workspace()` creates the new subdirectories and empty `interests_tech.md` / `interests_markets.md` templates if missing. Existing `interests.md` is left untouched — user manually renames or splits it. (No auto-migration: theme-by-theme automatic categorization is too risky.)

## 5. Classification Pipeline

Hybrid: source-mapping at ingestion (cheap, deterministic) + haiku at cycle time (just-in-time, only for ambiguous sources).

### 5.1 At collector insert

```python
source_category = SOURCE_TO_CATEGORY.get(source_name)  # 'tech' | 'markets' | None
insert_article(..., category=source_category)
```

`None` is stored as SQL `NULL`.

### 5.2 At cycle slot start, before per-category dispatch

```python
def classify_pending() -> int:
    """Tag any unprocessed articles with NULL category via haiku. Returns count tagged."""
    rows = SELECT * FROM pending_articles WHERE processed=0 AND category IS NULL
    for row in rows:
        category = classify_article(row.title, row.body)  # haiku, single call
        UPDATE pending_articles SET category=? WHERE guid=?
```

Haiku classifier prompt:

> 다음 기사가 (a) AI 활용·신규 AI 정보·일반 컴퓨터 기술에 해당하면 `tech`, (b) 그 외 또는 애매하면 `markets`. 한 단어만 답해.
>
> 제목: ...
> 본문 (앞 500자): ...

Model: `claude-haiku-4-5-20251001`. Timeout 15s.

Error handling:
- Per-article classifier failure or unparseable response (anything not exactly `tech` or `markets` after lowercase + trim) → fallback `markets` (per the global tie-breaker rule).
- `classify_pending()` itself catches subprocess-level haiku errors; if it can't run at all, log warning and return — the per-category dispatch then sees no new tagged articles and skips. Articles stay `NULL` until a later slot retries.

Cost estimate: 5–30 ambiguous articles per slot × ~$0.001 = under $0.05/slot. Slots are 4×/day so well under $1/month.

### 5.3 Cycle dispatch

```python
classify_pending()
for category in ('tech', 'markets'):
    unprocessed = get_unprocessed(category=category)
    if not unprocessed:
        continue
    run_cycle_for_category(category, slot, unprocessed)
```

Sequential by design (one Claude binary at a time, no stdout collisions, single lock for the whole slot).

## 6. Cycle Execution Per Category

### 6.1 Prompt parameterization

`prompts/cycle.md` stays as a single file. Python prepends a category block:

```
## 카테고리
현재 사이클: {category}
범위: {scope_text[category]}
사용자 관심사: (interests_{category}.md 내용)

(rest of prompts/cycle.md)

Input file: {input_path}
```

`scope_text`:
- `tech`: "AI 활용·신규 AI 정보·일반 컴퓨터 기술. 시장 영향 일반 산업 뉴스는 markets 사이클에서 처리하므로 다루지 마."
- `markets`: "시장·매크로·정책·지정학·일반 산업. AI 회사 실적·주가 영향처럼 시장 관점이면 여기서 다뤄도 됨."

### 6.2 Output

- File: `workspace/cycles/{category}/{slot}.md` (full report — Korean digest + `## Graph updates` block).
- Telegram: digest only (above the Graph updates split), prefixed with `[TECH]` or `[MARKETS]` so the user can distinguish at a glance.
- Graph: `apply_graph_updates(entities, relations, cycle_id=f"{category}-{slot}", category=category)`.
- DB: `mark_processed([row.guid for row in unprocessed])`.

### 6.3 Empty bucket behavior

If `get_unprocessed(category)` is empty, skip the entire per-category block — no Claude call, no Telegram message, no log noise beyond a single info-level "no new articles for {category}".

### 6.4 Locking

Existing `workspace/state/lockfile` is acquired once per slot at the top of `_cycle_job` and released after both categories finish. Two categories share the same lock to prevent overlap with the next slot.

## 7. MCP / Tracker Changes

### 7.1 Query category hint

`run_tracker()` calls a new `classify_query(query) -> 'tech' | 'markets' | 'both'` (haiku) at entry. Anything outside that set (including timeouts and unparseable responses) is normalized to `'both'` (= no implicit filter). Adds a hint line into the system prompt:

> User query category hint: `{hint}`. Use this as a default filter when calling graph/cycle/interests tools, but pass `category=None` if the question genuinely spans both.

The hint guides Claude but doesn't force filtering — for cross-category questions ("AI가 NVDA 주가에 미친 영향"), the hint is `both` and Claude can pass `category=None` to traverse the full graph.

### 7.2 MCP tool signature changes

| Tool | Before | After |
|---|---|---|
| `graph_query` | `(node)` | `(node, category: str \| None = None)` — filters `WHERE n.category = $cat` when set |
| `read_cycle_reports` | `(n=4)` | `(category: str, n=4)` — reads from `workspace/cycles/{category}/` |
| `get_interest_weights` | `(days=14)` | `(category: str, days=14)` — parses `interests_{category}.md` |
| `read_interests` | `()` | `(category: str)` |
| `write_interests` | `(content)` | `(category: str, content: str)` |
| `classify_query` (new) | — | `(query: str) -> str` |

Breaking change for the interest tools. Acceptable since the system has a single user.

### 7.3 Manifesto

`manifesto.md` stays a single file. Perspective is category-independent. Existing `read_manifesto` / `write_manifesto` MCP tools unchanged.

## 8. Migration Summary

Idempotent self-migration on first run after deploy:

1. `init_db()` runs `ALTER TABLE pending_articles ADD COLUMN category TEXT` inside try/except.
2. `ensure_workspace()` creates `workspace/input/{tech,markets}/`, `workspace/cycles/{tech,markets}/`, and empty `interests_tech.md` / `interests_markets.md` templates if absent.
3. First cycle slot triggers `classify_pending()` which lazily tags the existing 207 untagged articles.
4. Per-category cycle runs populate Neo4j with `category` property going forward.

Manual operator steps (one-time):

1. Edit `sources.md` to add `Category` column with the table from §4.2 (including the 4 new tech sources).
2. Move themes from existing `interests.md` into `interests_tech.md` and `interests_markets.md` as appropriate. Delete or rename the old file.
3. (Optional) Run `MATCH (n) WHERE n.category IS NULL SET n.category = 'markets'` in Neo4j to backfill historical nodes.

## 9. Testing Strategy

| Test file | New / changed coverage |
|---|---|
| `tests/test_classifier.py` (new) | haiku classifier mock; source-map lookup; `None` fallback; unparseable response → `markets` |
| `tests/test_cycle.py` | per-category iteration; empty bucket skip; per-category report path; cycle_id format `{cat}-{slot}`; `apply_graph_updates` receives category arg; classify_pending called before dispatch |
| `tests/test_input_builder.py` | `build_input_file(slot, category)` writes to `workspace/input/{category}/...` |
| `tests/test_graph_writer.py` | `apply_graph_updates(..., category=)` sets node and relation property; `coalesce` doesn't overwrite existing |
| `tests/test_sources.py` | header-based parser; missing Category cell → `None`; old 4-column layout fails clearly |
| `tests/test_store.py` | `get_unprocessed(category=)` filter; `update_category(guid, cat)`; `category=None` returns NULL rows for classifier batch |
| `tests/test_mcp_server.py` | each tool's new category param; `read_cycle_reports` reads correct subfolder; `read_interests`/`write_interests` per-category |
| `tests/test_tracker.py` | `classify_query` hint injected into prompt; classification failure doesn't block tracker |
| `tests/test_dispatcher.py` | unchanged |

## 10. Out of Scope (deferred)

- Backfilling existing Neo4j nodes/relations with categories.
- Auto-migrating existing `interests.md` content into the two new files.
- Per-category cron schedules (e.g., tech cycle hourly, markets cycle 4x/day). Both run on the same `00/06/12/18 KST` schedule for now.
- Per-category Telegram chats. Single chat with `[TECH]` / `[MARKETS]` prefixes.
- arxiv/preprint feeds — high volume, low signal — revisit if tech bucket is too thin after 1 week.
- A 3rd "neither" / `none` category for irrelevant articles. Tie-breaker rule pushes everything into `markets` so this isn't needed.
