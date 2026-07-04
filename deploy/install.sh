#!/usr/bin/env bash
# One-shot host provisioning for newsbot. Run from the repo as the service user:
#
#   sudo ./deploy/install.sh
#
# THIS IS THE APPROVAL GATE: the copies installed here run as root (newsbot-ops)
# or as the service user (systemd units). Review `git diff deploy/` before
# running — especially if claude has been editing the repo.
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }
USER_NAME="${SUDO_USER:-}"
if [ -z "$USER_NAME" ] || [ "$USER_NAME" = root ]; then
  echo "run via sudo from the service user account (SUDO_USER is empty/root)" >&2
  exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

CLAUDE_BIN="$(sudo -u "$USER_NAME" bash -lc 'command -v claude' || true)"
if [ -z "$CLAUDE_BIN" ]; then
  echo "claude CLI not found for user $USER_NAME — install it first (https://claude.ai/install.sh)" >&2
  exit 1
fi

[ -f "$ROOT/.env" ] || echo "WARNING: $ROOT/.env missing — units will fail to start until it exists" >&2
if grep -q "bolt://neo4j:7687" "$ROOT/.env" 2>/dev/null; then
  echo "WARNING: .env still has compose-era NEO4J_URI=bolt://neo4j:7687 — change to bolt://localhost:7687" >&2
fi

render() {
  sed -e "s|@USER@|$USER_NAME|g" \
      -e "s|@ROOT@|$ROOT|g" \
      -e "s|@CLAUDE_BIN@|$CLAUDE_BIN|g" "$1" > "$2"
}

render "$ROOT/deploy/newsbot-poller.service" /etc/systemd/system/newsbot-poller.service
render "$ROOT/deploy/newsbot-dispatcher.service" /etc/systemd/system/newsbot-dispatcher.service

# newsbot-ops: root-owned, outside the repo — the only unattended-root path.
render "$ROOT/deploy/newsbot-ops" /usr/local/sbin/newsbot-ops
chown root:root /usr/local/sbin/newsbot-ops
chmod 755 /usr/local/sbin/newsbot-ops
bash -n /usr/local/sbin/newsbot-ops

# Sudoers entry: exactly one command, no wildcards. visudo-validated before install.
sudoers_tmp="$(mktemp)"
printf '%s ALL=(root) NOPASSWD: /usr/local/sbin/newsbot-ops\n' "$USER_NAME" > "$sudoers_tmp"
visudo -cf "$sudoers_tmp"
install -m 0440 -o root -g root "$sudoers_tmp" /etc/sudoers.d/newsbot
rm -f "$sudoers_tmp"

systemctl daemon-reload
systemctl enable newsbot-poller.service newsbot-dispatcher.service

cat <<EOF
Installed:
  /etc/systemd/system/newsbot-{poller,dispatcher}.service  (user=$USER_NAME, root=$ROOT)
  /usr/local/sbin/newsbot-ops                               (root:root 755)
  /etc/sudoers.d/newsbot                                    (NOPASSWD: newsbot-ops only)

Next steps (see plan-host-migration.md checklist):
  1. chown -R $USER_NAME:$USER_NAME $ROOT/workspace     # if migrating off containers
  2. docker compose --project-directory $ROOT up -d     # neo4j only
  3. systemctl start newsbot-poller newsbot-dispatcher
  4. sudo -n /usr/local/sbin/newsbot-ops status         # as $USER_NAME, no password
EOF
