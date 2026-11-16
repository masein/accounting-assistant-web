# Deployment

CI builds the API Docker image and publishes it to **GitHub Container Registry**
(`ghcr.io/<owner>/accounting-assistant-api`) and copies it to the
`docker.netixsystem.com` mirror (behind ArvanCloud) — best-effort, so a mirror
outage never blocks a release. The server runs it with `docker-compose.prod.yml`,
and its `deployer` takes each new image from **whichever registry answers** (§5).

## 1. One-time GitHub setup

Nothing is required for the **primary** GHCR push — it uses the built-in
`GITHUB_TOKEN`. The published package is **private by default** (inherits the
repo). Either make the package public (Package settings → Change visibility) or,
to keep it private, have the server log in with a PAT (see §3).

Optional **Variables** (Settings → Secrets and variables → Actions → Variables):

| Variable | Purpose |
| --- | --- |
| `MIRROR_ENABLED` | set to `false` to skip the `docker.netixsystem.com` mirror entirely |
| `IMAGE_NAME` | override the mirror image path (e.g. Harbor project) |

Optional **Secrets** — only if the mirror registry is behind auth:
`REGISTRY_USERNAME` / `REGISTRY_PASSWORD`.

## 2. Publish an image

The **Publish image** workflow (`.github/workflows/publish-image.yml`) runs:

- on every green CI run of `main` → tags `latest` and `sha-<short-sha>`, on GHCR
  and (best-effort) on the mirror; **Mirror sync** (`.github/workflows/mirror-sync.yml`)
  checks hourly that the mirror's `latest` is GHCR's and copies it if not
- on a version tag → the semver tags. Cut a release:
  ```bash
  git tag v1.0.0 && git push origin v1.0.0
  ```
  → publishes `…/accounting-assistant-api:1.0.0`, `:1.0`, `:1`, `:latest`.
- manually from the **Actions** tab (Run workflow).

## 3. Run it on the server

```bash
# once: install Docker + the compose plugin. Both registries allow anonymous
# pulls; if you make the GHCR package private, `docker login ghcr.io` first
# with a GitHub PAT (classic) that has `read:packages`.

# into one folder (the docs use /opt/accounting), copy from the repo:
#   docker-compose.prod.yml
#   scripts/deploy.sh  scripts/backup-loop.sh  scripts/backup.sh  scripts/restore.sh
#   .env          (from .env.prod.example — set AUTH_SECRET, DB_PASSWORD, METIS_API_KEY,
#                  and DEPLOY_DIR to this folder's absolute path)
cd /opt/accounting
cp .env.prod.example .env && $EDITOR .env

sh scripts/deploy.sh      # the first image, from ghcr.io or (if that doesn't answer) the mirror
docker compose up -d      # db, api, deployer, backup  (.env sets COMPOSE_FILE=docker-compose.prod.yml)
```

On start the container's entrypoint runs a **one-shot pre-start** — it waits for
the database, applies migrations, and seeds — and only then launches the web
server. So the schema is always at head before anything serves, a brand-new
database boots ready, and a **migration failure aborts the container loudly**
(non-zero exit, traceback in `docker compose logs api`) instead of leaving a
half-started app. First boot seeds the chart of accounts and the `admin`
super-admin under the Default company. Its first password is
`INITIAL_ADMIN_PASSWORD` from `.env` when you set one; otherwise a random one is
written once to the log — find it with
`docker compose logs api | grep "one-time password"`. Sign in as `admin` with
it and change it straight away. (Outside production the seeded password is
`admin`, and that sign-in is locked to the change-password screen.)

Health check: `curl http://SERVER:8000/health` → `{"status":"ok",...}`. A cold
boot (migrations + fonts) can take up to ~60s before it turns healthy — that's
the healthcheck `start_period`, not a failure.

## 4. Update to a new image

Nothing to do: the deployer (§5) does it within `DEPLOY_INTERVAL` seconds of a
publish. To deploy right now, or to see what happened:

```bash
docker compose run --rm deployer once   # one check now (or: sh scripts/deploy.sh)
cat .deploy/status                      # the last check: up to date / deployed / rolled back, and from where
docker compose logs --tail=50 deployer  # every deploy, fallback and rollback, with times
```

For a controlled release instead of following `main`, pin a published version
in `.env` (`DEPLOY_TAG=1.2.3`) and run `docker compose up -d deployer`; set it
back to `latest` to follow `main` again.

Postgres data (`pgdata`) and uploaded branding logos (`uploads`) persist across
redeploys in named volumes.

## 5. Zero-touch deploys from two registries

