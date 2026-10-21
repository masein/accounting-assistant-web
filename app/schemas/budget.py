from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field
from app.core.money import Money


class BudgetLimitCreate(BaseModel):
    month: str = Field(..., pattern=r"^\d{4}-\d{2}$")
    category: str = Field(..., min_length=1)
    limit_amount: Money = Field(..., gt=0, description="A budget of 0 is meaningless — delete the budget instead.")
    currency: str | None = Field(None, pattern=r"^[A-Za-z]{3,8}$",
                                 description="The currency it counts, in its own amounts; empty is the books' own")


class BudgetLimitRead(BudgetLimitCreate):
    id: UUID
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class BudgetLimitUpdate(BaseModel):
    month: str | None = Field(None, pattern=r"^\d{4}-\d{2}$")
    category: str | None = Field(None, min_length=1)
    limit_amount: Money | None = Field(None, gt=0)


class BudgetRollForward(BaseModel):
    from_month: str = Field(..., pattern=r"^\d{4}-\d{2}$")
    months: int = Field(1, ge=1, le=12)
    change_pct: float = Field(0, ge=-99, le=1000, description="% of the source month, not compounded")
    overwrite: bool = False


class BudgetActualRow(BaseModel):
    id: str | None = None
    month: str
    category: str
    currency: str | None = None
    label: str | None = None                 # "6110 — Salaries" for a budget set by code
    limit_amount: Money
    actual_amount: Money
    variance: Money
    utilization_pct: float


class BudgetActualResponse(BaseModel):
    rows: list[BudgetActualRow]
