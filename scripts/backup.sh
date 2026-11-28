#!/usr/bin/env bash
# On-demand backup of the PRODUCTION stack from the host:
#   scripts/backup.sh [backup-dir]        (default: ./backups)
# Produces db-<stamp>.dump (pg_dump custom format), uploads-<stamp>.tgz and
# private-uploads-<stamp>.tgz (period snapshots).
# Run it before a risky deploy or migration; the `backup` service does the
# same every night. Copy the folder off the box afterwards.
#
# Each file is written as NAME.tmp and renamed only when complete. If any step
# fails the script stops with an error and removes the half-written file, so a
# truncated dump is never left behind looking like a good one (review 2026-11-28).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
COMPOSE=(docker compose -f docker-compose.prod.yml)
OUT="${1:-./backups}"
mkdir -p "$OUT"
stamp="$(date +%Y%m%d-%H%M%S)"
DB_USER="$(grep -E '^DB_USER=' .env 2>/dev/null | cut -d= -f2- || true)"
DB_USER="${DB_USER:-postgres}"

partial=""     # the file being written right now, removed if the script stops before it's complete
finish() {
  status=$?
  if [ "$status" -ne 0 ]; then
    [ -n "$partial" ] && rm -f "$partial"
    echo "[backup] FAILED (exit $status) — this backup is incomplete; nothing half-written was kept." >&2
  fi
}
trap finish EXIT

echo "[backup] database → $OUT/db-$stamp.dump"
partial="$OUT/db-$stamp.dump.tmp"
"${COMPOSE[@]}" exec -T db pg_dump -U "$DB_USER" -d accounting -Fc -Z 6 > "$partial"
mv "$partial" "$OUT/db-$stamp.dump"

echo "[backup] uploads volume → $OUT/uploads-$stamp.tgz"
partial="$OUT/uploads-$stamp.tgz.tmp"
"${COMPOSE[@]}" run --rm --no-deps --entrypoint sh -v "$(cd "$OUT" && pwd)":/out api \
  -c "tar -czf /out/uploads-$stamp.tgz.tmp -C /app/app/uploads ."
mv "$partial" "$OUT/uploads-$stamp.tgz"

echo "[backup] private_uploads volume → $OUT/private-uploads-$stamp.tgz"
partial="$OUT/private-uploads-$stamp.tgz.tmp"
"${COMPOSE[@]}" run --rm --no-deps --entrypoint sh -v "$(cd "$OUT" && pwd)":/out api \
  -c "tar -czf /out/private-uploads-$stamp.tgz.tmp -C /app/app/private_uploads ."
mv "$partial" "$OUT/private-uploads-$stamp.tgz"
partial=""

ls -lh "$OUT/db-$stamp.dump" "$OUT/uploads-$stamp.tgz" "$OUT/private-uploads-$stamp.tgz"
echo "[backup] done. Copy $OUT somewhere off this machine."
