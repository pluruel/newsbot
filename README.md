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
| Continuous (300s; `POLL_INTERVAL_SECONDS`) | `poller` — collect news + fire breaking/spike alerts + market volatility alerts (`MARKET_PULSE=0` to disable) |
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
uv sync && docker compose up -d && sudo ./deploy/install.sh \
  && sudo systemctl start newsbot-poller newsbot-dispatcher
```

Run `./backup.sh -h` / `./restore.sh -h` for all flags. Moving the whole system to a new
host? See `migration.md`.

---

## Graph maintenance — entity dedup

The cycle resolver prevents most fragmentation at write time, but the graph can still
accumulate near-duplicate entities (the same real-world thing under variant names or
labels: `NVIDIA`/`Nvidia`, `Citi`/`Citigroup`, Company vs Institution). Four scripts
under `scripts/` clean this up. **Every graph write is dry-run by default — `--apply`
is required to mutate, and you should `./backup.sh` first.** Labels are matched as query
parameters (no Cypher injection); names/labels never string-interpolated except merge's
MERGE.

| Script | Input JSON | Does |
|---|---|---|
| `audit_duplicates.py` | — (emits with `--out`) | scan the live graph for duplicate candidates (rule-based), optionally Haiku-confirm SAME/DIFF |
| `merge_entities.py` | `merges.json` | fold the `from` node into `to` — moves relations/aliases/mention_count, deletes `from` |
| `alias_cleanup.py` | `alias_cleanup.json` | strip wrongly-attached aliases from a node (so the resolver won't re-merge things you kept apart) |
| `apply_renames.py` | `renames.json` | change a node's `canonical_name` (old name preserved as an alias) |

Merge JSON is `[{"from_name","from_label","to_name","to_label"}]`; renames
`[{"name","label","new_name"}]`; alias cleanup `[{"name","label","remove_aliases"}]`.

### Workflow

```bash
# 1. detect + let Haiku confirm same-vs-distinct → confirmed pairs only (needs CLAUDE token)
.venv/bin/python -m scripts.audit_duplicates --out merges.json
#    --no-llm skips Haiku and emits every rule candidate for pure hand review

# 2. review merges.json by hand: drop parent/subsidiary or distinct-event pairs,
#    collapse chains to one survivor per cluster, prefer spaced canonical names.
#    (hand-build renames.json / alias_cleanup.json for any renames + alias fixes.)

# 3. snapshot, then apply IN THIS ORDER:
./backup.sh
.venv/bin/python -m scripts.merge_entities merges.json           # dry-run preview
.venv/bin/python -m scripts.merge_entities merges.json --apply    # ① merge duplicates
.venv/bin/python -m scripts.alias_cleanup  alias_cleanup.json --apply  # ② BEFORE renames
.venv/bin/python -m scripts.apply_renames  renames.json --apply        # ③ AFTER merges
```

Order matters: alias cleanup keys on the pre-rename names, and renames target the nodes
merges just consolidated onto. The `*.json` are one-off inputs — not committed; delete
them after applying.

### Verify (read-only — no graph writes)

```bash
# re-scan the live graph; the candidate count should drop sharply after cleanup
.venv/bin/python -m scripts.audit_duplicates --no-llm 2>&1 >/dev/null | grep "candidate pair"
```

Remaining candidates are expected to include pairs you deliberately kept separate (the
rules re-flag them every run) and Haiku-DIFF distinct pairs — not necessarily unfixed
duplicates. To isolate genuinely-same leftovers, run `audit_duplicates.py --out round2.json`
and let Haiku filter the residual for a second round.

Recurring events (visits, summits, negotiations) use `YYYY-MM` canonical names by default
(`.claude/commands/cycle.md`) to avoid per-day fragmentation.

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

Two host systemd units — `newsbot-poller` (continuous collection) and
`newsbot-dispatcher` (Telegram bot + every scheduled job + claude subprocesses) —
plus one container: `neo4j`. See `plan-host-migration.md` for the rationale.

```bash
cp .env.example .env        # then fill in every value (table below)
uv sync                     # build ./.venv ON THIS HOST — units run it directly
docker compose up -d        # neo4j only (ports bind to 127.0.0.1)
.venv/bin/python -m newsparser.scripts.migrate_conversations   # one-time: fold legacy sessions/*.jsonl + interest-events.jsonl into conversations.db (idempotent, no-op once done)
sudo ./deploy/install.sh    # systemd units + /usr/local/sbin/newsbot-ops + sudoers
sudo systemctl start newsbot-poller newsbot-dispatcher
```

The migration step is idempotent and self-skipping (it renames its sources to
`*.migrated`), so it is safe to leave in a redeploy script; on a host with no legacy
JSONL it is a no-op. Neo4j is a derived projection — after the store is migrated and
Neo4j is up, `reproject_all()` rebuilds the conversation subgraph from SQLite.

`install.sh` validates `.env` before installing anything and exits with an error if
`ALLOWED_CHAT_ID` is missing or a compose-era `NEO4J_URI=bolt://neo4j:7687` is still
present — fix `.env` and re-run. The dispatcher unit waits for the neo4j bolt port
(`ExecStartPre`, up to 120s) before starting, so `systemctl start` right after
`docker compose up -d` is safe even on a cold boot.

