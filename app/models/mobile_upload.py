from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.tenant import TenantMixin


class MobileUpload(Base, TenantMixin):
    """A file the phone sends in pieces (roadmap ROADMAP_ANDROID_CHAT P0.7).

    Mobile data drops halfway through a statement, so the phone sends 256 KB
    at a time and, after a drop, asks again with the file's hash: the row says
    how much is already here and the phone goes on from there. When the last
    piece lands and the bytes match the hash, the file becomes an ordinary
    attachment. A finished row also lets the same file, sent again before it
    belongs to a voucher, be attached without sending its bytes twice.
    Unfinished uploads are forgotten after a day."""

    __tablename__ = "mobile_uploads"
    __table_args__ = (Index("ix_mobile_uploads_user_sha256", "user_id", "sha256"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE", name="fk_mobile_uploads_user"),
    )
    sha256: Mapped[str] = mapped_column(String(64))
    file_name: Mapped[str] = mapped_column(String(256))
    content_type: Mapped[str] = mapped_column(String(128))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    received: Mapped[int] = mapped_column(BigInteger, default=0)
    attachment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("transaction_attachments.id", ondelete="SET NULL", name="fk_mobile_uploads_attachment"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now(), index=True)
