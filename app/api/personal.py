"""Personal-finance endpoints: net worth and the holdings behind it.

Kept in its own router rather than bolted onto /reports because these are
personal-mode concepts (what you own in grams and dollars, not what the books
say it cost).
"""
from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.account import Account
from app.models.personal_holding import PersonalHolding
from app.services.net_worth_service import compute_net_worth

router = APIRouter(prefix="/personal", tags=["personal"])


class HoldingUpsert(BaseModel):
    account_code: str = Field(..., min_length=1, max_length=64)
    # Capped at 16 to match exchange_rates.from_currency: a longer unit could
    # be stored here but could never have a rate, so the holding would be
    # permanently unvaluable. Fail at entry instead.
    unit: str = Field(..., min_length=1, max_length=16)
    quantity: float = Field(..., ge=0)
    label: str | None = Field(default=None, max_length=128)


class HoldingRead(BaseModel):
    id: UUID
    account_code: str
    account_name: str | None = None
    unit: str
    quantity: float
    label: str | None = None


class NetWorthLine(BaseModel):
    account_code: str
    account_name: str
    book_value: int
    market_value: int
    unrealized_gain: int
    revalued: bool = False
    unit: str | None = None
    quantity: float | None = None
    rate: float | None = None


class NetWorthResponse(BaseModel):
    as_of: date
    currency: str
    assets: list[NetWorthLine]
    liabilities: list[NetWorthLine]
    total_assets: int
    total_liabilities: int
    net_worth: int
    unrealized_gain: int
    trend: list[dict]
    # Units held but with no exchange rate on file — surfaced so the UI can say
    # "set a rate" instead of quietly valuing the holding at nothing.
    missing_rates: list[str] = []


def _account_names(db: Session, codes: list[str]) -> dict[str, str]:
    if not codes:
        return {}
    rows = db.execute(select(Account).where(Account.code.in_(codes))).scalars().all()
    return {a.code: a.name for a in rows}


@router.get("/holdings", response_model=list[HoldingRead])
def list_holdings(db: Session = Depends(get_db)) -> list[HoldingRead]:
    rows = db.execute(
        select(PersonalHolding).order_by(PersonalHolding.account_code, PersonalHolding.unit)
    ).scalars().all()
    names = _account_names(db, [r.account_code for r in rows])
    return [
        HoldingRead(
            id=r.id, account_code=r.account_code, account_name=names.get(r.account_code),
            unit=r.unit, quantity=r.quantity, label=r.label,
        )
        for r in rows
    ]


@router.post("/holdings", response_model=HoldingRead, status_code=201)
def upsert_holding(payload: HoldingUpsert, db: Session = Depends(get_db)) -> HoldingRead:
    code = payload.account_code.strip()
    unit = payload.unit.strip().upper()
    acc = db.execute(select(Account).where(Account.code == code)).scalars().first()
    if acc is None:
        raise HTTPException(status_code=400, detail=f"Account code '{code}' not found")

    row = db.execute(
        select(PersonalHolding).where(
            PersonalHolding.account_code == code, PersonalHolding.unit == unit
        )
    ).scalars().first()
    if row is None:
        row = PersonalHolding(account_code=code, unit=unit, quantity=payload.quantity,
                              label=payload.label)
        db.add(row)
    else:
        row.quantity = payload.quantity
        row.label = payload.label
    db.commit()
    db.refresh(row)
    return HoldingRead(
        id=row.id, account_code=row.account_code, account_name=acc.name,
        unit=row.unit, quantity=row.quantity, label=row.label,
    )


@router.delete("/holdings/{holding_id}", status_code=204)
def delete_holding(holding_id: UUID, db: Session = Depends(get_db)) -> None:
    row = db.get(PersonalHolding, holding_id)
    if not row:
        raise HTTPException(status_code=404, detail="Holding not found")
    db.delete(row)
    db.commit()


