from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

_JSONType = JSON().with_variant(JSONB(), "postgresql")


class MobileTurn(Base):
    """One message from a phone, known by the phone's own id (roadmap
    ROADMAP_ANDROID_CHAT P0.5, P1.5).

    The phone queues what is typed offline and sends it again until it gets
    an answer, so the same message can arrive twice: after a dropped
    connection, from the background sender while the app sends it too, or on
    another server worker while the first is still asking the model. The row
    is claimed before the turn runs; a second arrival waits for the first and
    gets its reply, and a turn that failed (the model was down) may run again.
    Kept for a week. Not tenant-scoped: a turn belongs to its user."""

    __tablename__ = "mobile_turns"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE", name="fk_mobile_turns_user"),
        primary_key=True,
    )
    client_message_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    state: Mapped[str] = mapped_column(String(16), default="running")       # running | done | failed
    attempt: Mapped[int] = mapped_column(Integer, default=1)                # a retry takes a failed turn over by bumping it
    thread_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reply: Mapped[dict[str, Any] | None] = mapped_column(_JSONType, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
