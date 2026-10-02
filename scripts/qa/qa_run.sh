#!/bin/sh
# Run one QA script (Playwright) against aa-qa: qa_run.sh <script.py> [args]
Q=$(cd "$(dirname "$0")" && pwd)
. "$Q/.env"
docker run --rm --network container:aa-qa -v "$Q":/qa -e QA_PASSWORD="$QA_PASSWORD" -e BASE=http://localhost:8000 -w /qa \
  aa-playwright:1.49.1 python -u /qa/"$1" "$2" "$3"
