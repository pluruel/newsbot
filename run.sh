#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PYTHON=".venv/bin/python"

cleanup() {
  echo ""
  echo "Stopping bot..."
  kill "$BOT_PID" 2>/dev/null || true
  echo "Stopping docker services..."
  docker compose stop
}
trap cleanup EXIT INT TERM

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
