#!/bin/sh
# The standby: on its first start (an empty data volume) it copies production with pg_basebackup through the
# tunnel; from then on it is a read-only hot standby replaying production's changes as they happen.
#
# A replication slot on production keeps the changes this copy hasn't received yet (the Mac off, the link down),
# capped at 4 GB there (max_slot_wal_keep_size). Past that the slot is dropped and this copy can't catch up:
# `docker compose down`, `docker volume rm accounting-replica_pgdata`, `docker compose up -d` copies it afresh.
set -eu
PGDATA=${PGDATA:-/var/lib/postgresql/data}
CONN="host=tunnel port=5432 user=replicator connect_timeout=30"

if [ ! -s "$PGDATA/PG_VERSION" ]; then
  echo "first start: copying production"
  mkdir -p "$PGDATA" && chown postgres:postgres "$PGDATA" && chmod 700 "$PGDATA"
  until pg_isready -h tunnel -p 5432 -t 30 -q; do echo "waiting for the tunnel…"; sleep 5; done
  export PGPASSWORD="$REPL_PASSWORD"
  gosu postgres pg_receivewal -d "$CONN" --slot="$SLOT" --create-slot --if-not-exists
  gosu postgres pg_basebackup -d "$CONN" -D "$PGDATA" -X stream -S "$SLOT" -R -c fast --progress
  # the connection the standby keeps open (written here, with its password, only inside this volume)
  printf "primary_conninfo = '%s password=%s application_name=%s'\nprimary_slot_name = '%s'\n" \
    "$CONN" "$REPL_PASSWORD" "$SLOT" "$SLOT" >> "$PGDATA/postgresql.auto.conf"
  unset PGPASSWORD
  echo "copied; starting as a standby"
fi

# hot standby: queries are allowed and read the copy; a query that would hold up replay waits at most 5 minutes
exec docker-entrypoint.sh postgres -c hot_standby=on -c max_standby_streaming_delay=300s \
  -c max_standby_archive_delay=300s -c unix_socket_directories=/var/run/postgresql
