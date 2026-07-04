# Newsparser — Developer Notes

Context for working on this codebase with Claude Code.

---

## Architecture

- Python handles all I/O, scheduling, DB, Telegram, and Neo4j.
- Claude is invoked headless via CLI subprocess (`claude -p ...`) — see `newsparser/claude/runner.py`. Do not switch to the Anthropic API directly.
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
  news-tainted runs (cycle/reflect/weekly, classifier, resolver) get `permission_mode="default"`
  + allowlists; only the tracker (trusted telegram input) runs `bypassPermissions`. Denied tool
  calls are logged by `runner.py` and surface in `workspace/jobs.json` under `activity.denials`.
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

## State & Backups

- All non-git runtime state = SQLite DBs + docs under `workspace/`, the Neo4j graph (docker volume), and `.env`. `backup.sh` / `restore.sh` snapshot and rebuild this as one gzip archive; see README "Backup & Restore".
- `backup.sh` snapshots **every** `*.db` under `workspace/` automatically (via `find`), so adding a new SQLite DB there needs no script change.
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
