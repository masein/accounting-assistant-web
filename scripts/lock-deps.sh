#!/usr/bin/env bash
# Re-pin every dependency (with hashes) from the requirements*.txt ranges.
#
#   scripts/lock-deps.sh            # re-resolve everything to the newest allowed
#   scripts/lock-deps.sh fastapi    # move only these packages (keep the rest)
#
# uv resolves for the production image's platform — CPython 3.12 on linux
# x86-64 — whatever machine runs this (no emulation), so the pins and
# environment markers are the image's and CI's. The runtime lock is resolved
# first and constrains the dev and e2e locks, so tests run exactly what ships.
# The Dockerfile and CI install with --require-hashes; a lock that doesn't
# match its .txt fails tests/test_dependency_lock.py.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

upgrade="--upgrade"
if [[ $# -gt 0 ]]; then
  upgrade=""
  for p in "$@"; do upgrade="$upgrade --upgrade-package $p"; done
fi
target="--python-version 3.12 --python-platform x86_64-manylinux_2_28 --generate-hashes --quiet"

docker run --rm -v "$PWD":/src -w /src python:3.12-slim sh -c "
  pip install -q --retries 10 uv >/dev/null &&
  uv pip compile requirements.txt $target $upgrade -o requirements.lock &&
  uv pip compile requirements-dev.txt $target $upgrade -c requirements.lock -o requirements-dev.lock &&
  uv pip compile requirements-e2e.txt $target $upgrade -c requirements.lock -o requirements-e2e.lock
"
echo "Locked: requirements.lock, requirements-dev.lock, requirements-e2e.lock"
