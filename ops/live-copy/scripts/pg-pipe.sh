#!/bin/sh
# One database connection, over ssh, to production's db container (socat runs this for each connection). The
# server's gate (accounting-copy-gate, DEPLOY.md §13) answers `db-pipe` with its db container's port 5432.
. /scripts/ssh-common.sh
exec ssh $SSH_OPTS "$REMOTE" db-pipe
