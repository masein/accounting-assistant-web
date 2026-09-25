"""The web server can run as a restricted database role (roadmap 2026-09 §1.4).

Unit tests run everywhere; the privilege tests need PostgreSQL (the CI job
"Tests on PostgreSQL", or TEST_DATABASE_URL locally) and create a throwaway
role for the duration of the test.
"""
from __future__ import annotations

import os
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from app.core.config import settings
from app.db import roles

PG_URL = os.environ.get("TEST_DATABASE_URL", "")
needs_pg = pytest.mark.skipif(not PG_URL.startswith("postgresql"), reason="needs a PostgreSQL test database")


# --- configuration --------------------------------------------------------------------

def test_off_by_default_runs_as_database_url(monkeypatch):
    monkeypatch.setattr(settings, "app_db_password", "")
    assert not roles.app_role_configured()
    assert roles.runtime_database_url() == settings.database_url
    from app.db import session
    assert session.get_admin_engine() is session.engine


def test_runtime_url_swaps_only_the_credentials(monkeypatch):
    monkeypatch.setattr(settings, "database_url", "postgresql+psycopg://owner:ownerpw@db:5432/accounting")
    monkeypatch.setattr(settings, "app_db_user", "aa_app")
    monkeypatch.setattr(settings, "app_db_password", "p@ss:w/rd#1234567")
    url = make_url(roles.runtime_database_url())
    assert url.username == "aa_app" and url.password == "p@ss:w/rd#1234567"
    assert (url.host, url.port, url.database, url.drivername) == ("db", 5432, "accounting", "postgresql+psycopg")


@pytest.mark.parametrize("name", ["", "AA_APP", "aa-app", "aa app", "1aa", "aa;drop", "a" * 64, 'aa"app'])
def test_role_names_must_be_plain_identifiers(monkeypatch, name):
    monkeypatch.setattr(settings, "app_db_user", name)
    with pytest.raises(roles.AppRoleError):
        roles.app_role_name()


def test_sqlite_and_unconfigured_are_no_ops(monkeypatch):
    sqlite = sa.create_engine("sqlite://")
    assert roles.ensure_app_role(sqlite, role="aa_app", password="x" * 20) == []
    monkeypatch.setattr(settings, "app_db_password", "")
    pg_like = type("E", (), {"dialect": type("D", (), {"name": "postgresql"})()})()
    assert roles.ensure_app_role(pg_like) == []


# --- privileges (PostgreSQL) ----------------------------------------------------------------

def _connect_as(role: str, password: str):
    url = make_url(PG_URL).set(username=role, password=password)
    return sa.create_engine(url, poolclass=sa.pool.NullPool)


def _denied(conn, sql: str, **params) -> bool:
    try:
        with conn.begin_nested():
            conn.execute(sa.text(sql), params)
    except DBAPIError as exc:
        return "permission denied" in str(exc).lower() or "must be owner" in str(exc).lower()
    return False


@pytest.fixture()
def app_role():
    admin = sa.create_engine(PG_URL, poolclass=sa.pool.NullPool)
    name = f"aa_test_{uuid.uuid4().hex[:10]}"
    password = uuid.uuid4().hex + "Aa1!"
    try:
        yield admin, name, password
    finally:
        with admin.begin() as conn:
            conn.execute(sa.text("DROP TABLE IF EXISTS aa_role_probe"))
        roles.drop_app_role(admin, name)
        admin.dispose()


