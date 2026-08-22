"""
Alembic environment for NeuroReconstruct.

Reads the same DATABASE_URL the application uses, but runs migrations over a
SYNCHRONOUS driver: the app talks async (aiosqlite / aioodbc) while migrations
are one-shot and simpler to reason about synchronously. _sync_url() rewrites the
driver; nothing else differs.

Usage (from backend/):
    python -m alembic upgrade head     # apply
    python -m alembic stamp head       # mark an already-populated DB as current
    python -m alembic revision --autogenerate -m "..."
"""
import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# The app's modules live one level up from migrations/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import Base, DATABASE_URL  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _sync_url() -> str:
    """Map the application's async URL onto the equivalent sync driver."""
    url = os.environ.get("DATABASE_URL") or DATABASE_URL
    return (url
            .replace("+aiosqlite", "")
            .replace("+aioodbc", "+pyodbc")
            .replace("+asyncpg", "+psycopg2"))


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting -- useful for handing DDL to a DBA."""
    context.configure(
        url=_sync_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _sync_url()

    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # SQLite cannot ALTER most things in place; batch mode rewrites the
            # table instead. Harmless on SQL Server, essential for local runs.
            render_as_batch=connection.dialect.name == "sqlite",
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
