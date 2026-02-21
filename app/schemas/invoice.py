from __future__ import annotations

from datetime import date, datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class InvoiceItemBase(BaseModel):
    product_name: str = Field(..., min_length=1)
    quantity: float = Field(default=1, gt=0)
    unit_price: int = Field(default=0, ge=0)
    unit_cost: int | None = Field(default=None, ge=0)
    line_total: int | None = Field(default=None, ge=0)
    description: str | None = None
    inventory_item_id: UUID | None = None


class InvoiceItemCreate(InvoiceItemBase):
    pass


class InvoiceItemRead(InvoiceItemBase):
    id: UUID

    model_config = {"from_attributes": True}


class InvoiceBase(BaseModel):
    number: str = Field(..., min_length=1)
    kind: str = Field(..., description="sales or purchase")
    issue_date: date
    due_date: date
    amount: int = Field(..., ge=0)
    currency: str = Field(default="IRR")
    description: Optional[str] = None
    entity_id: UUID | None = None
    items: list[InvoiceItemCreate] = Field(default_factory=list)


class InvoiceCreate(InvoiceBase):
    status: str = Field(default="issued", description="draft|issued|paid|canceled")


class InvoiceUpdate(BaseModel):
    number: str | None = None
    kind: str | None = None
    status: str | None = None
    issue_date: date | None = None
    due_date: date | None = None
    amount: int | None = Field(default=None, ge=0)
    currency: str | None = None
    description: str | None = None
    entity_id: UUID | None = None
    items: list[InvoiceItemCreate] | None = None


class MarkInvoicePaidRequest(BaseModel):
    payment_date: date
    bank_account_code: str = Field(default="1110")
    bank_entity_id: UUID | None = None
    reference: str | None = None
    description: str | None = None


class InvoiceRead(InvoiceBase):
    id: UUID
    status: str
    transaction_id: UUID | None = None
    pdf_url: str | None = None
    items: list[InvoiceItemRead] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class InvoiceTimelineEvent(BaseModel):
    at: datetime
    event: str
    detail: str | None = None


class InvoiceOCRResult(BaseModel):
    vendor_name: str | None = None
    invoice_or_receipt_no: str | None = None
    date: str | None = None
    amount: int | None = None
    currency: str | None = None
    confidence: float | None = None
    raw_text: str | None = None
    suggested: InvoiceCreate
    created_invoice: InvoiceRead | None = None
