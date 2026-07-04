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

Three Compose services: `neo4j`, `poller` (`python -m newsparser.collector.run_poller`, a
continuous 600s loop), `dispatcher` (`python -m newsparser.dispatcher`). `docker compose up -d`
is the whole deploy — there is no `run.sh` and no system cron. See README "Deployment".

Gotchas that bite, none obvious from the file tree:

- **The real entrypoint is `newsparser/dispatcher.py`.** Two decoys ship in the image but are
  unwired — don't run them: `newsparser/bot/dispatcher.py` (a dead `classify_message` enum) and
  `newsparser/bot/telegram_bot.py` (a stale `__main__` launcher).
- **All scheduling is APScheduler inside the dispatcher** (PTB JobQueue + `CronTrigger`), not
  system cron. Cron strings + `tz="Asia/Seoul"` live in each `newsparser/bots/*/bot.py` `Cron(...)`;
  `dispatcher._register_cron_jobs` registers them. Drop a `*/bot.py` with a `Cron` trigger and
  `registry.load()` globs it in (`/reload` re-globs at runtime).
- **The image bakes its own `.venv`, but not `mcp.json`/`.claude/`/`CLAUDE.md`.** The Dockerfile
  builds `.venv` at image build time (`uv sync --frozen`) and copies `newsparser/` + `sources.md` —
  fully self-contained for the interpreter, independent of the host. `mcp.json`, `.claude/`
  (commands + settings.json + hooks), and `CLAUDE.md` are excluded by `.dockerignore` and reach the
  dispatcher only via individual bind mounts in `docker-compose.yml`
  (`./mcp.json`, `./.claude`, `./CLAUDE.md`) — there's no `.:/app` full-repo mount (dropped since
  PR #12's "ghcr로 경로 변경" / "배포방식 개선"). The poller mounts only `./workspace`, so it must
  never need those files. Running the dispatcher image standalone (without those three mounts)
  breaks the tracker's MCP tools and the `/cycle` `/reflect` `/weekly` slash commands.
- **Host `.venv` is NOT needed for deploy.** Before PR #12 the dispatcher bind-mounted the whole
  repo (`.:/app`), which shadowed the image's baked `/app/.venv` with the host one — a copied
  `.venv` with a dangling interpreter symlink would keep the dispatcher (and its
  `.venv/bin/python -m newsparser.mcp_server` MCP server) from starting, so `uv sync` on the deploy
  host was mandatory. That full mount is gone now, so the image's own venv is what runs in both
  services. `uv sync` on the host is only for local dev (pytest, ad hoc scripts) — see
  "Development Environment" above.
- **`IS_SANDBOX=1` is required, not optional.** The container runs as root and calls `claude` with
  `bypassPermissions`; the CLI hard-exits under root unless `IS_SANDBOX=1`.
- The dispatcher drives the **host** Docker daemon via the mounted `/var/run/docker.sock`
  (`docker.io` is baked in): the `service_status` / `restart_service` / `tail_logs` MCP tools and
  `/rebuild` shell out to `docker`/`docker compose`. They no-op if the socket or CLI is absent.
- Auth is the env token (`CLAUDE_CODE_OAUTH_TOKEN` from `claude setup-token`), so in-container
  `~/.claude` is ephemeral and fine to lose. Persistent state is exactly `neo4j_data` + `workspace/`
  (see State & Backups below).

### Known deploy gaps (verified, not yet fixed)

- **`TELEGRAM_CHAT_ID` missing from `.env.example`.** `newsparser/bot/sender.py` reads it via
  `os.environ["TELEGRAM_CHAT_ID"]` for all report/alert delivery, but the template ships only
  `ALLOWED_CHAT_ID` (inbound auth gate) + `TELEGRAM_ALERT_CHAT_ID`. Every send site is `try/except`,
  so the system looks alive but delivers nothing. Add it to `.env.example`.
- **`IS_SANDBOX` ships blank** in `.env.example` while the docs' old auto-export (`run.sh`) is gone
  — must be set to `1` by hand or every `claude -p` call exits 1.
- **`neo4j` has no `restart:` policy** while poller/dispatcher are `restart: unless-stopped`. After
  a host/daemon reboot neo4j stays down and the apps crash-loop against an unreachable bolt. Add
  `restart: unless-stopped` to neo4j in `docker-compose.yml`.
- **`.claude/hooks` are dead.** `block_env_read.py` / `block_secret_bash.py` are never registered
  under a `hooks` key in `settings.json`, so with `bypassPermissions` the .env-leak guard is off.
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
