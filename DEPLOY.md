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
- `private-uploads-<stamp>.tgz` — the `private_uploads` volume (period-close snapshots)

into `BACKUP_DIR` (default `./backups` next to the compose file) and deletes
copies older than `BACKUP_KEEP_DAYS` (14). **Copy that folder off the server**
(rclone to object storage, scp to another box, …) — a backup on the same disk
only protects against mistakes, not against losing the machine.

Before a risky deploy take one by hand:

```bash
scripts/backup.sh            # → ./backups/db-<stamp>.dump + uploads-<stamp>.tgz + private-uploads-<stamp>.tgz
```

Restore (destructive — replaces the live database; asks for confirmation):

```bash
scripts/restore.sh backups/db-20260924-020000.dump backups/uploads-20260924-020000.tgz \
                   backups/private-uploads-20260924-020000.tgz
```

It stops the api, runs `pg_restore --clean`, optionally unpacks the uploads
and the private uploads, and starts the api again (which applies any newer
migrations on boot).

### Servers set up before 2026-11-16: the `private_uploads` volume

Period-close snapshots (`app/private_uploads/snapshots`) used to be written into
the api container's own layer. Every redeploy deleted them, and no backup held
them. They now live on the `private_uploads` volume. The deployer only replaces
the image, never the compose file, so an existing server needs this once (done on
production on 2026-11-16):

```bash
cd /opt/accounting
for f in docker-compose.prod.yml scripts/backup-loop.sh scripts/backup.sh scripts/restore.sh; do cp -p "$f" "$f.before-$(date +%Y%m%d)"; done
# copy those four files in from the repo, then:
docker compose up -d --no-deps backup          # creates the volume (and mounts it read-only for the backup)
docker run --rm --user 0 --entrypoint sh -v accounting_private_uploads:/v accounting-assistant-api:deployed \
  -c 'mkdir -p /v/snapshots && chown -R 999:999 /v'   # the app's user (images from now on seed it themselves)
docker compose up -d --no-deps api             # a few seconds, as on any deploy
```

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

## 13. A live copy on another machine

A read-only copy of production's database, kept current as changes happen, runs
on the owner's always-on Mac (set up 2026-11-16). It also keeps the uploads,
the snapshots and the server's own backups, plus its own hourly and daily dumps.
It is a backup off the server, and a place to query the books or react to
changes without touching production. The stack is in
[`ops/live-copy/`](ops/live-copy/README.md); its README says how to run, check
and rebuild it. In short:

- **Database:** a Postgres 16 *hot standby* (physical streaming replication),
  usually a second behind production. It runs as `linux/amd64`, production's
  architecture; a physical standby must match it.
- **The link:** SSH, with a key of its own. The server's sshd allows no port
  forwarding (`AllowTcpForwarding no`, left so), so each database connection is
  carried by its own SSH session to `nc` on the server.
- **Files:** `rsync` every 5 minutes of the uploads, the private uploads and
  `/opt/accounting/backups`.
- **Dumps:** hourly (kept 7 days) and daily (kept 90 days). A live copy alone
  isn't a backup: a mistaken delete reaches it within a second.

What it needed on the server (no restart):

```sql
-- docker exec -i accounting-db-1 psql -U postgres
create role replicator with replication login password '…';   -- the password is in the copy's .env only
alter system set max_slot_wal_keep_size = '4GB';               -- what a copy that's behind may hold back
select pg_reload_conf();
```

```bash
# pg_hba.conf, in the accounting_pgdata volume: only from the server's own end of the stack's network
echo 'host replication replicator 172.18.0.1/32 scram-sha-256' | \
  docker exec -i accounting-db-1 sh -c 'cat >> /var/lib/postgresql/data/pg_hba.conf'
docker exec accounting-db-1 psql -U postgres -c 'select pg_reload_conf()'
```

The copy's SSH key in `/root/.ssh/authorized_keys` can run only
`/usr/local/bin/accounting-copy-gate`
([`ops/live-copy/server/accounting-copy-gate.sh`](ops/live-copy/server/accounting-copy-gate.sh)):

```
restrict,command="/usr/local/bin/accounting-copy-gate" ssh-ed25519 AAAA… accounting-replica (this Mac)
```

