from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # globally unique
    password_hash: Mapped[str] = mapped_column(String(256))
    password_salt: Mapped[str] = mapped_column(String(128))
    preferred_language: Mapped[str] = mapped_column(String(8), default="en")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    # The company this login belongs to (null only for a super-admin/provisioner).
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=True, index=True
    )
    is_superadmin: Mapped[bool] = mapped_column(Boolean, default=False)
    # Company role (RBAC). One role per user per company; enforced server-side
    # via app/core/permissions.py. Orthogonal to is_superadmin (platform-level).
    # The pre-RBAC single company login is backfilled to "owner".
    role: Mapped[str] = mapped_column(String(16), default="owner", server_default="owner")
    # Optional link to the employee Entity so a staff user's "my time / my
    # expenses / my payslips / my assigned work" resolve to their own records.
    entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entities.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Bumped on password reset / role change / deactivation so old session
    # tokens stop working.
    token_version: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
