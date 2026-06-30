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

All jobs run inside the `dispatcher` service (APScheduler via the python-telegram-bot JobQueue) — there is no system crontab. Cron strings live in each `newsparser/bots/*/bot.py`. Times are KST.

| Schedule | Job |
|----------|-----|
| Continuous (600s; `POLL_INTERVAL_SECONDS`) | `poller` — collect news + fire breaking/spike alerts |
| 00 / 06 / 12 / 18 daily | `cycle`, per category (tech + markets): classify pending → cycle report → update graph → post to Telegram |
| 07:30 daily | `market_daily` — market / FX snapshot |
| Mon 09:00 | `weekly` rollup |
| Sun 21:00 | `reflect` |

---

## Storage

All non-graph state lives under `workspace/` (a host bind mount); the graph lives in the `neo4j_data` Docker volume. Both persist across container recreation — nothing else does. Back up with `./backup.sh`.

| Layer | Path | Override |
|---|---|---|
| Articles + cycle queue | `workspace/newsparser.db` | `DB_PATH` |
| Market OHLCV / FX | `workspace/market.db` | `MARKET_DB_PATH` |
| Claude run / cost ledger | `workspace/state/claude_runs.db` | (via `WORKSPACE_DIR`) |
| Cycle reports | `workspace/cycles/{tech,markets}/{slot}.md` | `WORKSPACE_DIR` |
| Interest profiles | `workspace/me/interests_{tech,markets}.md` | `WORKSPACE_DIR` |
| Knowledge graph | Neo4j (`neo4j_data` volume; auth via `NEO4J_PASSWORD`) | `NEO4J_*` |

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

Run `./backup.sh -h` / `./restore.sh -h` for all flags. Moving the whole system to a new
host? See `migration.md`.

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

## Deployment

Runs as three Docker Compose services: `neo4j`, `poller` (continuous collection),
and `dispatcher` (Telegram bot + every scheduled job). No `run.sh`, no system cron —
`docker compose up -d` is the whole deploy.

```bash
cp .env.example .env        # then fill in every value (table below)
uv sync                     # build ./.venv ON THIS HOST — see note
docker compose up -d        # neo4j + poller + dispatcher
```

`uv sync` on the deploy host is mandatory. The dispatcher bind-mounts the repo at
`/app`, so it runs the host's `./.venv/bin/python`, not the image's baked venv. A
`.venv` copied from another machine has a dangling interpreter symlink and the
dispatcher won't start — always rebuild it on the host where you deploy.

### Required `.env` values

| Var | Purpose |
|---|---|
| `TELEGRAM_BOT_TOKEN` | BotFather token |
| `TELEGRAM_CHAT_ID` | where reports/alerts are **sent** (without it the bot runs but delivers nothing) |
| `ALLOWED_CHAT_ID` | only this chat may command the bot (inbound auth gate) |
| `TELEGRAM_ALERT_CHAT_ID` | target for poller alerts |
| `CLAUDE_CODE_OAUTH_TOKEN` | from `claude setup-token` (see Authentication) — the only Anthropic auth |
| `NEO4J_PASSWORD` | set **before** the first `up`; neo4j bakes it into `neo4j_data`, so changing it later means resetting that volume |
| `IS_SANDBOX=1` | **required** — the container runs `claude` as root with `bypassPermissions`, and the CLI refuses root unless this is `1` |

Optional overrides (safe defaults, leave unset for a standard deploy): `CLAUDE_BIN`
(`claude`), `NEO4J_URI` (compose sets `bolt://neo4j:7687`), `NEO4J_USER` (`neo4j`),
`DB_PATH`, `MARKET_DB_PATH`, `WORKSPACE_DIR`, `POLL_INTERVAL_SECONDS` (`600`).

### One-time manual steps

1. Create the bot via @BotFather → `TELEGRAM_BOT_TOKEN`; message it once to learn your numeric chat id.
2. Choose `NEO4J_PASSWORD` before the first boot.
3. Run `claude setup-token` (Authentication section) → `CLAUDE_CODE_OAUTH_TOKEN`.
4. Set `IS_SANDBOX=1`.

### Host prerequisites

- Docker Engine + Docker Compose v2 (the space form `docker compose`).
- The dispatcher controls the host Docker daemon through the mounted
  `/var/run/docker.sock` (the `/rebuild` and service status / restart / logs ops
  tools). Keep that mount.
- Outbound HTTPS to: `api.anthropic.com` / `claude.ai`, `api.telegram.org`, the RSS
  + article source domains, and `query*.finance.yahoo.com` (yfinance). Every data
  source is keyless — the only secrets are the three tokens/password above.
- Disk for the `neo4j_data` volume, `./workspace`, and `backups/` (no auto-retention).

---

## Authentication (headless)

Claude is invoked as a subprocess (`claude -p`) by the scheduled jobs and the Telegram bot.
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

### 3. Ensure the containers see the variable

Both app services load `.env` via `env_file:` in `docker-compose.yml`, so once the token
is in `.env` it reaches every `claude -p` call automatically — no extra wiring. `docker compose up -d`
(or `/rebuild` from Telegram) picks up a changed value on the next start.

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
