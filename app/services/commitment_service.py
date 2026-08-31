"""Installment plans and cheques: create, settle, summarise.

Settlement posts through the canonical transaction builder, so a cheque
clearing or an installment being paid gets the same guards as a hand-keyed
voucher — balanced legs, no future date, no posting into a closed period.
"""
from __future__ import annotations

import uuid
from datetime import date
from typing import Iterable

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.commitment import (
    BOUNCED,
    CHEQUE,
    INSTALLMENT,
    PAY,
    PENDING,
    RECEIVE,
    SETTLED,
    Commitment,
)


def add_months(d: date, months: int) -> date:
    """Same day-of-month N months on, clamped to the month's length so the
    31st doesn't skip February."""
    total = d.month - 1 + months
    year = d.year + total // 12
    month = total % 12 + 1
    for day in range(d.day, 0, -1):
        try:
            return date(year, month, day)
        except ValueError:
            continue
    raise ValueError("unreachable")


def split_amount(total: int, count: int) -> list[int]:
    """Divide a total into ``count`` whole units, putting the rounding
    remainder on the FIRST installment.

    Front-loading matters: the schedule must sum to exactly the amount owed, and
    a lender's first payment absorbing the odd rial is the familiar convention.
    """
    if count <= 0:
        raise ValueError("count must be positive")
    base = total // count
    remainder = total - base * count
    return [base + remainder] + [base] * (count - 1)


def create_installment_plan(
    db: Session,
    *,
    title: str,
    total_amount: int,
    count: int,
    first_due: date,
    direction: str = PAY,
    counterparty: str | None = None,
    counter_account_code: str | None = None,
    note: str | None = None,
) -> list[Commitment]:
    """Generate a monthly schedule as ``count`` rows sharing a plan_id."""
    if count <= 0:
        raise HTTPException(status_code=400, detail="Installment count must be at least 1")
    if total_amount <= 0:
        raise HTTPException(status_code=400, detail="Total amount must be positive")

    plan_id = uuid.uuid4()
    rows: list[Commitment] = []
    for i, amount in enumerate(split_amount(total_amount, count)):
        row = Commitment(
            kind=INSTALLMENT, direction=direction, title=title, amount=amount,
            due_date=add_months(first_due, i), status=PENDING,
            plan_id=plan_id, sequence=i + 1, plan_total=count,
            counterparty=counterparty, counter_account_code=counter_account_code, note=note,
        )
        db.add(row)
        rows.append(row)
    db.flush()
    return rows


def create_cheque(
    db: Session,
    *,
    title: str,
    amount: int,
    due_date: date,
    direction: str = PAY,
    reference: str | None = None,
    bank_name: str | None = None,
    counterparty: str | None = None,
    counter_account_code: str | None = None,
    note: str | None = None,
) -> Commitment:
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Cheque amount must be positive")
    row = Commitment(
        kind=CHEQUE, direction=direction, title=title, amount=amount,
        due_date=due_date, status=PENDING, reference=reference, bank_name=bank_name,
        counterparty=counterparty, counter_account_code=counter_account_code, note=note,
    )
    db.add(row)
    db.flush()
    return row


def settle(db: Session, row: Commitment, *, on: date | None = None, post: bool = True) -> Commitment:
    """Mark one commitment settled, optionally posting the ledger entry.

    Money out (paying an installment, an issued cheque clearing) debits the
    counter account and credits the bank; money in does the reverse.
    """
    if row.status == SETTLED:
        raise HTTPException(status_code=400, detail="Already settled")
    when = on or date.today()

    if post and row.counter_account_code:
        from app.services.ledger_posting import create_transaction_from_payload as _create_transaction_from_payload
        from app.schemas.transaction import TransactionCreate, TransactionLineCreate
        from app.services.account_resolver import AccountResolutionError, resolve_account_code

        try:
            bank_code = resolve_account_code(db, "bank")
        except AccountResolutionError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

        legs = (
            [(row.counter_account_code, row.amount, 0), (bank_code, 0, row.amount)]
            if row.direction == PAY
            else [(bank_code, row.amount, 0), (row.counter_account_code, 0, row.amount)]
        )
        payload = TransactionCreate(
            date=when,
            reference=row.reference or None,
            description=row.title,
            lines=[
                TransactionLineCreate(account_code=code, debit=dr, credit=cr)
                for code, dr, cr in legs
            ],
        )
        txn = _create_transaction_from_payload(db, payload)
        row.settled_transaction_id = txn.id

    row.status = SETTLED
    row.settled_on = when
    db.flush()
    return row


def mark_bounced(db: Session, row: Commitment) -> Commitment:
    """A cheque that didn't clear. Stays outstanding — the money is still owed,
    which is exactly why it must not read as settled."""
    if row.kind != CHEQUE:
        raise HTTPException(status_code=400, detail="Only a cheque can bounce")
    row.status = BOUNCED
    db.flush()
    return row


def plan_summary(db: Session, plan_id: uuid.UUID) -> dict:
    rows = db.execute(
        select(Commitment).where(Commitment.plan_id == plan_id).order_by(Commitment.sequence)
    ).scalars().all()
    if not rows:
        return {}
    pending = [r for r in rows if r.status == PENDING]
    return {
        "plan_id": str(plan_id),
        "title": rows[0].title,
        "total_amount": sum(r.amount for r in rows),
        "remaining_amount": sum(r.amount for r in pending),
        "paid_count": sum(1 for r in rows if r.status == SETTLED),
        "total_count": len(rows),
        "next_due_date": min((r.due_date for r in pending), default=None),
    }


def outstanding(db: Session, *, direction: str | None = None) -> Iterable[Commitment]:
    """Everything still owed or awaited, soonest first."""
    q = select(Commitment).where(Commitment.status.in_([PENDING, BOUNCED]))
    if direction:
        q = q.where(Commitment.direction == direction)
    return db.execute(q.order_by(Commitment.due_date)).scalars().all()


def totals(db: Session) -> dict:
    rows = list(outstanding(db))
    return {
        "payable": sum(r.amount for r in rows if r.direction == PAY),
        "receivable": sum(r.amount for r in rows if r.direction == RECEIVE),
        "count": len(rows),
        "next_due_date": min((r.due_date for r in rows), default=None),
    }
