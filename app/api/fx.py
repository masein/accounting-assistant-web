"""Foreign exchange API: rate CRUD, reporting currency setting,
on-the-fly conversion and period-end revaluation posting.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.core.auth import SessionUser, get_current_user
from app.db.session import get_db
from app.models.account import Account
from app.models.exchange_rate import ExchangeRate
from app.models.transaction import Transaction, TransactionLine
from app.schemas.fx import (
    ConvertRequest,
    ConvertResponse,
    ExchangeRateCreate,
    ExchangeRateRead,
    FXRevalueLine,
    FXRevalueRequest,
    FXRevalueResponse,
    ReportingCurrencyRead,
    ReportingCurrencyUpdate,
)
from app.services.fx_service import (
    convert_minor,
    convert as fx_convert,
    get_rate,
    get_reporting_currency,
    set_reporting_currency,
    visible_rates,
)
from app.services.reporting.repository import distinct_currencies, most_common_currency
from app.services.book_text import book_date, bt

router = APIRouter(prefix="/fx", tags=["fx"])


# ─── Metadata ─────────────────────────────────────────────────

@router.get("/metadata")
def currency_metadata(db: Session = Depends(get_db)) -> dict:
    """Currencies currently in use, plus most-common and reporting currency.

    Useful for smart-defaulting dropdowns in the UI.
    """
    from app.services.fx_base import pending_summary
    used = distinct_currencies(db)
    return {
        "reporting_currency": get_reporting_currency(db),
        "most_common_currency": most_common_currency(db),
        "used_currencies": used,
        # entries with no rate yet: the combined (currency=ALL) views leave them out
        "unconverted": pending_summary(db),
    }


# ─── Reporting currency ───────────────────────────────────────

@router.get("/reporting-currency", response_model=ReportingCurrencyRead)
def read_reporting_currency(db: Session = Depends(get_db)) -> ReportingCurrencyRead:
    return ReportingCurrencyRead(currency=get_reporting_currency(db))


@router.put("/reporting-currency", response_model=ReportingCurrencyRead)
def update_reporting_currency(
    payload: ReportingCurrencyUpdate,
    db: Session = Depends(get_db),
) -> ReportingCurrencyRead:
    curr = set_reporting_currency(db, payload.currency)
    db.commit()
    return ReportingCurrencyRead(currency=curr)


# ─── Exchange rate CRUD ───────────────────────────────────────

def _rate_read(row: ExchangeRate) -> ExchangeRateRead:
    out = ExchangeRateRead.model_validate(row)
    out.shared = row.company_id is None
    note = row.note or ""
    out.source = note[len("feed:"):] if note.startswith("feed:") else None
    return out


@router.get("/rates", response_model=list[ExchangeRateRead])
def list_rates(
    from_currency: str | None = Query(None),
    to_currency: str | None = Query(None),
    latest: bool = Query(False, description="Only the newest rate of each pair (own and shared apart)"),
    limit: int = Query(1000, ge=1, le=5000),
    db: Session = Depends(get_db),
) -> list[ExchangeRateRead]:
    """This company's own rates and the shared ones — never another company's."""
    q = select(ExchangeRate).where(visible_rates()).order_by(
        ExchangeRate.effective_date.desc(), ExchangeRate.from_currency, ExchangeRate.to_currency)
    if from_currency:
        q = q.where(ExchangeRate.from_currency == from_currency.strip().upper())
    if to_currency:
        q = q.where(ExchangeRate.to_currency == to_currency.strip().upper())
    if latest:
        newest = (select(ExchangeRate.from_currency, ExchangeRate.to_currency, ExchangeRate.company_id,
                         func.max(ExchangeRate.effective_date).label("d"))
                  .where(visible_rates())
                  .group_by(ExchangeRate.from_currency, ExchangeRate.to_currency, ExchangeRate.company_id)
                  .subquery())
        q = q.join(newest, and_(newest.c.from_currency == ExchangeRate.from_currency,
                                newest.c.to_currency == ExchangeRate.to_currency,
                                newest.c.d == ExchangeRate.effective_date,
                                or_(newest.c.company_id == ExchangeRate.company_id,
                                    and_(newest.c.company_id.is_(None), ExchangeRate.company_id.is_(None)))))
    rows = db.execute(q.limit(limit)).scalars().all()
    return [_rate_read(r) for r in rows]


def _convert_waiting_entries(db: Session, shared: bool) -> None:
    """Entries posted before any rate existed for their date convert now
    (roadmap §4.6). A shared rate may unblock every company's."""
    from app.services.fx_base import fill_pending, fill_pending_all_companies
    if shared or _current_company_uuid() is None:
        fill_pending_all_companies(db)
    else:
        fill_pending(db)
        db.commit()


