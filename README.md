# JD TechShop — Inventory & Sales System

*Delivering Happiness* · Sokoto, Nigeria

A private web application for JD TechShop's stock, sales, customers, suppliers and reports.

- **Design and business rules:** [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- **What is built so far:** [`docs/CHECKLIST.md`](docs/CHECKLIST.md)
- **Running it in the shop (HTTPS, auto-start, daily backups):** [`docs/PRODUCTION.md`](docs/PRODUCTION.md)
- **Backups:** [`docs/BACKUP_RESTORE.md`](docs/BACKUP_RESTORE.md)
- **Bringing in your existing stock:** Products → *Import from Excel* (download the template, fill it in, upload)
- **Online instead, for free (Render + Neon):** [`docs/DEPLOY_RENDER_NEON.md`](docs/DEPLOY_RENDER_NEON.md)

---

## One-time setup on your MacBook

You need: Homebrew, Python 3.12 or newer, Node.js 20 or newer, PostgreSQL 16, Git, and VS Code.
Open the **Terminal** app and run these commands one block at a time.

### 1. Install the tools (skip any you already have)

```bash
# Homebrew (the Mac package installer) — skip if `brew --version` already works
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

brew install python@3.13 node postgresql@16 git
brew install --cask visual-studio-code

# Make the Postgres commands available in every new Terminal window
echo 'export PATH="$(brew --prefix)/opt/postgresql@16/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc

# Start PostgreSQL now and automatically at login
brew services start postgresql@16
```

Check: `psql --version` should print `16.x`, `python3.13 --version` should print `3.13.x`.

*This Mac uses **Postgres.app** instead of Homebrew's PostgreSQL (faster to install). With Postgres.app, skip the `postgresql@16` lines; its tools are in `/Applications/Postgres.app/Contents/Versions/latest/bin`.*

### 2. Create the two databases

```bash
createdb jdtechshop          # the real shop data
createdb jdtechshop_test     # used only by automated tests (they wipe it)
```

### 3. Put the project in place

Unzip `jd-techshop.zip` into a folder, for example `~/Projects/jd-techshop`, then:

```bash
cd ~/Projects/jd-techshop
code .            # opens the project in VS Code
```

**Open the `jd-techshop` folder itself in VS Code** (not `backend` or `frontend`).
If `code` is not found: in VS Code press ⌘⇧P → "Shell Command: Install 'code' command in PATH".

### 4. Set up the backend

In VS Code, open a terminal (**Terminal → New Terminal**):

```bash
cd backend
python3.13 -m venv .venv               # private Python environment for this project
source .venv/bin/activate              # your prompt now starts with (.venv)
pip install -r requirements.txt

cp .env.example .env                   # your private settings file
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Open `backend/.env` in VS Code and paste the long random text after `SECRET_KEY=`.
The default database lines work with Homebrew Postgres, which logs you in as your Mac user.

```bash
alembic upgrade head                   # builds the database tables
python -m app.cli create-owner         # creates YOUR owner login (asks for name, username, password)
python -m app.cli check                # should say: Database: connected (migration 0009, 1 user account(s))
python -m pytest                       # should say: 283 passed (about 1½ minutes)
```

### 5. Set up the frontend

Open a **second** terminal (click **+** in the terminal panel):

```bash
cd frontend
npm install
```

---

## Every day: start the app (development mode)

**Terminal 1 — backend API**
```bash
cd backend
source .venv/bin/activate
uvicorn app.main:app --reload
```
Leave it running. It listens on http://127.0.0.1:8000 (API docs at http://127.0.0.1:8000/api/docs).

**Terminal 2 — web interface**
```bash
cd frontend
npm run dev
```
Then open **http://localhost:5173** in Chrome or Safari and log in.

To stop either server, click its terminal and press **Ctrl + C**.

### Running as one server (closer to how the shop will use it)

```bash
cd frontend && npm run build && cd ..
cd backend && source .venv/bin/activate && uvicorn app.main:app --port 8000
```
Open http://localhost:8000. The backend serves the built interface itself, so only one terminal is needed.
For the shop itself (HTTPS, starts by itself, nightly backups) follow [`docs/PRODUCTION.md`](docs/PRODUCTION.md).

---

## Environment variables (`backend/.env`)

| Name | What it does | Example |
|------|--------------|---------|
| `ENVIRONMENT` | `development`, `production` or `test`. Production refuses to start without a strong secret and hides API docs. | `development` |
| `DATABASE_URL` | The shop database. | `postgresql+psycopg://localhost:5432/jdtechshop` |
| `TEST_DATABASE_URL` | Throw-away database for tests. **Must differ from DATABASE_URL** (tests check this). | `postgresql+psycopg://localhost:5432/jdtechshop_test` |
| `SECRET_KEY` | Signs login sessions. Keep it secret; changing it logs everyone out. | 64 random characters |
| `ACCESS_TOKEN_MINUTES` | How long a login lasts. | `720` (12 hours) |
| `COOKIE_SECURE` | `true` only when served over HTTPS. | `false` |
| `BUSINESS_TIMEZONE` | Defines "today" for reports. | `Africa/Lagos` |

`.env` is never committed to Git.

---

## Useful commands

| Task | Command (from `backend/` with `.venv` active) |
|------|---------|
| Run all tests | `python -m pytest` |
| Apply new database changes after an update | `alembic upgrade head` |
| Create another owner from the terminal | `python -m app.cli create-owner` |
| Check configuration and database | `python -m app.cli check` |
| Back up the database | `../scripts/backup.sh` |
| Run only the acceptance / concurrency tests | `python -m pytest tests/test_acceptance.py tests/test_concurrency.py` |
| Type-check and build the interface | `cd ../frontend && npm run build` |

If you are ever locked out of every owner account, run `python -m app.cli create-owner` to create a new one.

## Git

```bash
cd ~/Projects/jd-techshop
git init            # skip if the folder already has a .git directory
git add .
git commit -m "Phase 2 foundation"
```
To back up the code on GitHub, create an **empty private** repository on github.com and follow its "push an existing repository" commands. Keep it private.
