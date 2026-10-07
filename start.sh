#!/bin/bash
set -e

echo "Running database migrations..."
# The database may still be starting (fresh EasyPanel/compose boot): retry for ~60s.
for attempt in $(seq 1 12); do
  if alembic upgrade head; then
    break
  fi
  if [ "$attempt" -eq 12 ]; then
    echo "Migrations failed after $attempt attempts" >&2
    exit 1
  fi
  echo "Database not ready (attempt $attempt), retrying in 5s..."
  sleep 5
done

echo "Starting server..."
# One worker by default: the in-memory rate limiter is per process.
exec uvicorn app.main:app \
  --host 0.0.0.0 \
  --port 8000 \
  --workers "${WEB_CONCURRENCY:-1}" \
  --proxy-headers \
  --forwarded-allow-ips="*"
