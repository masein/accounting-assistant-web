"""Run the web server as a restricted database role (roadmap 2026-09 §1.4).

``DATABASE_URL`` is the schema owner: the container pre-start migrates, seeds
and installs the guards with it. When ``APP_DB_PASSWORD`` is set, pre-start
also creates (or brings up to date) a login role — ``APP_DB_USER``, default
``aa_app`` — and the web server connects as that role instead. It can read and
write rows but owns nothing, so a bug or an injected query running as the app
cannot ALTER or DROP a table, switch the audit trigger off, or UPDATE, DELETE
or TRUNCATE ``audit_logs``; the append-only log is then a database guarantee,
not just an ORM one.

Unset, everything keeps running as ``DATABASE_URL``, exactly as before.
"""
from __future__ import annotations

import logging
import re

import sqlalchemy as sa
from sqlalchemy.engine import make_url

from app.core.config import settings

_log = logging.getLogger("app.db.roles")

# Rows the app may add to but never change or remove.
APPEND_ONLY_TABLES = ("audit_logs",)
# Rows the app may only read (the schema version, for /health).
READ_ONLY_TABLES = ("alembic_version",)
_ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


class AppRoleError(RuntimeError):
    pass


def app_role_configured() -> bool:
    return bool(settings.app_db_password)


def app_role_name() -> str:
    name = (settings.app_db_user or "").strip()
    if not _ROLE_NAME.match(name):
        raise AppRoleError(f"APP_DB_USER {name!r} is not a plain lower-case identifier")
    return name


def runtime_database_url() -> str:
    """The URL the web server uses: the restricted role when configured."""
    if not app_role_configured():
        return settings.database_url
    url = make_url(settings.database_url).set(username=app_role_name(), password=settings.app_db_password)
    return url.render_as_string(hide_password=False)


def _exec_formatted(conn, template: str, **params) -> None:
    """Let PostgreSQL quote identifiers (%I) and literals (%L) itself —
    CREATE/ALTER ROLE and GRANT take no bind parameters."""
    args = ", ".join(f"CAST(:{k} AS text)" for k in params)  # format() is variadic: say the type
    sql = conn.execute(sa.text(f"SELECT format({template!r}, {args})"), params).scalar_one()
    conn.execute(sa.text(sql))


def ensure_app_role(admin_engine, *, role: str | None = None, password: str | None = None) -> list[str]:
    """Create or update the role and (re)apply its grants. Idempotent; runs on
    every boot so tables added by a migration are covered straight away.
    Returns what it did (empty on SQLite or when no role is configured)."""
    if admin_engine.dialect.name != "postgresql":
        return []
    if role is None and not app_role_configured():
        return []
    role = role or app_role_name()
    password = password if password is not None else settings.app_db_password
    if not _ROLE_NAME.match(role):
        raise AppRoleError(f"role name {role!r} is not a plain lower-case identifier")
    if len(password) < 16 and settings.app_env == "prod":
        raise AppRoleError("APP_DB_PASSWORD must be at least 16 characters in production")
    done: list[str] = []
    with admin_engine.begin() as conn:
        admin = conn.execute(sa.text("SELECT current_user")).scalar_one()
        if admin == role:
            raise AppRoleError("APP_DB_USER must differ from the DATABASE_URL user (the schema owner)")
        exists = conn.execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role}).first()
        attrs = "LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT"
        if exists:
            _exec_formatted(conn, f"ALTER ROLE %I WITH {attrs} PASSWORD %L", r=role, p=password)
            done.append(f"role {role} updated")
        else:
            _exec_formatted(conn, f"CREATE ROLE %I WITH {attrs} PASSWORD %L", r=role, p=password)
            done.append(f"role {role} created")
        db_name = conn.execute(sa.text("SELECT current_database()")).scalar_one()
        _exec_formatted(conn, "GRANT CONNECT ON DATABASE %I TO %I", d=db_name, r=role)
        _exec_formatted(conn, "GRANT USAGE ON SCHEMA public TO %I", r=role)
        _exec_formatted(conn, "REVOKE CREATE ON SCHEMA public FROM %I", r=role)
        _exec_formatted(conn, "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO %I", r=role)
        _exec_formatted(conn, "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO %I", r=role)
        present = set(sa.inspect(conn).get_table_names())
        for table in APPEND_ONLY_TABLES:
            if table in present:
                _exec_formatted(conn, "REVOKE UPDATE, DELETE, TRUNCATE ON TABLE %I FROM %I", t=table, r=role)
        for table in READ_ONLY_TABLES:
            if table in present:
                _exec_formatted(conn, "REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON TABLE %I FROM %I", t=table, r=role)
        # Tables and sequences the owner creates later get the same row rights.
        _exec_formatted(conn, "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                              "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO %I", r=role)
        _exec_formatted(conn, "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                              "GRANT USAGE, SELECT ON SEQUENCES TO %I", r=role)
        done.append(f"grants for {role} applied ({', '.join(APPEND_ONLY_TABLES)} append-only)")
    for item in done:
        _log.info("%s", item)
    return done


def drop_app_role(admin_engine, role: str) -> None:
    """Remove a role and everything granted to it (tests, decommissioning)."""
    if admin_engine.dialect.name != "postgresql" or not _ROLE_NAME.match(role):
        return
    with admin_engine.begin() as conn:
        if conn.execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role}).first():
            _exec_formatted(conn, "DROP OWNED BY %I", r=role)
            _exec_formatted(conn, "DROP ROLE %I", r=role)
