from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.budget import BudgetLimit
from app.schemas.budget import BudgetActualResponse, BudgetActualRow, BudgetLimitCreate, BudgetLimitRead
from app.services.budget_service import budget_utilization

router = APIRouter(prefix="/budgets", tags=["budgets"])


@router.get("", response_model=list[BudgetLimitRead])
def list_budgets(
    db: Session = Depends(get_db),
    month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$"),
) -> list[BudgetLimitRead]:
    q = select(BudgetLimit).order_by(BudgetLimit.month.desc(), BudgetLimit.category)
    if month:
        q = q.where(BudgetLimit.month == month)
    rows = db.execute(q).scalars().all()
    return [BudgetLimitRead.model_validate(r) for r in rows]


@router.post("", response_model=BudgetLimitRead, status_code=201)
def upsert_budget(payload: BudgetLimitCreate, db: Session = Depends(get_db)) -> BudgetLimitRead:
    row = db.execute(
        select(BudgetLimit).where(BudgetLimit.month == payload.month, BudgetLimit.category.ilike(payload.category.strip()))
    ).scalars().first()
    if row:
        row.limit_amount = payload.limit_amount
    else:
        row = BudgetLimit(month=payload.month, category=payload.category.strip(), limit_amount=payload.limit_amount)
        db.add(row)
    db.commit()
    db.refresh(row)
    return BudgetLimitRead.model_validate(row)


@router.delete("/{budget_id}", status_code=204)
def delete_budget(budget_id: UUID, db: Session = Depends(get_db)) -> None:
    row = db.get(BudgetLimit, budget_id)
    if not row:
        raise HTTPException(status_code=404, detail="Budget not found")
    db.delete(row)
    db.commit()


@router.get("/actual-vs-budget", response_model=BudgetActualResponse)
def actual_vs_budget(
    db: Session = Depends(get_db),
    month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
) -> BudgetActualResponse:
    rows = [BudgetActualRow(**r) for r in budget_utilization(db, month)]
    return BudgetActualResponse(rows=rows)
