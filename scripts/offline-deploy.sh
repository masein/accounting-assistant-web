#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REMOTE_HOST="${1:-}"
REMOTE_DIR="${2:-/opt/accounting-assistant}"
ARCHIVE_NAME="accounting-assistant-offline.tar"
IMAGES_TAR="accounting-assistant-images.tar"

if [[ -z "$REMOTE_HOST" ]]; then
  echo "Usage: $0 <user@server> [remote-dir]" >&2
  exit 1
fi

cd "$PROJECT_DIR"
rm -f "$ARCHIVE_NAME" "$IMAGES_TAR"

echo "[1/5] Creating project archive..."
tmp_archive="$(mktemp "${TMPDIR:-/tmp}/accounting-assistant-offline.XXXXXX.tar.gz")"
tar -czf "$tmp_archive" \
  --exclude='.git' \
  --exclude='.venv' \
  --exclude='.env' \
  --exclude='__pycache__' \
  --exclude='.pytest_cache' \
  --exclude='.ruff_cache' \
  --exclude='node_modules' \
  --exclude='app/uploads' \
  --exclude='accounting-assistant-offline.tar' \
  --exclude='accounting-assistant-images.tar' \
  --exclude='*.pyc' \
  .
mv "$tmp_archive" "$ARCHIVE_NAME"

echo "[2/5] Saving Docker images..."
required_images=("accounting-assistant-api:latest" "postgres:16")
for image in "${required_images[@]}"; do
  if ! docker image inspect "$image" >/dev/null 2>&1; then
    echo "Required Docker image not found locally: $image" >&2
    exit 1
  fi
done
docker save -o "$IMAGES_TAR" "${required_images[@]}"

test -s "$ARCHIVE_NAME" || { echo "Project archive was not created correctly." >&2; exit 1; }
test -s "$IMAGES_TAR" || { echo "Docker image archive was not created correctly." >&2; exit 1; }

echo "[3/5] Copying files to server..."
ssh "$REMOTE_HOST" "mkdir -p '$REMOTE_DIR'"
scp "$ARCHIVE_NAME" "$IMAGES_TAR" "$REMOTE_HOST:$REMOTE_DIR/"

echo "[4/5] Extracting project and loading images on server..."
ssh "$REMOTE_HOST" "mkdir -p '$REMOTE_DIR' && cd '$REMOTE_DIR' && tar -xzf '$ARCHIVE_NAME' && docker load -i '$IMAGES_TAR' && docker compose up -d"

echo "[5/5] Deployment complete."
echo "Check status with: ssh $REMOTE_HOST 'cd $REMOTE_DIR && docker compose ps'"
