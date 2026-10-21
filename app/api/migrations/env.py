"""Alembic environment. Migrations are plain SQL-ish scripts in versions/."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool, text

from db import sqlalchemy_url

if context.config.config_file_name is not None:
    fileConfig(context.config.config_file_name)

# Any fixed number; all API tasks use it so only one runs migrations at a time.
MIGRATION_LOCK_ID = 727001


def run_migrations_online() -> None:
    engine = create_engine(sqlalchemy_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        connection.execute(text("SELECT pg_advisory_lock(:id)"), {"id": MIGRATION_LOCK_ID})
        try:
            context.configure(connection=connection, target_metadata=None)
            with context.begin_transaction():
                context.run_migrations()
        finally:
            connection.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": MIGRATION_LOCK_ID})
            connection.commit()


if context.is_offline_mode():
    raise SystemExit("Offline (SQL script) mode is not used in this project.")
run_migrations_online()
