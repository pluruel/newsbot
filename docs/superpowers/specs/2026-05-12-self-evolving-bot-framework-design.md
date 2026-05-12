# Self-Evolving Bot Framework — Design Spec

**Date:** 2026-05-12  
**Status:** Approved for implementation planning

---

## Problem

Current setup runs on the host machine: cron registers scheduled jobs, Telegram bot runs as a foreground process, Claude is invoked via `claude -p` subprocess. Pain points:

- No way to add new automation without editing code and restarting manually
- Logging is a single append-only file (`cron.log`); no per-run separation, no cost tracking, no failure alerts
- Scheduled jobs and the bot are disconnected systems — different mental models, different entry points
- No self-modification path: improving a prompt or adding a bot requires human intervention at the terminal

---

## Goal

A unified bot framework where:
1. All automation (scheduled + Telegram-triggered) is expressed as the same `Bot` primitive
2. New bots can be created by telling the tracker in natural language — tracker writes the files, user reviews, `/reload` activates
3. Tracker can also rebuild the Docker image when new packages are needed (`docker compose up -d --build dispatcher`)
4. The only action requiring human physical intervention is `CLAUDE_CODE_OAUTH_TOKEN` rotation

---

## Container Topology

Three services total (down from host cron + host bot + two docker services):

| Service | Type | Role |
|---|---|---|
| `neo4j` | long-running | Unchanged |
| `poller` | long-running | Unchanged |
| `dispatcher` | long-running (new) | Telegram bot + APScheduler + bot runtime |

`dispatcher` image: same `newsparser` Dockerfile, different entrypoint (`newsparser.dispatcher`).

`dispatcher` volume mounts:
- `./:/app` — full repo (enables live file edits without rebuild)
- `/var/run/docker.sock:/var/run/docker.sock` — enables tracker to trigger `docker compose` commands

Auth: `CLAUDE_CODE_OAUTH_TOKEN` injected via `.env` (never committed to git, `chmod 600`).

`run.sh` is removed entirely. System start: `docker compose up -d`.

---

## Bot Abstraction

Every bot lives in `newsparser/bots/<name>/`:

```
newsparser/bots/
  cycle/
    bot.py
    prompt.md       # if Claude-driven
  weekly/bot.py
  reflect/bot.py
  market_daily/bot.py
  tracker/bot.py
  morning_summary/  # example new bot
    bot.py
    prompt.md
```

`bot.py` exports exactly one `BOT` object:

```python
from newsparser.bots import Bot, Cron, TelegramMatch, Context

async def run(ctx: Context) -> None:
    ...

BOT = Bot(
    name="morning_summary",
    triggers=[Cron("0 9 * * *", tz="Asia/Seoul")],
    run=run,
    enabled=True,   # set False to disable without deleting
)
```

### Triggers

| Type | Example | When it fires |
|---|---|---|
| `Cron(schedule, tz)` | `Cron("0 9 * * *", tz="Asia/Seoul")` | APScheduler, KST-native (no UTC offset tricks) |
| `TelegramMatch(pattern)` | `TelegramMatch(r"^https?://")` | Inbound Telegram message matching regex |

Existing slash commands (`/cycle`, `/weekly`, `/reflect`) are modeled as `TelegramMatch(r"^/cycle")` inside their respective bots.

### Context

`ctx` is injected into every `run()` call:

```python
ctx.claude(prompt, inputs)     # runs claude -p, records cost to claude_runs.db
ctx.telegram.send(text)        # sends message to triggering chat
ctx.workspace                  # helpers for workspace/ paths
ctx.market_db                  # sqlite3 connection to market.db
ctx.newsparser_db              # sqlite3 connection to newsparser.db
ctx.neo4j                      # neo4j driver session
ctx.logger                     # writes to workspace/logs/<name>/<ts>.log
ctx.message                    # inbound Telegram message (TelegramMatch bots only)
ctx.job_name                   # bot name string, for cost tracking
```

---

## Dispatcher

`newsparser/dispatcher.py` is the single entrypoint:

