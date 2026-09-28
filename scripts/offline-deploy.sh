#!/usr/bin/env bash
# Deploy to a server without internet access (roadmap 2026-09 §2.7).
#
#   scripts/offline-deploy.sh user@server [remote-dir]
#
# Ships what the production compose runs — the images and docker-compose.prod.yml
# with its backup scripts — never the source tree: that is inside the image,
# built here from the Dockerfile (locked, hash-checked dependencies). A local
# database dump, a backup folder or a .env can't ride along.
#
#   SKIP_BUILD=1        ship the image already tagged $API_IMAGE instead of building
#   API_IMAGE=repo:tag  the image name the server's compose runs (default below)
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REMOTE_HOST="${1:-}"
REMOTE_DIR="${2:-/opt/accounting-assistant}"
API_IMAGE="${API_IMAGE:-ghcr.io/masein/accounting-assistant-api:latest}"
BUNDLE="accounting-assistant-offline.tar.gz"
IMAGES_TAR="accounting-assistant-images.tar"

# Exactly what goes to the server besides the images.
SHIP=(
  docker-compose.prod.yml
  .env.prod.example
  DEPLOY.md
  scripts/backup-loop.sh
  scripts/backup.sh
  scripts/restore.sh
)
# Belt and braces: never these, even if one of the paths above grows a folder.
NEVER=('*.dump' '*.sql' '*.sql.gz' '*.tgz' '*.tar' '*.tar.gz' 'backups' '.env' '.env.local' 'app/uploads')

if [[ -z "$REMOTE_HOST" ]]; then
  echo "Usage: $0 <user@server> [remote-dir]" >&2
  exit 1
fi
cd "$PROJECT_DIR"
workdir="$(mktemp -d "${TMPDIR:-/tmp}/aa-offline.XXXXXX")"
trap 'rm -rf "$workdir"' EXIT

echo "[1/5] Building $API_IMAGE ..."
if [[ -z "${SKIP_BUILD:-}" ]]; then
  docker build --pull -t "$API_IMAGE" .
fi

echo "[2/5] Bundling the compose file and scripts..."
for f in "${SHIP[@]}"; do
  test -e "$f" || { echo "Missing $f" >&2; exit 1; }
done
excludes=()
for pattern in "${NEVER[@]}"; do excludes+=(--exclude="$pattern"); done
tar -czf "$workdir/$BUNDLE" "${excludes[@]}" "${SHIP[@]}"

echo "[3/5] Saving the images..."
images=("$API_IMAGE" "postgres:16" "containrrr/watchtower:1.7.1")
for image in "${images[@]}"; do
  if ! docker image inspect "$image" >/dev/null 2>&1; then
    docker pull "$image" || { echo "Image not available locally and can't be pulled: $image" >&2; exit 1; }
  fi
done
docker save -o "$workdir/$IMAGES_TAR" "${images[@]}"
test -s "$workdir/$BUNDLE" && test -s "$workdir/$IMAGES_TAR" || { echo "Bundle not created." >&2; exit 1; }

echo "[4/5] Copying to $REMOTE_HOST:$REMOTE_DIR ..."
ssh "$REMOTE_HOST" "mkdir -p '$REMOTE_DIR'"
scp "$workdir/$BUNDLE" "$workdir/$IMAGES_TAR" "$REMOTE_HOST:$REMOTE_DIR/"

echo "[5/5] Loading the images and starting the production compose..."
ssh "$REMOTE_HOST" "set -e; cd '$REMOTE_DIR'
  tar -xzf '$BUNDLE' && docker load -i '$IMAGES_TAR' && rm -f '$BUNDLE' '$IMAGES_TAR'
  if [ ! -f .env ]; then
    echo 'No .env on the server: copy .env.prod.example to .env, fill it in, then run:' >&2
    echo '  docker compose -f docker-compose.prod.yml up -d --pull never' >&2
    exit 2
  fi
  API_IMAGE='$API_IMAGE' docker compose -f docker-compose.prod.yml up -d --pull never"

echo "Deployed. Status: ssh $REMOTE_HOST 'cd $REMOTE_DIR && docker compose -f docker-compose.prod.yml ps'"
