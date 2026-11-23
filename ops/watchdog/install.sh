#!/bin/bash
# Installs the watchdog on this Mac (ops/watchdog/README.md): copies it to a folder of its own, keeps its settings,
# and has launchd run it every 5 minutes while this user is logged in (and on waking). Run again to update it.
#
#   ops/watchdog/install.sh /path/to/watchdog            install or update
#   ops/watchdog/install.sh --remove                     stop it (the folder stays)
set -euo pipefail
LABEL=app.accountingassistant.watchdog
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
SRC=$(cd "$(dirname "$0")" && pwd)

if [ "${1:-}" = --remove ]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo "Stopped; its folder and log stay."
  exit 0
fi

DEST=${1:?"where to install it: install.sh /path/to/watchdog"}
mkdir -p "$DEST/state"
DEST=$(cd "$DEST" && pwd)
cp "$SRC/watchdog.sh" "$DEST/watchdog.sh"
chmod 755 "$DEST/watchdog.sh"
cp "$SRC/watchdog.env.example" "$DEST/watchdog.env.example"
if [ ! -f "$DEST/watchdog.env" ]; then
  cp "$SRC/watchdog.env.example" "$DEST/watchdog.env"
  echo "Wrote $DEST/watchdog.env: set ALERT_USERNAME (or ALERT_CHAT_ID), RELAY_DIR and COPY_DIR, then run this again."
fi

mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key><string>$LABEL</string>
	<key>ProgramArguments</key><array><string>/bin/bash</string><string>$DEST/watchdog.sh</string></array>
	<key>StartInterval</key><integer>300</integer>
	<key>RunAtLoad</key><true/>
	<key>EnvironmentVariables</key><dict><key>PATH</key><string>/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin</string></dict>
	<key>StandardOutPath</key><string>$DEST/state/launchd.log</string>
	<key>StandardErrorPath</key><string>$DEST/state/launchd.log</string>
</dict>
</plist>
PLIST
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Running every 5 minutes: $DEST/watchdog.sh (log: $DEST/state/watchdog.log)"
"$DEST/watchdog.sh" --status
