#!/usr/bin/env bash
#
# backup.sh — one-command, full-state snapshot of the Newsparser project.
#
# Captures everything that is NOT reproducible from git:
#   - every SQLite DB under workspace/ (newsparser.db, market.db,
#     state/claude_runs.db, ...) via a *consistent online snapshot*
#     (safe even while the poller/dispatcher are writing)
#   - all runtime documents: cycles/, briefs/, me/, input/, logs/, sessions/, ...
#   - the Neo4j knowledge graph (docker volume) — best effort, requires docker
#   - .env (secrets) so a blank checkout runs identically
#
# Output: a single gzip tarball under backups/  (+ a .sha256 sidecar)
#
# Usage:
#   ./backup.sh                 # full backup -> backups/
#   ./backup.sh -o /mnt/usb     # write archive to another directory
#   ./backup.sh --no-secrets    # exclude .env
#   ./backup.sh --no-neo4j      # skip the Neo4j graph dump
#
set -euo pipefail

# --- locate repo root (this script lives at the root) --------------------
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

# --- config / args -------------------------------------------------------
OUT_DIR="$ROOT/backups"
INCLUDE_SECRETS=1
INCLUDE_NEO4J=1
WORKSPACE_DIR="${WORKSPACE_DIR:-workspace}"

print_help() { awk 'NR==1{next} /^#/{sub(/^# ?/,"");print;next}{exit}' "${BASH_SOURCE[0]}"; }

while [ $# -gt 0 ]; do
  case "$1" in
    -o|--output)
      [ $# -ge 2 ] || { echo "backup.sh: $1 requires a directory argument" >&2; exit 2; }
      OUT_DIR="$2"; shift 2 ;;
    --no-secrets) INCLUDE_SECRETS=0; shift ;;
    --no-neo4j)   INCLUDE_NEO4J=0; shift ;;
    -h|--help)    print_help; exit 0 ;;
    *) echo "backup.sh: unknown argument '$1'" >&2; exit 2 ;;
  esac
done

# --- pick a python (venv preferred — guaranteed by CLAUDE.md) ------------
if [ -x "$ROOT/.venv/bin/python" ]; then
  PYBIN="$ROOT/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYBIN="$(command -v python3)"
else
  echo "backup.sh: no python found (needed for consistent SQLite snapshots)" >&2
  exit 1
fi

# --- timestamp comes from the chosen python (no GNU-date assumptions) -----
TS="$("$PYBIN" -c 'import datetime;print(datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))')"
NOW_ISO="$("$PYBIN" -c 'import datetime;print(datetime.datetime.now().astimezone().isoformat())')"

log()  { printf '  %s\n' "$*"; }
step() { printf '\n\033[1m▶ %s\033[0m\n' "$*"; }

step "Newsparser backup  ($NOW_ISO)"

if [ ! -d "$WORKSPACE_DIR" ]; then
  echo "backup.sh: workspace dir '$WORKSPACE_DIR' not found — nothing to back up" >&2
  exit 1
fi

