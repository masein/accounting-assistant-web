from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class BudgetLimitCreate(BaseModel):
    month: str = Field(..., pattern=r"^\d{4}-\d{2}$")
    category: str = Field(..., min_length=1)
    limit_amount: int = Field(..., ge=0)


class BudgetLimitRead(BudgetLimitCreate):
    id: UUID
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class BudgetActualRow(BaseModel):
    month: str
    category: str
    limit_amount: int
    actual_amount: int
    variance: int
    utilization_pct: float


class BudgetActualResponse(BaseModel):
    rows: list[BudgetActualRow]
