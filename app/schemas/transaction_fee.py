from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class PaymentMethodRead(BaseModel):
    id: UUID
    key: str
    name: str
    is_active: bool

    model_config = {"from_attributes": True}


class TransactionFeeRead(BaseModel):
    id: UUID
    method_id: UUID
    method_name: str
    bank_id: UUID
    bank_name: str
    fee_type: Literal["flat", "percent", "hybrid", "free"]
    fee_value: int
    flat_fee: int
    percent_bps: int
    max_fee: int | None = None
    effective_from: date
    is_active: bool


class TransactionFeeUpsertRequest(BaseModel):
    method_name: str = Field(..., min_length=1, description="Payment method name, e.g. Paya")
    bank_id: UUID | None = None
    bank_name: str | None = None
    fee_type: Literal["flat", "percent", "hybrid", "free"] = "flat"
    fee_value: int = Field(0, ge=0, description="Compatibility field: flat amount or percent bps")
    flat_fee: int = Field(0, ge=0, description="Flat fee amount in Rials")
    percent_bps: int = Field(0, ge=0, description="Percent fee in basis points (1% = 100)")
    max_fee: int | None = Field(None, ge=0, description="Optional cap in Rials")
    effective_from: date | None = None
    update_scope: Literal["future_only", "recalculate_current_month_pending"] = "future_only"

    @model_validator(mode="after")
    def validate_bank(self) -> "TransactionFeeUpsertRequest":
        if not self.bank_id and not (self.bank_name or "").strip():
            raise ValueError("Provide bank_id or bank_name")
        return self


class TransactionFeeUpsertResponse(BaseModel):
    rule: TransactionFeeRead
    recalculated_pending_entries: int = 0


class FeeLineItem(BaseModel):
    account_code: str
    debit: int
    credit: int
    line_description: str | None = None


class TransactionFeeCalculateRequest(BaseModel):
    amount: int = Field(..., ge=0, description="Input amount in Rials")
    method_name: str = Field(..., min_length=1)
    bank_id: UUID | None = None
    bank_name: str | None = None
    amount_mode: Literal["net", "gross"] = "net"
    as_of_date: date | None = None
    track_pending: bool = False
    transaction_id: UUID | None = None

    @model_validator(mode="after")
    def validate_bank(self) -> "TransactionFeeCalculateRequest":
        if not self.bank_id and not (self.bank_name or "").strip():
            raise ValueError("Provide bank_id or bank_name")
        return self


class TransactionFeeCalculateResponse(BaseModel):
    amount_mode: Literal["net", "gross"]
    input_amount: int
    base_amount: int
    fee_amount: int
    gross_amount: int
    net_amount: int
    applied_cap: bool
    fee_type: Literal["flat", "percent", "hybrid", "free"]
    method_name: str
    bank_name: str
    line_items: list[FeeLineItem] = Field(default_factory=list)
