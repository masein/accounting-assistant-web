#!/bin/bash
# The watchdog (DEPLOY.md §15): from the Mac that keeps the live copy and carries the Telegram relay, every few
# minutes it checks production, the relay, the live copy (database link, files, dumps) and the disk, and tells the
# owner on Telegram — through the same bot — when something goes wrong, while it stays wrong (a reminder every few
# hours), and when it is right again. A Mac that slept or was off is said when it wakes: nothing was checked, the bot
# didn't answer and the copy didn't follow meanwhile.
#
#   watchdog.sh            one round of checks (launchd runs it every 5 minutes: install.sh)
#   watchdog.sh --status   say what each check finds, send nothing
#   watchdog.sh --test     send a test message to whoever is told
#
# Configuration: watchdog.env beside this script (or WATCHDOG_ENV), see watchdog.env.example. Bash 3.2 (macOS) and
# later; needs docker, curl, df.
set -u

HERE=$(cd "$(dirname "$0")" && pwd)
CONF=${WATCHDOG_ENV:-"$HERE/watchdog.env"}
# shellcheck disable=SC1090
[ -f "$CONF" ] && . "$CONF"

# production's address, from watchdog.env: no built-in default, so the public repository names no server. Unset,
# the prod check fails and says so (review 2026-11-28).
APP_URL=${APP_URL:-}
RELAY_DIR=${RELAY_DIR:-"$HERE/../telegram-relay"}
COPY_DIR=${COPY_DIR:-"$HERE/../prod-replica"}
TOKEN_FILE=${TOKEN_FILE:-"$RELAY_DIR/token.env"}
RELAY_CONTAINER=${RELAY_CONTAINER:-telegram-relay-relay-1}
REPLICA_CONTAINER=${REPLICA_CONTAINER:-accounting-replica-replica-1}
ALERT_CHAT_ID=${ALERT_CHAT_ID:-}
ALERT_USERNAME=${ALERT_USERNAME:-}
ALERT_LANG=${ALERT_LANG:-en}
STATE_DIR=${STATE_DIR:-"$HERE/state"}
TELEGRAM_API=${TELEGRAM_API:-https://api.telegram.org}
# when a check counts as failing, and how often it is said again while it stays so
RELAY_STALE_SECONDS=${RELAY_STALE_SECONDS:-300}      # the relay touches its heartbeat on every poll
WAL_STALE_SECONDS=${WAL_STALE_SECONDS:-600}          # production's keepalives reach the copy every few seconds
FILES_STALE_SECONDS=${FILES_STALE_SECONDS:-1800}     # files are synced every 5 minutes
DUMP_STALE_SECONDS=${DUMP_STALE_SECONDS:-7800}       # a dump every hour
DISK_MIN_GB=${DISK_MIN_GB:-20}
FAILS_BEFORE_ALERT=${FAILS_BEFORE_ALERT:-2}          # two rounds in a row: a blip is not an outage
REMIND_SECONDS=${REMIND_SECONDS:-21600}
GAP_SECONDS=${GAP_SECONDS:-1800}                     # a round missed for this long: the Mac slept or was off

mkdir -p "$STATE_DIR"
LOG="$STATE_DIR/watchdog.log"
NOW=${WATCHDOG_NOW:-$(date -u +%s)}
MODE=${1:-run}

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >> "$LOG"; }

mtime() { date -r "$1" +%s 2>/dev/null; }

# seconds → «5 min», «3 h 20 min», «2 d 4 h»
span() {
  local s=$1 m h d
  m=$(( s / 60 )); h=$(( m / 60 )); d=$(( h / 24 ))
  if [ "$d" -gt 0 ]; then echo "${d} d $(( h % 24 )) h"
  elif [ "$h" -gt 0 ]; then echo "${h} h $(( m % 60 )) min"
  else echo "$(( m > 0 ? m : 1 )) min"; fi
}

# an epoch second in this Mac's own time: «11-23 17:40»
clock() { date -r "$1" "+%m-%d %H:%M" 2>/dev/null || date -d "@$1" "+%m-%d %H:%M"; }

