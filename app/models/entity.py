"""Entities (clients, banks, employees, etc.) that can be linked to transactions for reporting."""
from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Entity(Base):
    """
    A party you do business with: client, bank, employee, supplier, etc.
    Link transactions to entities to run reports like "all transactions with client X".
    """

    __tablename__ = "entities"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    type: Mapped[str] = mapped_column(String(32), index=True)  # client, bank, employee, supplier
    name: Mapped[str] = mapped_column(String(256), index=True)
    code: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    transaction_links: Mapped[list["TransactionEntity"]] = relationship(
        "TransactionEntity", back_populates="entity", cascade="all, delete-orphan"
    )


class TransactionEntity(Base):
    """Links a transaction to an entity with a role (e.g. this voucher's client is Innotech)."""

    __tablename__ = "transaction_entities"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id", ondelete="CASCADE"), index=True
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entities.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(32), index=True)  # client, bank, payee, supplier

    transaction: Mapped["Transaction"] = relationship("Transaction", back_populates="entity_links")
    entity: Mapped["Entity"] = relationship("Entity", back_populates="transaction_links")