@needs_pg
def test_restricted_role_reads_and_writes_rows_but_cannot_touch_the_audit_log(app_role):
    admin, name, password = app_role
    done = roles.ensure_app_role(admin, role=name, password=password)
    assert any("created" in d for d in done)

    with admin.connect() as conn:
        attrs = conn.execute(sa.text(
            "SELECT rolsuper, rolcreaterole, rolcreatedb, rolbypassrls, rolcanlogin FROM pg_roles WHERE rolname = :r"
        ), {"r": name}).one()
    assert tuple(attrs) == (False, False, False, False, True)

    eng = _connect_as(name, password)
    try:
        with eng.connect() as conn:
            assert conn.execute(sa.text("SELECT current_user")).scalar_one() == name
            conn.execute(sa.text("SELECT count(*) FROM companies"))
            # ordinary rows: full read/write
            rid = uuid.uuid4()
            conn.execute(sa.text("INSERT INTO rate_limit_events (id, bucket, identity, at) "
                                 "VALUES (:i, 'role-test', 'x', now())"), {"i": rid})
            conn.execute(sa.text("UPDATE rate_limit_events SET identity = 'y' WHERE id = :i"), {"i": rid})
            conn.execute(sa.text("DELETE FROM rate_limit_events WHERE id = :i"), {"i": rid})
            # audit log: add and read — never change or remove
            aid = uuid.uuid4()
            conn.execute(sa.text(
                "INSERT INTO audit_logs (id, timestamp, action, entity_type, actor_source) "
                "VALUES (:i, now(), 'role_test', 'test', 'system')"), {"i": aid})
            assert conn.execute(sa.text("SELECT action FROM audit_logs WHERE id = :i"),
                                {"i": aid}).scalar_one() == "role_test"
            conn.commit()

            conn.execute(sa.text("SELECT 1"))  # open the transaction the savepoints live in
            assert _denied(conn, "UPDATE audit_logs SET action = 'tampered' WHERE id = :i", i=aid)
            assert _denied(conn, "DELETE FROM audit_logs WHERE id = :i", i=aid)
            assert _denied(conn, "TRUNCATE audit_logs")
            assert _denied(conn, "ALTER TABLE audit_logs DISABLE TRIGGER ALL")
            assert _denied(conn, "DROP TABLE companies")
            assert _denied(conn, "ALTER TABLE companies ADD COLUMN pwned int")
            assert _denied(conn, "TRUNCATE rate_limit_events")
            if int(conn.execute(sa.text("SHOW server_version_num")).scalar_one()) >= 150000:
                assert _denied(conn, "CREATE TABLE pwned (id int)")
            conn.rollback()
    finally:
        eng.dispose()
    try:  # the owner (a superuser in CI) tidies its test row up
        with admin.begin() as conn:
            conn.execute(sa.text("SET LOCAL session_replication_role = replica"))
            conn.execute(sa.text("DELETE FROM audit_logs WHERE action = 'role_test'"))
    except DBAPIError:
        pass


@needs_pg
def test_schema_version_is_read_only(app_role):
    admin, name, password = app_role
    created = not sa.inspect(admin).has_table("alembic_version")
    if created:  # the PG test database is built by create_all, never stamped
        with admin.begin() as conn:
            conn.execute(sa.text("CREATE TABLE alembic_version (version_num varchar(32) PRIMARY KEY)"))
    roles.ensure_app_role(admin, role=name, password=password)
    eng = _connect_as(name, password)
    try:
        with eng.connect() as conn:
            conn.execute(sa.text("SELECT version_num FROM alembic_version"))
            assert _denied(conn, "INSERT INTO alembic_version VALUES ('pwned')")
            assert _denied(conn, "DELETE FROM alembic_version")
            conn.rollback()
    finally:
        eng.dispose()
        if created:
            with admin.begin() as conn:
                conn.execute(sa.text("DROP TABLE alembic_version"))


@needs_pg
def test_rerun_is_idempotent_rotates_the_password_and_covers_new_tables(app_role):
    admin, name, password = app_role
    roles.ensure_app_role(admin, role=name, password=password)
    # quotes, a backslash and SQL punctuation: PostgreSQL's own %L quoting
    # must carry it intact (nothing is interpolated by Python)
    new_password = "it's a \\ \"test\"; DROP ROLE x; --" + uuid.uuid4().hex
    done = roles.ensure_app_role(admin, role=name, password=new_password)
    assert any("updated" in d for d in done)
    old = _connect_as(name, password)
    with pytest.raises(DBAPIError):
        with old.connect():
            pass
    old.dispose()

    # A table a later migration creates gets the same row rights by default.
    with admin.begin() as conn:
        conn.execute(sa.text("CREATE TABLE aa_role_probe (id int)"))
    eng = _connect_as(name, new_password)
    try:
        with eng.begin() as conn:
            conn.execute(sa.text("INSERT INTO aa_role_probe VALUES (1)"))
            assert conn.execute(sa.text("SELECT count(*) FROM aa_role_probe")).scalar_one() == 1
    finally:
        eng.dispose()


@needs_pg
def test_the_owner_itself_cannot_be_the_app_role():
    admin = sa.create_engine(PG_URL, poolclass=sa.pool.NullPool)
    try:
        owner = make_url(PG_URL).username
        with pytest.raises(roles.AppRoleError):
            roles.ensure_app_role(admin, role=owner, password="x" * 20)
    finally:
        admin.dispose()


def test_short_password_refused_in_production(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "prod")
    pg_like = type("E", (), {"dialect": type("D", (), {"name": "postgresql"})()})()
    with pytest.raises(roles.AppRoleError):
        roles.ensure_app_role(pg_like, role="aa_app", password="short")
