#!/bin/sh
# How the live copy is doing:  sh status.sh
cd "$(dirname "$0")" || exit 1
echo "== containers"
docker compose ps --format '  {{.Service}}: {{.Status}}'
echo "== database copy"
docker compose exec -T replica psql -U postgres -At -c "
  select '  link: ' || coalesce((select status from pg_stat_wal_receiver), 'NOT CONNECTED')
      || chr(10) || '  last change from production applied: '
      || coalesce(to_char(pg_last_xact_replay_timestamp() at time zone 'UTC', 'YYYY-MM-DD HH24:MI:SS') || ' UTC', 'none yet')
      || chr(10) || '  received up to ' || coalesce(pg_last_wal_receive_lsn()::text, '-')
      || ', applied up to ' || coalesce(pg_last_wal_replay_lsn()::text, '-')" 2>/dev/null \
  || echo "  the replica isn't answering — see: docker compose logs --tail 50 replica"
echo "== files (synced every 5 minutes)"
echo "  last full sync: $(cat data/files/.last-sync 2>/dev/null || echo never)"
for d in data/files/*/; do [ -d "$d" ] && echo "  $(du -sh "$d" | cut -f1)	$d"; done
echo "== dumps"
echo "  hourly: $(ls data/dumps/hourly/*.dump 2>/dev/null | wc -l | tr -d ' ') kept, latest $(ls data/dumps/hourly/*.dump 2>/dev/null | tail -1 | xargs -n1 basename 2>/dev/null)"
echo "  daily:  $(ls data/dumps/daily/*.dump 2>/dev/null | wc -l | tr -d ' ') kept, latest $(ls data/dumps/daily/*.dump 2>/dev/null | tail -1 | xargs -n1 basename 2>/dev/null)"
