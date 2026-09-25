"""Simple key-value store for application settings persisted in the database.

Multi-tenant: settings are per-company (``company_id`` from ``TenantMixin`` is
stamped/filtered by the session events), so the same key — e.g.
``reporting_currency`` — exists once per tenant. The primary key is a
surrogate id; uniqueness is (company_id, key).

History: the model originally had ``key`` as the sole PK and migration 015
changed the TABLE to PK (company_id, key) without updating the model — so
fresh ``create_all`` deployments kept the global-key PK and any second company
writing a setting hit a UniqueViolation (the "Reset failed." bug). Migration
027 normalizes both variants to this shape.
"""
from __future__ import annotations

import uuid

from sqlalchemy import Column, Index, String, Text, text
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base
from app.db.tenant import TenantMixin


# Keys stored ONCE for the whole installation (``company_id IS NULL``), never
# per company. Boot-time backfills that attach orphan rows to the Default
# company must skip these: moving the platform AI config into a company either
# hijacks it or, once that company already has a copy, violates
# uq_app_settings_company_key and crash-loops the boot (found 2026-09-25).
PLATFORM_SETTING_KEYS: frozenset[str] = frozenset({"ai_config"})


class AppSetting(Base, TenantMixin):
    __tablename__ = "app_settings"
    # One row per key per company, and one per key for the platform. A plain
    # UNIQUE (company_id, key) would let the platform (NULL company) hold the
    # same key twice — NULLs never collide — so these are partial indexes.
    __table_args__ = (
        Index("uq_app_settings_company_key", "company_id", "key", unique=True,
              postgresql_where=text("company_id IS NOT NULL"), sqlite_where=text("company_id IS NOT NULL")),
        Index("uq_app_settings_global_key", "key", unique=True,
              postgresql_where=text("company_id IS NULL"), sqlite_where=text("company_id IS NULL")),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key = Column(String(128), nullable=False, index=True)
    value = Column(Text, nullable=False, default="")