Code deploys are `git pull && uv sync` + `sudo -n newsbot-ops restart dispatcher`
(detached restart with an import guard — safe to trigger from inside a claude run).
`.venv` must be built on the deploy host: a copied one has a dangling interpreter
symlink and the units won't start.

Only re-run `sudo ./deploy/install.sh` when `deploy/` itself changed (the unit
templates, `newsbot-ops`, or `install.sh`) — those are rendered into root-owned
locations outside the repo, so a plain `git pull` doesn't update them. Ordinary
`newsparser/` code changes need nothing beyond the restart above. Run
`git diff <previously-installed-commit>..HEAD -- deploy/` if unsure whether this
deploy needs it.

Privileged ops (service restart/status/logs) go through `/usr/local/sbin/newsbot-ops`,
a root-owned copy of `deploy/newsbot-ops` installed outside the repo — editing the
repo copy does nothing until a human re-runs `sudo ./deploy/install.sh`. That manual
re-install is the intended security gate.

### Required `.env` values

| Var | Purpose |
|---|---|
| `TELEGRAM_BOT_TOKEN` | BotFather token |
| `TELEGRAM_CHAT_ID` | where reports/alerts are **sent** (without it the bot runs but delivers nothing) |
| `ALLOWED_CHAT_ID` | only this chat may command the bot (inbound auth gate, fail-closed — the dispatcher refuses to start without it) |
| `TELEGRAM_ALERT_CHAT_ID` | target for poller alerts |
| `CLAUDE_CODE_OAUTH_TOKEN` | from `claude setup-token` (see Authentication) — the only Anthropic auth |
| `NEO4J_PASSWORD` | set **before** the first `up`; neo4j bakes it into `neo4j_data`, so changing it later means resetting that volume |

Optional overrides (safe defaults, leave unset for a standard deploy): `CLAUDE_BIN`
(the units set it via `deploy/install.sh`), `NEO4J_URI` (`bolt://localhost:7687`;
a compose-era `bolt://neo4j:7687` leftover makes `install.sh` fail — `EnvironmentFile`
beats the unit's fallback), `NEO4J_USER` (`neo4j`), `DB_PATH`, `MARKET_DB_PATH`,
`WORKSPACE_DIR`, `POLL_INTERVAL_SECONDS` (`300`), `MARKET_PULSE` (`1`; set to `0`
to turn off intraday volatility alerts). `IS_SANDBOX` is no longer needed —
claude runs as the service user, not root.

### One-time manual steps

1. Create the bot via @BotFather → `TELEGRAM_BOT_TOKEN`; message it once to learn your numeric chat id.
2. Choose `NEO4J_PASSWORD` before the first boot.
3. Run `claude setup-token` (Authentication section) → `CLAUDE_CODE_OAUTH_TOKEN`.
4. `sudo ./deploy/install.sh` (installs units, `newsbot-ops`, sudoers entry).

### Host prerequisites

- Docker Engine + Docker Compose v2 (the space form `docker compose`) — for neo4j only.
  The service user does **not** need to be in the docker group: neo4j control goes
  through the root-owned `newsbot-ops` (sudoers-whitelisted, NOPASSWD).
- claude CLI installed for the service user (`curl -fsSL https://claude.ai/install.sh | bash`).
- `uv` for building `./.venv`.
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

### 3. Ensure the services see the variable

Both systemd units load `.env` via `EnvironmentFile=`, so once the token is in `.env`
it reaches every `claude -p` call automatically — no extra wiring. A changed value is
picked up on the next `systemctl restart` (or `sudo -n newsbot-ops restart dispatcher`).

### 4. (Optional) Remove the stale credentials file

Once the env var is in place, the CLI no longer needs the credentials file:

```bash
rm ~/.claude/.credentials.json
```

If you prefer to leave it, the env var takes precedence.

### Token lifetime

Long-lived tokens do not rotate automatically but can be revoked (manual revoke, subscription
expiry, or a security event). If calls start failing with auth errors again, re-run
`claude setup-token` and update `.env`.
