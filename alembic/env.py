"""Alembic environment configuration."""
import sys
from pathlib import Path

# Ensure the project root is on sys.path so 'app' package is importable.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import settings
from app.db.base import Base
import app.models  # noqa: F401 — register all models

config = context.config

# Override sqlalchemy.url from app settings (so .env is the single source of truth)
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    # Keep the app's loggers alive: the default (disable_existing_loggers=True)
    # silenced app.prestart, so a failed boot exited 1 with no traceback in
    # `docker compose logs api` (found 2026-09-25).
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def include_object(obj, name, type_, reflected, compare_to):
    """Leave the tenant company_id constraints out of model/database comparison.

    TenantMixin keeps ``company_id`` nullable and FK-less in the models on
    purpose (the test harness writes rows with no company); in a real database
    migration 015 and ``app/db/guards.install_tenant_guards`` own its NOT NULL
    and ``fk_<table>_company`` foreign key. Everything else is compared, and
    ``alembic check`` is a gating CI step."""
    from app.db.tenant import tenant_model_tablenames

    tenant_tables = tenant_model_tablenames()
    if type_ == "column" and name == "company_id" and obj.table.name in tenant_tables:
        return False
    if type_ == "foreign_key_constraint" and obj.table.name in tenant_tables \
            and [c.name for c in obj.columns] == ["company_id"] \
            and obj.referred_table.name == "companies":
        return False
    return True


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (generates SQL script)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        include_object=include_object,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (connects to database)."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata,
                          include_object=include_object)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
