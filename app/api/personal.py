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
