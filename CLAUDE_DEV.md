# Newsparser — Developer Notes

Context for working on this codebase with Claude Code.

---

## Architecture

- Python handles all I/O, scheduling, DB, Telegram, and Neo4j.
- Claude is invoked headless via CLI subprocess (`claude -p ...`) — see `newsparser/claude/runner.py`. Do not switch to the Anthropic API directly, **except** for the short tool-less Haiku calls, which go through `newsparser/claude/haiku.py` (`ask_haiku`). Anything that needs a tool, MCP, a session, or a slash command stays on `run_claude`.
  - Why the exception: a `claude -p` round trip is 5-8s regardless of prompt size — each one prefills ~21k tokens of Claude Code scaffolding and spends 200-1100 output tokens thinking before emitting one word, and no CLI flag disables either (`--effort low` does not move it). The same call over `/v1/messages` is ~0.9s. The tracker paid this twice serially before the user's answer even started.
  - `ask_haiku` raises `ClaudeError` — the same type `runner.py` raises — so call sites keep one `except (ClaudeError, RuntimeError, OSError)` fallback whichever path they use.
  - Auth reuses the CLI's own `CLAUDE_CODE_OAUTH_TOKEN`, which authenticates against `/v1/messages` as `Authorization: Bearer` (the SDK's `auth_token=`) but **401s as `x-api-key`**. `ANTHROPIC_API_KEY` overrides it when a host has a real key. No new secret to provision.
  - **Every `ask_haiku` call site passes `usage_tag=`** — usage accumulates in the
    `haiku_usage` table (per UTC day × tag) and is exposed by the `haiku_usage` MCP tool.
    The tag list is *enumerated by hand in two places* — `mcp_server.py`'s `haiku_usage`
    docstring and `CLAUDE.md`'s MCP list — so adding/renaming a call site means updating
    both, or they drift silently (`classify_article` was missed once already). Current
    tags: triage, classify_article, classify_query, market_headlines, graph_resolver,
    tracker_depth. Note `classify_article` has no pipeline callers anymore
    (`triage.triage_article` replaced it in `run_cycle`); it stays as a tested utility.
  - **Article triage is `newsparser/triage.py`**: the bucket axis is fixed in code; weights
    are runtime state at `workspace/me/triage_weights_{category}.json`, written weekly by
    `/reflect` (absent file → 1.0 per bucket, i.e. pure salience ranking). Haiku returns
    only `(bucket, salience)`; the score `weight × salience` is multiplied in Python at
    cycle selection time. **Keep it that way** — putting weights into the prompt would make
    model behavior shift with every weekly refresh and lose retroactive re-scoring of the
    pending queue. The poller tags per pass (row cap + wall-clock budget, after alert
    handling); every failure is fail-open (untriaged rows score `DEFAULT_SCORE` and stay
    queued). `/reflect` can't run the module (no Bash in its tool policy), so
    `run_reflect.py` snapshots the axis to `workspace/me/triage-buckets.json` pre-run.
  - **`ask_haiku` pins `max_retries=0`.** The SDK defaults to 2, which would turn `timeout` into a per-attempt bound instead of the wall-clock ceiling `run_claude`'s `threading.Timer` kill gave every call site — and timeouts are themselves retryable (`APITimeoutError` subclasses `APIConnectionError`). It also compounds: `resolver.py` and `scripts/audit_duplicates.py` already wrap their call in a 3-attempt backoff loop, so the default would make those 9 HTTP requests and stretch the resolver's worst case from ~183s to ~548s. Re-enable SDK retries only per call site, and divide the declared timeout when you do.
- `CLAUDE.md` is auto-loaded by every `claude -p` call from the project root and acts as the system prompt — keep it minimal (role + style).
- Slash command specs live in `.claude/commands/` (auto-loaded per `claude -p` call):
  - `.claude/commands/cycle.md` — `/cycle` analysis spec, invoked by `newsparser/scripts/run_cycle.py`.
- The `/tracker` flow is different: `newsparser/bot/tracker.py` builds its prompt inline and uses MCP tools via `mcp.json`.

---

## Development Environment

- Python runtime: `.venv/` created by `uv`. Always use `.venv/bin/python` and `.venv/bin/pytest`.
- Never use `uv run python` or `uv run pytest` — invoke the venv binaries directly.
- Example: `.venv/bin/pytest tests/ -v`

---

## Deployment

Two host systemd units — `newsbot-poller` (`python -m newsparser.collector.run_poller`, a
continuous 600s loop) and `newsbot-dispatcher` (`python -m newsparser.dispatcher`) — plus one
container: `neo4j` (the only compose service, ports bound to 127.0.0.1). Provisioning is
`sudo ./deploy/install.sh`; there is no Dockerfile, no image build, no system cron.
See README "Deployment" and `plan-host-migration.md`.

