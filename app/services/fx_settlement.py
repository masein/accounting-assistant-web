"""Realised exchange gains and losses (roadmap 2026-09 §4.6, part 3).

A USD invoice booked when $1 = £0.80 and paid when $1 = £0.85 brings in more
pounds than the receivable was carried at. As Xero and QuickBooks do it, the
payment entry then carries:

* the bank line at the payment's rate (the pounds that actually arrived);
* the receivable (or payable) line at the invoice's rate — what it was booked
  at, so it goes to zero in pounds when it goes to zero in dollars;
* one base-only line (no dollar amount) for the difference, on the realised
  FX gain or loss account.

The last payment or credit note on an invoice takes exactly what is left of
its base value, so rounding across several part-payments never leaves a
penny on the receivable. An overpayment's excess (customer credit or supplier
advance) is a new balance at the payment's rate.

Entries settled here get ``fx_role = 'settlement'``: the base-amount hook
(app/services/fx_base.py) leaves them alone. Nothing is settled while the
invoice or the payment still waits for a rate; ``settle_waiting`` does it
once both are known (after a rate is entered, the daily feeds, boot).
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.transaction import Transaction, TransactionLine

SETTLEMENT = "settlement"
FX_MARK = "Realised FX"


def is_fx_line(line: TransactionLine) -> bool:
    return not (line.debit or 0) and not (line.credit or 0) and (line.line_description or "").startswith(FX_MARK)


def _half_up(x: Decimal) -> int:
    return int(x.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _control_account_id(db: Session, inv) -> object | None:
    from app.models.account import Account
    from app.services.account_resolver import resolve_account_code
    code = resolve_account_code(db, "ar" if inv.kind == "sales" else "ap")
    return db.execute(select(Account.id).where(Account.code == code)).scalar_one_or_none()


def _lines(db: Session, txn_id) -> list[TransactionLine]:
    return list(db.execute(select(TransactionLine).where(TransactionLine.transaction_id == txn_id)).scalars())


def invoice_for(db: Session, txn: Transaction):
    """The invoice a payment or credit-note entry settles, if any."""
    from app.models.credit_note import CreditNote
    from app.models.invoice import Invoice
    from app.models.payment import Payment
    inv_id = db.execute(select(Payment.invoice_id).where(Payment.transaction_id == txn.id)).scalars().first()
    if inv_id is None:
        inv_id = db.execute(select(CreditNote.invoice_id).where(CreditNote.transaction_id == txn.id,
                                                                CreditNote.invoice_id.is_not(None))).scalars().first()
    return db.get(Invoice, inv_id) if inv_id else None


def _settling_txn_ids(db: Session, inv) -> list:
    """Every live entry that settles part of ``inv``: payments and credit notes."""
    from app.models.credit_note import CreditNote
    from app.models.payment import Payment
    ids = [t for t in db.execute(select(Payment.transaction_id).where(Payment.invoice_id == inv.id)).scalars() if t]
    ids += [t for t in db.execute(select(CreditNote.transaction_id).where(CreditNote.invoice_id == inv.id)).scalars()
            if t and t not in ids]
    live = set(db.execute(select(Transaction.id).where(Transaction.id.in_(ids))).scalars()) if ids else set()
    return [t for t in ids if t in live]


def settle(db: Session, txn: Transaction, inv) -> dict | None:
    """Give ``txn`` (a payment or credit note on ``inv``) the invoice's rate on
    its receivable/payable line and post the difference as realised FX.
    Idempotent. Returns ``{"difference": ...}`` (+ gain, − loss), or None when
    there is nothing to settle (base-currency invoice, a rate still missing)."""
    from app.services.fx_base import base_currency, convert_transaction, to_base
    base = base_currency(db)
    if (inv.currency or base).upper() == base or txn.fx_role not in (None, SETTLEMENT):
        return None
    from app.services.period_service import is_period_locked
    if txn.date and is_period_locked(db, txn.date):
        return None                                           # closed figures never move
    if not inv.transaction_id:
        return None
    booked = db.get(Transaction, inv.transaction_id)
    if booked is None or booked.fx_rate is None:
        return None
    if txn.fx_rate is None and not convert_transaction(db, txn):
        return None
    control = _control_account_id(db, inv)
    if control is None:
        return None

    # What the invoice put on the receivable/payable, in its currency and in base.
    inv_foreign = inv_base = 0
    for ln in _lines(db, booked.id):
        if ln.account_id == control:
            inv_foreign += abs((ln.debit or 0) - (ln.credit or 0))
            inv_base += abs((ln.base_debit or 0) - (ln.base_credit or 0))
    if not inv_foreign:
        return None

    lines = _lines(db, txn.id)
    mine = [ln for ln in lines if ln.account_id == control and ((ln.debit or 0) or (ln.credit or 0))]
    if not mine:
        return None
    this_foreign = sum(abs((ln.debit or 0) - (ln.credit or 0)) for ln in mine)

    # What earlier settlements already took off it.
    prior_foreign = prior_base = 0
    all_known = True
    for tid in _settling_txn_ids(db, inv):
        if tid == txn.id:
            continue
        other = db.get(Transaction, tid)
        for ln in _lines(db, tid):
            if ln.account_id != control or is_fx_line(ln):
                continue
            prior_foreign += abs((ln.debit or 0) - (ln.credit or 0))
            if other.fx_role == SETTLEMENT:
                prior_base += abs((ln.base_debit or 0) - (ln.base_credit or 0))
            else:
                all_known = False
    if all_known and prior_foreign + this_foreign >= inv_foreign:
        carried = inv_base - prior_base                       # the last one takes what is left
    else:
        carried = _half_up(Decimal(this_foreign) * Decimal(inv_base) / Decimal(inv_foreign))

    # Everything else at the payment's own rate.
    others = [ln for ln in lines if ln not in mine and not is_fx_line(ln) and ((ln.debit or 0) or (ln.credit or 0))]
    for ln, (bd, bc) in zip(others, to_base([(ln.debit or 0, ln.credit or 0) for ln in others], txn.fx_rate)):
        ln.base_debit, ln.base_credit = bd, bc
    # The control line(s) at the invoice's rate, split in proportion if several.
    left = carried
    for i, ln in enumerate(mine):
        share = left if i == len(mine) - 1 else _half_up(
            Decimal(abs((ln.debit or 0) - (ln.credit or 0))) * Decimal(carried) / Decimal(this_foreign))
        left -= share
        ln.base_debit, ln.base_credit = (share, 0) if (ln.debit or 0) else (0, share)

    # The difference, on one base-only line.
    for ln in lines:
        if is_fx_line(ln):
            db.delete(ln)
    total_dr = sum(ln.base_debit or 0 for ln in others + mine)
    total_cr = sum(ln.base_credit or 0 for ln in others + mine)
    difference = total_dr - total_cr                          # > 0: more came in than was booked → gain
    if difference:
        from app.models.account import Account
        from app.services.account_resolver import resolve_account_code
        code = resolve_account_code(db, "fx_gain" if difference > 0 else "fx_loss")
        acc = db.execute(select(Account.id).where(Account.code == code)).scalar_one()
        db.add(TransactionLine(
            transaction_id=txn.id, account_id=acc, debit=0, credit=0,
            base_debit=max(-difference, 0), base_credit=max(difference, 0),
            line_description=f"{FX_MARK} {'gain' if difference > 0 else 'loss'} — {inv.number}",
        ))
    txn.fx_role = SETTLEMENT
    db.flush()
    return {"difference": difference, "carried": carried}


def realised_on(db: Session, txn_id) -> tuple[int | None, str | None]:
    """(realised gain + / loss −, base currency) booked on a payment entry;
    (None, None) when it realised nothing."""
    from app.services.fx_base import base_currency
    total = 0
    found = False
    for ln in _lines(db, txn_id):
        if is_fx_line(ln):
            found = True
            total += (ln.base_credit or 0) - (ln.base_debit or 0)
    return (total, base_currency(db)) if found else (None, None)


def settle_txn(db: Session, txn: Transaction) -> dict | None:
    inv = invoice_for(db, txn)
    return settle(db, txn, inv) if inv is not None else None


def settle_waiting(db: Session) -> int:
    """Settle every payment and credit note on a foreign invoice that has not
    been settled yet (its rate or the invoice's arrived since). Returns how
    many were settled."""
    from app.models.credit_note import CreditNote
    from app.models.invoice import Invoice
    from app.models.payment import Payment
    from app.services.fx_base import base_currency
    base = base_currency(db)
    pairs = list(db.execute(
        select(Transaction, Invoice).join(Payment, Payment.transaction_id == Transaction.id)
        .join(Invoice, Invoice.id == Payment.invoice_id)
        .where(Transaction.fx_role.is_(None), Invoice.currency != base)).all())
    pairs += list(db.execute(
        select(Transaction, Invoice).join(CreditNote, CreditNote.transaction_id == Transaction.id)
        .join(Invoice, Invoice.id == CreditNote.invoice_id)
        .where(Transaction.fx_role.is_(None), Invoice.currency != base)).all())
    done = 0
    for txn, inv in sorted(pairs, key=lambda p: (p[0].date, str(p[0].created_at or ""))):
        if settle(db, txn, inv) is not None:
            done += 1
    return done
