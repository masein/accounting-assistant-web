from __future__ import annotations

import datetime as _dt
from datetime import date, datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.schemas.entity import EntityLink

# Maximum amount per line: 100 trillion Rials (10^14) — well above any realistic transaction
MAX_LINE_AMOUNT = 100_000_000_000_000


# ----- Single line (for create/update) -----
class TransactionLineBase(BaseModel):
    account_code: str = Field(..., description="Account code (e.g. 1110, 2110)")
    debit: int = Field(0, ge=0, le=MAX_LINE_AMOUNT, description="Debit amount (smallest unit, e.g. Rials)")
    credit: int = Field(0, ge=0, le=MAX_LINE_AMOUNT, description="Credit amount (smallest unit)")
    line_description: Optional[str] = None

    @model_validator(mode="after")
    def debit_xor_credit(self):
        """A journal line must have either debit or credit, not both."""
        if self.debit > 0 and self.credit > 0:
            raise ValueError("A line cannot have both debit and credit; use separate lines")
        return self


class TransactionLineCreate(TransactionLineBase):
    pass


class TransactionLineRead(BaseModel):
    id: UUID
    account_id: UUID
    account_code: str
    debit: int
    credit: int
    line_description: Optional[str] = None

    model_config = {"from_attributes": True}


# ----- Transaction (journal entry) -----
class TransactionBase(BaseModel):
    date: date
    reference: Optional[str] = None
    description: Optional[str] = None


class TransactionCreate(TransactionBase):
    lines: list[TransactionLineCreate] = Field(..., min_length=2)
    entity_links: list[EntityLink] = Field(default_factory=list, description="Link to clients, banks, etc. for reports")
    attachment_ids: list[UUID] = Field(default_factory=list, description="Uploaded receipt/invoice attachment ids")


class TransactionUpdate(BaseModel):
    date: Optional[_dt.date] = None
    reference: Optional[str] = None
    description: Optional[str] = None
    lines: Optional[list[TransactionLineCreate]] = None
    entity_links: Optional[list[EntityLink]] = None
    attachment_ids: Optional[list[UUID]] = None


class TransactionEntityLinkRead(BaseModel):
    role: str
    entity_id: UUID
    entity_name: str | None = None
    entity_type: str | None = None


class AttachmentRead(BaseModel):
    id: UUID
    file_name: str
    content_type: str
    size_bytes: int
    url: str
    transaction_id: Optional[UUID] = None


class AttachmentOCRResponse(BaseModel):
    vendor_name: str | None = None
    invoice_or_receipt_no: str | None = None
    date: str | None = None
    amount: int | None = None
    currency: str | None = None
    confidence: float | None = None
    raw_text: str | None = None


class TransactionRead(TransactionBase):
    id: UUID
    lines: list[TransactionLineRead]
    entity_links: list[TransactionEntityLinkRead] = Field(default_factory=list)
    attachments: list[AttachmentRead] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ----- Import (bulk) -----
class ImportTransactionLine(BaseModel):
    account_code: str
    debit: int = Field(0, ge=0, le=MAX_LINE_AMOUNT)
    credit: int = Field(0, ge=0, le=MAX_LINE_AMOUNT)
    line_description: Optional[str] = None

    @model_validator(mode="after")
    def debit_xor_credit(self):
        if self.debit > 0 and self.credit > 0:
            raise ValueError("A line cannot have both debit and credit; use separate lines")
        return self


class ImportTransaction(BaseModel):
    date: date
    reference: Optional[str] = None
    description: Optional[str] = None
    lines: list[ImportTransactionLine] = Field(..., min_length=1)


class ImportTransactionsRequest(BaseModel):
    transactions: list[ImportTransaction] = Field(..., min_length=1)


class ImportTransactionsResponse(BaseModel):
    imported: int
    ids: list[UUID] = Field(..., description="Created transaction IDs")


# ----- AI suggestion (fill form from plain language) -----
class SuggestTransactionRequest(BaseModel):
    user_message: str = Field(..., min_length=1, description="e.g. I paid 500,000 for rent")


class SuggestTransactionResponse(BaseModel):
    """Same shape as TransactionCreate so the UI can pre-fill the form."""
    date: date
    reference: Optional[str] = None
    description: Optional[str] = None
    lines: list[TransactionLineCreate]


# ----- Chat (conversational: AI asks which client, which bank, what for, then fills transaction) -----
class ChatMessage(BaseModel):
    role: str = Field(..., pattern="^(user|assistant)$")
    content: str = Field(..., min_length=1)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(..., min_length=1)
    attachment_ids: list[UUID] = Field(default_factory=list, description="Uploaded attachments to consider in chat")


class ResolvedEntityLink(BaseModel):
    """Resolved mention: role and entity_id (from DB get-or-create). Use for dropdowns."""
    role: str = Field(..., description="client, bank, payee, supplier")
    entity_id: UUID = Field(..., description="Resolved entity id for linking")


class ChatResponse(BaseModel):
    message: str = Field(..., description="Assistant reply to show in chat")
    transaction: Optional[SuggestTransactionResponse] = None
    confidence: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="AI confidence in the suggested transaction (0.0-1.0). Shown to user when present.",
    )
    reasoning: Optional[str] = Field(
        default=None,
        description="Brief explanation of why these accounts were chosen (1-2 sentences).",
    )
    report: Optional[dict] = Field(
        default=None,
        description="Structured report payload for manager insights queries (balance sheet, ledger, inventory, etc.)",
    )
    entity_mentions: Optional[list[dict[str, str]]] = Field(
        default=None,
        description="When AI returns a transaction, parties to link: [{ role, name }] for get-or-create",
    )
    resolved_entities: Optional[list[ResolvedEntityLink]] = Field(
        default=None,
        description="Resolved entity ids (role + entity_id) from DB; prefer over entity_mentions for setting dropdowns",
    )
    form_updates: Optional[dict[str, str]] = Field(
        default=None,
        description="Partial form field updates (e.g. {date: '2026-02-23'}) to apply to the current voucher form",
    )
