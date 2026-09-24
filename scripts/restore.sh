#!/usr/bin/env bash
# Restore the PRODUCTION database (and optionally the uploads volume) from a
# backup made by scripts/backup.sh or the `backup` service:
#   scripts/restore.sh backups/db-20260924-020000.dump [backups/uploads-20260924-020000.tgz]
# DESTRUCTIVE: replaces the current database contents. Stops the api first,
# restores with pg_restore --clean, then starts the api (which re-runs
# migrations if the dump is older than the image).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
COMPOSE=(docker compose -f docker-compose.prod.yml)
DUMP="${1:?usage: restore.sh <db.dump> [uploads.tgz]}"
UPLOADS="${2:-}"
DB_USER="$(grep -E '^DB_USER=' .env 2>/dev/null | cut -d= -f2- || true)"
DB_USER="${DB_USER:-postgres}"
[ -f "$DUMP" ] || { echo "no such file: $DUMP" >&2; exit 1; }

read -r -p "This REPLACES the live database with $DUMP. Type RESTORE to continue: " ans
[ "$ans" = "RESTORE" ] || { echo "aborted"; exit 1; }

echo "[restore] stopping api"
"${COMPOSE[@]}" stop api
echo "[restore] restoring database"
"${COMPOSE[@]}" exec -T db pg_restore -U "$DB_USER" -d accounting --clean --if-exists --no-owner --no-acl < "$DUMP"
if [ -n "$UPLOADS" ]; then
  [ -f "$UPLOADS" ] || { echo "no such file: $UPLOADS" >&2; exit 1; }
  echo "[restore] restoring uploads volume"
  "${COMPOSE[@]}" run --rm --no-deps --entrypoint sh -v "$(cd "$(dirname "$UPLOADS")" && pwd)":/in api \
    -c "rm -rf /app/app/uploads/* && tar -xzf /in/$(basename "$UPLOADS") -C /app/app/uploads"
fi
echo "[restore] starting api"
"${COMPOSE[@]}" up -d api
echo "[restore] done — check: ${COMPOSE[*]} logs --tail=60 api"
