from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.invoice import InvoiceItemCreate, InvoiceItemRead


class QuoteCreate(BaseModel):
    number: str | None = Field(None, description="Blank → next number in the Q- series")
    issue_date: date
    valid_until: date
    amount: int = Field(0, ge=0, description="Single-amount quotes; ignored when items are given")
    currency: str = "IRR"
    description: str | None = None
    entity_id: UUID | None = None
    status: str = Field("draft", description="draft | sent")
    items: list[InvoiceItemCreate] = Field(default_factory=list)


class QuoteUpdate(BaseModel):
    number: str | None = None
    issue_date: date | None = None
    valid_until: date | None = None
    amount: int | None = Field(None, ge=0)
    currency: str | None = None
    description: str | None = None
    entity_id: UUID | None = None
    status: str | None = Field(None, description="draft | sent | accepted | declined")
    items: list[InvoiceItemCreate] | None = None


class QuoteConvert(BaseModel):
    number: str | None = Field(None, description="Invoice number; blank → next in the company's invoice series")
    issue_date: date | None = Field(None, description="Defaults to today")
    due_date: date | None = Field(None, description="Defaults to issue date + the quote's validity length, min 0")
    status: str = Field("issued", description="issued (posts AR) | draft")


class QuoteItemRead(InvoiceItemRead):
    position: int = 0


class QuoteRead(BaseModel):
    id: UUID
    number: str
    status: str
    # draft/sent whose valid_until has passed show "expired" here.
    effective_status: str
    issue_date: date
    valid_until: date
    amount: int
    subtotal: int
    tax_total: int
    currency: str
    description: str | None = None
    entity_id: UUID | None = None
    entity_name: str | None = None
    converted_invoice_id: UUID | None = None
    converted_invoice_number: str | None = None
    sent_at: datetime | None = None
    decided_at: datetime | None = None
    pdf_url: str
    items: list[QuoteItemRead] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
