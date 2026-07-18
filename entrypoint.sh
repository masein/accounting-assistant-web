#!/bin/sh
# Container entrypoint. Runs the schema bootstrap (DB wait + migrate + seed) in a
# ONE-SHOT pre-start step, THEN execs the web server. This is what makes deploys
# self-healing and deterministic:
#   - uvicorn only starts after the schema is at head, so /health answers as soon
#     as it's up (no serving-before-migrated window, no false "unhealthy").
#   - if migrations fail, we exit non-zero HERE with the traceback in the logs
#     (see `docker compose logs api`) instead of crash-looping a half-started app.
# `set -e` makes a non-zero pre-start abort the container rather than fall through
# to uvicorn. `exec` hands PID 1 to uvicorn so signals/shutdown behave correctly.
set -e

echo "[entrypoint] pre-start: waiting for DB, applying migrations, seeding ..."
python -m app.prestart
echo "[entrypoint] pre-start OK — starting web server: $*"

# Tell the app the schema is already bootstrapped so the lifespan skips the
# (idempotent) redo and comes up fast.
export ENTRYPOINT_BOOTSTRAP=1
exec "$@"
