#!/bin/sh
# A throwaway QA server: a scratch database (aa_scratch_qa) on the compose `db`, the app at
# http://127.0.0.1:8899 running the code in $WT (default: this checkout). AI keys and SMTP are
# blanked, so AI and mail take their "not configured" paths. NEVER point this at a real database.
set -e
Q=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$Q/../.." && pwd)
WT=${WT:-$REPO}
# the running compose stack's database, found by its labels (so this works from a worktree too).
# This app's compose project, by name: "the first running db" picked another
# project's Postgres once (2026-10-07), where the run would have dropped and
# created its scratch database. QA_PROJECT overrides it.
PROJECT=${QA_PROJECT:-accounting-assistant}
DB=$(docker ps -q --filter label=com.docker.compose.project="$PROJECT" --filter label=com.docker.compose.service=db | head -1)
[ -n "$DB" ] || { echo "no running db for compose project '$PROJECT' (set QA_PROJECT, or: docker compose up -d db)"; exit 1; }
NETWORK=${QA_NETWORK:-${PROJECT}_default}
# the run's password: generated once, kept in scripts/qa/.env (git-ignored, mode 600), never printed
if [ ! -f "$Q/.env" ]; then
  (umask 077; printf 'QA_PASSWORD=%s\n' "$(python3 -c 'import secrets; print("Qa#" + secrets.token_urlsafe(18))')" > "$Q/.env")
fi
. "$Q/.env"
ENV_FILE=""; [ -f "$REPO/.env" ] && ENV_FILE="--env-file $REPO/.env"
docker rm -f aa-qa >/dev/null 2>&1 || true
docker exec -i "$DB" psql -U postgres -c "DROP DATABASE IF EXISTS aa_scratch_qa WITH (FORCE)" -c "CREATE DATABASE aa_scratch_qa" >/dev/null
mkdir -p "$Q/out"
docker run -d --name aa-qa --network "$NETWORK" -p 127.0.0.1:8899:8000 -v "$WT":/app -v "$Q":/qa -w /app \
  $ENV_FILE --entrypoint "" \
  -e QA_PASSWORD="$QA_PASSWORD" -e E2E_PASSWORD="$QA_PASSWORD" -e DATABASE_URL=postgresql+psycopg://postgres:postgres@db:5432/aa_scratch_qa \
  -e AUTH_SECRET=local-qa-secret-not-for-production-0123456789abcdef -e APP_ENV=test -e SCHEDULER_ENABLED=false \
  -e API_RATE_LIMIT_PER_MINUTE=600 -e E2E_USERNAME=e2e_owner \
  -e METIS_API_KEY= -e ANTHROPIC_API_KEY= -e AI_API_KEY= -e AI_PROVIDER=lmstudio -e LM_STUDIO_BASE_URL=http://127.0.0.1:9 \
  -e SMTP_HOST= -e SMTP_USERNAME= -e SMTP_PASSWORD= \
  aa-api-test sh -c "python -m app.prestart >/tmp/prestart.log 2>&1; PYTHONPATH=/app python /qa/qa_seed.py; python -m tests_e2e.seed_user >/dev/null; exec uvicorn app.main:app --host 0.0.0.0 --port 8000" >/dev/null
for i in $(seq 1 90); do curl -fsS http://127.0.0.1:8899/health >/dev/null 2>&1 && break; sleep 2; done
curl -fsS http://127.0.0.1:8899/health | head -c 200; echo
