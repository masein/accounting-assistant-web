from __future__ import annotations

import datetime as _dt
from datetime import date, datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.entity import EntityLink
from app.utils.digits import ascii_digits

# Maximum amount per line: 100 trillion Rials (10^14) — well above any realistic transaction
MAX_LINE_AMOUNT = 100_000_000_000_000


# ----- Single line (for create/update) -----
class TransactionLineBase(BaseModel):
    account_code: str = Field(..., max_length=16, description="Account code (e.g. 1110, 2110)")
    debit: int = Field(0, ge=0, le=MAX_LINE_AMOUNT, description="Debit amount (smallest unit, e.g. Rials)")
    credit: int = Field(0, ge=0, le=MAX_LINE_AMOUNT, description="Credit amount (smallest unit)")
    line_description: Optional[str] = Field(None, max_length=512)

    @field_validator("account_code", mode="before")
    @classmethod
    def _ascii_code(cls, v):
        """«۶۱۱۲» typed on a Persian keyboard is account 6112."""
        return ascii_digits(v).strip() if isinstance(v, str) else v

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
    # the same in the company's base currency (None: no rate known yet)
    base_debit: Optional[int] = None
    base_credit: Optional[int] = None
    line_description: Optional[str] = None

    model_config = {"from_attributes": True}


# ----- Transaction (journal entry) -----
class TransactionBase(BaseModel):
    date: date
    reference: Optional[str] = Field(None, max_length=128)
    description: Optional[str] = Field(None, max_length=2000)
    currency: Optional[str] = Field(
        default=None,
        max_length=8,
        description="ISO-like currency code (IRR, USD, EUR, GBP, AED, TRY, IRT). Defaults to the company's base currency.",
    )
    fx_rate: Optional[float] = Field(
        default=None, gt=0,
        description="1 unit of `currency` = fx_rate units of the base currency. Omit to use the rate on file "
                    "for the date; ignored for an entry in the base currency.",
    )


class TransactionCreate(TransactionBase):
    lines: list[TransactionLineCreate] = Field(..., min_length=2)
    entity_links: list[EntityLink] = Field(default_factory=list, description="Link to clients, banks, etc. for reports")
    attachment_ids: list[UUID] = Field(default_factory=list, description="Uploaded receipt/invoice attachment ids")


class TransactionUpdate(BaseModel):
    date: Optional[_dt.date] = None
    reference: Optional[str] = None
    description: Optional[str] = None
    currency: Optional[str] = Field(default=None, max_length=8)
    fx_rate: Optional[float] = Field(default=None, gt=0)
    lines: Optional[list[TransactionLineCreate]] = None
    entity_links: Optional[list[EntityLink]] = None
    attachment_ids: Optional[list[UUID]] = None


class TransactionEntityLinkRead(BaseModel):
    role: str
    entity_id: UUID
    entity_name: str | None = None
    entity_type: str | None = None
    # signed minor-unit share of the journal (+ = debit), when known
    amount: int | None = None


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
    currency: str = "IRR"
    lines: list[TransactionLineRead]
    entity_links: list[TransactionEntityLinkRead] = Field(default_factory=list)
    attachments: list[AttachmentRead] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class EntityTransactionRead(TransactionRead):
    """A journal as seen on a counterparty's statement of account: what the
    entity paid (Debtor), what it received (Creditor) and the running balance
    after the journal (Remaining). ``entity_placed`` is False when the journal
    could not be attributed to the entity's account — the columns then render
    blank instead of a guessed figure."""
    entity_paid: int = 0
    entity_received: int = 0
    entity_balance: int = 0
    entity_placed: bool = True
    # The entity's control account on the active chart (prefills manual entry).
    entity_control_account: Optional[str] = None


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


# ----- Excel journal import -----

class ExcelAccountMapping(BaseModel):
    """One row of the account mapping table: maps Title hierarchy to an account code."""
    title1: str = ""
    title2: str = ""
    title3: str = ""
    account_code: str = Field(..., description="Target account code in our chart")

class ExcelImportPreviewLine(BaseModel):
    title1: str = ""
    title2: str = ""
    title3: str = ""
    description: str = ""
    debit: float = 0
    credit: float = 0
    suggested_code: Optional[str] = None
    project_group: Optional[str] = None
    project: Optional[str] = None
    project_name: Optional[str] = None

class ExcelImportPreviewVoucher(BaseModel):
    voucher_number: str
    date_code: Optional[str] = None
    gregorian_date: Optional[date] = None
    lines: list[ExcelImportPreviewLine]
    total_debit: float = 0
    total_credit: float = 0
    is_balanced: bool = True

class ExcelImportPreviewAccount(BaseModel):
    title1: str = ""
    title2: str = ""
    title3: str = ""
    suggested_code: Optional[str] = None
    suggested_name: Optional[str] = None
    exists_in_chart: bool = False

class ExcelImportPreviewResponse(BaseModel):
    file_token: str = Field(..., description="Token to reference the uploaded file in confirm step")
    headers: list[str]
    column_mapping: dict[str, Optional[int]]
    vouchers: list[ExcelImportPreviewVoucher]
    unique_accounts: list[ExcelImportPreviewAccount]
    jalali_year: int
    total_rows: int = 0
    total_vouchers: int = 0
    errors: list[str] = Field(default_factory=list)
    raw_preview: list[list[Optional[str]]] = Field(default_factory=list)
    file_sha256: str = ""
    # Set when this exact file was already confirmed: {at, times, imported, filename}.
    already_imported: Optional[dict] = None

class ExcelImportConfirmRequest(BaseModel):
    """Confirm import with account mappings and optional overrides."""
    file_token: str = Field(..., description="Token from preview step identifying the uploaded file")
    jalali_year: int = Field(..., description="Jalali year for date conversion")
    account_mappings: list[ExcelAccountMapping] = Field(..., min_length=1)
    column_mapping: Optional[dict[str, Optional[int]]] = None
    amount_multiplier: float = Field(1.0, description="Multiply amounts by this (e.g. 10000 for toman→rial)")
    currency: str = Field("IRR", description="Source currency of amounts (IRR, USD, EUR, etc.)")

class ExcelImportConfirmResponse(BaseModel):
    imported: int
    transaction_ids: list[UUID]
    accounts_created: int = 0
    errors: list[str] = Field(default_factory=list)
