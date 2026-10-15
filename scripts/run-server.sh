#!/usr/bin/env bash
# Run JD TechShop for real use: one server for the screens and the API.
# It listens only on this Mac (127.0.0.1); Caddy in front of it provides HTTPS to the shop's devices.
# Started automatically by scripts/install-autostart.sh; you can also run it by hand to test.
#   PORT=8080 (default)   WORKERS=2 (default)
set -euo pipefail
cd "$(dirname "$0")/../backend"
command -v pg_isready > /dev/null || PATH="/Applications/Postgres.app/Contents/Versions/latest/bin:$PATH"

# Real use: production rules (strong secret key required, API docs hidden) and HTTPS-only cookies.
# These override backend/.env, so the same .env still works for development.
export ENVIRONMENT="${ENVIRONMENT:-production}"
export COOKIE_SECURE="${COOKIE_SECURE:-true}"
PORT="${PORT:-8080}"
WORKERS="${WORKERS:-2}"

if [ ! -f ../frontend/dist/index.html ]; then
  echo "$(date '+%F %T') The screens are not built. Run:  cd frontend && npm run build"
  exit 1
fi

# After a power cut the database may still be starting: wait for it (up to 2 minutes).
DB_URL="${DATABASE_URL:-$(grep -E '^DATABASE_URL=' .env | cut -d= -f2-)}"
DB_URL="${DB_URL/postgresql+psycopg:/postgresql:}"
for _ in $(seq 60); do
  pg_isready --quiet --dbname="$DB_URL" && break
  sleep 2
done

echo "$(date '+%F %T') Starting JD TechShop on 127.0.0.1:$PORT ($ENVIRONMENT, $WORKERS workers)"
exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" --workers "$WORKERS" \
  --proxy-headers --forwarded-allow-ips 127.0.0.1 --timeout-graceful-shutdown 20 --no-server-header
