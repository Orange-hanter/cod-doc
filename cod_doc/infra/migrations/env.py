"""Alembic environment for cod-doc.

Resolves DB URL from override → env → embedded default.
Models discovered via cod_doc.infra.models.Base.metadata.
"""

from __future__ import annotations

import logging.config

from alembic import context
from sqlalchemy import engine_from_config, pool

from cod_doc.infra.db import register_sqlite_pragmas, resolve_db_url
from cod_doc.infra.models import Base

config = context.config

if config.config_file_name is not None:
    logging.config.fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _get_url() -> str:
    return config.get_main_option("sqlalchemy.url") or resolve_db_url()


def run_migrations_offline() -> None:
    url = _get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=url.startswith("sqlite"),
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    cfg_section = config.get_section(config.config_ini_section, {})
    url = _get_url()
    cfg_section["sqlalchemy.url"] = url
    connectable = engine_from_config(
        cfg_section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        future=True,
    )
    # SYM-002: engine_from_config идёт мимо make_engine, а миграция без
    # busy_timeout на живой БД упирается в `database is locked`.
    register_sqlite_pragmas(connectable, url)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=connection.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
