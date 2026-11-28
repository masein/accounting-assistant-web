#!/bin/sh
# The only command the live copy's SSH key may run on the server (DEPLOY.md §13). In the SSH user's authorized_keys:
#   restrict,command="/usr/local/bin/accounting-copy-gate" ssh-ed25519 AAAA… accounting-replica
# `restrict` turns off forwarding, a terminal and the rest; this script allows three things, and logs each request:
#   db-pipe                 one database connection: stdin/stdout ⇄ the db container's port 5432 (busybox nc)
#   has <name>              whether one of the synced folders exists (exit 0/1)
#   rsync --server --sender …  rsync READING one of the synced folders (the copy pulls; nothing can be written)
set -u
LOG=/var/log/accounting-copy-gate.log
cmd=${SSH_ORIGINAL_COMMAND:-}
DB=accounting-db-1
folder() {
  case "$1" in
    uploads)          echo /var/lib/docker/volumes/accounting_uploads/_data/ ;;
    private_uploads)  echo /var/lib/docker/volumes/accounting_private_uploads/_data/ ;;
    server-backups)   echo /opt/accounting/backups/ ;;
  esac
}
say() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) ${SSH_CLIENT%% *} $*" >> "$LOG" 2>/dev/null; }
deny() { say "DENIED: $cmd"; echo "not allowed" >&2; exit 1; }

case "$cmd" in
  db-pipe)
    ip=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' "$DB" 2>/dev/null | awk '{print $1}')
    [ -n "$ip" ] || { say "db-pipe: $DB not found"; exit 1; }
    say "db-pipe $ip"
    exec nc "$ip" 5432 ;;
  "has "*)
    d=$(folder "${cmd#has }"); [ -n "$d" ] || deny
    test -d "$d" ;;
  "rsync --server --sender "*)
    set -f; set -- $cmd; set +f
    for last; do :; done
    case "$last" in
      /var/lib/docker/volumes/accounting_uploads/_data/|/var/lib/docker/volumes/accounting_private_uploads/_data/|/opt/accounting/backups/) ;;
      *) deny ;;
    esac
    for a; do
      case "$a" in
        rsync|--server|--sender|.|"$last") ;;
        -[A-Za-z]*) case "$a" in *=*|*/*) deny ;; esac ;;      # flag bundles only, no option values or paths
        *) deny ;;
      esac
    done
    say "rsync $last"
    exec "$@" ;;                                    # the words just checked: rsync --server --sender <flags> . <folder>
  *) deny ;;
esac