`restrict` turns off forwarding, a terminal and the rest. The gate allows a
database connection (`db-pipe`), checking for a synced folder (`has <name>`),
and `rsync` *sending* those three folders, so the key can't change anything on the
server. Every request goes into `/var/log/accounting-copy-gate.log`, refusals as
`DENIED`. Server admin is done as root with its password, not with this key.

The replication slot is `mac_replica`. Watch it with
`select slot_name, active, wal_status from pg_replication_slots;`. A copy that's
gone for good must drop its slot (`select pg_drop_replication_slot('mac_replica');`),
or production keeps up to 4 GB of changes for it. To remove the copy entirely:
drop the slot, delete the `replicator` role, the `pg_hba.conf` line and the key's
line in `authorized_keys`, then `docker compose down -v` on the Mac.

## 14. Telegram and Bale: the bot, and Telegram through a relay

The bot lives on the server (`app/services/messenger.py`). Someone who only
uses Telegram or Bale needs nothing else (scenarios CU1–CU9):

- **Signing in in the chat:** `/login` (or the greeting's **Sign in** button)
  asks for the username, then the password, then the two-factor code when it's
  on. Every answer — the password and the code above all — is deleted from the chat as soon
  as they're read; the checks are the web's and the phone's own — per-username
  and per-chat limits, a suspended company, an address still to confirm, the
  first password's lock — and each sign-in is an audit row (`login`, "Telegram
  chat (password)"). A chat can also still be linked from the app's account menu
  with a one-time code.
- **A new account in the chat:** `/signup` makes a person's own books, as the
  sign-up page does, when `ALLOW_SELF_SIGNUP` is on. With mail set up
  (`SMTP_HOST`) it also asks for an e-mail address, and the chat signs in only
  after it's confirmed. Business companies are still made by a super-admin.
- **Signing out:** `/stop`. A password change, "sign out everywhere", or a
  suspended company ends the chat's sign-in too, as it ends a browser's.
- **What it does:** a message is a turn of the web chat's own handler, as that
  user (company, role, language, AI budget): the quick answers, a statement's
  steps and a receipt's reading are the same. Voice notes are transcribed; a
  photo or a file (PDF, CSV, Excel, 20 MB at most) goes in as the web's
  attachments, its caption as the message. Each card comes with Confirm /
  Cancel. A file the reply has — an invoice's or a payslip's PDF, or through the
  assistant's `get_file` the journal's Excel sheet or a month's close pack — is
  sent as a document.

Telegram is filtered where production runs, so the server can't reach
api.telegram.org to set a webhook or send a reply. Its Telegram side runs on a
machine that can reach Telegram: the **relay**, in
[`ops/telegram-relay/`](ops/telegram-relay/README.md), on the owner's Mac next to
the live copy (§13).

```
Telegram  ⇄  relay (getUpdates; sends the replies)  ⇄  POST /bots/telegram/relay/update  →  the bot, on the server
```

- **Turning it on:** a platform admin goes to Settings → Messenger bots →
  Telegram → **Use a relay**, which makes a *relay key*, shown once
  (`POST /admin/messenger-bots/telegram/relay`). The server keeps only its
  hash, and no bot token at all. The relay's `.env` holds the key; its
  `token.env` holds the bot token, alone, because the watchdog (§15) reads it.
- **The relay's two calls:** `…/relay/hello` (on start and every 10 minutes:
  the bot's username, for the links the app shows, and when the relay was last
  seen) and `…/relay/update` (one update, plus a voice note's, a photo's or a
  file's bytes, which the server can't fetch — as a multipart form). The answer
  is the Bot API calls to make; a document comes with its bytes, and the relay
  uploads it. A wrong key gets a plain 404, as a wrong webhook secret does.
- **Nothing lost:** the server marks an update handled only at the end, so one
  it stopped half way through is handled again on the next try; when the server
  doesn't answer, the relay keeps its offset and sends the same updates again on
  its next round.
- **Webhooks:** relay mode replaces a webhook setup for that bot. Bale, which is
  reachable from Iran, stays on its webhook.
- **A new key:** clicking "New relay key" replaces the old one. The relay then
  stops with `the server doesn't know this relay key` until its `.env` has the
  new one.