# --- what is said ------------------------------------------------------------------------------------------------

say() {   # say KEY ARG…: the message in ALERT_LANG
  local key=$1; shift
  if [ "$ALERT_LANG" = fa ]; then
    case $key in
      prod)        printf '⚠️ سایت حسابداری جواب نمی‌دهد (%s): %s' "${APP_URL:-?}" "$1" ;;
      relay)       printf '⚠️ ربات تلگرام کار نمی‌کند: رلهٔ روی این مک %s. پیام‌ها به ربات جواب نمی‌گیرند.' "$1" ;;
      copy_link)   printf '⚠️ نسخهٔ زندهٔ پایگاه داده روی این مک از سرور جدا شده: %s.' "$1" ;;
      copy_files)  printf '⚠️ فایل‌های سرور روی این مک به‌روز نمی‌شوند: %s.' "$1" ;;
      copy_dumps)  printf '⚠️ پشتیبان ساعتی پایگاه داده روی این مک ساخته نمی‌شود: %s.' "$1" ;;
      disk)        printf '⚠️ دیسک این مک دارد پر می‌شود: %s گیگابایت جا مانده.' "$1" ;;
      back)        printf '✅ درست شد: %s (بعد از %s).' "$1" "$2" ;;
      still)       printf '(هنوز، از %s) ' "$1" ;;
      gap)         printf 'ℹ️ این مک از %s تا %s چیزی را بررسی نکرد (خواب بود یا خاموش)؛ در این مدت ربات جواب نمی‌داد و نسخهٔ پشتیبان از سرور عقب ماند.' "$1" "$2" ;;
      test)        printf '✅ پیام آزمایشی نگهبان حسابداری: هشدارها به این گفتگو می‌آیند.' ;;
      name_prod)   printf 'سایت حسابداری' ;;
      name_relay)  printf 'ربات تلگرام' ;;
      name_copy_link)  printf 'نسخهٔ زندهٔ پایگاه داده' ;;
      name_copy_files) printf 'همگام‌سازی فایل‌ها' ;;
      name_copy_dumps) printf 'پشتیبان ساعتی' ;;
      name_disk)   printf 'فضای دیسک' ;;
    esac
  else
    case $key in
      prod)        printf "⚠️ The accounting site isn't answering (%s): %s" "${APP_URL:-?}" "$1" ;;
      relay)       printf "⚠️ The Telegram bot is down: the relay on this Mac %s. Messages to the bot get no answer." "$1" ;;
      copy_link)   printf "⚠️ The live copy of the database on this Mac has lost the server: %s." "$1" ;;
      copy_files)  printf "⚠️ The server's files aren't being copied to this Mac: %s." "$1" ;;
      copy_dumps)  printf "⚠️ The hourly database dump on this Mac isn't being made: %s." "$1" ;;
      disk)        printf "⚠️ This Mac's disk is filling up: %s GB left." "$1" ;;
      back)        printf '✅ Back to normal: %s (after %s).' "$1" "$2" ;;
      still)       printf '(still, since %s) ' "$1" ;;
      gap)         printf "ℹ️ This Mac checked nothing from %s to %s (asleep or off): meanwhile the bot didn't answer and the copy fell behind the server." "$1" "$2" ;;
      test)        printf '✅ Test message from the accounting watchdog: alerts come to this chat.' ;;
      name_prod)   printf 'the accounting site' ;;
      name_relay)  printf 'the Telegram bot' ;;
      name_copy_link)  printf 'the live copy of the database' ;;
      name_copy_files) printf 'the file sync' ;;
      name_copy_dumps) printf 'the hourly dump' ;;
      name_disk)   printf 'disk space' ;;
    esac
  fi
}

# --- telling the owner -------------------------------------------------------------------------------------------

