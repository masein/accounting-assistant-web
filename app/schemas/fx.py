from __future__ import annotations

from datetime import date, datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class ExchangeRateBase(BaseModel):
    from_currency: str = Field(..., min_length=1, max_length=8)
    to_currency: str = Field(..., min_length=1, max_length=8)
    rate: float = Field(..., gt=0)
    effective_date: date
    note: Optional[str] = Field(None, max_length=256)


class ExchangeRateCreate(ExchangeRateBase):
    pass


class ExchangeRateRead(ExchangeRateBase):
    id: UUID
    created_at: datetime

    model_config = {"from_attributes": True}


class ReportingCurrencyRead(BaseModel):
    currency: str


class ReportingCurrencyUpdate(BaseModel):
    currency: str = Field(..., min_length=1, max_length=8)


class ConvertRequest(BaseModel):
    amount: float
    from_currency: str = Field(..., min_length=1, max_length=8)
    to_currency: str = Field(..., min_length=1, max_length=8)
    on_date: Optional[date] = None


class ConvertResponse(BaseModel):
    amount: float
    from_currency: str
    to_currency: str
    on_date: date
    rate: Optional[float] = None
    converted: Optional[float] = None
    error: Optional[str] = None


class FXRevalueRequest(BaseModel):
    as_of: date
    target_currency: str = Field(..., min_length=1, max_length=8, description="Reporting currency to revalue into")
    account_codes: Optional[list[str]] = Field(
        None,
        description="Limit revaluation to these account codes (defaults to all foreign-currency bearing accounts).",
    )
    dry_run: bool = Field(True, description="When true, preview adjustments without posting")
    gain_account_code: Optional[str] = Field(
        None, description="Account for FX gain (e.g. 4910). Required when dry_run=false."
    )
    loss_account_code: Optional[str] = Field(
        None, description="Account for FX loss (e.g. 6910). Required when dry_run=false."
    )
    reference: Optional[str] = Field(None, max_length=128)
    description: Optional[str] = Field(None, max_length=2000)


class FXRevalueLine(BaseModel):
    account_code: str
    account_name: str
    source_currency: str
    source_balance: int
    target_currency: str
    rate: float
    target_balance: int
    current_target_balance: int
    adjustment: int


class FXRevalueResponse(BaseModel):
    as_of: date
    target_currency: str
    lines: list[FXRevalueLine]
    total_adjustment: int
    posted_transaction_id: Optional[UUID] = None
    errors: list[str] = Field(default_factory=list)
