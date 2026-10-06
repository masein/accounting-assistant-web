"""A party's available credit: what is left of each 'credit' row.

A 'credit' row (app/models/credit_note.py) is money the company owes a
customer, or a supplier owes the company: an overpayment, or the part of a
credit note beyond what an invoice still owed. Refunds and applications to
other invoices are rows of their own that name the credit they draw on
(``credit_id``); what is left of a credit is its amount less theirs.

Only live entries count (security review, 2026-10-06): a refund or a use whose
entry was reversed gives its amount back, and a credit whose own entry was
reversed (its overpayment reversed, its invoice voided) is gone. A caller about
to spend credit locks the rows first (``lock=True``), so two requests at once
can't both spend the same credit.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.models.credit_note import CreditNote

CONSUMING = ("refund", "applied", "withdrawn")
# what moved money or settled another invoice: a "withdrawn" row is the credit
# taken back when the payment that made it is undone (a bounced cheque)
MOVED = ("refund", "applied")


def _reversed(db: Session, transaction_ids) -> set[str]:
    """Which of these entries were reversed or undone (an "undo" audit row names each)."""
    ids = {str(t) for t in transaction_ids if t is not None}
    if not ids:
        return set()
    return set(db.execute(select(AuditLog.entity_id).where(
        AuditLog.action == "undo", AuditLog.entity_type == "transaction", AuditLog.entity_id.in_(ids))).scalars())


def used(db: Session, credit_ids: list[UUID], *, kinds=CONSUMING) -> dict[UUID, int]:
    """How much of each credit has been refunded, applied or withdrawn (``kinds``), by live entries."""
    if not credit_ids:
        return {}
    rows = db.execute(
        select(CreditNote.credit_id, CreditNote.transaction_id, func.coalesce(func.sum(CreditNote.amount), 0))
        .where(CreditNote.credit_id.in_(credit_ids), CreditNote.note_type.in_(kinds))
        .group_by(CreditNote.credit_id, CreditNote.transaction_id)
    ).all()
    gone = _reversed(db, [t for _c, t, _s in rows])
    out: dict[UUID, int] = {}
    for cid, txn, total_ in rows:
        if txn is not None and str(txn) in gone:
            continue
        out[cid] = out.get(cid, 0) + int(total_ or 0)
    return out


def remaining(db: Session, credits: list[CreditNote]) -> list[tuple[CreditNote, int]]:
    """Each live credit with what is left of it, the ones with nothing left dropped."""
    gone = _reversed(db, [c.transaction_id for c in credits])
    live = [c for c in credits if c.transaction_id is None or str(c.transaction_id) not in gone]
    spent = used(db, [c.id for c in live])
    out = []
    for c in live:
        left = int(c.amount or 0) - spent.get(c.id, 0)
        if left > 0:
            out.append((c, left))
    return out


def _credits(db: Session, query, lock: bool) -> list[CreditNote]:
    if lock:
        query = query.with_for_update()
    return list(db.execute(query.order_by(CreditNote.date, CreditNote.created_at)).scalars().all())


def for_invoice(db: Session, invoice_id: UUID, *, lock: bool = False) -> list[tuple[CreditNote, int]]:
    """The credit this invoice gave rise to (an overpayment on it, or a credit
    note on it beyond what it still owed), oldest first, with what is left."""
    q = select(CreditNote).where(CreditNote.invoice_id == invoice_id, CreditNote.note_type == "credit")
    return remaining(db, _credits(db, q, lock))


def for_party(db: Session, entity_id: UUID | None, kind: str, currency: str, *,
              lock: bool = False) -> list[tuple[CreditNote, int]]:
    """A party's available credit on one side (sales: what we owe the customer;
    purchase: what the supplier owes us) in one currency, oldest first."""
    if entity_id is None:
        return []
    q = select(CreditNote).where(
        CreditNote.entity_id == entity_id, CreditNote.note_type == "credit",
        CreditNote.kind == kind, func.upper(CreditNote.currency) == (currency or "").upper(),
    )
    return remaining(db, _credits(db, q, lock))


def consumed(db: Session, invoice_id: UUID) -> int:
    """How much of this invoice's own credit was refunded or used, by live entries."""
    rows = list(db.execute(select(CreditNote.id).where(
        CreditNote.invoice_id == invoice_id, CreditNote.note_type == "credit")).scalars())
    return sum(used(db, rows, kinds=MOVED).values())


def by_invoice(db: Session, invoice_ids) -> dict[UUID, int]:
    """for_invoice for many invoices at once: invoice id → its credit left."""
    ids = list(invoice_ids)
    rows: list[CreditNote] = []
    for i in range(0, len(ids), 5000):   # psycopg caps a statement at 65,535 parameters
        rows += db.execute(select(CreditNote).where(
            CreditNote.invoice_id.in_(ids[i:i + 5000]), CreditNote.note_type == "credit")).scalars()
    if not rows:
        return {}
    out: dict[UUID, int] = {}
    for c, left in remaining(db, rows):
        out[c.invoice_id] = out.get(c.invoice_id, 0) + left
    return out


def by_party(db: Session, entity_ids) -> dict[tuple, int]:
    """for_party for many parties at once: (entity id, kind, CURRENCY) → credit left."""
    ids = list(entity_ids)
    if not ids:
        return {}
    rows = list(db.execute(select(CreditNote).where(
        CreditNote.entity_id.in_(ids), CreditNote.note_type == "credit")).scalars())
    out: dict[tuple, int] = {}
    for c, left in remaining(db, rows):
        key = (c.entity_id, c.kind, (c.currency or "").upper())
        out[key] = out.get(key, 0) + left
    return out


def total(pairs: list[tuple[CreditNote, int]]) -> int:
    return sum(left for _c, left in pairs)
