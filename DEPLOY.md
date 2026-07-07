# Deployment

CI builds the API Docker image and pushes it to the private registry
`docker.netixsystem.com`; a server then pulls that image and runs it with
`docker-compose.prod.yml`.

## 1. One-time GitHub setup

Add these under **Settings → Secrets and variables → Actions → Secrets**:

| Secret | Value |
| --- | --- |
| `REGISTRY_USERNAME` | login for `docker.netixsystem.com` |
| `REGISTRY_PASSWORD` | password / access token for that user |

Optional **Variable** (same screen, "Variables" tab) — only if your registry
needs a project/namespace (e.g. Harbor):

| Variable | Example |
| --- | --- |
| `IMAGE_NAME` | `docker.netixsystem.com/<project>/accounting-assistant-api` |

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
# once: install Docker + the compose plugin, then:
docker login docker.netixsystem.com          # same creds as the CI secret

# copy these two files to the server (or `git clone`):
#   docker-compose.prod.yml
#   .env          (from .env.prod.example — set AUTH_SECRET, DB_PASSWORD, METIS_API_KEY)
cp .env.prod.example .env && $EDITOR .env

docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
```

On start the API builds the schema and applies migrations automatically (its
lifespan runs `create_all` → Alembic `stamp`/`upgrade` → seed), so a brand-new
database boots ready. First boot seeds the chart of accounts and the `admin`
super-admin under the Default company (log in as `admin` / `admin`, then change
the password).

Health check: `curl http://SERVER:8000/health` → `{"status":"ok",...}`.

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

## Notes
- `docker-compose.yml` (no suffix) stays the **dev** stack: it builds locally
  and bind-mounts the source for live reload. `docker-compose.prod.yml` runs the
  published image with no source mount.
- Put a TLS reverse proxy (Caddy/Nginx/Traefik) in front for a public URL and
  set `APP_CORS_ORIGINS` to that URL.
