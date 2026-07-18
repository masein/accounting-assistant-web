"""Schemas for shareholder equity: cap table + equity transactions."""
from __future__ import annotations

from datetime import date
from uuid import UUID

from pydantic import BaseModel, Field


# --- cap table --------------------------------------------------------------
class ShareholdingCreate(BaseModel):
    entity_id: UUID
    shares: int | None = Field(None, ge=0)
    percent: float | None = Field(None, ge=0, le=100)
    par_value: int | None = Field(None, ge=0)
    since: date | None = None
    share_class: str = "ordinary"
    notes: str | None = Field(None, max_length=512)


class ShareholdingUpdate(BaseModel):
    shares: int | None = Field(None, ge=0)
    percent: float | None = Field(None, ge=0, le=100)
    par_value: int | None = Field(None, ge=0)
    since: date | None = None
    share_class: str | None = None
    notes: str | None = Field(None, max_length=512)


class ShareholdingRead(BaseModel):
    id: UUID
    entity_id: UUID
    entity_name: str | None = None
    shares: int | None = None
    percent: float | None = None
    par_value: int | None = None
    since: date | None = None
    share_class: str = "ordinary"
    # Derived from the ledger / equity events:
    paid_in: int = 0
    dividends_declared: int = 0
    dividends_paid: int = 0
    dividends_outstanding: int = 0


class CapTableResponse(BaseModel):
    registered_capital: int = 0
    currency: str | None = None
    total_percent: float = 0
    total_paid_in: int = 0
    rows: list[ShareholdingRead] = Field(default_factory=list)


# --- equity transactions ----------------------------------------------------
class ContributionRequest(BaseModel):
    entity_id: UUID
    amount: int = Field(..., gt=0)
    date: date
    to_capital: bool = True
    asset_account_code: str | None = None
    reference: str | None = Field(None, max_length=128)


class CapitalIncreaseRequest(BaseModel):
    amount: int = Field(..., gt=0)
    date: date
    source: str = "retained_earnings"  # retained_earnings | cash | revaluation_surplus
    entity_id: UUID | None = None
    reference: str | None = Field(None, max_length=128)


class DividendAllocation(BaseModel):
    entity_id: UUID
    amount: int = Field(..., gt=0)


class DividendDeclareRequest(BaseModel):
    total_amount: int = Field(..., gt=0)
    date: date
    allocations: list[DividendAllocation] | None = None
    reference: str | None = Field(None, max_length=128)


class DividendPayRequest(BaseModel):
    entity_id: UUID
    amount: int = Field(..., gt=0)
    date: date
    bank_account_code: str | None = None
    reference: str | None = Field(None, max_length=128)


class CurrentAccountRequest(BaseModel):
    entity_id: UUID
    amount: int = Field(..., gt=0)
    date: date
    direction: str  # "in" (lends) | "out" (withdraws)
    bank_account_code: str | None = None
    reference: str | None = Field(None, max_length=128)


class EquityPostingResponse(BaseModel):
    transaction_ids: list[str] = Field(default_factory=list)
    event_ids: list[str] = Field(default_factory=list)
    summary_lines: list[str] = Field(default_factory=list)
    allocations: list[dict] = Field(default_factory=list)
    registered_capital: int | None = None