def _current_company_uuid() -> UUID | None:
    from app.db.tenant import get_current_company
    cid = get_current_company()
    try:
        return UUID(str(cid)) if cid else None
    except (ValueError, TypeError):
        return None


@router.post("/rates", response_model=ExchangeRateRead, status_code=201)
def create_rate(
    payload: ExchangeRateCreate,
    db: Session = Depends(get_db),
    user: SessionUser = Depends(get_current_user),
) -> ExchangeRateRead:
    """A rate for the current company — for that pair it replaces the shared
    rate in this company's books only. ``shared`` (platform admin) sets it for
    every company."""
    fc = payload.from_currency.strip().upper()
    tc = payload.to_currency.strip().upper()
    if fc == tc:
        raise HTTPException(status_code=400, detail="from_currency and to_currency must differ")
    owner = None if payload.shared else _current_company_uuid()
    if payload.shared and not getattr(user, "is_superadmin", False):
        raise HTTPException(status_code=403, detail="Only the platform admin sets rates for every company.")
    if owner is None and not payload.shared and not getattr(user, "is_superadmin", False):
        raise HTTPException(status_code=400, detail="Pick a company first.")
    existing = db.execute(
        select(ExchangeRate)
        .where(ExchangeRate.from_currency == fc)
        .where(ExchangeRate.to_currency == tc)
        .where(ExchangeRate.effective_date == payload.effective_date)
        .where(ExchangeRate.company_id.is_(None) if owner is None else ExchangeRate.company_id == owner)
    ).scalar_one_or_none()
    if existing:
        existing.rate = float(payload.rate)
        existing.note = payload.note
        db.commit()
        db.refresh(existing)
        _convert_waiting_entries(db, shared=existing.company_id is None)
        return _rate_read(existing)
    row = ExchangeRate(
        company_id=owner,
        from_currency=fc,
        to_currency=tc,
        rate=float(payload.rate),
        effective_date=payload.effective_date,
        note=payload.note,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    _convert_waiting_entries(db, shared=row.company_id is None)
    return _rate_read(row)


@router.delete("/rates/{rate_id}", status_code=204)
def delete_rate(rate_id: UUID, db: Session = Depends(get_db),
                user: SessionUser = Depends(get_current_user)) -> None:
    row = db.get(ExchangeRate, rate_id)
    owner = _current_company_uuid()
    if not row or (row.company_id is not None and row.company_id != owner):
        raise HTTPException(status_code=404, detail="Rate not found")   # another company's: not there for you
    if row.company_id is None and not getattr(user, "is_superadmin", False):
        raise HTTPException(status_code=403, detail="A shared rate — only the platform admin can delete it. "
                                                    "Add your own rate for this pair instead; it takes precedence "
                                                    "in your company.")
    db.delete(row)
    db.commit()


# ─── Entries saved in the wrong currency ──────────────────────

class RelabelRequest(BaseModel):
    from_currency: str = Field(..., min_length=1, max_length=8)
    to_currency: str | None = Field(None, max_length=8, description="Default: the base currency")
    apply: bool = Field(False, description="False: only count what would change")


@router.post("/relabel")
def relabel_entries(payload: RelabelRequest, db: Session = Depends(get_db)) -> dict:
    """Move every entry and invoice saved in ``from_currency`` to
    ``to_currency`` (the base currency by default), amounts unchanged.

    For books where amounts were right but the currency label was wrong: until
    2026-09-28 recurring rules, petty cash, invoices without a currency and
    other paths saved entries as IRR whatever the company's currency, so a UK
    company's pounds could sit there as "rials" waiting for a rial rate
    (roadmap §4.6). Entries in a closed period are left as they are.
    """
    from app.models.credit_note import CreditNote
    from app.models.invoice import Invoice
    from app.models.payment import Payment
    from app.services.audit_service import log_audit_event
    from app.services.fx_base import base_currency
    from app.services.period_service import is_period_locked

    src = payload.from_currency.strip().upper()
    dst = (payload.to_currency or base_currency(db)).strip().upper()
    if src == dst:
        raise HTTPException(status_code=400, detail="Pick two different currencies.")
    entries = db.execute(select(Transaction).where(Transaction.currency == src,
                                                   Transaction.fx_role.is_(None))).scalars().all()
    locked = [t for t in entries if is_period_locked(db, t.date)]
    movable = [t for t in entries if t not in locked]
    invoices = db.execute(select(Invoice).where(Invoice.currency == src)).scalars().all()
    out = {"from_currency": src, "to_currency": dst, "entries": len(movable), "locked": len(locked),
           "invoices": len(invoices), "applied": False}
    if not payload.apply:
        return out
    for t in movable:
        t.currency = dst                                  # base amounts follow (app/services/fx_base.py)
    for inv in invoices:
        inv.currency = dst
        for p in db.execute(select(Payment).where(Payment.invoice_id == inv.id)).scalars():
            p.currency = dst
        for n in db.execute(select(CreditNote).where(CreditNote.invoice_id == inv.id)).scalars():
            n.currency = dst
    log_audit_event(db, action="update", entity_type="currency_relabel", entity_id=f"{src}->{dst}",
                    detail=f"{len(movable)} entries, {len(invoices)} invoices relabelled {src} → {dst}; "
                           f"{len(locked)} in a closed period left")
    db.commit()
    return {**out, "applied": True}


# ─── One-off conversion helper ────────────────────────────────

@router.post("/convert", response_model=ConvertResponse)
def convert_amount(
    payload: ConvertRequest,
    db: Session = Depends(get_db),
) -> ConvertResponse:
    on = payload.on_date or date.today()
    rate = get_rate(db, payload.from_currency, payload.to_currency, on, strict=payload.strict)
    if rate is None:
        return ConvertResponse(
            amount=payload.amount,
            from_currency=payload.from_currency,
            to_currency=payload.to_currency,
            on_date=on,
            error=f"No rate available from {payload.from_currency} to {payload.to_currency} on or before {on}",
        )
    converted = payload.amount * rate
    return ConvertResponse(
        amount=payload.amount,
        from_currency=payload.from_currency,
        to_currency=payload.to_currency,
        on_date=on,
        rate=rate,
        converted=converted,
    )


# ─── Period-end revaluation ───────────────────────────────────

# Balances that stay at their historical rate: they are not money owed or
# held, so a rate change is no gain or loss (IAS 21 / FRS 102 s30).
NON_MONETARY_CATEGORIES = ("fixed_assets", "accumulated_depreciation", "prepaid_expense", "supplier_advance")


def _non_monetary_codes() -> set[str]:
    from app.services.account_resolver import POSTING_CODES
    return {codes[c] for codes in POSTING_CODES.values() for c in NON_MONETARY_CATEGORIES if c in codes}


@router.post("/revalue", response_model=FXRevalueResponse)
def revalue_foreign_currency_balances(
    payload: FXRevalueRequest,
    db: Session = Depends(get_db),
) -> FXRevalueResponse:
    """Period-end revaluation of foreign-currency balances (roadmap §4.6).

    Every line keeps its value in the base currency at the rate it was posted
    at. For each account and foreign currency: the foreign balance at the
    ``as_of`` rate, minus the base value it is carried at, is the unrealised
    gain or loss. Posted (``dry_run=false``) as one entry per currency whose
    lines move base values only — the foreign balances do not change — with
    the net to the gain or loss account. Running it again for the same date
    and rate finds nothing left to adjust.
    """
    from app.services.fx_base import base_currency
    from app.services.reporting.common import ASSET, LIABILITY, classify_account_code

    base = base_currency(db)
    target = (payload.target_currency or base).strip().upper()
    if target != base:
        raise HTTPException(status_code=400, detail=f"Revaluation is into the base currency, {base}.")
    on = payload.as_of
    from app.services.period_service import assert_period_open
    assert_period_open(db, on)  # a revaluation entry is a posting too (review H7)
    errors: list[str] = []

    q = (
        select(TransactionLine.account_id, Transaction.currency, TransactionLine.debit, TransactionLine.credit,
               TransactionLine.base_debit, TransactionLine.base_credit)
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .where(Transaction.deleted_at.is_(None), Transaction.date <= on, Transaction.currency != base)
    )
    accounts = {a.id: a for a in db.execute(select(Account)).scalars().all()}
    if payload.account_codes:
        codes = {c.strip() for c in payload.account_codes if c and c.strip()}
        acc_ids = [a.id for a in accounts.values() if a.code in codes]
        if not acc_ids:
            raise HTTPException(status_code=400, detail="No accounts matched the provided codes")
        q = q.where(TransactionLine.account_id.in_(acc_ids))
        wanted = set(acc_ids)
    else:
        skip = _non_monetary_codes()
        wanted = {a.id for a in accounts.values()
                  if classify_account_code(a.code) in (ASSET, LIABILITY) and a.code not in skip}
    # (account, currency) -> [foreign balance, base value, lines without a rate]
    held: dict[tuple[UUID, str], list[int]] = defaultdict(lambda: [0, 0, 0])
    for account_id, curr, debit, credit, bdebit, bcredit in db.execute(q).all():
        if account_id not in wanted:
            continue
        slot = held[(account_id, (curr or "IRR").upper())]
        slot[0] += (debit or 0) - (credit or 0)
        slot[1] += (bdebit or 0) - (bcredit or 0)
        if bdebit is None and bcredit is None and (debit or credit):
            slot[2] += 1

    revalue_lines: list[FXRevalueLine] = []
    for (account_id, fc), (foreign, carried, unconverted) in sorted(
            held.items(), key=lambda kv: (accounts[kv[0][0]].code if kv[0][0] in accounts else "", kv[0][1])):
        acc = accounts.get(account_id)
        if acc is None or (foreign == 0 and carried == 0):
            continue
        if unconverted:
            errors.append(f"{acc.code}: {unconverted} {fc} entr{'y has' if unconverted == 1 else 'ies have'} "
                          f"no rate yet — add a {fc}→{base} rate for {'its' if unconverted == 1 else 'their'} "
                          "date first")
            continue
        rate = get_rate(db, fc, base, on, strict=True)
        if rate is None:
            errors.append(f"Missing rate {fc}->{base} on/before {on.isoformat()} for account {acc.code}")
            continue
        value = convert_minor(foreign, rate)
        revalue_lines.append(FXRevalueLine(
            account_code=acc.code, account_name=acc.name, source_currency=fc, source_balance=foreign,
            target_currency=base, rate=rate, target_balance=value, current_target_balance=carried,
            adjustment=value - carried,
        ))
    total_adjustment = sum(ln.adjustment for ln in revalue_lines)

    posted: list[UUID] = []
    # Post whenever ANY account moves: a USD asset and a USD liability can
    # revalue by equal and opposite amounts (net gain 0) and both balances
    # still have to change.
    if not payload.dry_run and any(ln.adjustment for ln in revalue_lines):
        if not payload.gain_account_code or not payload.loss_account_code:
            raise HTTPException(
                status_code=400,
                detail="gain_account_code and loss_account_code are required when dry_run=false",
            )
        by_code = {a.code: a for a in accounts.values()}
        gain_acc = by_code.get(payload.gain_account_code.strip())
        loss_acc = by_code.get(payload.loss_account_code.strip())
        if not gain_acc or not loss_acc:
            raise HTTPException(status_code=400, detail="Gain or loss account not found")
        from app.services.audit_service import log_audit_event
        for fc in sorted({ln.source_currency for ln in revalue_lines if ln.adjustment}):
            moves = [ln for ln in revalue_lines if ln.source_currency == fc and ln.adjustment]
            rate = moves[0].rate
            txn = Transaction(
                date=on,
                reference=(payload.reference or f"FX-REVAL-{on.isoformat()}")[:120] + f"-{fc}",
                description=(payload.description
                             or bt(db, "fx_reval", ccy=fc, rate=f"{rate:g}", base=base, on=book_date(db, on))),
                currency=fc, fx_rate=rate, fx_role="revaluation",
            )
            db.add(txn)
            db.flush()
            net = 0
            for ln in moves:
                net += ln.adjustment
                db.add(TransactionLine(
                    transaction_id=txn.id, account_id=by_code[ln.account_code].id, debit=0, credit=0,
                    base_debit=max(ln.adjustment, 0), base_credit=max(-ln.adjustment, 0),
                    line_description=bt(db, "fx_revalued_line", ccy=fc, amount=f"{ln.source_balance:,}", rate=f"{rate:g}"),
                ))
            if net:
                db.add(TransactionLine(
                    transaction_id=txn.id, account_id=(gain_acc if net > 0 else loss_acc).id, debit=0, credit=0,
                    base_debit=max(-net, 0), base_credit=max(net, 0),
                    line_description=bt(db, "fx_gain_line" if net > 0 else "fx_loss_line", ccy=fc),
                ))
            log_audit_event(db, action="create", entity_type="fx_revaluation", entity_id=str(txn.id),
                            detail=f"{fc} as of {on.isoformat()} at {rate:g}: net {net}")
            posted.append(txn.id)
        db.commit()

    return FXRevalueResponse(
        as_of=on,
        target_currency=base,
        lines=revalue_lines,
        total_adjustment=total_adjustment,
        posted_transaction_id=posted[0] if posted else None,
        posted_transaction_ids=posted,
        errors=errors,
    )
