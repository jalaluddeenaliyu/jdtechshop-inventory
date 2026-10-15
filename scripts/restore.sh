#!/usr/bin/env bash
# Restore a backup INTO A DATABASE YOU NAME. This replaces everything in that database.
# Usage:  ./scripts/restore.sh backups/jdtechshop-20261014-200000.dump postgresql://localhost:5432/jdtechshop_restore_test
set -euo pipefail
# Scheduled jobs start with a bare PATH: fall back to Postgres.app's tools.
command -v pg_dump > /dev/null || PATH="/Applications/Postgres.app/Contents/Versions/latest/bin:$PATH"
FILE="${1:?Give the backup file}"
TARGET="${2:?Give the target database URL}"
TARGET="${TARGET/postgresql+psycopg:/postgresql:}"
echo "This will REPLACE all data in: $TARGET"
read -r -p "Type RESTORE to continue: " answer
[ "$answer" = "RESTORE" ] || { echo "Cancelled."; exit 1; }
pg_restore --clean --if-exists --no-owner --single-transaction --dbname="$TARGET" "$FILE"
echo "Restore finished."
