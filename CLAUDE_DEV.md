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

What `/cycle` does at runtime is defined by `.claude/commands/cycle.md` — that is the source of truth, not this file.

`/tracker` is the catch-all for free-text Telegram messages. The bot dispatcher in `newsparser/bot/dispatcher.py` routes anything that isn't `/cycle`, `/weekly`, or `/reflect` to `run_tracker()`. The tracker prompt and tool list live in `newsparser/bot/tracker.py`.