# Resolve output dir up front and refuse to write inside the workspace
# (otherwise the doc-copy below would sweep prior archives into each backup).
WS_ABS="$(cd "$WORKSPACE_DIR" && pwd)"
mkdir -p "$OUT_DIR"
OUT_ABS="$(cd "$OUT_DIR" && pwd)"
case "$OUT_ABS/" in
  "$WS_ABS"/*) echo "backup.sh: output dir is inside the workspace ($OUT_ABS) — choose a path outside $WS_ABS" >&2; exit 2 ;;
esac

# Suppress GNU tar's benign "file changed as we read it" noise (GNU-only flags).
TAR_WARN=()
_tarver="$(tar --version 2>/dev/null || true)"
case "$_tarver" in *GNU*) TAR_WARN=(--warning=no-file-changed --warning=no-file-removed) ;; esac

# --- staging area --------------------------------------------------------
STAGE="$(mktemp -d "${TMPDIR:-/tmp}/newsparser-backup.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT
ARCHIVE_NAME="newsparser-backup-$TS"
PAYLOAD="$STAGE/$ARCHIVE_NAME"
mkdir -p "$PAYLOAD/workspace"

# --- 1. copy workspace tree, excluding live DBs + transient lock files ---
# DBs (and their -wal/-shm/-journal sidecars) are excluded here and captured
# below as consistent snapshots; lock files are transient. A doc file that
# changes mid-read makes GNU tar exit 1 ("file changed as we read it") — that
# is benign, so we tolerate read-side rc<=1 instead of aborting the backup.
step "Copying workspace documents"
set +e
tar -c -C "$WORKSPACE_DIR" \
    --exclude='*.db' --exclude='*.db-wal' --exclude='*.db-shm' \
    --exclude='*-journal' --exclude='*.db-mj*' \
    --exclude='./state/locks' --exclude='state/locks' \
    "${TAR_WARN[@]}" \
    . | tar -x -C "$PAYLOAD/workspace"
ps=("${PIPESTATUS[@]}"); src_rc=${ps[0]:-0}; dst_rc=${ps[1]:-0}
set -e
[ "$src_rc" -le 1 ] || { echo "backup.sh: workspace copy failed (tar read rc=$src_rc)" >&2; exit 1; }
[ "$dst_rc" -eq 0 ] || { echo "backup.sh: workspace copy failed (tar write rc=$dst_rc)" >&2; exit 1; }
FILE_COUNT="$(find "$PAYLOAD/workspace" -type f | wc -l | tr -d ' ')"
log "$FILE_COUNT document file(s)"

# --- 2. consistent snapshot of every SQLite DB ---------------------------
# All-or-nothing: if ANY db can't be snapshotted we abort with a clear error
# and write NO archive. A backup that silently omits a DB is a trap — you'd
# only discover the gap at restore time. The archive is packed last (step 6),
# so aborting here leaves nothing partial behind.
step "Snapshotting SQLite databases"
DB_LIST=()
while IFS= read -r db; do
  rel="${db#"$WORKSPACE_DIR"/}"
  dst="$PAYLOAD/workspace/$rel"
  mkdir -p "$(dirname "$dst")"
  if ! "$PYBIN" - "$db" "$dst" <<'PY'
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
# read-only source + .backup() => atomic, WAL-safe snapshot of a live DB
s = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
d = sqlite3.connect(dst)
try:
    with d:
        s.backup(d)
finally:
    s.close(); d.close()
PY
  then
    echo "backup.sh: FAILED to snapshot '$rel' — aborting, no archive written." >&2
    echo "  A backup must be complete to be trustworthy. Fix the DB and retry." >&2
    exit 1
  fi
  DB_LIST+=("$rel")
  log "$rel  ($(du -h "$dst" | cut -f1))"
done < <(find "$WORKSPACE_DIR" -type f -name '*.db' | sort)
[ "${#DB_LIST[@]}" -eq 0 ] && log "(no .db files found)"

# --- 3. secrets ----------------------------------------------------------
ENV_INCLUDED=no
if [ "$INCLUDE_SECRETS" -eq 1 ] && [ -f "$ROOT/.env" ]; then
  step "Including .env (secrets)"
  mkdir -p "$PAYLOAD/env"
  cp -p "$ROOT/.env" "$PAYLOAD/env/.env"
  ENV_INCLUDED=yes
  log "stored under env/.env  — keep this archive private"
fi

# --- 4. Neo4j knowledge graph (best effort) ------------------------------
NEO4J_INCLUDED=no
neo4j_available() {
  command -v docker >/dev/null 2>&1 || return 1
  docker compose version >/dev/null 2>&1 || return 1
  # match a whole 'neo4j' line without a pipe (pipefail + grep -q SIGPIPE safe)
  local services; services="$(docker compose config --services 2>/dev/null || true)"
  case $'\n'"$services"$'\n' in *$'\n'neo4j$'\n'*) ;; *) return 1 ;; esac
  return 0
}
neo4j_is_running() {
  local cid; cid="$(docker compose ps -q neo4j 2>/dev/null)"
  [ -n "$cid" ] || return 1
  [ "$(docker inspect -f '{{.State.Running}}' "$cid" 2>/dev/null)" = "true" ]
}
backup_neo4j() {
  # neo4j-admin requires the DB offline; stop it (if up), dump via a one-off
  # container sharing the same data volume, then restore prior running state.
  local was_running=no
  neo4j_is_running && was_running=yes
  mkdir -p "$PAYLOAD/neo4j"
  # the dump runs as the in-container neo4j user (uid 7474); make the host
  # bind-mount world-writable so it can create the dump file there.
  chmod 777 "$PAYLOAD/neo4j"
  [ "$was_running" = yes ] && { log "stopping neo4j for a consistent dump"; docker compose stop neo4j >/dev/null 2>&1 || true; }
  local rc=0
  docker compose run --rm --no-deps \
    -v "$PAYLOAD/neo4j:/backup" neo4j \
    neo4j-admin database dump neo4j --to-path=/backup --overwrite-destination=true \
    >/dev/null 2>&1 || rc=1
  [ "$was_running" = yes ] && { log "restarting neo4j"; docker compose start neo4j >/dev/null 2>&1 || true; }
  if [ "$rc" -ne 0 ] || [ ! -f "$PAYLOAD/neo4j/neo4j.dump" ]; then
    rm -rf "$PAYLOAD/neo4j"
    return 1
  fi
  log "neo4j.dump  ($(du -h "$PAYLOAD/neo4j/neo4j.dump" | cut -f1))"
  return 0
}
if [ "$INCLUDE_NEO4J" -eq 1 ]; then
  step "Dumping Neo4j graph"
  if neo4j_available; then
    if backup_neo4j; then
      NEO4J_INCLUDED=yes
    else
      log "⚠ Neo4j dump failed — continuing without it"
    fi
  else
    log "docker/compose/neo4j not available — skipping (SQLite+docs still captured)"
  fi
fi

# --- 5. manifest ---------------------------------------------------------
step "Writing MANIFEST"
GIT_COMMIT="$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || echo unknown)"
GIT_BRANCH="$(git -C "$ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
if git -C "$ROOT" rev-parse HEAD >/dev/null 2>&1; then
  [ -n "$(git -C "$ROOT" status --porcelain 2>/dev/null)" ] && GIT_DIRTY=dirty || GIT_DIRTY=clean
else
  GIT_DIRTY=unknown
fi

{
  echo "Newsparser backup manifest"
  echo "=========================="
  echo "created_at : $NOW_ISO"
  echo "host       : $(hostname 2>/dev/null || echo '?')  user: ${USER:-?}"
  echo "git_commit : $GIT_COMMIT ($GIT_BRANCH, $GIT_DIRTY)"
  echo "workspace  : $WORKSPACE_DIR  ($FILE_COUNT document files)"
  echo "secrets    : env included = $ENV_INCLUDED"
  echo "neo4j      : graph dump included = $NEO4J_INCLUDED"
  echo
  echo "SQLite databases (table : rows):"
  for rel in "${DB_LIST[@]}"; do
    echo "  $rel"
    "$PYBIN" - "$PAYLOAD/workspace/$rel" <<'PY'
import sqlite3, sys
db = sys.argv[1]
c = sqlite3.connect(db)
for (name,) in c.execute("select name from sqlite_master where type='table' order by name"):
    try:
        n = c.execute(f'select count(*) from "{name}"').fetchone()[0]
    except Exception as e:
        n = f"? ({e})"
    print(f"      {name:<24} {n}")
c.close()
PY
  done
  [ "${#DB_LIST[@]}" -eq 0 ] && echo "  (none)"
} > "$PAYLOAD/MANIFEST.txt"
cat "$PAYLOAD/MANIFEST.txt" | sed 's/^/  /'

# --- 6. pack -------------------------------------------------------------
# OUT_ABS was resolved (and validated outside the workspace) up front.
step "Packing archive"
ARCHIVE="$OUT_ABS/$ARCHIVE_NAME.tar.gz"
tar -czf "$ARCHIVE.tmp" -C "$STAGE" "$ARCHIVE_NAME"
mv "$ARCHIVE.tmp" "$ARCHIVE"

# checksum sidecar
if command -v sha256sum >/dev/null 2>&1; then
  ( cd "$OUT_ABS" && sha256sum "$(basename "$ARCHIVE")" > "$(basename "$ARCHIVE").sha256" )
elif command -v shasum >/dev/null 2>&1; then
  ( cd "$OUT_ABS" && shasum -a 256 "$(basename "$ARCHIVE")" > "$(basename "$ARCHIVE").sha256" )
fi

SIZE="$(du -h "$ARCHIVE" | cut -f1)"
step "Done"
log "archive : $ARCHIVE"
log "size    : $SIZE"
[ -f "$ARCHIVE.sha256" ] && log "checksum: $ARCHIVE.sha256"
echo
echo "Restore with:  ./restore.sh \"$ARCHIVE\""
