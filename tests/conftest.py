"""
Shared fixtures for the accounting-assistant test suite.

Uses an in-process SQLite database so tests run without Postgres.

NOTE (P2-7): SQLite lacks several PostgreSQL features used in production:
  - BIGINT type promotions (INT → BIGINT for IRR values)
  - regexp_replace / regex-based entity cleanup
  - information_schema queries
  - Full-text search, advisory locks, etc.
Consider adding a CI job with a real PostgreSQL container for integration tests.
"""
from __future__ import annotations

import uuid
from collections.abc import Generator
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
import app.models  # noqa: F401 — register all models with Base.metadata
from app.db.seed import seed_chart_if_empty, seed_payment_methods_if_empty
from app.db.session import get_db
from app.main import app
from app.models.account import Account
from app.models.entity import Entity, TransactionEntity
from app.models.transaction import Transaction, TransactionLine


# ---------------------------------------------------------------------------
# Engine & session wired to SQLite
# ---------------------------------------------------------------------------
_SQLALCHEMY_TEST_URL = "sqlite://"

_engine = create_engine(
    _SQLALCHEMY_TEST_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(_engine, "connect")
def _set_sqlite_pragma(dbapi_connection, _connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


_TestSession = sessionmaker(bind=_engine, autoflush=False, autocommit=False)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session", autouse=True)
def _create_tables():
    Base.metadata.create_all(bind=_engine)
    db = _TestSession()
    seed_chart_if_empty(db)
    seed_payment_methods_if_empty(db)
    db.close()
    yield
    Base.metadata.drop_all(bind=_engine)


@pytest.fixture()
def db() -> Generator[Session, None, None]:
    session = _TestSession()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture()
def client(db: Session) -> Generator[TestClient, None, None]:
    """FastAPI TestClient that uses the test DB session."""

    def _override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


class _CSRFTestClient:
    """Wraps TestClient to inject CSRF cookie + header on mutating requests."""

    _SAFE = {"GET", "HEAD", "OPTIONS"}

    def __init__(self, client: TestClient, csrf_token: str):
        self._client = client
        self._csrf = csrf_token

    def __getattr__(self, name):
        return getattr(self._client, name)

    def _inject(self, kwargs):
        headers = dict(kwargs.get("headers") or {})
        headers.setdefault("X-CSRF-Token", self._csrf)
        kwargs["headers"] = headers
        return kwargs

    def get(self, *a, **kw):      return self._client.get(*a, **kw)
    def head(self, *a, **kw):     return self._client.head(*a, **kw)
    def options(self, *a, **kw):  return self._client.options(*a, **kw)
    def post(self, *a, **kw):     return self._client.post(*a, **self._inject(kw))
    def put(self, *a, **kw):      return self._client.put(*a, **self._inject(kw))
    def patch(self, *a, **kw):    return self._client.patch(*a, **self._inject(kw))
    def delete(self, *a, **kw):   return self._client.delete(*a, **self._inject(kw))


@pytest.fixture()
def auth_client(client: TestClient) -> _CSRFTestClient:
    """TestClient with auth cookie + CSRF token so protected endpoints work."""
    from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token

    from app.core.config import settings

    token = create_session_token(user_id=str(uuid.uuid4()), username="testuser", is_admin=True)
    csrf = generate_csrf_token()
    client.cookies.set(settings.auth_cookie_name, token)
    client.cookies.set(CSRF_COOKIE, csrf)
    return _CSRFTestClient(client, csrf)


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------
@pytest.fixture()
def make_transaction(db: Session):
    """Factory: create a balanced transaction with given lines."""

    def _factory(
        lines: list[tuple[str, int, int]],
        *,
        tx_date: date | None = None,
        description: str = "test",
        reference: str | None = None,
        entity_links: list[tuple[str, str]] | None = None,
    ) -> Transaction:
        tx = Transaction(
            date=tx_date or date(2026, 1, 15),
            description=description,
            reference=reference,
        )
        db.add(tx)
        db.flush()
        for account_code, debit, credit in lines:
            acc = db.query(Account).filter(Account.code == account_code).one()
            db.add(
                TransactionLine(
                    transaction_id=tx.id,
                    account_id=acc.id,
                    debit=debit,
                    credit=credit,
                    line_description=f"{account_code} line",
                )
            )
        if entity_links:
            for role, name in entity_links:
                etype = "employee" if role == "payee" else role
                entity = db.query(Entity).filter(Entity.name == name, Entity.type == etype).first()
                if not entity:
                    entity = Entity(name=name, type=etype)
                    db.add(entity)
                    db.flush()
                db.add(TransactionEntity(transaction_id=tx.id, entity_id=entity.id, role=role))
        db.flush()
        return tx

    return _factory
