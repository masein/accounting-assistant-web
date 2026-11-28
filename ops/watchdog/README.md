# The watchdog

Production, the Telegram relay and the live copy, checked every five minutes from the Mac that runs the relay
(`ops/telegram-relay`) and keeps the copy (`ops/live-copy`). When something goes wrong it says so on Telegram,
through the same bot, to the owner's own chat: once it has failed two rounds in a row, again every six hours while
it stays wrong, and once more when it is right. See DEPLOY.md §15.

| Check | Fails when |
|---|---|
| `prod` | `APP_URL/health` doesn't answer in 20 s, or doesn't say `"status":"ok"` and `"database":"connected"` |
| `relay` | the relay's container isn't running, or its heartbeat (`state/alive`, touched on every poll of Telegram) is older than 5 minutes |
| `copy_link` | the copy's database isn't running, isn't connected to production, or has heard nothing from it (not even a keepalive) for 10 minutes |
| `copy_files` | the files' last full sync (`data/files/.last-sync`, every 5 minutes) is older than 30 minutes |
| `copy_dumps` | the newest hourly dump is older than 2 h 10 min |
| `disk` | less than 20 GB is free where the copy is kept |

A round missed for more than half an hour — the Mac asleep or off — is said when it wakes: meanwhile nothing was
checked, the bot didn't answer and the copy fell behind. The Mac can't say it is off; a message like that after a
quiet night means it slept.

## Who is told

The Telegram chat that `ALERT_USERNAME` linked in the app (account menu → Telegram), read from the live copy and
remembered for when the copy itself is down; or `ALERT_CHAT_ID`. With neither, a notification on the Mac. The bot's
token is read from the relay's `token.env`, and only reaches `curl` through its config on standard input, never its
command line. A message that reaches nobody (no network on waking) is tried again the next round.

## Install, update, remove

```bash
ops/watchdog/install.sh /path/to/watchdog      # copies it there, keeps watchdog.env, runs it every 5 minutes
/path/to/watchdog/watchdog.sh --status         # what each check finds now; sends nothing
/path/to/watchdog/watchdog.sh --test           # a test message to whoever is told
ops/watchdog/install.sh --remove               # stops it (launchd); the folder stays
```

The first install writes `watchdog.env` from `watchdog.env.example` (readable only by you, `chmod 600`): set
`APP_URL` (production's address; there is no default, and the `prod` check fails while it's empty),
`ALERT_USERNAME` (or `ALERT_CHAT_ID`), `RELAY_DIR` and `COPY_DIR`, then run `install.sh` again. `ALERT_LANG=fa` says it in Persian. It runs under launchd
(`~/Library/LaunchAgents/app.accountingassistant.watchdog.plist`) while the user is logged in, as OrbStack does, and
right after waking. Its log: `state/watchdog.log`; what launchd saw: `state/launchd.log`.

Bash 3.2 (macOS) or later, `docker`, `curl`, `df`. Tests: `tests/test_watchdog.py` runs the script against stand-in
`docker` and `curl`.
