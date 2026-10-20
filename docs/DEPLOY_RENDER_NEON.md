# Put JD TechShop online for free (Render + Neon)

**Cost:** ₦0 a month. **Time:** about 30–45 minutes the first time.

- **Neon** stores the data (the PostgreSQL database). Free, permanent, 1 GB.
- **Render** runs the app (interface and API together, as one service). Free.
- **GitHub** holds the code; Render builds the app from it.

What you give up on the free plans:
- The app sleeps after 15 minutes without visitors and takes about a minute to wake. Step 6 keeps it awake during shop hours.
- Neon keeps only 6 hours of history, so your own nightly backup (step 7) is essential.

When the shop depends on the app every day, change the Render plan to **Starter ($7/month)** to stop the sleeping. Nothing else needs to change.

---

## Step 1 — Put the code on GitHub (private)

1. On github.com, click **New repository**. Name it `jd-techshop`, choose **Private**, and leave every box unticked. Click **Create repository**.
2. On your Mac, in Terminal:
   ```bash
   cd ~/Projects/jd-techshop
   git remote add origin https://github.com/YOUR-USERNAME/jd-techshop.git
   git push -u origin main
   ```
   If `git remote add` says the remote already exists, run
   `git remote set-url origin https://github.com/YOUR-USERNAME/jd-techshop.git` and push again.

Check: refresh the GitHub page. You should see `render.yaml`, `backend`, `frontend` and `docs`.

## Step 2 — Create the database on Neon

1. Go to **neon.com** and sign up (no credit card needed). Signing in with GitHub is easiest.
2. Create a project:
   - **Project name:** `jdtechshop`
   - **Postgres version:** **16** (it matches the backup tool on your Mac; see step 7)
   - **Region:** **AWS Europe (Frankfurt)**
3. On the project dashboard, click **Connect**.
   - Turn **Connection pooling OFF**. The address must *not* contain `-pooler`.
   - Copy the connection string. It looks like:
     `postgresql://neondb_owner:xxxxx@ep-something-123.eu-central-1.aws.neon.tech/neondb?sslmode=require`

Keep this string private, like a password. Anyone who has it can read and change your data.
You can paste it exactly as Neon gives it. The app fixes the `postgresql://` start by itself.

## Step 3 — Create the app on Render

1. In Render, click **New → Blueprint**.
2. Connect GitHub if asked, and pick the `jd-techshop` repository.
3. Render reads `render.yaml` and shows one service called **jdtechshop** (free, Frankfurt).
4. It asks for **DATABASE_URL**. Paste the Neon connection string from step 2.
   You don't need to type a `SECRET_KEY`; Render creates one automatically.
5. Click **Apply**. The first build takes about 3–6 minutes.

Check: open the service and look at **Logs**. You should see
`Running upgrade -> 0001` (the database tables being created) and then
`Uvicorn running on http://0.0.0.0:...`.
Then open `https://jdtechshop.onrender.com/api/health` (use your service's real address,
shown at the top of its page). It should show `"database":"ok"`.

## Step 4 — Create your owner login

The new database has no users yet. On your Mac:

```bash
cd ~/Projects/jd-techshop/backend
source .venv/bin/activate
DATABASE_URL="PASTE-THE-NEON-STRING-HERE" python -m app.cli check
DATABASE_URL="PASTE-THE-NEON-STRING-HERE" python -m app.cli create-owner
```

`check` should say `Database: connected (migration 0001, 0 user account(s))`.
`create-owner` asks for your name, a username and a password.

Now open your Render address in the browser and log in. 🎉
Then go to **Users** and create accounts for your staff.

## Step 5 — Remove the old services

Only once the new app works: in Render, open **jdtechshop-inventory-api** and
**jdtechshop-inventory**, then **Settings → Delete service** for each.
They use the same free hours as the new service, so leaving them running can cause
the new one to be stopped before the month ends.

## Step 6 — Keep the app awake during shop hours

1. Sign up at **cron-job.org** (free).
2. **Create cronjob:**
   - **URL:** `https://jdtechshop.onrender.com/api/ping` (your real address)
   - **Schedule:** every 10 minutes, from your opening time to closing time (e.g. 07:00–21:00)
   - **Time zone:** Africa/Lagos
3. Save.

Use `/api/ping`, not `/api/health`. The ping page doesn't touch the database, so
Neon can still switch off when nobody is working. That keeps you inside Neon's free monthly allowance.

## Step 7 — Back up every evening

Neon's free plan keeps only 6 hours of history, so this is your real safety net.
On your Mac:

```bash
cd ~/Projects/jd-techshop
DATABASE_URL="PASTE-THE-NEON-STRING-HERE" ./scripts/backup.sh
```

Then copy the newest file in `backups/` to Google Drive or a USB drive.

If the backup says `server version mismatch`, your Neon database is a newer Postgres
than your Mac's tools. Fix it with `brew install postgresql@17`, then use
`$(brew --prefix)/opt/postgresql@17/bin/pg_dump`, or ask me to adjust the script.

To avoid pasting the string each time, save it in a file that never goes to GitHub:
```bash
echo 'export NEON_URL="PASTE-THE-NEON-STRING-HERE"' > ~/.jdtechshop-neon
chmod 600 ~/.jdtechshop-neon
# then each evening:
source ~/.jdtechshop-neon && DATABASE_URL="$NEON_URL" ./scripts/backup.sh
```

---

## Updating the app later

Each time new code is ready:
```bash
cd ~/Projects/jd-techshop
git add . && git commit -m "Describe the change"
git push
```
Render rebuilds automatically, and any database changes are applied when the app starts.

## Optional — your own web address

Buy a domain (for example `jdtechshop.com.ng`). In Render: **Settings → Custom Domains → Add**,
then add the DNS record Render shows at the company you bought the domain from.
Render adds the HTTPS padlock for free.

## If something goes wrong

| What you see | What it means / what to do |
|---|---|
| Build fails at `npm ci` | `frontend/package-lock.json` is missing from GitHub. Commit it and push. |
| Logs show `password authentication failed` | The `DATABASE_URL` on Render is wrong. Copy it again from Neon (**Connect**) and update it under **Environment**. |
| `/api/health` shows `"database":"unreachable"` | Neon project paused or the string is wrong. Open the Neon dashboard to wake it, then re-check the string. |
| Page takes about a minute to load | The free service was asleep. Check that the cron-job.org job is running. |
| "Please log in" right after logging in | Make sure you're using the `https://` address, not `http://`. |
| Neon says compute hours exhausted | The database pauses until next month (no data is lost). Check that you used `/api/ping` in step 6. |
