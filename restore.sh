#!/usr/bin/env bash
#
# restore.sh — rebuild full project state from a backup.sh archive.
#
# Run this in a fresh checkout to make it run identically to the source:
#   - restores every SQLite DB and runtime document into workspace/
#   - restores .env (only if missing locally, unless --restore-env)
#   - loads the Neo4j knowledge graph (best effort, requires docker)
#
# Usage:
#   ./restore.sh                          # restore newest backups/*.tar.gz
#   ./restore.sh path/to/backup.tar.gz    # restore a specific archive
#   ./restore.sh -y ...                   # don't prompt before overwriting
#   ./restore.sh --restore-env ...        # overwrite an existing .env
#   ./restore.sh --no-neo4j ...           # skip the Neo4j graph load
#   ./restore.sh --no-safety ...          # skip the pre-restore safety backup
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

ARCHIVE=""
ASSUME_YES=0
RESTORE_ENV=0
INCLUDE_NEO4J=1
SAFETY=1
WORKSPACE_DIR="${WORKSPACE_DIR:-workspace}"

print_help() { awk 'NR==1{next} /^#/{sub(/^# ?/,"");print;next}{exit}' "${BASH_SOURCE[0]}"; }

while [ $# -gt 0 ]; do
  case "$1" in
    -y|--yes|--force) ASSUME_YES=1; shift ;;
    --restore-env)    RESTORE_ENV=1; shift ;;
    --no-neo4j)       INCLUDE_NEO4J=0; shift ;;
    --no-safety)      SAFETY=0; shift ;;
    -h|--help)        print_help; exit 0 ;;
    -*) echo "restore.sh: unknown option '$1'" >&2; exit 2 ;;
    *)  ARCHIVE="$1"; shift ;;
  esac
done

log()  { printf '  %s\n' "$*"; }
step() { printf '\n\033[1m▶ %s\033[0m\n' "$*"; }

# --- resolve archive (default: newest in backups/) -----------------------
if [ -z "$ARCHIVE" ]; then
  ARCHIVE="$(ls -1t "$ROOT"/backups/newsparser-backup-*.tar.gz 2>/dev/null | head -1 || true)"
  [ -n "$ARCHIVE" ] && log "no archive given — using newest: $ARCHIVE"
fi
if [ -z "$ARCHIVE" ] || [ ! -f "$ARCHIVE" ]; then
  echo "restore.sh: backup archive not found." >&2
  echo "  pass a path, or place one in backups/  (run ./backup.sh first)" >&2
  exit 1
fi
ARCHIVE="$(cd "$(dirname "$ARCHIVE")" && pwd)/$(basename "$ARCHIVE")"

step "Newsparser restore"
log "source: $ARCHIVE"

# --- verify checksum if a sidecar exists ---------------------------------
if [ -f "$ARCHIVE.sha256" ]; then
  if command -v sha256sum >/dev/null 2>&1; then
    ( cd "$(dirname "$ARCHIVE")" && sha256sum -c "$(basename "$ARCHIVE").sha256" >/dev/null ) \
      && log "checksum OK" || { echo "restore.sh: checksum verification FAILED" >&2; exit 1; }
  elif command -v shasum >/dev/null 2>&1; then
    ( cd "$(dirname "$ARCHIVE")" && shasum -a 256 -c "$(basename "$ARCHIVE").sha256" >/dev/null ) \
      && log "checksum OK" || { echo "restore.sh: checksum verification FAILED" >&2; exit 1; }
  fi
fi

# --- extract -------------------------------------------------------------
STAGE="$(mktemp -d "${TMPDIR:-/tmp}/newsparser-restore.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT
tar -xzf "$ARCHIVE" -C "$STAGE"
PAYLOAD="$(find "$STAGE" -maxdepth 1 -type d -name 'newsparser-backup-*' | head -1)"
[ -z "$PAYLOAD" ] && PAYLOAD="$STAGE"
if [ ! -d "$PAYLOAD/workspace" ]; then
  echo "restore.sh: archive does not contain a workspace/ payload — wrong file?" >&2
  exit 1
fi

if [ -f "$PAYLOAD/MANIFEST.txt" ]; then
  step "Backup contents"
  sed 's/^/  /' "$PAYLOAD/MANIFEST.txt"
fi

# --- confirm before overwriting existing state ---------------------------
EXISTING=0
if [ -d "$WORKSPACE_DIR" ] && [ -n "$(find "$WORKSPACE_DIR" -type f \
      ! -name '.gitkeep' 2>/dev/null | head -1)" ]; then
  EXISTING=1
fi
if [ "$EXISTING" -eq 1 ] && [ "$ASSUME_YES" -eq 0 ]; then
  step "This will overwrite existing data in '$WORKSPACE_DIR'"
  if [ ! -t 0 ]; then
    echo "  non-interactive shell — re-run with -y to proceed." >&2
    exit 1
  fi
  printf "  Continue? [y/N] "
  read -r reply || reply=""        # EOF/Ctrl-D -> treat as "no"
  case "$reply" in y|Y|yes|YES) ;; *) echo "  aborted."; exit 0 ;; esac