Gotchas that bite, none obvious from the file tree:

- **The real entrypoint is `newsparser/dispatcher.py`.** A decoy ships in the tree but is
  unwired — don't run it: `newsparser/bot/dispatcher.py` (a dead `classify_message` enum).
- **All scheduling is APScheduler inside the dispatcher** (PTB JobQueue + `CronTrigger`), not
  system cron. Cron strings + `tz="Asia/Seoul"` live in each `newsparser/bots/*/bot.py` `Cron(...)`;
  `dispatcher._register_cron_jobs` registers them. Drop a `*/bot.py` with a `Cron` trigger and
  `registry.load()` globs it in (`/reload` re-globs at runtime).
- **Everything runs the host `.venv` directly** (`WorkingDirectory=<repo>`, `ExecStart=
  <repo>/.venv/bin/python`), so `uv sync` on the deploy host is mandatory and code changes are
  live after a unit restart — no rebuild step. `mcp.json`'s `.venv/bin/python` is relative, which
  is why `WorkingDirectory` must stay the repo root.
- **Privileged ops go through `/usr/local/sbin/newsbot-ops`** (root-owned, sudoers NOPASSWD,
  installed by `deploy/install.sh`): the `service_status` / `restart_service` / `tail_logs` MCP
  tools shell out to `sudo -n newsbot-ops`. The repo copy `deploy/newsbot-ops` is a template —
  editing it does nothing until a human re-runs install.sh (that's the security gate).
  `restart dispatcher` is detached (~5s delay + import guard) so a claude run can trigger it and
  still deliver its reply.
- **systemd gives units a minimal PATH** — the dispatcher unit sets `CLAUDE_BIN` (wired by
  install.sh) so `runner.py` finds the CLI.
- **Headless tool policy lives in `newsparser/claude/policy.py`** (see `plan-tool-policy.md`):
  news-tainted runs (cycle/reflect/weekly) get `permission_mode="default"` + allowlists; only the
  tracker (trusted telegram input) runs `bypassPermissions`. Denied tool calls are logged by
  `runner.py` and surface in `workspace/jobs.json` under `activity.denials`. The classifier,
  resolver, and headline picker are no longer on this list — they moved to `ask_haiku`, which has
  no tool surface to police at all, so their taint is handled by construction rather than policy.
- Auth is the env token (`CLAUDE_CODE_OAUTH_TOKEN` from `claude setup-token`) loaded via
  `EnvironmentFile=.env`. Persistent state is exactly `neo4j_data` + `workspace/`
  (see State & Backups below).

### Known deploy gaps (verified, not yet fixed)

- **`TELEGRAM_CHAT_ID` missing from `.env.example`.** `newsparser/bot/sender.py` reads it via
  `os.environ["TELEGRAM_CHAT_ID"]` for all report/alert delivery, but the template ships only
  `ALLOWED_CHAT_ID` (inbound auth gate) + `TELEGRAM_ALERT_CHAT_ID`. Every send site is `try/except`,
  so the system looks alive but delivers nothing. Add it to `.env.example`.
- **`.claude/hooks` are dead.** `block_env_read.py` / `block_secret_bash.py` are never registered
  under a `hooks` key in `settings.json`. Partially superseded by the `permissions.deny` Read rules
  (`.env`, `~/.claude`, backups), but registering them would add a second layer for Bash-based leaks.
- **`.gitignore` omits `workspace/market.db` and `workspace/state/`** (only `newsparser.db` is
  ignored) — risk of committing binary state.
- Minor: dispatcher/poller call `init_db()` but not `ensure_workspace()`, so `me/` interest /
  manifesto / ignore templates aren't seeded until the first `/cycle` (non-crash — writers self-mkdir).

---

## Market data

Two resolutions, two writers, one table:

- **Daily bars** — `market_daily`, written by the `market_daily` cron bot (07:30 KST).
- **Intraday bars** — `market_intraday`, keyed `(instrument, interval, ts)`. The `interval`
  column is load-bearing: it was added when 15m bars arrived because the old
  `(instrument, ts)` key silently merged resolutions, which would make `annotate.py`'s ±60m
  before/after lookup pick a 15m bar as "the previous hour". **Any new resolution must pass
  `interval=` to `upsert_intraday`/`get_intraday`** — both default to `1h` so the pre-existing
  callers (`annotate.py`, the `market_query` MCP tool) keep seeing exactly the hourly series.

**Intraday volatility alerts live in `newsparser/collector/run_poller.py`, not in a
`bots/*/bot.py` cron.** Two reasons, both easy to get wrong: the alert needs the headline
window to be as fresh as possible, so it runs immediately after articles land; and every cron
bot goes through the JobManager, whose recent-job list caps at 10 (`jobs.py:26`) — a 5-minute
bot would evict the cycle/weekly history that `job_status` exists to show. `MARKET_PULSE=0`
disables it.

Detection is in `newsparser/market/pulse.py` and fires on `z > 3.0 AND |return| ≥ rolling p99`.
Both halves are needed and the constants are measured, not guessed (60 days of real 15m bars,
all eight instruments): z alone fires 7.2×/day with over half of it thin-session FX noise; a
fixed percentage floor alone misses regime shifts. Together they land at ~3.4 alerts/day. A
60-minute cooldown was measured too and removed only a further 0.3/day, so it is deliberately
absent. **Re-measure before touching these numbers.** Headline attachment
(`newsparser/market/headlines.py`) is one haiku call that returns *indices only* — the message
is rendered from the DB rows those indices point at, never from model prose, same rule
`run_cycle.py:304` follows.

Gotcha: yfinance has **no 10m interval** (valid: 1m/2m/5m/15m/30m/60m/90m/1h/4h/1d…), and
sub-hourly history only reaches back ~60 days — which is why `market_pulse` rows are the
durable record of what fired, not the bars themselves.

---

## State & Backups

- All non-git runtime state = SQLite DBs + docs under `workspace/`, the Neo4j graph (docker volume), and `.env`. `backup.sh` / `restore.sh` snapshot and rebuild this as one gzip archive; see README "Backup & Restore".
- **Conversation history is `workspace/conversations.db`** (SQLite, `newsparser/store/conversations.py`), separate from the article DB `newsparser.db` and `market.db`. It replaced the old `workspace/sessions/*.jsonl`; one turn per row with a `reply_to_id` DAG edge, WAL-mode (concurrent tracker writes + reproject/demand scans), and a trigram `messages_fts` index for the `search_conversations` recall tool. It also holds `interest_events` (the conversation-derived demand signal, ex-`me/interest-events.jsonl`). Best-effort mirrored into Neo4j by `newsparser/graph/conversation_projector.py` (`(:Message)-[:REPLIES_TO]/[:IN_CHAT]/[:MENTIONS]`); Neo4j is a derived projection (`reproject_all()` rebuilds it), never the source of truth. Clearing history (`clear_conversation_history`) purges both SQLite and the graph.
- **Legacy migration:** `newsparser/scripts/migrate_conversations.py` folds the old `sessions/*.jsonl` + `me/interest-events.jsonl` into `conversations.db` (idempotent, renames sources to `*.migrated`). Run once on deploy, from the repo root so the default `WORKSPACE_DIR` matches the unit; then `reproject_all()` to include the migrated turns in the graph. See README "Deployment".
- `backup.sh` snapshots **every** `*.db` under `workspace/` automatically (via `find`), so adding a new SQLite DB there needs no script change (`conversations.db` is covered with no script change).
- **When the storage layout changes, update `backup.sh` AND `restore.sh` together, then re-verify.** Required whenever a change:
  - adds state **outside** `workspace/`, or a new backend/volume (another graph store, Redis, a second docker volume);
  - moves/renames a DB or changes a path convention (`DB_PATH`, `MARKET_DB_PATH`, `WORKSPACE_DIR`);
  - adds new transient files to exclude, or new secrets beyond `.env`.
- Re-verify after such a change (snapshot → restore into a throwaway dir → diff row counts):
  `WORKSPACE_DIR=/tmp/ws ./backup.sh -o /tmp/bk && WORKSPACE_DIR=/tmp/ws2 ./restore.sh -y /tmp/bk/newsparser-backup-*.tar.gz`

---

## Slash Command Behavior (runtime reference)

`/cycle`, `/weekly`, `/reflect` are **not** user-typed Telegram commands — they are prompts the
scheduled jobs hand to `claude -p` (e.g. `run_cycle.py` runs `run_claude("/cycle {slot} {category}")`),
resolved from `.claude/commands/*.md`. That `.md` is the source of truth for behavior, not this file.

Inbound Telegram routing lives in `newsparser/dispatcher.py`: `registry.telegram_bots()` regex-matches
each bot's `TelegramMatch` trigger against the message; the tracker bot's catch-all `.*` (sorted last)
handles everything else. The only true user-typed commands are `/reload` (rebuild the bot registry — a
`CommandHandler` in `dispatcher.py`) and `/rebuild` (rebuild the image — special-cased inside
`newsparser/bots/tracker/bot.py`). The tracker prompt and MCP tool list live in `newsparser/bot/tracker.py`
(invoked via `bots/tracker/bot.py`); `newsparser/bot/dispatcher.py`'s `classify_message` is a dead leftover.
