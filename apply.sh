#!/usr/bin/env bash
#
# apply.sh — apply the checked-out code to the running services.
#
# A code deploy needs exactly these things (README "Deployment"):
#   0. .env checks/migrations  — NEWSBOT_API_KEY present, no ANTHROPIC_API_KEY
#   1. uv sync                 — the units run ./.venv directly, no build step
#   2. restart both units      — running python keeps pre-restart code in memory
#
# Usage (on the deploy host, after updating the checkout):
#   git pull && ./apply.sh
#
# Not covered: changes under deploy/ (unit templates, newsbot-ops, install.sh)
# still need `sudo ./deploy/install.sh` — they are rendered into root-owned
# locations a git pull never touches.
#
# On a dev box (no units installed) the restart step is skipped; note that
# plain `uv sync` removes the dev extras there — restore with `uv sync --extra dev`.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

# .env checks run before anything changes, so a bad .env leaves the old code
# running instead of restarting the units into a broken state.
ENV_FILE="$ROOT/.env"
if [[ -f "$ENV_FILE" ]]; then
  # One-time migration (2026-10): the direct-API key was renamed when the
  # scheduled jobs moved off `claude -p` — same key, now also used by
  # cycle/weekly/reflect. Idempotent; a no-op once the old name is gone.
  if grep -q '^NEWSBOT_HAIKU_API_KEY=' "$ENV_FILE"; then
    if grep -q '^NEWSBOT_API_KEY=' "$ENV_FILE"; then
      sed -i '/^NEWSBOT_HAIKU_API_KEY=/d' "$ENV_FILE"
      echo "migrated: removed obsolete NEWSBOT_HAIKU_API_KEY from .env"
    else
      sed -i 's/^NEWSBOT_HAIKU_API_KEY=/NEWSBOT_API_KEY=/' "$ENV_FILE"
      echo "migrated: NEWSBOT_HAIKU_API_KEY -> NEWSBOT_API_KEY in .env"
    fi
  fi
  # The tracker's `claude -p` inherits the unit environment and the CLI prefers
  # ANTHROPIC_API_KEY over the OAuth token — chat answers would bill to the API.
  if grep -qE '^ANTHROPIC_(API_KEY|AUTH_TOKEN)=' "$ENV_FILE"; then
    echo "error: .env sets ANTHROPIC_API_KEY/ANTHROPIC_AUTH_TOKEN — rename it to NEWSBOT_API_KEY" >&2
    exit 1
  fi
  if ! grep -qE '^NEWSBOT_API_KEY=.+' "$ENV_FILE"; then
    echo "error: NEWSBOT_API_KEY missing or empty in .env — the scheduled jobs and Haiku calls need it" >&2
    exit 1
  fi
else
  echo "skip: no .env (dev box?) — key checks not run"
fi

uv sync

for unit in newsbot-poller newsbot-dispatcher; do
  if systemctl list-unit-files --no-legend "${unit}.service" 2>/dev/null | grep -q .; then
    sudo systemctl restart "$unit"
    echo "restarted: $unit"
  else
    echo "skip: ${unit}.service not installed (dev box?)"
  fi
done
