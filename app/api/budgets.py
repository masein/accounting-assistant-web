from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.budget import BudgetLimit
from app.schemas.budget import (
    BudgetActualResponse, BudgetActualRow, BudgetLimitCreate, BudgetLimitRead, BudgetLimitUpdate, BudgetRollForward,
)
from app.services.audit_service import log_audit_event
from app.services.budget_service import budget_utilization, roll_forward

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


@router.patch("/{budget_id}", response_model=BudgetLimitRead)
def update_budget(budget_id: UUID, payload: BudgetLimitUpdate, db: Session = Depends(get_db)) -> BudgetLimitRead:
    """Change a budget's amount, category or month (roadmap §4.7)."""
    row = db.get(BudgetLimit, budget_id)
    if not row:
        raise HTTPException(status_code=404, detail="Budget not found")
    month = payload.month or row.month
    category = (payload.category.strip() if payload.category else row.category)
    clash = db.execute(select(BudgetLimit).where(
        BudgetLimit.month == month, BudgetLimit.category.ilike(category), BudgetLimit.id != row.id)).scalars().first()
    if clash:
        raise HTTPException(status_code=409, detail=f"{month} already has a budget for {category}.")
    before = {"month": row.month, "category": row.category, "limit_amount": row.limit_amount}
    row.month, row.category = month, category
    if payload.limit_amount is not None:
        row.limit_amount = payload.limit_amount
    import json
    log_audit_event(db, action="update", entity_type="budget", entity_id=str(row.id),
                    detail=json.dumps({"from": before, "to": {"month": row.month, "category": row.category,
                                                              "limit_amount": row.limit_amount}}, ensure_ascii=False))
    db.commit()
    db.refresh(row)
    return BudgetLimitRead.model_validate(row)


@router.post("/roll-forward")
def roll_budgets_forward(payload: BudgetRollForward, db: Session = Depends(get_db)) -> dict:
    """Copy a month's budgets into the months after it (roadmap §4.7)."""
    try:
        out = roll_forward(db, from_month=payload.from_month, months=payload.months,
                           change_pct=payload.change_pct, overwrite=payload.overwrite)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    import json
    log_audit_event(db, action="roll_forward", entity_type="budget", entity_id=payload.from_month,
                    detail=json.dumps({**out, "change_pct": payload.change_pct, "overwrite": payload.overwrite}))
    db.commit()
    return out


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
