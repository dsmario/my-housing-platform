FROM python:3.13.16-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini admin.py db.py identity.py main.py seed.py start.sh ./
COPY migrations ./migrations

RUN useradd --create-home app

# Tests, not shipped: docker compose run --rm api-test
FROM base AS test
COPY requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY tests ./tests
USER app
CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"]

# The image that runs (last stage, so a plain build gives this one).
FROM base
USER app
EXPOSE 8000
CMD ["sh", "./start.sh"]
