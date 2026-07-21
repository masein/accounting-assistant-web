"""Entities (clients, banks, employees, etc.) that can be linked to transactions for reporting."""
from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.tenant import TenantMixin


class Entity(Base, TenantMixin):
    """
    A party you do business with: client, bank, employee, supplier, etc.
    Link transactions to entities to run reports like "all transactions with client X".
    """

    __tablename__ = "entities"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    type: Mapped[str] = mapped_column(String(32), index=True)  # client, bank, employee, supplier, shareholder
    name: Mapped[str] = mapped_column(String(256), index=True)
    code: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    # Billing identity — auto-fills the Bill-To / recipient card on documents.
    legal_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Official-invoice identity (فاکتور رسمی): the Iranian tax invoice requires
    # the payer's economic code (شماره اقتصادی), national ID (شناسه ملی),
    # province/county, city, and 10-digit postal code as separate fields.
    economic_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    national_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    province: Mapped[str | None] = mapped_column(String(128), nullable=True)
    city: Mapped[str | None] = mapped_column(String(128), nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    email: Mapped[str | None] = mapped_column(String(256), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    website: Mapped[str | None] = mapped_column(String(256), nullable=True)
    tax_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    contact_person: Mapped[str | None] = mapped_column(String(256), nullable=True)
    payment_terms: Mapped[str | None] = mapped_column(String(128), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    transaction_links: Mapped[list["TransactionEntity"]] = relationship(
        "TransactionEntity", back_populates="entity", cascade="all, delete-orphan"
    )


class TransactionEntity(Base, TenantMixin):
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
