from __future__ import annotations

from datetime import date, datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class RecurringRuleBase(BaseModel):
    name: str = Field(..., min_length=1)
    direction: str = Field(default="payment", description="payment|receipt")
    frequency: str = Field(default="monthly", description="monthly|yearly")
    amount: int | None = Field(default=None, ge=0)
    start_date: date
    next_run_date: date
    entity_id: UUID | None = None
    bank_name: str | None = None
    reference_prefix: str | None = None
    note: str | None = None
    status: str = Field(default="active", description="active|paused")


class RecurringRuleCreate(RecurringRuleBase):
    pass


class RecurringRuleUpdate(BaseModel):
    name: str | None = None
    direction: str | None = None
    frequency: str | None = None
    amount: int | None = Field(default=None, ge=0)
    start_date: date | None = None
    next_run_date: date | None = None
    entity_id: UUID | None = None
    bank_name: str | None = None
    reference_prefix: str | None = None
    note: str | None = None
    status: str | None = None


class RecurringRuleRead(RecurringRuleBase):
    id: UUID
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RecurringFromTextRequest(BaseModel):
    text: str = Field(..., min_length=3, description="e.g. Pay 30M rent every month from Mellat")
    start_date: date | None = None
