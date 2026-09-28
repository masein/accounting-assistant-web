"""Base-currency amounts (roadmap 2026-09 §4.6, option 2 — as Xero and
QuickBooks keep them).

Every journal line carries, next to its amount in the entry's currency, the
same amount in the company's base currency at the rate of the entry's date.
That is what lets a report add a USD sale and a GBP sale together, a
revaluation move only the base value of a USD bank account, and a payment
settle an invoice booked at another rate (realised gain/loss, part 2).

Rules
-----
* The rate is fixed when the entry is posted: ``Transaction.fx_rate`` — 1 for
  an entry in the base currency, the caller's rate if it gave one (the voucher
  form, an import), else the rate on file for that date (the company's own
  first, then the shared one, crossing through USD/EUR/GBP/IRR). A later
  change to the rates table never moves posted entries.
* Changing an entry's currency or date re-reads the rate, unless the same
  change sets ``fx_rate`` itself.
* No rate known: the entry still posts, with ``fx_rate`` and the lines' base
  amounts NULL. Base-currency reports leave it out and say so, and it is
  converted as soon as a rate is added (``fill_pending``: after a rate is
  entered, after the daily feeds, and on every boot).
* Each line is rounded half-up; if the rounded lines no longer balance, the
  unit is taken back from the line whose rounding added the most, so every
  entry balances in base as it does in its own currency.
* Entries with an ``fx_role`` (revaluations) carry base amounts the system set
  itself and are never recomputed here.
* All of this runs in one ``before_flush`` hook, so every writer — the
  canonical posting path and the dozen that build lines directly (invoices,
  payroll, fixed assets, imports, reversals…) — gets base amounts without
  knowing about them.
"""
from __future__ import annotations

import logging
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable

from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session

from app.models.transaction import Transaction, TransactionLine

log = logging.getLogger("app.fx_base")


# --- arithmetic -------------------------------------------------------------------------------------

def to_base(amounts: list[tuple[int, int]], rate: float | Decimal) -> list[tuple[int, int]]:
    """``[(debit, credit), ...]`` in the entry's currency → the same in base,
    each rounded half-up, then nudged by whole units until debits equal
    credits again (only when the entry balanced to begin with)."""
    r = Decimal(repr(float(rate))) if not isinstance(rate, Decimal) else rate
    exact = [(Decimal(int(d or 0)) * r, Decimal(int(c or 0)) * r) for d, c in amounts]
    out = [[int(d.quantize(Decimal(1), rounding=ROUND_HALF_UP)), int(c.quantize(Decimal(1), rounding=ROUND_HALF_UP))]
           for d, c in exact]
    if sum(int(d or 0) for d, _ in amounts) != sum(int(c or 0) for _, c in amounts):
        return [tuple(x) for x in out]
    diff = sum(d for d, _ in out) - sum(c for _, c in out)
    while diff:
        # Candidates: lower a debit / raise a credit (diff > 0), or the reverse.
        # Take the one whose rounding moved furthest in the wrong direction.
        best, best_err = None, None
        for i, ((ed, ec), (rd, rc)) in enumerate(zip(exact, out)):
            for side, e, rnd in ((0, ed, rd), (1, ec, rc)):
                step = -1 if (diff > 0) == (side == 0) else 1
                if (e == 0 and rnd == 0) or rnd + step < 0:
                    continue
                err = (rnd - e) * (-step)        # how much this nudge corrects its own rounding
                if best_err is None or err > best_err:
                    best, best_err = (i, side, step), err
        if best is None:
            break
        i, side, step = best
        out[i][side] += step
        diff += step if side == 0 else -step
    return [tuple(x) for x in out]


# --- which rate ---------------------------------------------------------------------------------------

def base_currency(db: Session) -> str:
    from app.services.fx_service import get_reporting_currency
    return (get_reporting_currency(db) or "IRR").strip().upper()


def rate_to_base(db: Session, currency: str | None, on: date, base: str | None = None) -> float | None:
    """The rate on or before ``on`` (never a later one: it would be fixed
    into the entry)."""
    from app.services.fx_service import get_rate
    base = base or base_currency(db)
    ccy = (currency or "IRR").strip().upper()
    if ccy == base:
        return 1.0
    return get_rate(db, ccy, base, on, strict=True)


# --- converting one entry -------------------------------------------------------------------------------

def _lines_of(db: Session, txn: Transaction) -> list[TransactionLine]:
    """Every line the entry will have after this flush."""
    lines: list[TransactionLine] = []
    seen: set[int] = set()
    if txn.id is not None and inspect(txn).persistent:
        for ln in db.execute(select(TransactionLine).where(TransactionLine.transaction_id == txn.id)
                             .execution_options(include_deleted=True)).scalars():
            lines.append(ln)
            seen.add(id(ln))
    for obj in db.new:
        if isinstance(obj, TransactionLine) and id(obj) not in seen and _txn_of(db, obj) is txn:
            lines.append(obj)
            seen.add(id(obj))
    return [ln for ln in lines if ln not in db.deleted]


