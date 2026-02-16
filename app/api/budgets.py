from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.session import get_db
from app.models.budget import BudgetLimit
from app.models.transaction import Transaction, TransactionLine
from app.schemas.budget import BudgetActualResponse, BudgetActualRow, BudgetLimitCreate, BudgetLimitRead

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
    limits = db.execute(select(BudgetLimit).where(BudgetLimit.month == month)).scalars().all()
    actual_by_cat: dict[str, int] = {}
    txns = db.execute(
        select(Transaction).options(selectinload(Transaction.lines).selectinload(TransactionLine.account))
    ).scalars().all()
    for t in txns:
        m = f"{t.date.year:04d}-{t.date.month:02d}"
        if m != month:
            continue
        for ln in t.lines:
            code = ln.account.code
            if code.startswith("61") or code.startswith("62"):
                cat = ln.account.name
                actual_by_cat[cat] = actual_by_cat.get(cat, 0) + max(0, ln.debit - ln.credit)
    rows: list[BudgetActualRow] = []
    for b in limits:
        actual = actual_by_cat.get(b.category, 0)
        util = (actual / b.limit_amount * 100.0) if b.limit_amount > 0 else 0.0
        rows.append(
            BudgetActualRow(
                month=b.month,
                category=b.category,
                limit_amount=b.limit_amount,
                actual_amount=actual,
                variance=b.limit_amount - actual,
                utilization_pct=round(util, 2),
            )
        )
    rows.sort(key=lambda x: x.utilization_pct, reverse=True)
    return BudgetActualResponse(rows=rows)
