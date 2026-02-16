from __future__ import annotations

from datetime import date, datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.entity import EntityLink


# ----- Single line (for create/update) -----
class TransactionLineBase(BaseModel):
    account_code: str = Field(..., description="Account code (e.g. 1110, 2110)")
    debit: int = Field(0, ge=0, description="Debit amount (smallest unit, e.g. Rials)")
    credit: int = Field(0, ge=0, description="Credit amount (smallest unit)")
    line_description: Optional[str] = None


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
    lines: list[TransactionLineCreate] = Field(..., min_length=1)
    entity_links: list[EntityLink] = Field(default_factory=list, description="Link to clients, banks, etc. for reports")
    attachment_ids: list[UUID] = Field(default_factory=list, description="Uploaded receipt/invoice attachment ids")


class TransactionUpdate(BaseModel):
    date: Optional[date] = None
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
    debit: int = 0
    credit: int = 0
    line_description: Optional[str] = None


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
    entity_mentions: Optional[list[dict[str, str]]] = Field(
        default=None,
        description="When AI returns a transaction, parties to link: [{ role, name }] for get-or-create",
    )
    resolved_entities: Optional[list[ResolvedEntityLink]] = Field(
        default=None,
        description="Resolved entity ids (role + entity_id) from DB; prefer over entity_mentions for setting dropdowns",
    )
