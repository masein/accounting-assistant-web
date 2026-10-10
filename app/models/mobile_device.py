from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class MobileDevice(Base):
    """A phone signed in to the app (roadmap ROADMAP_ANDROID_CHAT P0.1).

    The phone holds a short bearer access token and a refresh token; only the
    refresh token's hash is kept here. Each refresh replaces it, and the one
    before is remembered: presenting that old one again means a copy was
    taken, and the device is revoked. ``token_version`` is the user's at sign
    in, so a password change (or a log-out-everywhere) ends the phone's
    session too. Not tenant-scoped: a device belongs to its user."""

    __tablename__ = "mobile_devices"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE", name="fk_mobile_devices_user"),
        index=True,
    )
    name: Mapped[str] = mapped_column(String(80))
    platform: Mapped[str] = mapped_column(String(16), default="android", server_default="android")
    app_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    token_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    refresh_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    previous_refresh_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    refresh_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
