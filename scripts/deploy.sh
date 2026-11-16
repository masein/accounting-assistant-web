#!/bin/sh
# Automatic deploys from two registries (DEPLOY.md §5).
#
#   sh scripts/deploy.sh          one check now: pull the newest api image, deploy it if it's new
#   sh scripts/deploy.sh loop     a check every DEPLOY_INTERVAL seconds — what the `deployer` service runs
#
# CI publishes every green build of main to GitHub's registry (ghcr.io) and copies it to the mirror
# (docker.netixsystem.com). Each check takes the image from the first registry in DEPLOY_REGISTRIES that
# answers — so when one is down (the mirror answered 502 for days in 2026-11; ghcr.io can be out of reach
# from some networks) the other one deploys, with no one changing anything. A registry that failed is left
# alone for DEPLOY_RETRY_AFTER seconds, so a dead one doesn't slow every check.
#
# The api runs the local tag accounting-assistant-api:deployed. A new image is tagged there, the api is
# recreated, and the script waits for its health check. If it isn't healthy within DEPLOY_HEALTH_TIMEOUT the
# previous image is put back and the bad one is not tried again until a newer one is published.
#
# Runs on the server itself or in the `deployer` container (docker CLI + compose), from the stack's folder.
set -u

REGISTRIES=${DEPLOY_REGISTRIES-"ghcr.io/masein/accounting-assistant-api docker.netixsystem.com/accounting-assistant-api"}
TAG=${DEPLOY_TAG:-latest}
LOCAL=${DEPLOY_LOCAL_IMAGE:-accounting-assistant-api}
SERVICE=${DEPLOY_SERVICE:-api}
INTERVAL=${DEPLOY_INTERVAL:-120}
PULL_TIMEOUT=${DEPLOY_PULL_TIMEOUT:-600}
HEALTH_TIMEOUT=${DEPLOY_HEALTH_TIMEOUT:-300}
RETRY_AFTER=${DEPLOY_RETRY_AFTER:-1800}
STATE=${DEPLOY_STATE_DIR:-.deploy}
# the stack's compose file(s): COMPOSE_FILE from the environment or .env, else the production file
if [ -z "${COMPOSE_FILE:-}" ] && ! grep -qs '^COMPOSE_FILE=' .env; then
  export COMPOSE_FILE=docker-compose.prod.yml
fi

log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) [deploy] $*"; }
note() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*" > "$STATE/status"; }
image_id() { docker image inspect -f '{{.Id}}' "$1" 2>/dev/null; }
revision() { docker image inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$1" 2>/dev/null | cut -c1-7; }
slug() { echo "$1" | tr -c 'A-Za-z0-9' '_'; }

# In the deployer container the stack's folder must be mounted at the same path as on the host, or compose
# would see another project (and bind mounts would point at the wrong place): check before touching anything.
same_project() {
  self=$(hostname)
  dir=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' "$self" 2>/dev/null) || return 0
  [ -z "$dir" ] && return 0                     # not a compose container: run by hand on the server
  [ "$dir" = "$(pwd)" ] && return 0
  log "this container runs in $(pwd) but the stack is in $dir — set DEPLOY_DIR=$dir in .env and run 'docker compose up -d' again"
  return 1
}

wait_healthy() {
  waited=0
  while [ "$waited" -lt "$HEALTH_TIMEOUT" ]; do
    cid=$(docker compose ps -q "$SERVICE" 2>/dev/null | head -1)
    if [ -n "$cid" ]; then
      health=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$cid" 2>/dev/null)
      case "$health" in healthy) return 0 ;; esac
    fi
    sleep 5; waited=$((waited + 5))
  done
  return 1
}

