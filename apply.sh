#!/usr/bin/env bash
#
# apply.sh — apply the checked-out code to the running services.
#
# A code deploy needs exactly two things (README "Deployment"):
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

uv sync

for unit in newsbot-poller newsbot-dispatcher; do
  if systemctl list-unit-files --no-legend "${unit}.service" 2>/dev/null | grep -q .; then
    sudo systemctl restart "$unit"
    echo "restarted: $unit"
  else
    echo "skip: ${unit}.service not installed (dev box?)"
  fi
done
