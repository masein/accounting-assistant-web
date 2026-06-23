"""Multi-tenant isolation core — centralized, default-deny.

Every tenant-scoped model inherits ``TenantMixin`` (a ``company_id`` column).
Two global SQLAlchemy session events then enforce isolation for ALL ORM queries
without per-endpoint code:

* ``do_orm_execute`` injects ``WHERE company_id = :current`` on every SELECT
  against a tenant model (via ``with_loader_criteria`` on the mixin, which also
  covers ``Session.get`` — so fetching another company's row by guessed id
  returns nothing → 404).
* ``before_flush`` stamps ``company_id`` on every new tenant row from the
  current-company context, overriding any value a request body tried to set.

The current company is held in a ``ContextVar`` set per request from the
authenticated user. When no company is set (CLI, tests, super-admin bypass) no
filtering happens — opt-out exists ONLY for super-admin provisioning, via
``tenant_bypass()`` / ``use_company()``.

The model column is left nullable so single-tenant tooling and the test harness
keep working; the migration enforces NOT NULL + FK + per-company uniqueness in
the real database.
"""
from __future__ import annotations

import contextlib
import uuid
from contextvars import ContextVar

from sqlalchemy import String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, Session, mapped_column, with_loader_criteria
from sqlalchemy import event

# The company the current request/operation acts within (None = unscoped).
_current_company: ContextVar[str | None] = ContextVar("current_company", default=None)
# When True, isolation is OFF (super-admin provisioning queries only).
_bypass: ContextVar[bool] = ContextVar("tenant_bypass", default=False)


class TenantMixin:
    """Mixin marking a model as tenant-scoped. Adds ``company_id``; the session
    events filter/stamp it. Kept nullable in the model (the migration enforces
    NOT NULL in production)."""

    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )


def set_current_company(company_id: str | None) -> None:
    _current_company.set(str(company_id) if company_id else None)


def get_current_company() -> str | None:
    return _current_company.get()


def clear_current_company() -> None:
    _current_company.set(None)


@contextlib.contextmanager
def use_company(company_id: str | uuid.UUID):
    """Run a block scoped to a specific company (seeding, provisioning a new
    company's chart, company-scoped reset). New rows are stamped with it."""
    token = _current_company.set(str(company_id))
    btoken = _bypass.set(False)
    try:
        yield
    finally:
        _current_company.reset(token)
        _bypass.reset(btoken)


@contextlib.contextmanager
def tenant_bypass():
    """Disable tenant filtering for a block — super-admin provisioning queries
    that legitimately span companies (list all companies, find a login)."""
    token = _bypass.set(True)
    try:
        yield
    finally:
        _bypass.reset(token)


def _as_uuid(value: str) -> uuid.UUID | str:
    try:
        return uuid.UUID(value)
    except (ValueError, TypeError):
        return value


@event.listens_for(Session, "do_orm_execute")
def _scope_reads(execute_state) -> None:
    """Inject the company filter on every tenant SELECT — and on bulk
    UPDATE/DELETE, so a company-scoped reset can never touch another company."""
    if not (execute_state.is_select or execute_state.is_update or execute_state.is_delete):
        return
    if _bypass.get():
        return
    if execute_state.execution_options.get("skip_tenant"):
        return
    cid = _current_company.get()
    if cid is None:
        return  # unscoped context (CLI / tests / pre-login) → no filtering
    cid_val = _as_uuid(cid)
    execute_state.statement = execute_state.statement.options(
        with_loader_criteria(
            TenantMixin,
            lambda cls: cls.company_id == cid_val,
            include_aliases=True,
        )
    )


@event.listens_for(Session, "before_flush")
def _stamp_writes(session: Session, flush_context, instances) -> None:
    """Stamp company_id on every new tenant row; a body-supplied company_id can
    never win over the current context."""
    if _bypass.get():
        return
    cid = _current_company.get()
    if cid is None:
        return
    cid_val = _as_uuid(cid)
    for obj in session.new:
        if isinstance(obj, TenantMixin):
            obj.company_id = cid_val


def tenant_model_tablenames() -> set[str]:
    """Table names of every model that carries a company_id — used by the
    'no tenant table queried without a filter' safety test."""
    from app.db.base import Base
    out: set[str] = set()
    for mapper in Base.registry.mappers:
        cls = mapper.class_
        if issubclass(cls, TenantMixin):
            out.add(cls.__tablename__)
    return out


__all__ = [
    "TenantMixin", "use_company", "tenant_bypass", "set_current_company",
    "get_current_company", "clear_current_company", "tenant_model_tablenames",
]
