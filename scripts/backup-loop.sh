#!/bin/sh
# Runs inside the `backup` service (postgres image → pg_dump available).
# Once a day at BACKUP_HOUR: pg_dump (custom format, compressed) + tars of the
# uploads and private_uploads (period snapshots) volumes into /backups, then
# prune files older than BACKUP_KEEP_DAYS.
# Set BACKUP_ON_START=1 to take one immediately when the container starts.
#
#   sh backup-loop.sh          the daily loop (what the service runs)
#   sh backup-loop.sh once     one backup now; exits non-zero if any part of it failed
#                              (docker compose run --rm backup once)
#
# A backup only counts when every part of it worked (review 2026-11-28). Each file
# is written as NAME.tmp and renamed when complete; a step that fails removes its
# .tmp and fails the whole backup, which is tried again an hour later (and every
# hour after that until one works). Before, `run_backup` was called inside `if`,
# where `set -e` is off, and ended with an echo — so a failed pg_dump was logged
# as done and the day marked backed up.
set -eu
HOUR="${BACKUP_HOUR:-02}"
KEEP="${BACKUP_KEEP_DAYS:-14}"
# the service's mounts; the tests point these at folders of their own
OUT="${BACKUP_INTO:-/backups}"
UPLOADS="${BACKUP_UPLOADS:-/uploads}"
PRIVATE_UPLOADS="${BACKUP_PRIVATE_UPLOADS:-/private_uploads}"
RETRY_SECONDS="${BACKUP_RETRY_SECONDS:-3600}"

# keep FILE COMMAND…: COMMAND writes FILE.tmp; when it succeeds the .tmp becomes FILE.
# When it fails the half-written FILE.tmp is removed and this returns non-zero.
keep() {
  file=$1; shift
  if "$@" && mv "$file.tmp" "$file"; then
    return 0
  fi
  echo "[backup] FAILED: $1 writing $(basename "$file") — its partial file was removed" >&2
  rm -f "$file.tmp"
  return 1
}

run_backup() {
  stamp="$(date +%Y%m%d-%H%M%S)"
  echo "[backup] $stamp starting"
  # A .tmp left by a backup that was killed part-way (the container stopped mid-dump)
  # is never renamed. Remove those once they are 6 hours old: no backup here runs that
  # long, so it can't be one still being written.
  find "$OUT" -type f -name '*.tmp' -mmin +360 -delete || return 1
  keep "$OUT/db-$stamp.dump" pg_dump -Fc -Z 6 -f "$OUT/db-$stamp.dump.tmp" || return 1
  if [ -d "$UPLOADS" ]; then
    keep "$OUT/uploads-$stamp.tgz" tar -czf "$OUT/uploads-$stamp.tgz.tmp" -C "$UPLOADS" . || return 1
  fi
  if [ -d "$PRIVATE_UPLOADS" ]; then
    keep "$OUT/private-uploads-$stamp.tgz" \
      tar -czf "$OUT/private-uploads-$stamp.tgz.tmp" -C "$PRIVATE_UPLOADS" . || return 1
  fi
  find "$OUT" -type f \( -name 'db-*.dump' -o -name 'uploads-*.tgz' -o -name 'private-uploads-*.tgz' \) \
    -mtime "+$KEEP" -delete || return 1
  echo "[backup] $stamp done: $(ls -1 "$OUT" | wc -l) file(s) kept"
}

if [ "${1:-}" = once ]; then
  if run_backup; then exit 0; else exit 1; fi
fi

last=""        # the day whose backup is done
retry_at=""    # after a failure: when to try again (epoch seconds), whatever the hour

if [ "${BACKUP_ON_START:-0}" = "1" ]; then
  # A failure here doesn't stop the container (a restart would only run it again at
  # once); it is logged and tried again in an hour, like the nightly one.
  if ! run_backup; then
    retry_at=$(( $(date +%s) + RETRY_SECONDS ))
    echo "[backup] FAILED — trying again in $(( RETRY_SECONDS / 60 )) minutes" >&2
  fi
fi

while :; do
  now="$(date +%s)"
  today="$(date +%Y%m%d)"
  due=""
  if [ -n "$retry_at" ]; then
    if [ "$now" -ge "$retry_at" ]; then due=1; fi
  elif [ "$(date +%H)" = "$HOUR" ] && [ "$last" != "$today" ]; then
    due=1
  fi
  if [ -n "$due" ]; then
    if run_backup; then
      last="$today"; retry_at=""
    else
      retry_at=$(( now + RETRY_SECONDS ))
      echo "[backup] FAILED — trying again in $(( RETRY_SECONDS / 60 )) minutes" >&2
    fi
  fi
  sleep 300
done
