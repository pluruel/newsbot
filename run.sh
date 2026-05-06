#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PYTHON=".venv/bin/python"

cleanup() {
  echo ""
  echo "Stopping services..."
  kill "$POLLER_PID" "$SCHEDULER_PID" "$BOT_PID" 2>/dev/null || true
  docker compose stop neo4j
}
trap cleanup EXIT INT TERM

# Neo4j only
echo "Starting Neo4j..."
docker compose up -d neo4j

echo "Waiting for Neo4j to be ready..."
until docker compose exec -T neo4j wget -q --spider http://localhost:7474 2>/dev/null; do
  sleep 2
done
echo "Neo4j ready."

# Override URI for local run
export NEO4J_URI="bolt://localhost:7687"

echo "Starting poller..."
$PYTHON -m newsparser.collector.run_poller &
POLLER_PID=$!

echo "Starting scheduler..."
$PYTHON -m newsparser.scheduler.cron &
SCHEDULER_PID=$!

echo "Starting bot..."
$PYTHON -m newsparser.bot.telegram_bot &
BOT_PID=$!

echo "All services running. Ctrl+C to stop."
wait "$BOT_PID"
