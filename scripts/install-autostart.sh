#!/usr/bin/env bash
# Make JD TechShop start by itself when this Mac's user logs in, restart if it ever stops,
# and back up the database every evening. Uses macOS launchd (no extra software).
#
#   ./scripts/install-autostart.sh              install (or update) both jobs
#   ./scripts/install-autostart.sh --print      only show what would be installed
#   ./scripts/install-autostart.sh --uninstall  remove both jobs
#
# Options (environment variables):
#   BACKUP_TIME=21:30                 when the daily backup runs (24-hour clock)
#   BACKUP_COPY_DIR=/Volumes/Backup   also copy each backup here (USB drive, iCloud Drive folder…)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AGENTS="$HOME/Library/LaunchAgents"
SERVER=com.jdtechshop.server
BACKUP=com.jdtechshop.backup
BACKUP_TIME="${BACKUP_TIME:-21:30}"
HOUR=$((10#${BACKUP_TIME%%:*}))
MINUTE=$((10#${BACKUP_TIME##*:}))
PATH_FOR_JOBS="/Applications/Postgres.app/Contents/Versions/latest/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"

server_plist() {
  cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$SERVER</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>$ROOT/scripts/run-server.sh</string></array>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>$PATH_FOR_JOBS</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$ROOT/logs/server.log</string>
  <key>StandardErrorPath</key><string>$ROOT/logs/server.log</string>
</dict>
</plist>
EOF
}

backup_plist() {
  local copy=""
  [ -n "${BACKUP_COPY_DIR:-}" ] && copy="<key>BACKUP_COPY_DIR</key><string>$BACKUP_COPY_DIR</string>"
  cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$BACKUP</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>$ROOT/scripts/backup.sh</string></array>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>$PATH_FOR_JOBS</string>$copy</dict>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>$HOUR</integer><key>Minute</key><integer>$MINUTE</integer></dict>
  <key>StandardOutPath</key><string>$ROOT/logs/backup.log</string>
  <key>StandardErrorPath</key><string>$ROOT/logs/backup.log</string>
</dict>
</plist>
EOF
}

case "${1:-}" in
  --print)
    echo "# $AGENTS/$SERVER.plist"; server_plist
    echo; echo "# $AGENTS/$BACKUP.plist"; backup_plist
    ;;
  --uninstall)
    for job in $SERVER $BACKUP; do
      launchctl bootout "gui/$(id -u)/$job" 2> /dev/null || true
      rm -f "$AGENTS/$job.plist"
    done
    echo "Removed. JD TechShop will no longer start by itself."
    ;;
  "")
    [ -f "$ROOT/frontend/dist/index.html" ] || { echo "Build the screens first:  cd frontend && npm run build"; exit 1; }
    mkdir -p "$AGENTS" "$ROOT/logs"
    server_plist > "$AGENTS/$SERVER.plist"
    backup_plist > "$AGENTS/$BACKUP.plist"
    for job in $SERVER $BACKUP; do
      plutil -lint -s "$AGENTS/$job.plist"
      launchctl bootout "gui/$(id -u)/$job" 2> /dev/null || true   # reload if already installed
      launchctl bootstrap "gui/$(id -u)" "$AGENTS/$job.plist"
    done
    echo "Installed:"
    echo "  - JD TechShop server: running now, and starts at every login (log: logs/server.log)"
    printf '  - Database backup: every day at %02d:%02d (log: logs/backup.log)\n' "$HOUR" "$MINUTE"
    echo "Check it:  curl -s http://127.0.0.1:8080/api/health"
    ;;
  *) echo "Unknown option: $1 (use --print or --uninstall)"; exit 1 ;;
esac
