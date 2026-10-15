# Running JD TechShop in the shop

How to run the system for real use: one Mac in the shop runs everything, and the shop's phones,
tablets and laptops use it over the shop Wi-Fi with HTTPS. It starts by itself, restarts itself if it
stops, and backs itself up every evening.

```
  phones / laptops ── Wi-Fi ──►  https://jdtechshop.local  (Caddy, port 443, HTTPS)
                                        │
                                        ▼
                         JD TechShop server, 127.0.0.1:8080   ◄── started by launchd at login
                                        │
                                        ▼
                         PostgreSQL (Postgres.app)            ◄── daily backup at 21:30
```

Only Caddy can be reached from the Wi-Fi. The server itself listens on this Mac only.

Steps marked **(you)** need your Mac password or a device in your hand, so you do them yourself.

---

## 0. Before you start

1. **Move the project out of Documents.** macOS protects `~/Documents`, and jobs started in the
   background can be refused access to it. Move the folder to your home folder, then rebuild the
   Python environment (it remembers its old location):
   ```bash
   mv ~/Documents/jd-techshop ~/JDTechShop
   cd ~/JDTechShop/backend
   rm -rf .venv && python3.13 -m venv .venv && .venv/bin/pip install -r requirements.txt
   ```
   The rest of this guide assumes `~/JDTechShop`.
2. **Postgres.app starts at login:** open Postgres.app → Settings → tick
   *Automatically start at login*.
3. **The Mac stays awake (you):** System Settings → Battery (or Energy) → Options →
   *Prevent automatic sleeping when the display is off* → on, for *Power Adapter*.
   Keep it plugged in. The battery covers power cuts. Put the Wi-Fi router on a UPS so the
   devices can still reach the Mac.
4. **Lock the Mac down (you):** a login password, FileVault on (System Settings → Privacy &
   Security), and the firewall on (System Settings → Network → Firewall). After a restart with
   FileVault on, someone must log in to this Mac user once before the shop system starts.
5. **Check the secret key:** `backend/.env` must have a long random `SECRET_KEY` (it has). The
   server refuses to start in production without one. Make the file readable only by you:
   ```bash
   chmod 600 ~/JDTechShop/backend/.env
   ```
   Leave `ENVIRONMENT=development` in `.env`. The production start script switches to production
   mode and HTTPS-only cookies itself, so the same file still works for development.

## 1. Build the screens

Repeat this after every update:
```bash
cd ~/JDTechShop/frontend
npm ci && npm run build
```

## 2. Auto-start and daily backups

```bash
cd ~/JDTechShop
BACKUP_COPY_DIR="/Volumes/ShopBackup" ./scripts/install-autostart.sh
```

- `BACKUP_COPY_DIR` (optional, recommended) is a second place for every backup: a USB drive that
  stays plugged in (here named *ShopBackup*), or a folder synced by iCloud Drive or Google Drive
  (for example `"$HOME/Library/Mobile Documents/com~apple~CloudDocs/JDTechShop backups"`). If the
  drive is missing, the backup is still made on the Mac and `logs/backup.log` says so.
- `BACKUP_TIME=20:00` changes the time (default 21:30). If the Mac was asleep at that time, the
  backup runs when it wakes.
- Run `./scripts/install-autostart.sh --print` to see what it installs.
- Run `./scripts/install-autostart.sh --uninstall` to remove it.

Check it is running:
```bash
curl -s http://127.0.0.1:8080/api/health
```
It should print `{"status":"ok","database":"ok",…}`. Logs are in `~/JDTechShop/logs/`.

| To… | Run |
|-----|-----|
| Restart the server | `launchctl kickstart -k gui/$(id -u)/com.jdtechshop.server` |
| Stop it until the next login | `launchctl bootout gui/$(id -u)/com.jdtechshop.server` |
| Start it again | `./scripts/install-autostart.sh` |
| Back up right now | `./scripts/backup.sh` |
| Check the database and stock ledger | `cd backend && ENVIRONMENT=production .venv/bin/python -m app.cli check` |

The server restarts by itself within 10 seconds if it ever stops. After a power cut it waits for
the database to be ready before starting.

## 3. HTTPS for the shop's devices (you)

Passwords and sales go over the Wi-Fi, so they must be encrypted. Caddy does this and renews its
certificate by itself.

1. **Give the Mac a short name:** System Settings → General → Sharing → *Local hostname* → Edit →
   `jdtechshop`. The Mac is now `jdtechshop.local` on the Wi-Fi.
2. **Install and start Caddy.** It needs your password because HTTPS uses port 443:
   ```bash
   brew install caddy
   cp ~/JDTechShop/deploy/Caddyfile "$(brew --prefix)/etc/Caddyfile"
   sudo brew services start caddy
   ```
3. **Trust Caddy's certificate on this Mac:**
   ```bash
   sudo caddy trust
   ```
   Open https://jdtechshop.local in Safari or Chrome. You should see the login page with a padlock.
