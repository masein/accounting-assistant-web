from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class BudgetLimitCreate(BaseModel):
    month: str = Field(..., pattern=r"^\d{4}-\d{2}$")
    category: str = Field(..., min_length=1)
    limit_amount: int = Field(..., gt=0, description="A budget of 0 is meaningless — delete the budget instead.")


class BudgetLimitRead(BudgetLimitCreate):
    id: UUID
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class BudgetLimitUpdate(BaseModel):
    month: str | None = Field(None, pattern=r"^\d{4}-\d{2}$")
    category: str | None = Field(None, min_length=1)
    limit_amount: int | None = Field(None, gt=0)


class BudgetRollForward(BaseModel):
    from_month: str = Field(..., pattern=r"^\d{4}-\d{2}$")
    months: int = Field(1, ge=1, le=12)
    change_pct: float = Field(0, ge=-99, le=1000, description="% of the source month, not compounded")
    overwrite: bool = False


class BudgetActualRow(BaseModel):
    id: str | None = None
    month: str
    category: str
    label: str | None = None                 # "6110 — Salaries" for a budget set by code
    limit_amount: int
    actual_amount: int
    variance: int
    utilization_pct: float


class BudgetActualResponse(BaseModel):
    rows: list[BudgetActualRow]
