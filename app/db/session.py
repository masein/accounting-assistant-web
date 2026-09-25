from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.db.roles import app_role_configured, runtime_database_url

# The web server's engine: the restricted app role when APP_DB_PASSWORD is set
# (app/db/roles.py), otherwise DATABASE_URL.
engine = create_engine(runtime_database_url(), pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

_admin_engine = None


def get_admin_engine():
    """The schema owner (DATABASE_URL) — for create_all, migrations, guards,
    seeding and role grants at boot. The same engine when no app role is set."""
    global _admin_engine
    if not app_role_configured():
        return engine
    if _admin_engine is None:
        _admin_engine = create_engine(settings.database_url, pool_pre_ping=True, poolclass=NullPool)
    return _admin_engine


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

