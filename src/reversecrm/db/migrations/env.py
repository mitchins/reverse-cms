from __future__ import annotations

from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, make_url, pool

from reversecrm.db.schema import metadata

config = context.config
target_metadata = metadata


def _prepare_sqlite_parent() -> None:
    configured_url = config.get_main_option("sqlalchemy.url")
    if configured_url is None:
        return
    url = make_url(configured_url)
    database_path = url.database
    if url.get_backend_name() != "sqlite" or database_path is None:
        return
    if database_path not in ("", ":memory:"):
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    _prepare_sqlite_parent()
    connectable = engine_from_config(
        config.get_section(config.config_ini_section) or {},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
