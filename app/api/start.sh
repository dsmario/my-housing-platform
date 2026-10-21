#!/bin/sh
# Container entrypoint: bring the schema up to date, add test data where allowed,
# then start the API.
set -e
alembic upgrade head
if [ "$SEED_DATA" = "true" ]; then
  python seed.py
fi
exec uvicorn main:app --host 0.0.0.0 --port 8000
