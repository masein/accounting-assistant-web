#!/usr/bin/env bash
# On-demand backup of the PRODUCTION stack from the host:
#   scripts/backup.sh [backup-dir]        (default: ./backups)
# Produces db-<stamp>.dump (pg_dump custom format), uploads-<stamp>.tgz and
# private-uploads-<stamp>.tgz (period snapshots).
# Run it before a risky deploy or migration; the `backup` service does the
# same every night. Copy the folder off the box afterwards.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
COMPOSE=(docker compose -f docker-compose.prod.yml)
OUT="${1:-./backups}"
mkdir -p "$OUT"
stamp="$(date +%Y%m%d-%H%M%S)"
DB_USER="$(grep -E '^DB_USER=' .env 2>/dev/null | cut -d= -f2- || true)"
DB_USER="${DB_USER:-postgres}"

echo "[backup] database → $OUT/db-$stamp.dump"
"${COMPOSE[@]}" exec -T db pg_dump -U "$DB_USER" -d accounting -Fc -Z 6 > "$OUT/db-$stamp.dump"

echo "[backup] uploads volume → $OUT/uploads-$stamp.tgz"
"${COMPOSE[@]}" run --rm --no-deps --entrypoint sh -v "$(cd "$OUT" && pwd)":/out api \
  -c "tar -czf /out/uploads-$stamp.tgz -C /app/app/uploads ."

echo "[backup] private_uploads volume → $OUT/private-uploads-$stamp.tgz"
"${COMPOSE[@]}" run --rm --no-deps --entrypoint sh -v "$(cd "$OUT" && pwd)":/out api \
  -c "tar -czf /out/private-uploads-$stamp.tgz -C /app/app/private_uploads ."

ls -lh "$OUT/db-$stamp.dump" "$OUT/uploads-$stamp.tgz" "$OUT/private-uploads-$stamp.tgz"
echo "[backup] done. Copy $OUT somewhere off this machine."
