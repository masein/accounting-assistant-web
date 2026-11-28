# Live copy of production

A read-only copy of production's accounting database on another machine, kept current as changes happen. It also
holds production's uploaded files and hourly and daily dumps. [DEPLOY.md §13](../../DEPLOY.md) covers the production
side (the `replicator` role, `pg_hba.conf`, the slot limit and the SSH gate); this folder is the machine that keeps
the copy.

Run it from a folder **outside the repository**, because it keeps a password and production's data:

```bash
cp -R ops/live-copy ~/accounting-live-copy && cd ~/accounting-live-copy
cp .env.example .env && chmod 600 .env                 # fill in REPL_PASSWORD and SSH_HOST
ssh-keygen -t ed25519 -N "" -C "accounting-replica" -f ~/.ssh/accounting_replica
# put the public key in the SSH user's ~/.ssh/authorized_keys on production, behind the gate (DEPLOY.md §13), then:
ssh-keyscan -t ed25519 <SSH_HOST> > known_hosts && chmod 600 known_hosts   # check its fingerprint on the server
docker compose up -d                                   # the first start copies production (pg_basebackup)
sh status.sh
```

On the owner's Mac it runs from a folder beside the relay and the watchdog (for example `~/accounting/prod-replica`).

## What runs

| service   | what it does |
|-----------|--------------|
| `tunnel`  | Leads `tunnel:5432` to production's database. Production's sshd allows no port forwarding, so each connection is carried by its own SSH session. That session's request is `db-pipe`, which the server's gate answers with `nc` to the db container. One SSH connection is kept open and reused (`ControlMaster`), because a new handshake takes about 5 seconds. |
| `replica` | Postgres 16, a hot standby. On its first start it copies production with `pg_basebackup` through the tunnel, then replays production's changes, usually within a second. It runs as `linux/amd64` (production's architecture; on Apple Silicon, under Rosetta). Its data is in the Docker volume `accounting-replica_pgdata`. |
| `files`   | Every `SYNC_EVERY` seconds (300), `rsync` copies production's `uploads`, `private_uploads` (period snapshots) and its own nightly backups into `data/files/`. It can only read on the server. Nothing is deleted here when it's deleted there. |
| `dumps`   | Takes an hourly `pg_dump` of the standby into `data/dumps/hourly` (kept `HOURLY_KEEP_DAYS`, 7). The day's first dump is also copied to `data/dumps/daily` (kept `DAILY_KEEP_DAYS`, 90). It reaches the standby through its socket, so no password is needed. |

Everything restarts by itself (`restart: unless-stopped`). After a reboot the copy comes back once Docker starts,
so set the Docker app (OrbStack, Docker Desktop) to start at login.

## Using it

```bash
sh status.sh                                               # the link, the last change applied, files, dumps
docker compose exec replica psql -U postgres -d accounting  # query the copy (read-only)
```

- **From a database app:** connect to `127.0.0.1:55432` as production's database user and password. The standby
  has production's roles.
- **A copy you can change:** restore a dump somewhere else, for example:
  ```bash
  docker run -d --name scratch -e POSTGRES_PASSWORD=x -v "$PWD/data/dumps:/dumps:ro" postgres:16
  docker exec scratch createdb -U postgres accounting
  docker exec scratch pg_restore -U postgres -d accounting --no-owner --no-acl /dumps/hourly/<file>.dump
  ```
- **Reacting to changes:** every change the app makes writes a row to `audit_logs`, and those rows arrive here
  within a second. An action can start from the newest rows there. A stream of every row-level change would need
  `wal_level=logical` on production, which takes one restart there.

## When something's wrong

- **`link: NOT CONNECTED`:** run `docker compose logs --tail 30 tunnel`. Usually the server can't be reached, or
  the key's line was removed from `authorized_keys` there. On the server, `/var/log/accounting-copy-gate.log`
  shows every request this key made.
- **The standby says the WAL it needs "has already been removed":** it fell more than 4 GB behind (the machine
  was off a long time) and production dropped its slot. Copy it afresh:
  `docker compose stop replica dumps && docker compose rm -f replica && docker volume rm accounting-replica_pgdata && docker compose up -d`.
- **Retiring the copy:** run `docker compose down -v` here, then follow DEPLOY.md §13 on production (drop the slot,
  the role, the `pg_hba.conf` line and the key).