fi

# --- pre-restore safety backup of whatever is there now ------------------
# Full snapshot (incl. Neo4j) of the SAME workspace we are about to overwrite,
# so this restore is reversible. The Neo4j load below overwrites the existing
# graph, so the rollback copy MUST capture it too. Invoked via `bash` so it
# works even if backup.sh lost its +x bit.
if [ "$EXISTING" -eq 1 ] && [ "$SAFETY" -eq 1 ] && [ -f "$ROOT/backup.sh" ]; then
  step "Safety backup of current state (before overwrite)"
  if WORKSPACE_DIR="$WORKSPACE_DIR" bash "$ROOT/backup.sh" \
        -o "$ROOT/backups/pre-restore" >/dev/null 2>&1; then
    log "rollback point saved to backups/pre-restore/"
  else
    step "⚠ Safety backup FAILED — refusing to overwrite without a rollback point"
    log "Fix the cause, or re-run with --no-safety to override this guard."
    exit 1
  fi
fi

# --- restore workspace ---------------------------------------------------
step "Restoring workspace"
mkdir -p "$WORKSPACE_DIR"
# CRITICAL: purge any stale SQLite sidecars left in the target before extract.
# The archive ships clean .db snapshots with NO -wal/-shm/-journal. A leftover
# sidecar from a prior run would be checkpointed into our restored .db on first
# open — silently reverting it to old data (verified) or corrupting it.
find "$WORKSPACE_DIR" -type f \
  \( -name '*.db-wal' -o -name '*.db-shm' -o -name '*-journal' -o -name '*.db-mj*' \) \
  -delete 2>/dev/null || true
tar -c -C "$PAYLOAD/workspace" . | tar -x -C "$WORKSPACE_DIR"
log "$(find "$PAYLOAD/workspace" -type f | wc -l | tr -d ' ') file(s) restored"

# --- restore .env --------------------------------------------------------
if [ -f "$PAYLOAD/env/.env" ]; then
  if [ ! -f "$ROOT/.env" ] || [ "$RESTORE_ENV" -eq 1 ]; then
    cp -p "$PAYLOAD/env/.env" "$ROOT/.env"
    log ".env restored"
  else
    log ".env already present — kept existing (use --restore-env to overwrite)"
  fi
fi

# --- restore Neo4j graph (best effort) -----------------------------------
restore_neo4j() {
  command -v docker >/dev/null 2>&1 || { log "docker not available — skipping graph"; return 0; }
  docker compose version >/dev/null 2>&1 || { log "docker compose not available — skipping graph"; return 0; }
  local services; services="$(docker compose config --services 2>/dev/null || true)"
  case $'\n'"$services"$'\n' in *$'\n'neo4j$'\n'*) ;; *) log "no neo4j service — skipping graph"; return 0 ;; esac
  # make the dump readable by the in-container neo4j user (uid 7474)
  chmod -R a+rX "$PAYLOAD/neo4j" 2>/dev/null || true
  log "stopping neo4j (if running)"
  docker compose stop neo4j >/dev/null 2>&1 || true
  local rc=0
  docker compose run --rm --no-deps \
    -v "$PAYLOAD/neo4j:/backup" neo4j \
    neo4j-admin database load neo4j --from-path=/backup --overwrite-destination=true \
    >/dev/null 2>&1 || rc=1
  # always bring neo4j back up — never leave the service stopped on failure
  docker compose up -d neo4j >/dev/null 2>&1 || true
  if [ "$rc" -ne 0 ]; then
    log "⚠ neo4j load failed — graph NOT restored (check 'docker compose logs neo4j'); neo4j restarted on prior data"
    return 0
  fi
  log "neo4j graph loaded and started"
}
if [ "$INCLUDE_NEO4J" -eq 1 ] && [ -f "$PAYLOAD/neo4j/neo4j.dump" ]; then
  step "Restoring Neo4j graph"
  restore_neo4j
fi

# --- ensure full directory layout / templates exist ----------------------
if [ -x "$ROOT/.venv/bin/python" ]; then
  "$ROOT/.venv/bin/python" -c \
    'from newsparser.scheduler.workspace import ensure_workspace; ensure_workspace()' \
    >/dev/null 2>&1 && log "workspace layout verified" || true
fi

step "Done"
echo "  State restored from: $ARCHIVE"
echo
echo "  Next steps for a blank checkout:"
echo "    1. uv sync                       # install deps into .venv"
echo "    2. cp .env.example .env          # only if .env was NOT in the backup"
echo "    3. docker compose up -d          # start neo4j + poller + dispatcher"
