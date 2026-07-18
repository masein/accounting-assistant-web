"""Shareholder equity REST API: cap table (shareholdings) + equity transactions
(contribution / capital increase / dividend declare & pay / current account).

Writes post balanced double-entry via ``equity_service`` and are restricted to
Owner / CFO / Accountant (Perm.BOOKS_WRITE — see app/core/permissions.py). Reads
are Owner / CFO / Accountant (Perm.BOOKS_READ).
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.entity import Entity
from app.models.equity import EquityEvent, Shareholding
from app.schemas.equity import (
    CapitalIncreaseRequest,
    CapTableResponse,
    ContributionRequest,
    CurrentAccountRequest,
    DividendDeclareRequest,
    DividendPayRequest,
    EquityPostingResponse,
    ShareholdingCreate,
    ShareholdingRead,
    ShareholdingUpdate,
)
from app.services import equity_service as eq

router = APIRouter(prefix="/equity", tags=["equity"])


def _err(exc: eq.EquityError) -> HTTPException:
    return HTTPException(status_code=422, detail=str(exc))


def _entity_name(db: Session, entity_id) -> str | None:
    ent = db.get(Entity, entity_id)
    return ent.name if ent else None


def _event_totals(db: Session) -> dict:
    """Per-entity paid-in + dividend totals from tagged EquityEvents."""
    evs = db.execute(select(EquityEvent)).scalars().all()
    paid_in: dict = {}
    div_decl: dict = {}
    div_paid: dict = {}
    for e in evs:
        if e.entity_id is None:
            continue
        if e.event_type == "contribution":
            paid_in[e.entity_id] = paid_in.get(e.entity_id, 0) + int(e.amount or 0)
        elif e.event_type == "capital_increase":
            paid_in[e.entity_id] = paid_in.get(e.entity_id, 0) + int(e.amount or 0)
        elif e.event_type == "dividend_declared":
            div_decl[e.entity_id] = div_decl.get(e.entity_id, 0) + int(e.amount or 0)
        elif e.event_type == "dividend_paid":
            div_paid[e.entity_id] = div_paid.get(e.entity_id, 0) + int(e.amount or 0)
    return {"paid_in": paid_in, "div_decl": div_decl, "div_paid": div_paid}


# --------------------------------------------------------------------------- #
# cap table
# --------------------------------------------------------------------------- #
@router.get("/cap-table", response_model=CapTableResponse)
def cap_table(db: Session = Depends(get_db)) -> CapTableResponse:
    holdings = db.execute(select(Shareholding)).scalars().all()
    totals = _event_totals(db)
    rows: list[ShareholdingRead] = []
    total_percent = 0.0
    total_paid_in = 0
    for h in holdings:
        decl = totals["div_decl"].get(h.entity_id, 0)
        paid = totals["div_paid"].get(h.entity_id, 0)
        pin = totals["paid_in"].get(h.entity_id, 0)
        total_percent += float(h.percent or 0)
        total_paid_in += pin
        rows.append(ShareholdingRead(
            id=h.id, entity_id=h.entity_id, entity_name=_entity_name(db, h.entity_id),
            shares=h.shares, percent=(float(h.percent) if h.percent is not None else None),
            par_value=h.par_value, since=h.since, share_class=h.share_class,
            paid_in=pin, dividends_declared=decl, dividends_paid=paid,
            dividends_outstanding=decl - paid,
        ))
    rows.sort(key=lambda r: (r.percent or 0), reverse=True)
    company = eq._company_row(db)
    return CapTableResponse(
        registered_capital=(int(company.registered_capital or 0) if company else 0),
        currency=eq._reporting_currency(db),
        total_percent=round(total_percent, 4),
        total_paid_in=total_paid_in,
        rows=rows,
    )


@router.post("/shareholdings", response_model=ShareholdingRead, status_code=201)
def create_shareholding(payload: ShareholdingCreate, db: Session = Depends(get_db)) -> ShareholdingRead:
    ent = db.get(Entity, payload.entity_id)
    if ent is None:
        raise HTTPException(status_code=404, detail="Shareholder entity not found")
    if (ent.type or "").lower() != "shareholder":
        raise HTTPException(
            status_code=422,
            detail=f"'{ent.name}' is type '{ent.type}', not a shareholder. Register them as a shareholder first.",
        )
    existing = db.execute(
        select(Shareholding).where(Shareholding.entity_id == payload.entity_id)
    ).scalars().first()
    if existing:
        raise HTTPException(status_code=409, detail=f"{ent.name} is already on the cap table.")
    # Sum of percentages must not exceed 100.
    if payload.percent:
        current = sum(float(h.percent or 0) for h in db.execute(select(Shareholding)).scalars().all())
        if current + float(payload.percent) > 100.0001:
            raise HTTPException(
                status_code=422,
                detail=f"Total ownership would exceed 100% ({current + float(payload.percent):.2f}%).",
            )
    h = Shareholding(
        entity_id=payload.entity_id, shares=payload.shares, percent=payload.percent,
        par_value=payload.par_value, since=payload.since,
        share_class=(payload.share_class or "ordinary"), notes=payload.notes,
    )
    db.add(h)
    db.commit()
    db.refresh(h)
    return ShareholdingRead(
        id=h.id, entity_id=h.entity_id, entity_name=ent.name, shares=h.shares,
        percent=(float(h.percent) if h.percent is not None else None), par_value=h.par_value,
        since=h.since, share_class=h.share_class,
    )


@router.patch("/shareholdings/{shareholding_id}", response_model=ShareholdingRead)
def update_shareholding(
    shareholding_id: UUID, payload: ShareholdingUpdate, db: Session = Depends(get_db)
) -> ShareholdingRead:
    h = db.get(Shareholding, shareholding_id)
    if h is None:
        raise HTTPException(status_code=404, detail="Shareholding not found")
    if payload.percent is not None:
        others = sum(
            float(x.percent or 0)
            for x in db.execute(select(Shareholding)).scalars().all()
            if x.id != h.id
        )
        if others + float(payload.percent) > 100.0001:
            raise HTTPException(
                status_code=422,
                detail=f"Total ownership would exceed 100% ({others + float(payload.percent):.2f}%).",
            )
        h.percent = payload.percent
    for f in ("shares", "par_value", "since", "share_class", "notes"):
        val = getattr(payload, f, None)
        if val is not None:
            setattr(h, f, val)
    db.commit()
    db.refresh(h)
    return ShareholdingRead(
        id=h.id, entity_id=h.entity_id, entity_name=_entity_name(db, h.entity_id),
        shares=h.shares, percent=(float(h.percent) if h.percent is not None else None),
        par_value=h.par_value, since=h.since, share_class=h.share_class,
    )


@router.delete("/shareholdings/{shareholding_id}", status_code=204)
def delete_shareholding(shareholding_id: UUID, db: Session = Depends(get_db)) -> None:
    h = db.get(Shareholding, shareholding_id)
    if h is None:
        raise HTTPException(status_code=404, detail="Shareholding not found")
    db.delete(h)
    db.commit()


# --------------------------------------------------------------------------- #
# equity transactions
# --------------------------------------------------------------------------- #
def _resp(result: eq.EquityPostingResult) -> EquityPostingResponse:
    return EquityPostingResponse(
        transaction_ids=result.transaction_ids, event_ids=result.event_ids,
        summary_lines=result.summary_lines, allocations=result.allocations,
        registered_capital=result.registered_capital,
    )


@router.post("/contribution", response_model=EquityPostingResponse, status_code=201)
def post_contribution(payload: ContributionRequest, db: Session = Depends(get_db)) -> EquityPostingResponse:
    try:
        res = eq.contribution(
            db, entity_id=payload.entity_id, amount=payload.amount, txn_date=payload.date,
            to_capital=payload.to_capital, asset_account_code=payload.asset_account_code,
            reference=payload.reference,
        )
    except eq.EquityError as e:
        raise _err(e) from e
    db.commit()
    return _resp(res)


@router.post("/capital-increase", response_model=EquityPostingResponse, status_code=201)
def post_capital_increase(payload: CapitalIncreaseRequest, db: Session = Depends(get_db)) -> EquityPostingResponse:
    try:
        res = eq.capital_increase(
            db, amount=payload.amount, txn_date=payload.date, source=payload.source,
            entity_id=payload.entity_id, reference=payload.reference,
        )
    except eq.EquityError as e:
        raise _err(e) from e
    db.commit()
    return _resp(res)


@router.post("/dividend/declare", response_model=EquityPostingResponse, status_code=201)
def post_dividend_declare(payload: DividendDeclareRequest, db: Session = Depends(get_db)) -> EquityPostingResponse:
    allocations = None
    if payload.allocations:
        allocations = [(a.entity_id, a.amount) for a in payload.allocations]
    try:
        res = eq.declare_dividend(
            db, total_amount=payload.total_amount, txn_date=payload.date,
            allocations=allocations, reference=payload.reference,
        )
    except eq.EquityError as e:
        raise _err(e) from e
    db.commit()
    return _resp(res)


@router.post("/dividend/pay", response_model=EquityPostingResponse, status_code=201)
def post_dividend_pay(payload: DividendPayRequest, db: Session = Depends(get_db)) -> EquityPostingResponse:
    try:
        res = eq.pay_dividend(
            db, entity_id=payload.entity_id, amount=payload.amount, txn_date=payload.date,
            bank_account_code=payload.bank_account_code, reference=payload.reference,
        )
    except eq.EquityError as e:
        raise _err(e) from e
    db.commit()
    return _resp(res)


@router.post("/current-account", response_model=EquityPostingResponse, status_code=201)
def post_current_account(payload: CurrentAccountRequest, db: Session = Depends(get_db)) -> EquityPostingResponse:
    try:
        res = eq.shareholder_current_account(
            db, entity_id=payload.entity_id, amount=payload.amount, txn_date=payload.date,
            direction=payload.direction, bank_account_code=payload.bank_account_code,
            reference=payload.reference,
        )
    except eq.EquityError as e:
        raise _err(e) from e
    db.commit()
    return _resp(res)
