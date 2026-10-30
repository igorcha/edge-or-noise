#!/bin/sh
# What the container runs on every start: database tables, the demo project, then the web server.
set -e
cd "$(dirname "$0")/../api"

if [ -z "$DJANGO_SECRET_KEY" ]; then
  echo "DJANGO_SECRET_KEY is not set. Add it to the service's variables (see DEPLOY.md)." >&2
  exit 1
fi

# Create or update the database tables. Safe to run every time: it only applies what's new.
python manage.py migrate --noinput

# The demo project needs the price data. It's on a volume that's uploaded once after the first deploy,
# so start without it rather than fail: /api/health shows whether the data is there.
if [ -f "$BACKTESTER_DATA_DIR/bars/US100/manifest.json" ]; then
  python manage.py seed_demo  # does nothing if the demo already exists
else
  echo "No price data in $BACKTESTER_DATA_DIR/bars yet. Upload it (DEPLOY.md, step 6), then redeploy."
fi

# Gunicorn: the production web server. One process (WEB_CONCURRENCY) holds the price data in memory
# (~650MB) and answers 4 requests at a time on threads. 180s timeout: a parse can make two model
# calls of up to 60s each. The access log shows X-Forwarded-For, the visitor's address behind the proxy.
exec gunicorn config.wsgi \
  --bind "[::]:${PORT:-8000}" \
  --workers "${WEB_CONCURRENCY:-1}" --worker-class gthread --threads 4 \
  --timeout 180 --graceful-timeout 30 \
  --access-logfile - \
  --access-logformat '%({x-forwarded-for}i)s "%(r)s" %(s)s %(b)s %(M)sms'