@router.get("/net-worth", response_model=NetWorthResponse)
def net_worth(
    db: Session = Depends(get_db),
    as_of: date | None = Query(None),
    trend: bool = Query(True),
) -> NetWorthResponse:
    nw = compute_net_worth(db, as_of=as_of, with_trend=trend)

    def _line(l) -> NetWorthLine:
        return NetWorthLine(
            account_code=l.account_code, account_name=l.account_name,
            book_value=l.book_value, market_value=l.market_value,
            unrealized_gain=l.unrealized_gain, revalued=l.revalued,
            unit=l.unit, quantity=l.quantity, rate=l.rate,
        )

    return NetWorthResponse(
        as_of=nw.as_of,
        currency=nw.currency,
        assets=[_line(l) for l in nw.assets],
        liabilities=[_line(l) for l in nw.liabilities],
        total_assets=nw.total_assets,
        total_liabilities=nw.total_liabilities,
        net_worth=nw.net_worth,
        unrealized_gain=nw.unrealized_gain,
        trend=[{"period": p, "value": v} for p, v in nw.trend],
        missing_rates=sorted(set(nw.missing_rates)),
    )


# --- savings goals and the monthly report card (roadmap §4.12) ---------------------------------------------------

class GoalCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    account_code: str = Field(..., min_length=1, max_length=64, description="The asset account the money is saved in")
    target_amount: int = Field(..., gt=0)
    target_date: date | None = None


class GoalUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=128)
    account_code: str | None = Field(None, min_length=1, max_length=64)
    target_amount: int | None = Field(None, gt=0)
    target_date: date | None = None
    clear_target_date: bool = False
    archived: bool | None = None


def _goal_or_404(db: Session, goal_id: UUID):
    from app.models.savings_goal import SavingsGoal
    g = db.get(SavingsGoal, goal_id)
    if g is None:
        raise HTTPException(status_code=404, detail="Goal not found.")
    return g


def _goal_read(db: Session, g) -> dict:
    from app.services.personal_goals import progress
    return progress(db, [g])[0]


@router.get("/goals")
def list_goals(include_archived: bool = Query(False), db: Session = Depends(get_db)) -> list[dict]:
    """Each goal with its progress: saved so far, what's left, what each month still needs, on track or not."""
    from app.services.personal_goals import list_goals as _list
    return _list(db, include_archived=include_archived)


@router.post("/goals", status_code=201)
def create_goal(payload: GoalCreate, db: Session = Depends(get_db)) -> dict:
    from app.models.savings_goal import SavingsGoal
    from app.services.personal_goals import GoalError, check
    try:
        acc = check(db, name=payload.name, account_code=payload.account_code, target_amount=payload.target_amount)
    except GoalError as e:
        raise HTTPException(status_code=422, detail=str(e))
    g = SavingsGoal(name=payload.name.strip(), account_code=acc.code, target_amount=payload.target_amount,
                    target_date=payload.target_date)
    db.add(g)
    db.commit()
    db.refresh(g)
    return _goal_read(db, g)


@router.patch("/goals/{goal_id}")
def update_goal(goal_id: UUID, payload: GoalUpdate, db: Session = Depends(get_db)) -> dict:
    from app.services.personal_goals import GoalError, check
    g = _goal_or_404(db, goal_id)
    name = payload.name if payload.name is not None else g.name
    code = payload.account_code if payload.account_code is not None else g.account_code
    target = payload.target_amount if payload.target_amount is not None else g.target_amount
    try:
        check(db, name=name, account_code=code, target_amount=target)       # before touching the row
    except GoalError as e:
        raise HTTPException(status_code=422, detail=str(e))
    g.name, g.account_code, g.target_amount = name.strip(), code.strip(), target
    if payload.clear_target_date:
        g.target_date = None
    elif payload.target_date is not None:
        g.target_date = payload.target_date
    if payload.archived is not None:
        g.archived = payload.archived
    db.commit()
    db.refresh(g)
    return _goal_read(db, g)


@router.delete("/goals/{goal_id}", status_code=204)
def delete_goal(goal_id: UUID, db: Session = Depends(get_db)) -> None:
    g = _goal_or_404(db, goal_id)
    db.delete(g)
    db.commit()


@router.get("/report-card")
def report_card(
    month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$", description="YYYY-MM in the company's calendar; default last month"),
    lang: str | None = Query(None, pattern="^(fa|en)$"),
    db: Session = Depends(get_db),
) -> dict:
    """The month in a page: income, spending, saved, the checks, categories, budgets, net worth, goals."""
    from app.services.calendar_periods import company_calendar, last_n_months
    from app.services.report_card import report_card as _card
    try:
        out = _card(db, month, lang=lang)
    except (ValueError, KeyError):
        raise HTTPException(status_code=422, detail=f"'{month}' isn't a month (YYYY-MM).")
    out["months"] = [{"key": p.key, "label": p.label}
                     for p in reversed(last_n_months(date.today(), 12, company_calendar(db), out["lang"]))]
    return out