- **Updating:** the server first, then the relay (`docker compose up -d
  --build` in its folder). A relay from 2026-11-28 sends files as a multipart
  form, which older servers don't read; an older relay works with a newer
  server, without photos, files or documents.

The relay is a single Python file with httpx, in Docker. It handles updates in
order per chat and different chats at the same time, keeps its offset across
restarts, retries the server three times, and never logs the token. Tests:
`tests/test_telegram_relay.py`, `tests/test_messenger_bot.py` and
`tests/test_messenger_sign_in.py`. For a local end-to-end run without an AI key,
`tests_e2e/fake_llm.py` stands in for the AI.

## 15. The watchdog

The live copy (§13) and the relay (§14) run on one Mac, and production can't
see either. The **watchdog**, in [`ops/watchdog/`](ops/watchdog/README.md),
checks all three from that Mac every five minutes: production's `/health`, the
relay's container and heartbeat, the copy's link to production, its files and
hourly dumps, and the Mac's free disk. It tells the owner on Telegram, through
the same bot, when a check has failed two rounds in a row, every six hours while
it stays failed, and when it is right again. A Mac that slept or was off says so
when it wakes. On the owner's Mac it runs from
`/Users/masein/Development/accounting/watchdog` under launchd, and tells the
Telegram chat `masein` linked in the app.

```bash
ops/watchdog/install.sh /path/to/watchdog     # install or update; then set watchdog.env
/path/to/watchdog/watchdog.sh --status        # each check, now
```

## 16. Support: the support button and the desk on the Mac

Every signed-in person can write to support: the headset button beside the bell on every page (and, from the phone
apps and the bot, the same conversation). The server keeps the conversation and nothing else — no AI and no Claude
credentials for support. The **support desk** — Claude in Nightshift's support desk, on the owner's Mac — answers
it, and the night shift fixes what it reports (`app/api/support.py`, `app/services/support_desk.py`;
Nightshift's `docs/SHADOW_AND_SUPPORT.md` → "The product's own support chat"):

```
person ─► support panel ─► the server keeps it ─► POST /desk/pending (desk key) ─► the desk on the Mac
   ▲                                                                                    │ tickets → the night shift
   └──── panel, badge, bell line, push ◄── POST /desk/reply ◄───────────────────────────┘ → "fixed, it's live"
```

- **Turning it on:** a platform admin goes to Settings → Support desk → **Make a desk key** (shown once; the server
  keeps only its hash). On the Mac, `~/.nightshift.env` gets `NIGHTSHIFT_SUPPORT_APP_URL` (this app's address) and
  `NIGHTSHIFT_SUPPORT_APP_KEY` (the key); Nightshift's `[support.app]` is on; restart its bot job
  (`launchctl kickstart -k gui/$(id -u)/ai.nightshift.bot`). The card and the support panel then say the desk is
  online. A new key replaces the old one; **Turn off** forgets it.
- **What a message carries:** the text, screenshots (pictures or PDFs, 20 MB), and what the page knew: the page, the
  app's version (the scripts' `?v=`), the language, the screen, and the last ten failed requests with their
  `x-request-id` (`window.__recentErrors`) — the id finds each in `docker compose logs api`.
- **The desk's door** (`/desk/…`, outside the session guard, the desk key as a bearer): `hello` (online while seen in
  the last three minutes), `pending` (the waiting messages with the person, their company and the conversation so
  far; a message taken and not answered within ten minutes is offered again), `reply`, `attachments/{id}` (only a
  file attached to a support message). A wrong key gets a plain 404.
- **What the desk reads** (the owner's choice for the pilot, 2026-11-28): besides the conversation, that one
  customer's books as files, read-only from the live copy (§13), and the app's code. It changes nothing.
- **Limits:** 30 messages a day per person (the desk runs on the owner's Claude plan). A reply is a bell line (one per
  newest unread reply) and a push, until read.
- **From the bot:** `/support` in a signed-in Telegram/Bale chat writes to the same conversation (text, screenshots,
  voice notes) until `/done`. The desk's reply comes back to that chat — a bot on a webhook sends it at once; through
  the relay (§14) it waits in `messenger_outbox` and the relay fetches it every few seconds
  (`POST /bots/<platform>/relay/outbox`). Update the server before the relay: an older server has no outbox, and the
  relay then asks only every five minutes.

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
