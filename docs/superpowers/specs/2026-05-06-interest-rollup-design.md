# Interest Rollup — Design Spec

Date: 2026-05-06

## Summary

Before each morning brief, analyze recent tracker query events (query text + graph hit entities) and use Claude to synthesize an updated `interests.md`. This keeps the morning brief selection aligned with actual user behavior without manual maintenance.

---

## Data Flow

```
[Telegram tracker query]
    → tracker.py: graph traversal (neighbors, chains)
    → _log_interest_event(query, entities)
    → workspace/me/interest-events.jsonl

[Daily morning run]
    → run_morning()
        1. interests_rollup()
           - read interest-events.jsonl (last 14 days)
           - read current interests.md
           - Claude synthesizes updated interests.md
           - write result back to interests.md
        2. existing brief logic (now uses updated interests.md)
```

---

## Components

### 1. tracker.py — logging improvement

`_log_interest_event` gains an `entities` parameter populated from graph traversal results.

Event schema:
```json
{
  "ts": "2026-05-06T07:51:13Z",
  "type": "query",
  "entities": ["삼성전자", "TSMC"],
  "themes": ["테크관련 기사 모아줘"],
  "depth": "shallow"
}
```

If graph traversal fails, `entities` remains `[]` — Claude can still extract signal from the query text alone.

### 2. newsparser/scheduler/interests.py — new module

`interests_rollup()` function:

1. Load last 14 days of `interest-events.jsonl`
2. Load current `interests.md`
3. Build Claude prompt:
   - Pass all events (query + entities)
   - Pass current interests.md
   - Instruct Claude to:
     - Infer themes/entities from repeated graph hits and query patterns
     - Ignore meta-queries ("안녕", "기사 보여줘", "요약해줘")
     - Preserve `## User overrides` content verbatim, merge into final output
     - Update `## Themes` section
     - Refresh `Last updated` date
4. Write Claude's output back to `interests.md` (Claude returns the full file content as raw markdown, no commentary)

### 3. morning.py — integration

```python
def run_morning(date_str: str) -> None:
    try:
        interests_rollup()
    except Exception:
        logger.warning("Interest rollup failed — proceeding with existing interests.md")
    # existing brief logic unchanged
```

Rollup failure is non-fatal: brief continues with the previous `interests.md`.

---

## interests.md Format (unchanged)

```markdown
# Interests Profile
Last updated: YYYY-MM-DD

## Themes
- (Claude-generated content)

## User overrides
- (Manual entries — preserved verbatim by Claude)
```

---

## Decisions

| Decision | Choice | Reason |
|---|---|---|
| Rollup trigger | Daily before morning brief | Freshest signal at brief time |
| Signal source | Query text + graph hit entities | Query gives intent; entities give precision |
| Synthesis | Full Claude | Handles noise filtering, semantic clustering, merge naturally |
| Failure mode | Non-fatal, log and continue | Brief must not be blocked by rollup errors |
| Lookback window | 14 days | Enough history; older signal decays fast |
| Override handling | Claude merges, preserves User overrides | Manual intent always wins |
