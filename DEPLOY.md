# Deployment

CI builds the API Docker image and publishes it to **GitHub Container Registry**
(`ghcr.io/<owner>/accounting-assistant-api`); a server then pulls that image and
runs it with `docker-compose.prod.yml`. The old `docker.netixsystem.com`
registry (behind ArvanCloud) is kept only as a **best-effort mirror** — it can't
fail the publish, so ArvanCloud outages no longer block releases.

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

- on every push to `main` → tags `latest` and `sha-<short-sha>`
- on a version tag → the semver tags. Cut a release:
  ```bash
  git tag v1.0.0 && git push origin v1.0.0
  ```
  → publishes `…/accounting-assistant-api:1.0.0`, `:1.0`, `:1`, `:latest`.
- manually from the **Actions** tab (Run workflow).

## 3. Run it on the server

```bash
# once: install Docker + the compose plugin.

# Pull from GHCR. If the package is PRIVATE, log in first with a GitHub PAT
# (classic) that has `read:packages` — skip this line if you made it public:
echo "$GHCR_PAT" | docker login ghcr.io -u <github-username> --password-stdin

# copy these two files to the server (or `git clone`):
#   docker-compose.prod.yml
#   .env          (from .env.prod.example — set AUTH_SECRET, DB_PASSWORD, METIS_API_KEY)
cp .env.prod.example .env && $EDITOR .env
# Point the deploy at the GHCR image (drops ArvanCloud from the pull path):
echo 'API_IMAGE=ghcr.io/<owner>/accounting-assistant-api:latest' >> .env

docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
```

On start the container's entrypoint runs a **one-shot pre-start** — it waits for
the database, applies migrations, and seeds — and only then launches the web
server. So the schema is always at head before anything serves, a brand-new
database boots ready, and a **migration failure aborts the container loudly**
(non-zero exit, traceback in `docker compose logs api`) instead of leaving a
half-started app. First boot seeds the chart of accounts and the `admin`
super-admin under the Default company (log in as `admin` / `admin`, then change
the password).

Health check: `curl http://SERVER:8000/health` → `{"status":"ok",...}`. A cold
boot (migrations + fonts) can take up to ~60s before it turns healthy — that's
the healthcheck `start_period`, not a failure.

## 4. Update to a new image

```bash
# pin a new tag in .env (API_IMAGE=…:1.1.0) for reproducible deploys, or keep :latest
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
```

Postgres data (`pgdata`) and uploaded branding logos (`uploads`) persist across
redeploys in named volumes.

## 5. Zero-touch deploy (Watchtower)

The deploy is **pull-based**: a `watchtower` service in `docker-compose.prod.yml`
runs on the server, polls the registry, and auto-recreates the **api** container
whenever CI publishes a new image. Nothing has to reach *into* the server — it
only needs outbound access to the registry (which it already has), so this works
even though GitHub's cloud runners can't SSH in.

It's built into the prod stack — **just run the stack** (§3) and it's on:

```bash
docker compose -f docker-compose.prod.yml up -d   # starts api, db, AND watchtower
```

- Only the api is updated (it carries the label `com.centurylinklabs.watchtower.enable=true`); the database is never touched (`WATCHTOWER_LABEL_ENABLE`).
- On update Watchtower pulls the new image, recreates the container (which applies migrations on start), removes the old image, and keeps the `pgdata` / `uploads` volumes.
- Tune the check frequency with `WATCHTOWER_POLL_INTERVAL` (seconds; default `120`) in `.env`.

So the full loop is: **push to `main` → CI builds & pushes `:latest` → Watchtower
sees it within the poll interval → api redeploys.** For a controlled release
instead, pin `API_IMAGE=…:1.2.3` in `.env` and re-run `up -d` when you want it.

> The old SSH-based deploy job was removed — GitHub's runners can't reach the
> server. If you added `DEPLOY_ENABLED` / `DEPLOY_*` secrets earlier, you can
> delete them; they're unused now.

## 6. After a deploy: the 30-second health check

Right after `up -d` (or a Watchtower auto-update), confirm the api came back on
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

## Notes
- `docker-compose.yml` (no suffix) stays the **dev** stack: it builds locally
  and bind-mounts the source for live reload. `docker-compose.prod.yml` runs the
  published image with no source mount.
- Put a TLS reverse proxy (Caddy/Nginx/Traefik) in front for a public URL and
  set `APP_CORS_ORIGINS` to that URL.
