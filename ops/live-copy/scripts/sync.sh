#!/bin/sh
# Every SYNC_EVERY seconds: production's uploads, its period snapshots and the server's own nightly backups, copied
# into data/files/ here. Only changed files move (rsync), and only by reading: the server's gate lets this key's
# rsync send these three folders and nothing else. Nothing is deleted here when it goes from the server: this is a
# backup, so a file removed there stays here.
#
#   sh sync.sh          the loop (what the `files` service runs)
#   sh sync.sh once     one round; exits non-zero unless every folder synced
#
# .last-sync is stamped only when every folder synced (review 2026-11-28). The watchdog reads its age, so it must not
# move while the server can't be reached: the `has <name>` check used to treat "couldn't reach the server" like
# "that folder isn't there", skip the folder, and stamp a sync that never happened.
set -u
. "$(dirname "$0")/ssh-common.sh"
FILES=${SYNC_INTO:-/files}     # the service's mount; the tests point it at a folder of their own

sync_round() {
  ok=1
  for item in \
      "uploads|/var/lib/docker/volumes/accounting_uploads/_data/" \
      "private_uploads|/var/lib/docker/volumes/accounting_private_uploads/_data/" \
      "server-backups|/opt/accounting/backups/"; do
    name=${item%%|*}; src=${item#*|}
    # The gate answers 0 (the folder is there) or 1 (not on this server, yet); ssh itself exits 255 when it
    # couldn't reach the server or sign in. Anything but 0 or 1 is a failed round.
    ssh $SSH_OPTS "$REMOTE" "has $name" 2>/dev/null
    rc=$?
    if [ "$rc" = 1 ]; then
      continue                                  # not on this server (yet): nothing to copy
    elif [ "$rc" != 0 ]; then
      ok=0; echo "$(stamp) sync of $name failed: couldn't reach the server (ssh exit $rc)"
      continue
    fi
    mkdir -p "$FILES/$name"
    if ! rsync -a --partial -e "ssh $SSH_OPTS" "$REMOTE:$src" "$FILES/$name/"; then
      ok=0; echo "$(stamp) sync of $name failed"
    fi
  done
  if [ "$ok" = 1 ]; then
    stamp > "$FILES/.last-sync"
    return 0
  fi
  return 1
}

if [ "${1:-}" = once ]; then
  sync_round
  exit $?
fi

while :; do
  sync_round
  sleep "${SYNC_EVERY:-300}"
done
