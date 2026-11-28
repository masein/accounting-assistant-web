#!/bin/sh
# Port 5432 of this container leads to production's database. The server doesn't allow SSH port forwarding
# (sshd: AllowTcpForwarding no — left as it is), so each connection made here is carried over its own ssh session,
# as the stdin/stdout of busybox `nc` on the server, to the db container (looked up on every connection, so a
# recreated db container is followed). The server allows the replication user only from its own end of that
# network (pg_hba: that network's gateway address, DEPLOY.md §13).
set -u
. /scripts/ssh-common.sh
echo "$(stamp) listening on :5432 — each connection goes over ssh to $DB_CONTAINER on $SSH_HOST"
exec socat TCP-LISTEN:5432,reuseaddr,fork EXEC:"sh /scripts/pg-pipe.sh"
