# Newsparser — Developer Notes

Context for working on this codebase with Claude Code.

---

## Architecture

- Python handles all I/O, scheduling, DB, Telegram, and Neo4j.
- Claude is invoked headless via CLI subprocess (`claude -p ...`) — see `newsparser/claude/runner.py`. Do not switch to the Anthropic API directly.
- `CLAUDE.md` is auto-loaded by every `claude -p` call from the project root and acts as the system prompt — keep it minimal (role + style).
- Slash command specs live in `.claude/commands/`:
  - `.claude/commands/cycle.md` — cycle analysis (reads input file, writes report, calls helper scripts via Bash)
  - `.claude/commands/weekly.md` — weekly briefing synthesis
  - `.claude/commands/reflect.md` — interest profile update
- Outer coordinators (`newsparser/scripts/run_cycle.py`, `run_weekly.py`, `run_reflect.py`) build input files and call `run_claude("/cycle …")`.
- Helper scripts (`newsparser/scripts/apply_graph.py`, `mark_processed.py`) are called by Claude via Bash tool inside slash commands.
- MCP transport is stdio — spawned per `claude -p` call, no persistent container. Config: `mcp.json`.
- The `/tracker` flow is different: `newsparser/bot/tracker.py` builds its prompt inline and uses MCP tools via `mcp.json`.

---

## Development Environment

- Python runtime: `.venv/` created by `uv`. Always use `.venv/bin/python` and `.venv/bin/pytest`.
- Never use `uv run python` or `uv run pytest` — invoke the venv binaries directly.
- Example: `.venv/bin/pytest tests/ -v`

---

## Slash Command Behavior (runtime reference)

What `/cycle` does at runtime is defined by `.claude/commands/cycle.md` — that is the source of truth.

`/tracker` is the catch-all for free-text Telegram messages. The bot dispatcher in `newsparser/bot/dispatcher.py` routes anything that isn't `/cycle`, `/weekly`, or `/reflect` to `run_tracker()`. The tracker prompt and tool list live in `newsparser/bot/tracker.py`.
