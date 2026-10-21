FROM python:3.13.16-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini db.py identity.py main.py seed.py start.sh ./
COPY migrations ./migrations

RUN useradd --create-home app
USER app

EXPOSE 8000
CMD ["sh", "./start.sh"]
