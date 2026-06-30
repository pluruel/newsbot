# Newsparser

Personal market intelligence system. Collects and analyzes news, posts cycle reports to Telegram, and answers queries against a knowledge graph.

---

## Telegram Bot

Just send a message. Claude uses the knowledge graph and recent cycle reports to answer.

### Queries

```
How is Samsung doing lately?
What's the impact of Fed rate decisions on semiconductors?
Compare recent trends for Nvidia and AMD
```

### Interest weights

Tracked for query weighting and graph traversal.

```
Lower my AI interest weight
Add semiconductors theme with interest 0.8
Show my current interests
Show weight comparison        ← configured weights vs actual query frequency
```

### Manifesto

How the system understands your perspective — reflected in tracker tone.

```
Show my manifesto
I'm a semiconductor investor focused on risk — update accordingly
```

### Reset

```
Clear interest events         ← resets weight estimation baseline
Clear conversation history
```

---

## Automated jobs

| Schedule | Action |
|----------|--------|
| Hourly | Collect news |
| 00/06/12/18 KST | Per category (tech / markets): classify pending → cycle report → update graph → post to Telegram |

---

## Storage

| Layer | Path | Override |
|---|---|---|
| Articles + cycle queue | `workspace/newsparser.db` | `DB_PATH` env var |
| Cycle reports | `workspace/cycles/{tech,markets}/{slot}.md` | `WORKSPACE_DIR` env var |
| Interest profiles | `workspace/me/interests_{tech,markets}.md` | `WORKSPACE_DIR` env var |
| Knowledge graph | Neo4j (configured via `NEO4J_PASSWORD`) | — |

---

## Backup & Restore

All accumulated state lives outside git, so back it up regularly.

```bash
./backup.sh                 # one gzip snapshot -> backups/newsparser-backup-<ts>.tar.gz (+ .sha256)
./restore.sh                # restore the newest archive in backups/
./restore.sh path/to.tar.gz # restore a specific archive
```

`backup.sh` captures, in a single archive:

- every SQLite DB under `workspace/` (`newsparser.db`, `market.db`, `state/claude_runs.db`, …) via a
  **consistent online snapshot** — safe to run while the poller/dispatcher are writing
- all runtime documents (`cycles/`, `briefs/`, `me/`, `input/`, `logs/`, `sessions/`)
- the Neo4j knowledge graph (`neo4j-admin database dump`, best effort — needs docker)
- `.env` (so a blank checkout runs identically). Pass `--no-secrets` to exclude it; **keep archives private**.

`restore.sh` rebuilds that state into a fresh checkout. It prompts before overwriting existing data
(`-y` to skip), takes a pre-restore safety snapshot, restores `.env` only if missing (`--restore-env`
to overwrite), and loads the Neo4j graph when docker is available. Then:

```bash
uv sync && docker compose up -d   # install deps, start neo4j + poller + dispatcher
```

Run `./backup.sh -h` / `./restore.sh -h` for all flags.

---

## Architecture

```
Telegram message
    └─ tracker → Claude (sonnet) + MCP
                     ├─ graph_query          3-hop knowledge graph lookup
                     ├─ read_cycle_reports   recent cycle reports
                     ├─ get_interest_weights configured vs estimated comparison
                     ├─ read/write_interests update interest profile
                     ├─ read/write_manifesto update perspective
                     └─ clear_*             reset logs and history
```

---

## Running

```bash
cp .env.example .env
./run.sh
```

Required env vars: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `NEO4J_PASSWORD`

`IS_SANDBOX=1` must be set before running if you want sandbox mode — `run.sh` exports it automatically, but set it manually if invoking the bot or scripts directly.
---

## Authentication (headless / cron)

Claude is invoked as a subprocess (`claude -p`) by cron jobs and the Telegram bot.
The default OAuth flow stores a short-lived access token (~8 hours) in `~/.claude/.credentials.json`.
In an always-on environment this causes intermittent 401 failures as tokens expire and concurrent calls race to refresh them.

**Fix: issue a long-lived OAuth token once, then inject it as an env var.**
This keeps your Claude subscription (no API billing switch) and eliminates the refresh cycle entirely.

### 1. Issue the token (interactive terminal, one-time)

```bash
claude setup-token
```

Follow the browser login prompt. On success, the CLI prints a `sk-ant-oat01-...` token.

### 2. Add to `.env`

```
CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-...
```

Keep `.env` mode `600` and confirm it is in `.gitignore`.

### 3. Ensure cron sees the variable

`run.sh` installs the cron block automatically. The block sources no shell profile by default,
so the variable must be set in the crontab header. `run.sh` already writes a `PATH=` line to
the crontab; add `CLAUDE_CODE_OAUTH_TOKEN` the same way, or export it from a wrapper script
that sources `.env` before calling the Python entry points.

### 4. (Optional) Remove the stale credentials file

Once the env var is in place, the CLI no longer needs the credentials file:

```bash
rm /root/.claude/.credentials.json
```

If you prefer to leave it, the env var takes precedence.

### Token lifetime

Long-lived tokens do not rotate automatically but can be revoked (manual revoke, subscription
expiry, or a security event). If calls start failing with auth errors again, re-run
`claude setup-token` and update `.env`.
