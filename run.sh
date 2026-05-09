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
CRON_BLOCK="# --- newsparser ---
CRON_TZ=Asia/Seoul
0 0,6,12,18 * * * mkdir -p $SCRIPT_DIR/workspace/state/locks $SCRIPT_DIR/workspace/logs && flock -n $SCRIPT_DIR/workspace/state/locks/cycle $SCRIPT_DIR/.venv/bin/python -m newsparser.scripts.run_cycle >> $SCRIPT_DIR/workspace/logs/cron.log 2>&1
0 9 * * 1 mkdir -p $SCRIPT_DIR/workspace/state/locks $SCRIPT_DIR/workspace/logs && flock -n $SCRIPT_DIR/workspace/state/locks/weekly $SCRIPT_DIR/.venv/bin/python -m newsparser.scripts.run_weekly >> $SCRIPT_DIR/workspace/logs/cron.log 2>&1
0 21 * * 0 mkdir -p $SCRIPT_DIR/workspace/state/locks $SCRIPT_DIR/workspace/logs && flock -n $SCRIPT_DIR/workspace/state/locks/reflect $SCRIPT_DIR/.venv/bin/python -m newsparser.scripts.run_reflect >> $SCRIPT_DIR/workspace/logs/cron.log 2>&1
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
