"""Foreign exchange API: rate CRUD, reporting currency setting,
on-the-fly conversion and period-end revaluation posting.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

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
    convert as fx_convert,
    get_rate,
    get_reporting_currency,
    set_reporting_currency,
)
from app.services.reporting.repository import distinct_currencies, most_common_currency

router = APIRouter(prefix="/fx", tags=["fx"])


# ─── Metadata ─────────────────────────────────────────────────

@router.get("/metadata")
def currency_metadata(db: Session = Depends(get_db)) -> dict:
    """Currencies currently in use, plus most-common and reporting currency.

    Useful for smart-defaulting dropdowns in the UI.
    """
    used = distinct_currencies(db)
    return {
        "reporting_currency": get_reporting_currency(db),
        "most_common_currency": most_common_currency(db),
        "used_currencies": used,
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

@router.get("/rates", response_model=list[ExchangeRateRead])
def list_rates(
    from_currency: str | None = Query(None),
    to_currency: str | None = Query(None),
    db: Session = Depends(get_db),
) -> list[ExchangeRateRead]:
    q = select(ExchangeRate).order_by(ExchangeRate.effective_date.desc())
    if from_currency:
        q = q.where(ExchangeRate.from_currency == from_currency.strip().upper())
    if to_currency:
        q = q.where(ExchangeRate.to_currency == to_currency.strip().upper())
    rows = db.execute(q).scalars().all()
    return [ExchangeRateRead.model_validate(r) for r in rows]


@router.post("/rates", response_model=ExchangeRateRead, status_code=201)
def create_rate(
    payload: ExchangeRateCreate,
    db: Session = Depends(get_db),
) -> ExchangeRateRead:
    fc = payload.from_currency.strip().upper()
    tc = payload.to_currency.strip().upper()
    if fc == tc:
        raise HTTPException(status_code=400, detail="from_currency and to_currency must differ")
    existing = db.execute(
        select(ExchangeRate)
        .where(ExchangeRate.from_currency == fc)
        .where(ExchangeRate.to_currency == tc)
        .where(ExchangeRate.effective_date == payload.effective_date)
    ).scalar_one_or_none()
    if existing:
        existing.rate = float(payload.rate)
        existing.note = payload.note
        db.commit()
        db.refresh(existing)
        return ExchangeRateRead.model_validate(existing)
    row = ExchangeRate(
        from_currency=fc,
        to_currency=tc,
        rate=float(payload.rate),
        effective_date=payload.effective_date,
        note=payload.note,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return ExchangeRateRead.model_validate(row)


@router.delete("/rates/{rate_id}", status_code=204)
def delete_rate(rate_id: UUID, db: Session = Depends(get_db)) -> None:
    row = db.get(ExchangeRate, rate_id)
    if not row:
        raise HTTPException(status_code=404, detail="Rate not found")
    db.delete(row)
    db.commit()


# ─── One-off conversion helper ────────────────────────────────

@router.post("/convert", response_model=ConvertResponse)
def convert_amount(
    payload: ConvertRequest,
    db: Session = Depends(get_db),
) -> ConvertResponse:
    on = payload.on_date or date.today()
    rate = get_rate(db, payload.from_currency, payload.to_currency, on)
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

@router.post("/revalue", response_model=FXRevalueResponse)
def revalue_foreign_currency_balances(
    payload: FXRevalueRequest,
    db: Session = Depends(get_db),
) -> FXRevalueResponse:
    """Revalue foreign-currency account balances into the target reporting currency.

    Compares (a) the balance held in foreign currency converted at the as_of rate
    against (b) the balance posted so far in the target currency for the same
    account. The difference is the adjustment needed to align the books.

    Dry-run by default; pass dry_run=false plus gain/loss account codes to post
    a single balancing journal entry.
    """
    target = payload.target_currency.strip().upper()
    on = payload.as_of
    errors: list[str] = []

    # 1. Collect foreign currency balances per account, grouped by (account_id, currency)
    q = (
        select(
            TransactionLine.account_id,
            Transaction.currency,
            TransactionLine.debit,
            TransactionLine.credit,
        )
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .where(Transaction.deleted_at.is_(None))
        .where(Transaction.date <= on)
    )
    if payload.account_codes:
        codes = {c.strip() for c in payload.account_codes if c and c.strip()}
        acc_ids = [
            a.id
            for a in db.execute(select(Account).where(Account.code.in_(codes))).scalars().all()
        ]
        if not acc_ids:
            raise HTTPException(status_code=400, detail="No accounts matched the provided codes")
        q = q.where(TransactionLine.account_id.in_(acc_ids))
    rows = db.execute(q).all()
    # account_id -> currency -> net (debit - credit)
    by_acct: dict[UUID, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for account_id, curr, debit, credit in rows:
        by_acct[account_id][(curr or "IRR").upper()] += (debit or 0) - (credit or 0)

    accounts = {a.id: a for a in db.execute(select(Account)).scalars().all()}

    revalue_lines: list[FXRevalueLine] = []
    total_adjustment = 0
    for account_id, by_curr in by_acct.items():
        acc = accounts.get(account_id)
        if not acc:
            continue
        # Skip accounts that only have target-currency data
        foreign_currencies = [c for c in by_curr.keys() if c != target]
        if not foreign_currencies:
            continue
        current_target_balance = by_curr.get(target, 0)
        # Sum all foreign balances converted into target at the as_of rate
        converted_total = 0
        per_currency_rates: list[tuple[str, int, float, int]] = []
        for fc in foreign_currencies:
            src_balance = by_curr[fc]
            if src_balance == 0:
                continue
            rate = get_rate(db, fc, target, on)
            if rate is None:
                errors.append(
                    f"Missing rate {fc}->{target} on/before {on.isoformat()} for account {acc.code}"
                )
                continue
            tgt_val = int(round(src_balance * rate))
            converted_total += tgt_val
            per_currency_rates.append((fc, src_balance, rate, tgt_val))
        if not per_currency_rates:
            continue
        adjustment = converted_total - current_target_balance
        for fc, src_balance, rate, tgt_val in per_currency_rates:
            # Represent each source currency row; adjustment is surfaced on the
            # first (or only) row per account to avoid double-counting.
            revalue_lines.append(FXRevalueLine(
                account_code=acc.code,
                account_name=acc.name,
                source_currency=fc,
                source_balance=src_balance,
                target_currency=target,
                rate=rate,
                target_balance=tgt_val,
                current_target_balance=current_target_balance if fc == per_currency_rates[0][0] else 0,
                adjustment=adjustment if fc == per_currency_rates[0][0] else 0,
            ))
        total_adjustment += adjustment

    # 2. If not dry-run, post the adjustment as a journal entry in target currency
    posted_id: UUID | None = None
    if not payload.dry_run and total_adjustment != 0:
        if not payload.gain_account_code or not payload.loss_account_code:
            raise HTTPException(
                status_code=400,
                detail="gain_account_code and loss_account_code are required when dry_run=false",
            )
        gain_acc = db.execute(
            select(Account).where(Account.code == payload.gain_account_code.strip())
        ).scalar_one_or_none()
        loss_acc = db.execute(
            select(Account).where(Account.code == payload.loss_account_code.strip())
        ).scalar_one_or_none()
        if not gain_acc or not loss_acc:
            raise HTTPException(status_code=400, detail="Gain or loss account not found")

        # Build a balanced entry: adjust each foreign account up/down in target
        # currency, offset by the net unrealized gain/loss account.
        # Per-account adjustments we already have = (current - need_to_be)
        # We post: debit account if adjustment > 0 else credit it.
        # Counter-side lands on gain (credit) / loss (debit) as appropriate.
        txn = Transaction(
            date=on,
            reference=(payload.reference or f"FX-REVAL-{on.isoformat()}"),
            description=(payload.description or f"FX revaluation to {target} as of {on.isoformat()}"),
            currency=target,
        )
        db.add(txn)
        db.flush()

        running_delta = 0
        seen_accounts: set[UUID] = set()
        for ln in revalue_lines:
            # Only post one line per (account_code) — adjustment is on first source-currency row
            if ln.adjustment == 0:
                continue
            acc = db.execute(
                select(Account).where(Account.code == ln.account_code)
            ).scalar_one()
            if acc.id in seen_accounts:
                continue
            seen_accounts.add(acc.id)
            adj = ln.adjustment
            running_delta += adj
            if adj > 0:
                db.add(TransactionLine(
                    transaction_id=txn.id,
                    account_id=acc.id,
                    debit=abs(adj),
                    credit=0,
                    line_description=f"Reval {ln.source_currency}->{target} at {ln.rate}",
                ))
            else:
                db.add(TransactionLine(
                    transaction_id=txn.id,
                    account_id=acc.id,
                    debit=0,
                    credit=abs(adj),
                    line_description=f"Reval {ln.source_currency}->{target} at {ln.rate}",
                ))
        # Counter-side: if net running_delta > 0 the foreign accounts grew in value,
        # so we credit "FX gain". If negative, we debit "FX loss".
        if running_delta > 0:
            db.add(TransactionLine(
                transaction_id=txn.id,
                account_id=gain_acc.id,
                debit=0,
                credit=running_delta,
                line_description="Net unrealized FX gain",
            ))
        elif running_delta < 0:
            db.add(TransactionLine(
                transaction_id=txn.id,
                account_id=loss_acc.id,
                debit=abs(running_delta),
                credit=0,
                line_description="Net unrealized FX loss",
            ))
        db.commit()
        db.refresh(txn)
        posted_id = txn.id

    return FXRevalueResponse(
        as_of=on,
        target_currency=target,
        lines=revalue_lines,
        total_adjustment=total_adjustment,
        posted_transaction_id=posted_id,
        errors=errors,
    )
