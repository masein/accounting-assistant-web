#!/bin/sh
# Every SYNC_EVERY seconds: production's uploads, its period snapshots and the server's own nightly backups, copied
# into data/files/ here. Only changed files move (rsync), and only by reading: the server's gate lets this key's
# rsync send these three folders and nothing else. Nothing is deleted here when it goes from the server: this is a
# backup, so a file removed there stays here.
set -u
. /scripts/ssh-common.sh
while :; do
  ok=1
  for item in \
      "uploads|/var/lib/docker/volumes/accounting_uploads/_data/" \
      "private_uploads|/var/lib/docker/volumes/accounting_private_uploads/_data/" \
      "server-backups|/opt/accounting/backups/"; do
    name=${item%%|*}; src=${item#*|}
    ssh $SSH_OPTS "$REMOTE" "has $name" 2>/dev/null || continue     # not on this server (yet)
    mkdir -p "/files/$name"
    if ! rsync -a --partial -e "ssh $SSH_OPTS" "$REMOTE:$src" "/files/$name/"; then
      ok=0; echo "$(stamp) sync of $name failed"
    fi
  done
  [ "$ok" = 1 ] && stamp > /files/.last-sync
  sleep "${SYNC_EVERY:-300}"
done
