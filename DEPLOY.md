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

The API container runs `alembic upgrade head` on start, so schema migrations
apply automatically. First boot seeds the chart of accounts and the `admin`
super-admin (log in as `admin` / `admin`, then change the password).

Health check: `curl http://SERVER:8000/health` → `{"status":"ok",...}`.

## 4. Update to a new image

```bash
# pin a new tag in .env (API_IMAGE=…:1.1.0) for reproducible deploys, or keep :latest
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
```

Postgres data (`pgdata`) and uploaded branding logos (`uploads`) persist across
redeploys in named volumes.

## 5. Zero-touch deploy (optional)

Make every successful image publish deploy itself: the **Publish image**
workflow has a `deploy` job that SSHes into the server, syncs
`docker-compose.prod.yml`, and runs `compose pull && up -d`. It is **off until
you enable it**.

**On the server (one-time):**
```bash
mkdir -p /opt/accounting            # this becomes DEPLOY_PATH
cd /opt/accounting
cp /path/to/.env .                  # from .env.prod.example, secrets filled in
# ensure the deploy user is in the docker group:  sudo usermod -aG docker "$USER"
# add the deploy public key to ~/.ssh/authorized_keys
```
(The compose file is copied by CI each run, so you only maintain `.env`.)

**In GitHub → Settings → Secrets and variables → Actions:**

Add a **Variable** to turn it on:

| Variable | Value |
| --- | --- |
| `DEPLOY_ENABLED` | `true` |

Add **Secrets**:

| Secret | Value |
| --- | --- |
| `DEPLOY_HOST` | server hostname / IP |
| `DEPLOY_USER` | SSH user (in the `docker` group) |
| `DEPLOY_SSH_KEY` | that user's **private** key (full PEM contents) |
| `DEPLOY_PATH` | e.g. `/opt/accounting` |
| `DEPLOY_PORT` | optional, defaults to `22` |

Now: push to `main` (or a `v*` tag, or Run workflow) → image builds → server
pulls it and restarts. Set `DEPLOY_ENABLED` to anything but `true` to pause it.

## Notes
- `docker-compose.yml` (no suffix) stays the **dev** stack: it builds locally
  and bind-mounts the source for live reload. `docker-compose.prod.yml` runs the
  published image with no source mount.
- Put a TLS reverse proxy (Caddy/Nginx/Traefik) in front for a public URL and
  set `APP_CORS_ORIGINS` to that URL.
