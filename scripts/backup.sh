#!/usr/bin/env bash
# Back up the JD TechShop database to backups/jdtechshop-YYYYmmdd-HHMMSS.dump
# Usage (from the jd-techshop folder):   ./scripts/backup.sh
# Optional: BACKUP_DIR=/path        where backups are kept (default: backups/ in this folder)
#           BACKUP_COPY_DIR=/path   also copy each backup here — a USB drive or a cloud-synced folder,
#                                   so a stolen or broken Mac does not take the backups with it
set -euo pipefail
# Scheduled jobs start with a bare PATH: fall back to Postgres.app's tools.
command -v pg_dump > /dev/null || PATH="/Applications/Postgres.app/Contents/Versions/latest/bin:$PATH"
cd "$(dirname "$0")/.."
DB_URL="${DATABASE_URL:-$(grep -E '^DATABASE_URL=' backend/.env | cut -d= -f2-)}"
DB_URL="${DB_URL/postgresql+psycopg:/postgresql:}"   # pg_dump does not understand the +psycopg part
DIR="${BACKUP_DIR:-backups}"
mkdir -p "$DIR"
FILE="$DIR/jdtechshop-$(date +%Y%m%d-%H%M%S).dump"
pg_dump --format=custom --no-owner --dbname="$DB_URL" --file="$FILE"
pg_restore --list "$FILE" > /dev/null   # fails loudly if the file is unreadable
echo "Backup saved: $FILE ($(du -h "$FILE" | cut -f1))"
# Keep the 30 most recent backups.
ls -1t "$DIR"/jdtechshop-*.dump | tail -n +31 | xargs -r rm --

if [ -n "${BACKUP_COPY_DIR:-}" ]; then
  if [ -d "$BACKUP_COPY_DIR" ] && cp "$FILE" "$BACKUP_COPY_DIR/"; then
    echo "Copied to: $BACKUP_COPY_DIR"
    ls -1t "$BACKUP_COPY_DIR"/jdtechshop-*.dump | tail -n +31 | xargs -r rm --
  else
    echo "WARNING: could not copy the backup to $BACKUP_COPY_DIR (drive not plugged in?)" >&2
    exit 2
  fi
fi