1. **Boot**: scans `newsparser/bots/*/bot.py`, imports each `BOT` object
2. **Cron registration**: registers `Cron`-triggered bots with APScheduler (ThreadPoolExecutor — Claude calls are blocking subprocesses, isolated per thread)
3. **Telegram routing**: registers `TelegramMatch` patterns; falls back to `tracker` bot if no pattern matches
4. **Concurrency guard**: each bot has a per-name lock; concurrent runs of the same bot are skipped (replaces `flock`)

### `/reload` command

User sends `/reload` in Telegram → dispatcher:
1. Re-scans `newsparser/bots/*/bot.py`
2. Unregisters removed/disabled bots from APScheduler
3. Registers new/changed bots
4. Responds with diff of what changed

No container restart needed for Python-only changes.

---

## Bot Creation Flow (Telegram → File → Live)

```
User: "어제 cycle 요약 매일 아침 9시에 보내주는 봇 만들어줘"
  ↓
Tracker (Claude with Bash tool)
  ↓
Creates: newsparser/bots/morning_summary/bot.py
         newsparser/bots/morning_summary/prompt.md
  ↓
Telegram: "만들었어. 확인 후 /reload 보내줘."
  ↓
User reviews files in terminal/IDE
  ↓
User: "/reload"
  ↓
Dispatcher registers new bot, confirms in Telegram
```

### Docker rebuild flow (new package needed)

```
Tracker: edits pyproject.toml, adds dependency
Tracker: nohup docker compose up -d --build dispatcher &
         (detached so dispatcher dying doesn't interrupt the build)
Telegram: "패키지 추가하고 재시작했어. 잠깐 기다려줘."
```

Docker socket is mounted read-write into dispatcher. Tracker uses it for rebuild only — not for managing neo4j, poller, or other services (soft convention, not enforced).

---

## Logging (4 channels)

### 1. Real-time unified stream
All services use `json-file` driver with rotation:
```yaml
logging:
  driver: json-file
  options:
    max-size: "20m"
    max-file: "5"
```
`docker compose logs -f dispatcher` streams all bot activity live.

### 2. Per-run files
`ctx.logger` automatically opens `workspace/logs/<bot-name>/<ts>.log` on run start, tees all output to file + stdout, closes on completion. Files persist on host via volume mount.

### 3. Failure alerts
Global exception wrapper in dispatcher: if `run()` raises, capture traceback + last 30 lines of log → `ctx.telegram.send()` to the triggering chat (or a configured alert chat ID for cron-triggered bots).

### 4. Claude cost tracking
`ctx.claude()` calls `claude -p --output-format json`, parses result, appends to `workspace/state/claude_runs.db`:

```sql
CREATE TABLE runs (
    ts          TEXT,
    bot         TEXT,
    model       TEXT,
    duration_ms INTEGER,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    cost_usd    REAL,
    ok          INTEGER,
    error       TEXT
);
```

Query example: `SELECT bot, SUM(cost_usd) FROM runs WHERE ts > '2026-05-01' GROUP BY bot;`

---

## Migration Plan (existing jobs → bots/)

Existing scripts are preserved as-is during migration; bots are thin wrappers that call through to them:

| Current | New |
|---|---|
| `newsparser/scripts/run_cycle.py` | `newsparser/bots/cycle/bot.py` (calls existing script logic) |
| `newsparser/scripts/run_weekly.py` | `newsparser/bots/weekly/bot.py` |
| `newsparser/scripts/run_reflect.py` | `newsparser/bots/reflect/bot.py` |
| `newsparser/scripts/fetch_market_daily.py` | `newsparser/bots/market_daily/bot.py` |
| `newsparser/bot/telegram_bot.py` | `newsparser/bots/tracker/bot.py` |

Scripts under `newsparser/scripts/` are kept intact — the bots call them. Gradual internal cleanup is separate work.

---

## What Requires Human Intervention

Only one thing:

| Action | Why |
|---|---|
| `CLAUDE_CODE_OAUTH_TOKEN` rotation | Requires `claude setup-token` on a machine with an active Claude login session |

Everything else — new bots, prompt changes, config edits, package additions, container rebuilds — can be initiated via Telegram chat.

---

## Out of Scope

- Neo4j schema migrations (Cypher DDL) — separate concern, not automated
- Multi-user / access control — single-user setup
- GitHub integration — not planned
- External log aggregation (Loki, CloudWatch) — json-file + rotation is sufficient
