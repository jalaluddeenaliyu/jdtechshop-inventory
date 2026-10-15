# Backup and restore

All business records live in the PostgreSQL database `jdtechshop` (run by Postgres.app). If the
Mac is lost, stolen or broken, a recent backup copied somewhere else is the only way to recover them.

The scripts find Postgres.app's `pg_dump` / `pg_restore` by themselves. Run them from the project
folder (`~/JDTechShop` once set up for the shop, see [`PRODUCTION.md`](PRODUCTION.md)).

## Make a backup

```bash
./scripts/backup.sh
```

This writes `backups/jdtechshop-YYYYMMDD-HHMMSS.dump`, checks the file is readable, and keeps
the 30 most recent backups.

| Option | What it does |
|--------|--------------|
| `BACKUP_COPY_DIR=/Volumes/ShopBackup ./scripts/backup.sh` | Also copies the backup there (USB drive or cloud-synced folder), keeping 30 |
| `BACKUP_DIR=/some/folder ./scripts/backup.sh` | Keeps the backups somewhere other than `backups/` |

**Copy backups off the Mac.** A backup on the same machine does not protect against theft or a
dead disk. Use `BACKUP_COPY_DIR`, or copy the newest file to a USB drive kept at home, Google Drive
or iCloud Drive.

### Automatically every evening

`./scripts/install-autostart.sh` sets up a daily backup at 21:30 together with auto-start
(see [`PRODUCTION.md`](PRODUCTION.md), section 2). It replaces the older cron line. If you added
that line before, remove it with `crontab -e`.

## Restore a backup

Restoring **replaces everything** in the target database. The script asks you to type `RESTORE`
first. Always rehearse on a scratch database before doing it for real.

### Rehearsal (monthly)

```bash
createdb jdtechshop_restore_test
./scripts/restore.sh "$(ls -t backups/*.dump | head -1)" postgresql://localhost:5432/jdtechshop_restore_test
cd backend && DATABASE_URL=postgresql+psycopg://localhost:5432/jdtechshop_restore_test .venv/bin/python -m app.cli check && cd ..
dropdb jdtechshop_restore_test
```
`check` should report the migration number, the user accounts and *every product quantity matches
its movements*. The automated tests run this same drill every time (acceptance test 15).

### Real recovery

1. Stop the server: `launchctl bootout gui/$(id -u)/com.jdtechshop.server`
   (or Ctrl + C in its terminal if you started it by hand).
2. `./scripts/restore.sh <backup file> postgresql://localhost:5432/jdtechshop` and type `RESTORE`.
   On a new Mac, run `createdb jdtechshop` first.
3. `cd backend && .venv/bin/alembic upgrade head && cd ..` (brings an older backup up to the
   current version; harmless otherwise).
4. Start again with `./scripts/install-autostart.sh`, log in, and check that the latest sales you
   remember are there.

Anything recorded after the backup was taken is lost, which is why the backup runs every day.

## Verified

- 14 October 2026, by hand: a backup of the development database was restored into
  `jdtechshop_restore_test`. Users, audit log, settings and migration version matched, and the
  audit-log protection trigger came back too.
- From 15 October 2026, automatically on every test run (acceptance test 15): `backup.sh` →
  `restore.sh` into a brand-new database. Row counts and totals match, `app.cli check` passes
  against the copy, and the permanence triggers still refuse deletes. Typing anything other than
  `RESTORE` cancels.