Every green build of `main` is published to **two registries** (§2):
`ghcr.io/masein/accounting-assistant-api` (GitHub's, the primary) and
`docker.netixsystem.com/accounting-assistant-api` (the mirror; the hourly
**Mirror sync** workflow copies anything the publish couldn't). Either one can be
out — the mirror answered 502 for days in November 2026, and ghcr.io can be out
of reach from some networks — so the server takes each new image from **whichever
answers**, on its own.

The `deployer` service in `docker-compose.prod.yml` runs `scripts/deploy.sh loop`.
Every `DEPLOY_INTERVAL` seconds (default 120) it:

1. pulls `:DEPLOY_TAG` (default `latest`) from the first registry in
   `DEPLOY_REGISTRIES` that answers — GHCR, then the mirror. A registry that
   failed is skipped for `DEPLOY_RETRY_AFTER` seconds (default 30 minutes), so a
   dead one doesn't slow every check; then it's tried first again;
2. stops there if that image is the one running;
3. otherwise tags the running image `accounting-assistant-api:previous`, the new
   one `accounting-assistant-api:deployed` (the tag the api service runs), and
   recreates **only the api** (never the database);
4. waits up to `DEPLOY_HEALTH_TIMEOUT` seconds (default 300) for the api's health
   check. Healthy: done, older copies removed. Not healthy: the previous image
   is put back, and the new one isn't tried again until a newer one is published.

So the full loop is: **push to `main` → CI passes → Publish pushes `:latest` to
GHCR and the mirror → the deployer pulls it from whichever answers → api
redeploys, or rolls back.** No one needs to reach into the server: it only
pulls, so this works though GitHub's runners can't SSH in.

What to know when working on it:

- The api's image is a **local tag**; `docker compose pull` / `up` never pull it
  (`pull_policy: never`). Only `scripts/deploy.sh` pulls. To run a particular
  image by hand: `docker tag <image> accounting-assistant-api:deployed && docker compose up -d api`.
- The deployer holds the Docker socket, so its image is pinned to an exact
  version (`docker:27.5.1-cli`, which has the compose plugin).
- It mounts the stack's folder at the **same path** as on the host
  (`DEPLOY_DIR`), so compose inside is the same project. If they differ it refuses
  to act and says so in its log — it never starts a second stack.
- `.deploy/` (ignored by git) holds its state: `status`, `bad` (the image that
  failed its health check), `failed-<registry>` (when one last failed).
- A rollback after a new image already ran its migrations puts the old code on
  the new schema. Migrations here only add, so the old code runs, but fix
  forward: publish a fixed image and the deployer takes it.
- Offline servers: `DEPLOY_REGISTRIES=` (empty) keeps the deployer idle;
  `scripts/offline-deploy.sh` ships the image already tagged `:deployed`.
- Change the order or add a registry by editing `DEPLOY_REGISTRIES` in `.env`
  (space-separated, tried left to right), then `docker compose up -d deployer`.

### Moving a server from Watchtower to the deployer

A server set up before November 2026 runs `watchtower` and an api image named
straight from one registry (`API_IMAGE=…`). Once, in its folder:

```bash
cd /opt/accounting
cp docker-compose.prod.yml docker-compose.prod.yml.bak      # keep the old one
# copy in from the repo: docker-compose.prod.yml, scripts/deploy.sh (and the backup scripts)
# in .env: delete API_IMAGE= and WATCHTOWER_POLL_INTERVAL=, and add
#   COMPOSE_FILE=docker-compose.prod.yml
#   DEPLOY_DIR=/opt/accounting
# and check the settings the newer compose reads: AUTH_COOKIE_SECURE (true for HTTPS),
# FORWARDED_ALLOW_IPS, API_BIND, APP_PUBLIC_URL, APP_CORS_ORIGINS (see .env.prod.example)
sh scripts/deploy.sh                    # the first image from whichever registry answers — or, if
                                        # none does, the copy this server already runs, until one does
docker compose up -d --remove-orphans   # api on it, the deployer and backup started, watchtower removed
cat .deploy/status && docker compose ps
```

If you had added backups with a separate `docker-compose.backup.yml`, the
production file now has the `backup` service itself: run without that file and
delete it. To go back, restore `docker-compose.prod.yml.bak` and the `.env` lines.

## 6. After a deploy: the 30-second health check

Right after `up -d` (or an automatic deploy — `cat .deploy/status`), confirm the api came back on
its own — it should, with no manual restart:

```bash
# 1. Is it Up (healthy)?  NOT Restarting / Exited.
docker compose -f docker-compose.prod.yml ps

# 2. If it isn't healthy, the reason is in the pre-start logs (DB wait,
#    migration error, seed error — all printed loudly before the web server):
docker compose -f docker-compose.prod.yml logs --tail=80 api
```

What you want to see in the logs, in order:

```
[entrypoint] pre-start: waiting for DB, applying migrations, seeding ...
app.prestart Database is accepting connections (after 1 attempt(s)).
app.prestart Running schema bootstrap (create_all → migrate → seed) ...
app.prestart Schema bootstrap complete in N.Ns.
[entrypoint] pre-start OK — starting web server: uvicorn ...
INFO:     Uvicorn running on http://0.0.0.0:8000
```

If a migration is broken you'll instead see `PRE-START FAILED` with the full
traceback and the container will exit non-zero (and, under `restart:
unless-stopped`, keep retrying loudly) — it will **not** serve a half-migrated
app. Fix the migration and redeploy.

## 7. Backups and restore

The prod stack includes a `backup` service (`postgres:16`, `scripts/backup-loop.sh`).
Every day at `BACKUP_HOUR` (default 02, server time) it writes

- `db-<stamp>.dump` — `pg_dump -Fc` (compressed custom format) of the database
- `uploads-<stamp>.tgz` — the uploads volume (receipts, statements, branding)

into `BACKUP_DIR` (default `./backups` next to the compose file) and deletes
copies older than `BACKUP_KEEP_DAYS` (14). **Copy that folder off the server**
(rclone to object storage, scp to another box, …) — a backup on the same disk
only protects against mistakes, not against losing the machine.

Before a risky deploy take one by hand:

```bash
scripts/backup.sh            # → ./backups/db-<stamp>.dump + uploads-<stamp>.tgz
```

Restore (destructive — replaces the live database; asks for confirmation):

```bash
scripts/restore.sh backups/db-20260924-020000.dump backups/uploads-20260924-020000.tgz
```

It stops the api, runs `pg_restore --clean`, optionally unpacks the uploads,
and starts the api again (which applies any newer migrations on boot).

## 8. Proxy, cookies and ports

- `AUTH_COOKIE_SECURE=true` (default) — the session cookie always carries
  `Secure`; production is HTTPS. Only set it to `false` on a plain-HTTP test box.
- `FORWARDED_ALLOW_IPS` — the proxy address(es) allowed to set `X-Forwarded-*`
  (uvicorn runs with `--proxy-headers`). Default `127.0.0.1`; use your proxy's IP
  or `*` if the proxy is remote and port 8000 is firewalled to it.
- `API_BIND=127.0.0.1` when nginx/caddy on the same host terminates TLS, so the
  plain-HTTP port is not exposed to the internet.
- In `APP_ENV=prod` every response carries `Strict-Transport-Security`.
- `TRUST_PROXY_HEADERS=true` makes the app read `X-Forwarded-For` itself (audit
  IPs, per-IP login limits). Leave it `false` when `FORWARDED_ALLOW_IPS` covers
  your proxy — uvicorn already rewrites the client address, and trusting the
  header blindly lets anyone forge it.

## 9. More than one worker

One uvicorn worker is the default. To use several, set `WEB_CONCURRENCY=2` (up
to 4) in `.env` and recreate the api container. This is safe because:

- **Limits that matter are shared.** Login, sign-up, e-mail resend and chat
  limits are counted in Postgres (`rate_limit_events`), so every worker sees
  the same attempts.
- **Caches can't go stale across workers.** The dashboard and insights caches
  key on a per-company books version that is bumped in the same transaction
  as any ledger or invoice write.
- **Two-step uploads work on any worker.** Excel import tokens are stored in
  `upload_tokens`, scoped to the company.
- **AI provider changes reach every worker** within `AI_CONFIG_REFRESH_SECONDS`
  (default 15).
- **Background jobs run once.** Each scheduler tick takes a Postgres advisory
  lock, so only one worker runs it.

The per-request API limiter (120 requests per minute per user) stays per
worker, so with N workers the effective limit is N × 120. Each worker also
loads the app separately, so budget roughly 250 MB of memory per worker.

## 10. Logs, errors and metrics

- **Logs are JSON by default in production**, one object per line, with `request_id`, `company_id`, `method`, `path`, `status`, `ms` and `user_id` on every request line. Set `LOG_FORMAT=text` for human-readable lines. Every response carries an `x-request-id` header, and error bodies include it too, so an id a user quotes finds the whole request with `docker compose logs api | grep <id>`.
- **Error reporting is optional.** Set `SENTRY_DSN` to a Sentry project or a self-hosted GlitchTip (same protocol). Unhandled exceptions are sent with the request id as a tag. Cookies, auth headers, query strings and request bodies are stripped before sending.
- **Metrics:** set `METRICS_TOKEN`, then scrape `GET /metrics` with `Authorization: Bearer <token>`. Without a token the endpoint answers 404. Series:
  - `aa_http_requests_total` and `aa_http_request_duration_seconds`, labelled by route template, never raw ids;
  - `aa_llm_calls_total` and `aa_llm_call_duration_seconds`, by provider and purpose (chat, suggest, ocr) with an ok or error outcome;
  - `aa_job_runs_total` and `aa_job_duration_seconds`, per scheduler job.

  With several workers, `PROMETHEUS_MULTIPROC_DIR` (set in the compose file) aggregates them; the entrypoint empties it on start.

  A minimal Prometheus job:

  ```yaml
  - job_name: accounting-assistant
    scheme: https
    authorization: { credentials: "<METRICS_TOKEN>" }
    static_configs: [{ targets: ["accounting.example.com"] }]
  ```
- **Log rotation** is already on for every container: 10 MB × 5 files (`x-logging` in `docker-compose.prod.yml`).

## 11. A restricted database role for the web server

By default the web server connects as `DB_USER`, the owner of every table. To
make the audit log append-only even against the app's own credentials, give
the web server its own role:

1. Add a password to `.env` (at least 16 characters):
   ```bash
   echo "APP_DB_PASSWORD=$(openssl rand -hex 24)" >> .env
   ```
2. Recreate the api container (`docker compose -f docker-compose.prod.yml up -d api`,
   or let the deployer do it on the next image).

On start, pre-start (still running as `DB_USER`) creates the role `aa_app`
(`APP_DB_USER` to rename it) and grants it read and write on rows only:

- it cannot create, alter or drop tables, or turn the audit trigger off;
- on `audit_logs` it can only INSERT and SELECT — no UPDATE, DELETE or TRUNCATE;
- on `alembic_version` it can only SELECT.

The grants are re-applied on every start, so tables added by later migrations
are covered, and changing `APP_DB_PASSWORD` rotates the role's password on the
next start. Check it took:

```bash
docker compose -f docker-compose.prod.yml exec db psql -U postgres -d accounting -c "\du aa_app"
```

Backups and restores keep running as `DB_USER`; after a restore the next api
start re-applies the grants. To go back, remove `APP_DB_PASSWORD` and recreate
the api container; the unused role can then be dropped with
`DROP OWNED BY aa_app; DROP ROLE aa_app;`.

## 12. Dependencies and servers without internet

The image installs **`requirements.lock`** with `pip install --require-hashes`:
every package pinned to the version CI tested, every file checked against its
hash, whatever PyPI serves on the day of the build. `requirements.txt` keeps
the allowed ranges; the locks are generated from it:

```bash
scripts/lock-deps.sh              # re-resolve everything (newest allowed)
scripts/lock-deps.sh fastapi      # move only fastapi, keep the rest
```

It resolves inside `python:3.12-slim` for `linux/amd64` (the image's platform)
and writes `requirements.lock`, `requirements-dev.lock` (CI's test jobs) and
`requirements-e2e.lock` (the browser job). Commit the three with the change to
`requirements*.txt`; `tests/test_dependency_lock.py` fails when a lock doesn't
match its ranges. The dev stack (`docker-compose.yml`) still installs the
ranges, so a local run needs no re-lock.

**A server without internet:** `scripts/offline-deploy.sh user@server` builds
the image, tags it `accounting-assistant-api:deployed`, saves it with
`postgres:16` and the deployer's `docker` image, and copies them with
`docker-compose.prod.yml`, `.env.prod.example`, the backup scripts and
`scripts/deploy.sh` — never
the source tree, a `.env`, a `backups/` folder or a database dump. On the
server it loads the images and runs `docker compose -f docker-compose.prod.yml
up -d --pull never` (the first time it stops and asks for the `.env`). Set
`DEPLOY_REGISTRIES=` (empty) in that server's `.env` so the deployer stays idle.

## Notes
- `docker-compose.yml` (no suffix) stays the **dev** stack: it builds locally
  and bind-mounts the source for live reload. `docker-compose.prod.yml` runs the
  published image with no source mount.
- Put a TLS reverse proxy (Caddy/Nginx/Traefik) in front for a public URL and
  set `APP_CORS_ORIGINS` to that URL.
- Outgoing mail (signup confirmation, operator alerts) is a separate one-time
  setup — see **[docs/MAIL_SETUP.md](docs/MAIL_SETUP.md)**. It stays off until
  `SMTP_HOST` is set, so a deploy without it is fine. Note that the prod stack
  has no `.env` inside the container: mail variables reach the app only because
  `docker-compose.prod.yml` passes them through.
