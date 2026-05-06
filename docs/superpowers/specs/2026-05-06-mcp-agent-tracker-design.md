# Design: MCP-based Agent Tracker

**Date:** 2026-05-06  
**Status:** Approved

## Problem

`run_tracker()` currently uses a naive heuristic — `query.split()[0]` — as an entity hint for graph traversal. Context (graph, history, cycles) is always assembled unconditionally and injected into the prompt, regardless of relevance. Claude has no agency over what to look up.

## Goal

Give Claude (Sonnet) full agency over what context to gather before answering. Instead of Python pre-assembling context, Claude calls tools as needed via MCP.

---

## Architecture

```
[Telegram] → [bot (host)]
                  ↓
            run_tracker(chat_id, query)
                  ↓
            claude -p "..." --mcp-config mcp.json --model claude-sonnet-4-6
                  ↓ (HTTP/SSE)
            [mcp-server container :8766]
            ┌─────────────────────────────┐
            │ graph_query(entity, days)   │ → neo4j
            │ read_cycle_reports(n)       │ → workspace/cycles/
            │ read_conversation_history() │ → workspace/sessions/
            │ read_interests()            │ → workspace/me/interests.md
            └─────────────────────────────┘
                  ↓
            Claude synthesizes answer from tool results
                  ↓
            Python saves history + sends to Telegram
```

**Transport:** HTTP/SSE (required for Docker — stdio not viable cross-container)  
**Model:** `claude-sonnet-4-6` via `--model` flag

---

## MCP Tools

### `graph_query(entity: str, days: int = 7) -> str`
Wraps `get_context()` + `get_influence_chain()` + `format_context_for_claude()`. Claude may call this multiple times for different entities in the same query.

### `read_cycle_reports(n: int = 4) -> str`
Reads the `n` most recent files from `workspace/cycles/`, concatenated. Used when Claude needs recent event context.

### `read_conversation_history(chat_id: str, n: int = 10) -> str`
Wraps `load_history()`. Claude calls this when conversation continuity is relevant — not on every query.

### `read_interests() -> str`
Returns `workspace/me/interests.md`. Used when the query relates to the user's domain focus.

---

## Prompt Change

**Before:**
```
/tracker

## User query
{query}

## Graph context (always included)
...

## Conversation history (always included)
...
```

**After:**
```
You are a market intelligence assistant. Use the available tools
to gather relevant context, then answer the user's query.
Cite cycle reports by date. Lead with TL;DR if the answer is long.

User query: {query}
Chat ID (for history tool): {chat_id}
```

---

## Files

### New
| File | Purpose |
|---|---|
| `newsparser/mcp_server.py` | FastMCP server, defines 4 tools |
| `mcp.json` | Host-side claude CLI config pointing to `http://localhost:8766/sse` |

### Modified
| File | Change |
|---|---|
| `newsparser/claude/runner.py` | Add `mcp_config: str \| None` param; inject `--mcp-config` and `--model` flags |
| `newsparser/bot/tracker.py` | Remove manual entity extraction + context assembly; simplified prompt; pass `mcp_config` |
| `docker-compose.yml` | Add `mcp-server` service (port 8766, workspace volume, neo4j dep) |
| `pyproject.toml` | Add `fastmcp` dependency |

---

## docker-compose addition

```yaml
mcp-server:
  build: .
  command: .venv/bin/python -m newsparser.mcp_server
  ports:
    - "8766:8766"
  env_file: .env
  environment:
    NEO4J_URI: bolt://neo4j:7687
  volumes:
    - ./workspace:/app/workspace
  depends_on:
    neo4j:
      condition: service_healthy
  restart: unless-stopped
```

---

## What Does NOT Change

- History save/load ownership stays in Python (`run_tracker()` still writes turns after the call)
- Interest event logging moves into `graph_query` tool: each call logs the queried entity directly. `run_tracker()` no longer needs to extract entities post-hoc.
- Telegram integration unchanged
- All other scheduler jobs (cycle, morning, interests) unchanged

---

## Out of Scope

- `web_search` tool (future addition)
- Multi-turn tool calling loops beyond what claude CLI handles natively
- Changes to `/cycle` or `/morning` flows
