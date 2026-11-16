#!/bin/sh
# Shared by tunnel.sh and sync.sh: ssh to production with this copy's own key, the server's host key pinned
# (known_hosts beside compose.yml), never a password prompt, and a dead link noticed within ~90 s.
SSH_OPTS="-i /keys/id -o IdentitiesOnly=yes -o UserKnownHostsFile=/keys/known_hosts -o StrictHostKeyChecking=yes \
  -o BatchMode=yes -o ConnectTimeout=15 -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
  -o ControlMaster=auto -o ControlPath=/tmp/cm-%C -o ControlPersist=600"
# ControlMaster: one ssh connection, kept open, carries every session — a new handshake to the server takes ~5 s
REMOTE="$SSH_USER@$SSH_HOST"
stamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }
