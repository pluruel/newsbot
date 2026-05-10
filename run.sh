#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PYTHON=".venv/bin/python"

BOT_PID=""
cleanup() {
  echo ""
  if [ -n "${BOT_PID:-}" ]; then
    echo "Stopping bot..."
    kill "$BOT_PID" 2>/dev/null || true
  fi
  echo "Stopping docker services..."
  docker compose stop || true
}
trap cleanup EXIT INT TERM

echo "Installing cron entries..."
# vixie-cron on this system ignores CRON_TZ, so schedule is in UTC.
# UTC offsets correspond to KST (UTC+9):
#   cycle  03,09,15,21 UTC = 12,18,00,06 KST
#   weekly 00 Mon UTC       = 09 Mon KST
#   reflect 12 Sun UTC      = 21 Sun KST
CRON_BLOCK="# --- newsparser ---
PATH=/root/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
0 3,9,15,21 * * * cd $SCRIPT_DIR && mkdir -p workspace/state/locks workspace/logs && flock -n workspace/state/locks/cycle .venv/bin/python -m newsparser.scripts.run_cycle >> workspace/logs/cron.log 2>&1
0 0 * * 1 cd $SCRIPT_DIR && mkdir -p workspace/state/locks workspace/logs && flock -n workspace/state/locks/weekly .venv/bin/python -m newsparser.scripts.run_weekly >> workspace/logs/cron.log 2>&1
0 12 * * 0 cd $SCRIPT_DIR && mkdir -p workspace/state/locks workspace/logs && flock -n workspace/state/locks/reflect .venv/bin/python -m newsparser.scripts.run_reflect >> workspace/logs/cron.log 2>&1
# --- end newsparser ---"
(crontab -l 2>/dev/null | sed '/# --- newsparser ---/,/# --- end newsparser ---/d'; echo "$CRON_BLOCK") | crontab -
echo "Cron entries installed."

echo "Starting docker services (neo4j, poller)..."
docker compose up -d --build neo4j poller

echo "Waiting for Neo4j to be ready..."
until docker compose exec -T neo4j wget -q --spider http://localhost:7474 2>/dev/null; do
  sleep 2
done
echo "Neo4j ready."

export NEO4J_URI="bolt://localhost:7687"
export IS_SANDBOX="1"

echo "Starting bot (host)..."
$PYTHON -m newsparser.bot.telegram_bot &
BOT_PID=$!

echo "All services running. Ctrl+C to stop."
wait "$BOT_PID"
