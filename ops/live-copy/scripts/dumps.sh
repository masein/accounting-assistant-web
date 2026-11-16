#!/bin/sh
# Point-in-time copies, because a live copy is not a backup: a mistaken delete on production reaches the standby
# within a second. Every hour a pg_dump of the standby into data/dumps/hourly (kept HOURLY_KEEP_DAYS), and the
# first of each day also into data/dumps/daily (kept DAILY_KEEP_DAYS). The standby's socket is shared with this
# container, so no password is involved. Restore one with pg_restore (README.md).
set -u
H=/dumps/hourly; D=/dumps/daily
mkdir -p "$H" "$D"
until pg_isready -U postgres -h /var/run/postgresql -q; do sleep 10; done
while :; do
  stamp=$(date +%Y%m%d-%H%M%S); hour=$(date +%Y%m%d-%H); today=$(date +%Y%m%d)
  if ! ls "$H"/db-"$hour"*.dump >/dev/null 2>&1; then
    if pg_dump -h /var/run/postgresql -U postgres -d accounting -Fc -Z 6 -f "$H/db-$stamp.dump.tmp"; then
      mv "$H/db-$stamp.dump.tmp" "$H/db-$stamp.dump"
      ls "$D"/db-"$today"-*.dump >/dev/null 2>&1 || cp "$H/db-$stamp.dump" "$D/db-$stamp.dump"
      echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) dumped db-$stamp.dump"
    else
      rm -f "$H/db-$stamp.dump.tmp"; echo "dump failed — trying again in 5 minutes"; sleep 300; continue
    fi
  fi
  find "$H" -name 'db-*.dump' -mtime "+${HOURLY_KEEP_DAYS:-7}" -delete
  find "$D" -name 'db-*.dump' -mtime "+${DAILY_KEEP_DAYS:-90}" -delete
  sleep 300
done
