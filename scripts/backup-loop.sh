#!/bin/sh
# Runs inside the `backup` service (postgres image → pg_dump available).
# Once a day at BACKUP_HOUR: pg_dump (custom format, compressed) + tars of the
# uploads and private_uploads (period snapshots) volumes into /backups, then
# prune files older than BACKUP_KEEP_DAYS.
# Set BACKUP_ON_START=1 to take one immediately when the container starts.
set -eu
HOUR="${BACKUP_HOUR:-02}"
KEEP="${BACKUP_KEEP_DAYS:-14}"

run_backup() {
  stamp="$(date +%Y%m%d-%H%M%S)"
  echo "[backup] $stamp starting"
  pg_dump -Fc -Z 6 -f "/backups/db-$stamp.dump.tmp" && mv "/backups/db-$stamp.dump.tmp" "/backups/db-$stamp.dump"
  if [ -d /uploads ]; then
    tar -czf "/backups/uploads-$stamp.tgz.tmp" -C /uploads . && mv "/backups/uploads-$stamp.tgz.tmp" "/backups/uploads-$stamp.tgz"
  fi
  if [ -d /private_uploads ]; then
    tar -czf "/backups/private-uploads-$stamp.tgz.tmp" -C /private_uploads . \
      && mv "/backups/private-uploads-$stamp.tgz.tmp" "/backups/private-uploads-$stamp.tgz"
  fi
  find /backups -type f \( -name 'db-*.dump' -o -name 'uploads-*.tgz' -o -name 'private-uploads-*.tgz' \) \
    -mtime "+$KEEP" -delete
  echo "[backup] $stamp done: $(ls -1 /backups | wc -l) file(s) kept"
}

[ "${BACKUP_ON_START:-0}" = "1" ] && run_backup

last=""
while :; do
  now_hour="$(date +%H)"
  today="$(date +%Y%m%d)"
  if [ "$now_hour" = "$HOUR" ] && [ "$last" != "$today" ]; then
    if run_backup; then last="$today"; else echo "[backup] FAILED — will retry next hour" >&2; fi
  fi
  sleep 300
done
