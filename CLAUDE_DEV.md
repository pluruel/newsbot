# Newsparser — Developer Notes

Context for working on this codebase with Claude Code.

---

## Architecture

- Python handles all I/O, scheduling, DB, Telegram, and Neo4j.
- Claude is invoked headless via CLI subprocess (`claude -p ...`) — see `newsparser/claude/runner.py`. Do not switch to the Anthropic API directly.
- `CLAUDE.md` is auto-loaded by every `claude -p` call from the project root and acts as the system prompt — keep it minimal (role + style).
- Per-task instructions are injected by the Python caller from `prompts/`:
  - `prompts/cycle.md` — `/cycle` analysis spec, read by `newsparser/scheduler/cycle.py`.
- The `/tracker` flow is different: `newsparser/bot/tracker.py` builds its prompt inline and uses MCP tools via `mcp.json`.

---

## Development Environment

- Python runtime: `.venv/` created by `uv`. Always use `.venv/bin/python` and `.venv/bin/pytest`.
- Never use `uv run python` or `uv run pytest` — invoke the venv binaries directly.
- Example: `.venv/bin/pytest tests/ -v`

---

## Slash Command Behavior (runtime reference)

What `/cycle` does at runtime is defined by `prompts/cycle.md` — that is the source of truth, not this file.

`/tracker` is the catch-all for free-text Telegram messages. The bot dispatcher in `newsparser/bot/dispatcher.py` routes anything that isn't `/cycle`, `/weekly`, or `/reflect` to `run_tracker()`. The tracker prompt and tool list live in `newsparser/bot/tracker.py`.
