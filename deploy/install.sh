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
# command -v ran through the user's login profile — sanity-check what it
# resolved to before baking it into a root-owned unit file.
if [ ! -x "$CLAUDE_BIN" ] || [[ "$CLAUDE_BIN" != /* ]]; then
  echo "resolved CLAUDE_BIN looks wrong (not an absolute executable path): $CLAUDE_BIN" >&2
  exit 1
fi

[ -f "$ROOT/.env" ] || echo "WARNING: $ROOT/.env missing — units will fail to start until it exists" >&2
if grep -q "bolt://neo4j:7687" "$ROOT/.env" 2>/dev/null; then
  echo "ERROR: .env still has compose-era NEO4J_URI=bolt://neo4j:7687 — the 'neo4j' hostname" >&2
  echo "does not resolve on the host. Change it to bolt://localhost:7687 and re-run." >&2
  exit 1
fi
# The dispatcher chat gate is fail-closed on ALLOWED_CHAT_ID — without it the
# unit refuses to start, so catch the misconfiguration here.
if [ -f "$ROOT/.env" ] && ! grep -qE '^ALLOWED_CHAT_ID=..*' "$ROOT/.env"; then
  echo "ERROR: .env has no ALLOWED_CHAT_ID — the dispatcher requires it (fail-closed chat gate)." >&2
  exit 1
fi

# Escape sed replacement metacharacters (\, &, and the | delimiter) so an
# unusual path can't corrupt the rendered root-owned files.
sed_escape() { printf '%s' "$1" | sed -e 's/[\\&|]/\\&/g'; }
USER_ESC="$(sed_escape "$USER_NAME")"
ROOT_ESC="$(sed_escape "$ROOT")"
CLAUDE_BIN_ESC="$(sed_escape "$CLAUDE_BIN")"

render() {
  sed -e "s|@USER@|$USER_ESC|g" \
      -e "s|@ROOT@|$ROOT_ESC|g" \
      -e "s|@CLAUDE_BIN@|$CLAUDE_BIN_ESC|g" "$1" > "$2"
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
