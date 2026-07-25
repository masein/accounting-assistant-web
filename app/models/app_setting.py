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

from sqlalchemy import Column, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base
from app.db.tenant import TenantMixin


class AppSetting(Base, TenantMixin):
    __tablename__ = "app_settings"
    __table_args__ = (
        UniqueConstraint("company_id", "key", name="uq_app_settings_company_key"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key = Column(String(128), nullable=False, index=True)
    value = Column(Text, nullable=False, default="")