# The chat to tell: ALERT_CHAT_ID, or the Telegram chat ALERT_USERNAME linked in the app (read from the live copy,
# remembered for when the copy itself is down).
chat_id() {
  if [ -n "$ALERT_CHAT_ID" ]; then echo "$ALERT_CHAT_ID"; return; fi
  local found=""
  if printf '%s' "$ALERT_USERNAME" | grep -Eq '^[A-Za-z0-9_.@-]+$'; then
    found=$(docker exec "$REPLICA_CONTAINER" psql -U postgres -d accounting -Atq -c \
      "select l.chat_id from messenger_links l join users u on u.id = l.user_id
       where u.username = '$ALERT_USERNAME' and l.platform = 'telegram' and l.status = 'active'
         and l.chat_id is not null order by l.created_at desc limit 1" 2>/dev/null | head -1)
  fi
  if [ -n "$found" ]; then
    echo "$found" > "$STATE_DIR/chat"
    echo "$found"
  elif [ -s "$STATE_DIR/chat" ]; then
    cat "$STATE_DIR/chat"
  fi
}

token() { sed -n 's/^TELEGRAM_BOT_TOKEN=//p' "$TOKEN_FILE" 2>/dev/null | head -1 | tr -d '"'"'"' \r'; }

# send TEXT: on Telegram, else a notification on this Mac. 0 when someone was told.
send() {
  local text=$1 chat tok out
  chat=$(chat_id); tok=$(token)
  if [ -n "$chat" ] && [ -n "$tok" ]; then
    # the token only in curl's config on stdin, never on its command line
    out=$(printf 'url = "%s/bot%s/sendMessage"\n' "$TELEGRAM_API" "$tok" |
          curl -sS -m 20 -K - --data-urlencode "chat_id=$chat" --data-urlencode "text=$text" 2>&1)
    if printf '%s' "$out" | grep -q '"ok":true'; then log "told: $text"; return 0; fi
    log "telegram refused or unreachable: $(printf '%s' "$out" | head -c 200)"
  else
    log "no Telegram chat to tell (link one in the app, or set ALERT_CHAT_ID)"
  fi
  if command -v osascript >/dev/null 2>&1; then
    osascript -e "display notification \"$(printf '%s' "$text" | tr '"\\' "'/")\" with title \"Accounting watchdog\"" \
      >/dev/null 2>&1 && { log "notified on this Mac: $text"; return 0; }
  fi
  log "nobody told: $text"
  return 1
}

# --- the checks: each prints why it fails, nothing when all is well ------------------------------------------------

check_prod() {
  local out
  if [ -z "$APP_URL" ]; then printf 'APP_URL is not set in %s' "$CONF"; return; fi
  out=$(curl -sS -m 20 "$APP_URL/health" 2>&1) || { printf '%s' "${out:-no answer}" | head -c 160; return; }
  printf '%s' "$out" | grep -q '"status":"ok"' || { printf 'health says %s' "$(printf '%s' "$out" | head -c 160)"; return; }
  printf '%s' "$out" | grep -q '"database":"connected"' || printf 'its database is not connected'
}

check_relay() {
  local state age
  state=$(docker inspect -f '{{.State.Status}}' "$RELAY_CONTAINER" 2>/dev/null)
  if [ "$state" != running ]; then printf 'is %s' "${state:-not there}"; return; fi
  age=$(( NOW - $(mtime "$RELAY_DIR/state/alive" || echo 0) ))
  if [ "$age" -gt "$RELAY_STALE_SECONDS" ]; then printf 'has not polled Telegram for %s' "$(span "$age")"; fi
}

check_copy_link() {
  local row status age
  row=$(docker exec "$REPLICA_CONTAINER" psql -U postgres -Atq -c \
        "select status, extract(epoch from now() - last_msg_receipt_time)::int from pg_stat_wal_receiver" 2>/dev/null)
  if [ -z "$row" ]; then
    if docker exec "$REPLICA_CONTAINER" pg_isready -U postgres -q 2>/dev/null; then printf 'not connected to the server'
    else printf 'the database copy is not running'; fi
    return
  fi
  status=${row%%|*}; age=${row##*|}
  if [ "$status" != streaming ]; then printf 'the link is %s' "$status"; return; fi
  if [ "${age:-0}" -gt "$WAL_STALE_SECONDS" ]; then printf 'nothing from the server for %s' "$(span "$age")"; fi
}

check_copy_files() {
  local age
  age=$(( NOW - $(mtime "$COPY_DIR/data/files/.last-sync" || echo 0) ))
  if [ "$age" -gt "$FILES_STALE_SECONDS" ]; then printf 'last full sync %s ago' "$(span "$age")"; fi
}

check_copy_dumps() {
  local newest age
  newest=$(ls -t "$COPY_DIR"/data/dumps/hourly/*.dump 2>/dev/null | head -1)
  if [ -z "$newest" ]; then printf 'none yet'; return; fi
  age=$(( NOW - $(mtime "$newest") ))
  if [ "$age" -gt "$DUMP_STALE_SECONDS" ]; then printf 'the last one is %s old' "$(span "$age")"; fi
}

check_disk() {
  local kb gb
  kb=$(df -Pk "$COPY_DIR" 2>/dev/null | awk 'NR==2 {print $4}')
  [ -n "$kb" ] || return
  gb=$(( kb / 1024 / 1024 ))
  if [ "$gb" -lt "$DISK_MIN_GB" ]; then printf '%s' "$gb"; fi
}

CHECKS="prod relay copy_link copy_files copy_dumps disk"

# --- one round ---------------------------------------------------------------------------------------------------

if [ "$MODE" = --test ]; then
  send "$(say test)" && echo "sent" || { echo "not sent: see $LOG"; exit 1; }
  exit 0
fi

if [ "$MODE" = --status ]; then
  for c in $CHECKS; do
    why=$("check_$c")
    printf '%-11s %s\n' "$c" "${why:-ok}"
  done
  chat=$(chat_id)
  if [ -n "$chat" ]; then told="Telegram chat $chat"; else told="nobody linked yet: a notification on this Mac"; fi
  printf '%-11s %s\n' "told" "$told"
  exit 0
fi

# a round missed for long: the Mac slept or was off. Said once it reaches someone: the network is often not back
# in the first round after waking
if [ -s "$STATE_DIR/last-run" ]; then
  last=$(cat "$STATE_DIR/last-run")
  if [ $(( NOW - last )) -gt "$GAP_SECONDS" ]; then
    say gap "$(clock "$last")" "$(clock "$NOW")" > "$STATE_DIR/gap.pending"
  fi
fi
echo "$NOW" > "$STATE_DIR/last-run"
if [ -s "$STATE_DIR/gap.pending" ] && send "$(cat "$STATE_DIR/gap.pending")"; then
  rm -f "$STATE_DIR/gap.pending"
fi

for c in $CHECKS; do
  why=$("check_$c")
  fails_f="$STATE_DIR/$c.fails"; since_f="$STATE_DIR/$c.since"; told_f="$STATE_DIR/$c.told"
  if [ -z "$why" ]; then
    if [ -s "$told_f" ]; then
      since=$(cat "$since_f" 2>/dev/null || echo "$NOW")
      send "$(say back "$(say "name_$c")" "$(span $(( NOW - since )))")" || true
    fi
    rm -f "$fails_f" "$since_f" "$told_f"
    continue
  fi
  fails=$(( $(cat "$fails_f" 2>/dev/null || echo 0) + 1 ))
  echo "$fails" > "$fails_f"
  [ -s "$since_f" ] || echo "$NOW" > "$since_f"
  log "$c: $why ($fails)"
  [ "$fails" -ge "$FAILS_BEFORE_ALERT" ] || continue
  told=$(cat "$told_f" 2>/dev/null || echo "")
  if [ -z "$told" ] || [ $(( NOW - told )) -ge "$REMIND_SECONDS" ]; then
    msg=$(say "$c" "$why")
    [ -n "$told" ] && msg="$(say still "$(clock "$(cat "$since_f")")")$msg"
    # said once it reached someone; tried again next round if it didn't
    send "$msg" && echo "$NOW" > "$told_f"
  fi
done
exit 0