def convert_transaction(db: Session, txn: Transaction, *, relookup: bool = False, base: str | None = None) -> bool:
    """Set ``fx_rate`` (if unset or ``relookup``) and every line's base
    amounts. Returns False when no rate is known (base left NULL)."""
    if txn.fx_role:
        return True
    lines = _lines_of(db, txn)
    base = base or base_currency(db)
    ccy = (txn.currency or "IRR").strip().upper()
    if ccy == base:
        txn.fx_rate = 1.0
    elif relookup or txn.fx_rate is None:
        txn.fx_rate = rate_to_base(db, ccy, txn.date or date.today(), base) if txn.date else None
    if txn.fx_rate is None:
        for ln in lines:
            if (ln.debit or 0) or (ln.credit or 0):
                ln.base_debit = ln.base_credit = None
        return False
    amounts = [ln for ln in lines if (ln.debit or 0) or (ln.credit or 0)]
    for ln, (bd, bc) in zip(amounts, to_base([(ln.debit or 0, ln.credit or 0) for ln in amounts], txn.fx_rate)):
        ln.base_debit, ln.base_credit = bd, bc
    return True


# --- the hook: every write gets base amounts ---------------------------------------------------------------

def _txn_of(db: Session, line: TransactionLine) -> Transaction | None:
    state = inspect(line)
    if "transaction" not in state.unloaded:
        return line.transaction
    if line.transaction_id is None:
        return None
    return db.get(Transaction, line.transaction_id)


def _changed(obj, *names: str) -> bool:
    state = inspect(obj)
    return any(state.attrs[n].history.has_changes() for n in names)


@event.listens_for(Session, "before_flush")
def _fill_base_amounts(session: Session, _ctx, _instances) -> None:
    if session.info.get("fx_base_off"):
        return
    todo: dict[int, tuple[Transaction, bool]] = {}

    def want(txn: Transaction | None, relookup: bool = False) -> None:
        if txn is None or txn.fx_role or txn in session.deleted or txn.deleted_at is not None:
            return
        prev = todo.get(id(txn))
        todo[id(txn)] = (txn, relookup or (prev[1] if prev else False))

    with session.no_autoflush:
        for obj in list(session.new) + list(session.dirty) + list(session.deleted):
            if isinstance(obj, Transaction):
                if obj in session.new:
                    want(obj)
                elif obj in session.dirty and _changed(obj, "currency", "date", "fx_rate"):
                    # a new currency or date means a new rate — unless this
                    # same change says which rate
                    want(obj, relookup=not _changed(obj, "fx_rate"))
            elif isinstance(obj, TransactionLine):
                if obj in session.dirty and not _changed(obj, "debit", "credit", "transaction_id"):
                    continue
                want(_txn_of(session, obj))
        base = base_currency(session) if todo else None
        for txn, relookup in todo.values():
            convert_transaction(session, txn, relookup=relookup, base=base)


# --- catching up -------------------------------------------------------------------------------------------------

def pending(db: Session, currency: str | None = None) -> list[Transaction]:
    """Live entries still waiting for a rate."""
    q = select(Transaction).where(Transaction.fx_rate.is_(None), Transaction.fx_role.is_(None))
    if currency:
        q = q.where(Transaction.currency == currency.strip().upper())
    return list(db.execute(q.order_by(Transaction.date)).scalars())


def pending_summary(db: Session) -> dict:
    """What base-currency reports leave out: count and currencies of the
    entries with no rate yet."""
    rows = pending(db)
    return {"count": len(rows), "currencies": sorted({(t.currency or "IRR").upper() for t in rows})}


def fill_pending(db: Session) -> dict:
    """Convert every entry of the current company that had no rate, now that
    one may exist. Commits nothing; returns counts."""
    done = left = 0
    for txn in pending(db):
        if convert_transaction(db, txn):
            done += 1
        else:
            left += 1
    db.flush()
    return {"converted": done, "still_pending": left}


def recompute_all(db: Session) -> dict:
    """After the base currency changed: every entry re-read at the rate of its
    date into the new base. Revaluations were measured in the old base; their
    base amounts go to 0 and the response says to run the revaluation again."""
    done = left = reval = 0
    for txn in db.execute(select(Transaction)).scalars():
        if txn.fx_role == "revaluation":
            for ln in txn.lines:
                ln.base_debit = ln.base_credit = 0
            txn.fx_role = "legacy_revaluation"
            reval += 1
            continue
        if txn.fx_role:
            continue
        if convert_transaction(db, txn, relookup=True):
            done += 1
        else:
            left += 1
    db.flush()
    return {"converted": done, "still_pending": left, "revaluations_cleared": reval}


def fill_pending_all_companies(db: Session) -> dict:
    """Boot and daily-feed catch-up for every company. One company's failure
    is logged and never stops the others."""
    from app.db.tenant import tenant_bypass, use_company
    from app.models.company import Company
    with tenant_bypass():
        ids = [str(c) for c in db.execute(select(Company.id)).scalars()]
    total = {"converted": 0, "still_pending": 0}
    for cid in ids:
        try:
            with use_company(cid):
                out = fill_pending(db)
                db.commit()
            for k in total:
                total[k] += out[k]
        except Exception:  # noqa: BLE001
            db.rollback()
            log.exception("base-amount catch-up failed company=%s", cid)
    return total


def line_amounts(line: TransactionLine, base_view: bool) -> tuple[int, int]:
    """(debit, credit) of a line in the view a report shows: its own currency
    or the base currency (0 while not converted)."""
    if base_view:
        return int(line.base_debit or 0), int(line.base_credit or 0)
    return int(line.debit or 0), int(line.credit or 0)


def iter_base(lines: Iterable[TransactionLine]) -> Iterable[tuple[int, int]]:
    for ln in lines:
        yield int(ln.base_debit or 0), int(ln.base_credit or 0)