4. **Trust it on each phone, tablet or laptop, once.** Copy the certificate to the Desktop:
   ```bash
   sudo cp "/var/root/Library/Application Support/Caddy/pki/authorities/local/root.crt" ~/Desktop/JDTechShop-certificate.crt
   sudo chown "$USER" ~/Desktop/JDTechShop-certificate.crt
   ```
   (If the first command says *No such file*, find it with
   `sudo find /var/root /usr/local/var /opt/homebrew/var -name root.crt -path "*Caddy*" 2>/dev/null`.)
   Send it to the device (AirDrop, or a USB cable), then:
   - **iPhone/iPad:** open the file → Settings → *Profile Downloaded* → Install. Then Settings →
     General → About → Certificate Trust Settings → switch it on.
   - **Android:** Settings → Security → Encryption & credentials → Install a certificate →
     CA certificate → choose the file.
   - **Another Mac:** double-click it → Keychain Access → open it → Trust → *Always Trust*.
   - **Windows:** double-click → Install Certificate → Local Machine → *Trusted Root
     Certification Authorities*.
5. On each device, open **https://jdtechshop.local** and add it to the home screen.

The certificate file is public and safe to share. The private key never leaves the Mac.

*Reaching the shop from outside (not needed now):* use a real domain pointing at the shop, remove
`tls internal` from the Caddyfile and put the domain in place of `jdtechshop.local`. Caddy then
gets a public certificate by itself. Do this only with a strong owner password and current updates.

## 4. Backups: check them and practise restoring

- **Every morning:** look at the last lines of `logs/backup.log`. Each night should show
  `Backup saved` (and `Copied to` if you set a second place).
- **Monthly drill (10 minutes):** restore the newest backup into a scratch database and check it.
  The automated tests do the same thing (acceptance test 15).
  ```bash
  cd ~/JDTechShop
  createdb jdtechshop_restore_test
  ./scripts/restore.sh "$(ls -t backups/*.dump | head -1)" postgresql://localhost:5432/jdtechshop_restore_test
  cd backend && DATABASE_URL=postgresql+psycopg://localhost:5432/jdtechshop_restore_test .venv/bin/python -m app.cli check
  dropdb jdtechshop_restore_test
  ```
  `check` must say *every product quantity matches its movements*.
- **Real recovery** (the database is damaged, or you are moving to a new Mac): see
  [`BACKUP_RESTORE.md`](BACKUP_RESTORE.md).

## 5. Installing an update

```bash
cd ~/JDTechShop
./scripts/backup.sh                                            # 1. a fresh backup first
launchctl bootout gui/$(id -u)/com.jdtechshop.server           # 2. stop the server
#                                                                3. replace the code (git pull, or unzip over it, keeping backend/.env and backups/)
cd backend && .venv/bin/pip install -r requirements.txt && .venv/bin/alembic upgrade head && cd ..   # 4. database changes
(cd frontend && npm ci && npm run build)                       # 5. new screens
./scripts/install-autostart.sh                                 # 6. start again
curl -s http://127.0.0.1:8080/api/health                       # 7. check
```
If anything goes wrong, restore the backup from step 1 (see `BACKUP_RESTORE.md`).

## 6. Routine

| When | What |
|------|------|
| Every morning | Log in at https://jdtechshop.local; check last night's backup in `logs/backup.log` |
| Every week | Unplug the USB backup drive and take it home; plug in the other one (keep two) |
| Every month | Restore drill (section 4); `app.cli check`; install macOS updates; check the *Activity log* in Settings |
| When staff leave | Turn off their account in Users. Their sessions end immediately |

## 7. Troubleshooting

| Problem | Look at / do |
|---------|--------------|
| Devices cannot open the site | Is the Mac awake and on the same Wi-Fi? `curl -s http://127.0.0.1:8080/api/health` on the Mac. `sudo brew services list` shows whether Caddy is running |
| "Not secure" or certificate warning | The device has not trusted the certificate yet (section 3, step 4) |
| Health says database is not ok | Open Postgres.app and press Start; the server reconnects by itself |
| Server keeps restarting | `tail -50 ~/JDTechShop/logs/server.log` usually names the problem (for example the screens not built, or a missing `SECRET_KEY`) |
| Logged out on every request over HTTPS | Make sure you open the site through `https://`, not `http://…:8080` from another device |
| Backup log shows `could not copy` | The USB drive is not plugged in, or the cloud folder moved |

## What has been verified

- **Automated, on every test run:** all 15 acceptance tests. Test 14 kills the real server
  without warning, restarts it and checks the data and login survived. Test 15 runs the real
  `backup.sh` and `restore.sh` into a new database and checks counts, totals, the stock ledger and
  the database's protection rules. Concurrency tests cover cashiers, owners and suppliers acting
  at the same moment.
- **Rehearsed on this Mac (15 October 2026):** `run-server.sh` in production mode against the
  test database. Results: health ok, screens and deep links served, API docs hidden, no `Server`
  header, login cookie marked `Secure; HttpOnly`, listening on 127.0.0.1 only. Also checked:
  `backup.sh` with the bare PATH a scheduled job gets, a second copy, a clear warning when the
  copy drive is missing, and the generated launchd files pass `plutil -lint`.
- **Not yet done, because it needs your password or devices:** installing the launchd jobs and
  Caddy (sections 2–3) and trusting the certificate on the shop's phones. Do a first
  real-paper print of a receipt and a report at the same time.