# The newest image from the first registry that answers: sets $got (image id) and $from (reference).
fetch() {
  got=""; from=""; now=$(date +%s)
  for repo in $REGISTRIES; do
    ref="$repo:$TAG"
    mark="$STATE/failed-$(slug "$repo")"
    if [ -f "$mark" ] && [ $((now - $(cat "$mark"))) -lt "$RETRY_AFTER" ]; then
      continue                                  # failed a little while ago: the next one
    fi
    if timeout "$PULL_TIMEOUT" docker pull -q "$ref" >/dev/null 2>&1; then
      rm -f "$mark"
      got=$(image_id "$ref"); from=$ref
      return 0
    fi
    echo "$now" > "$mark"
    log "$ref did not answer — trying the next registry (this one again in ${RETRY_AFTER}s)"
  done
  return 1
}

check() {
  mkdir -p "$STATE"
  if [ -z "$(echo "$REGISTRIES" | tr -d ' ')" ]; then
    note "automatic deploys are off (DEPLOY_REGISTRIES is empty)"; return 0
  fi
  same_project || return 1
  current=$(image_id "$LOCAL:deployed")
  if ! fetch; then
    if [ -z "$current" ]; then                  # moving a server over: the copy it already runs, until one answers
      for repo in $REGISTRIES; do
        if [ -n "$(image_id "$repo:$TAG")" ]; then
          docker tag "$repo:$TAG" "$LOCAL:deployed"
          note "no registry answered; the copy of $repo:$TAG already here is in place ($(revision "$repo:$TAG"))"
          log "no registry answered; the copy of $repo:$TAG already here is in place — now run: docker compose up -d"
          return 0
        fi
      done
    fi
    note "no registry answered; the api stays as it is"
    log "no registry answered; the api stays as it is"; return 1
  fi
  if [ "$got" = "$current" ]; then
    note "up to date: $(revision "$LOCAL:deployed") from $from"; return 0
  fi
  if [ -f "$STATE/bad" ] && [ "$(cat "$STATE/bad")" = "$got" ]; then
    note "$from ($(revision "$from")) failed its health check before; waiting for a newer image"; return 0
  fi
  if [ -z "$current" ]; then                    # the first image: in place for 'docker compose up -d'
    docker tag "$from" "$LOCAL:deployed"
    note "first image in place: $(revision "$from") from $from"
    log "first image in place: $(revision "$from") from $from — now run: docker compose up -d"; return 0
  fi
  log "new image $(revision "$from") from $from (running $(revision "$LOCAL:deployed"))"
  docker tag "$LOCAL:deployed" "$LOCAL:previous"
  docker tag "$from" "$LOCAL:deployed"
  docker compose up -d --no-deps --force-recreate "$SERVICE"
  if wait_healthy; then
    rm -f "$STATE/bad"
    for repo in $REGISTRIES; do                 # an older copy pulled from the other registry isn't kept
      old="$repo:$TAG"
      [ "$old" = "$from" ] && continue
      id=$(image_id "$old")
      [ -n "$id" ] && [ "$id" != "$got" ] && [ "$id" != "$current" ] && docker image rm "$old" >/dev/null 2>&1
    done
    docker image prune -f >/dev/null 2>&1
    note "deployed $(revision "$from") from $from"
    log "deployed $(revision "$from") from $from"; return 0
  fi
  log "$(revision "$from") did not turn healthy in ${HEALTH_TIMEOUT}s — putting $(revision "$LOCAL:previous") back"
  echo "$got" > "$STATE/bad"
  docker tag "$LOCAL:previous" "$LOCAL:deployed"
  docker compose up -d --no-deps --force-recreate "$SERVICE"
  if wait_healthy; then
    note "rolled back to $(revision "$LOCAL:deployed"); $(revision "$from") failed its health check"
    log "rolled back to $(revision "$LOCAL:deployed")"
  else
    note "rolled back to $(revision "$LOCAL:deployed") but it isn't healthy either — see: docker compose logs $SERVICE"
    log "the previous image isn't healthy either — see: docker compose logs $SERVICE"
  fi
  return 1
}

case "${1:-once}" in
  once) check ;;
  loop)
    log "checking ${REGISTRIES:-no registry} for :$TAG every ${INTERVAL}s"
    while :; do check; sleep "$INTERVAL"; done ;;
  *) echo "usage: sh scripts/deploy.sh [once|loop]" >&2; exit 2 ;;
esac
