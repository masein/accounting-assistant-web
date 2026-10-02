"""A party's available credit: what is left of each 'credit' row.

A 'credit' row (app/models/credit_note.py) is money the company owes a
customer, or a supplier owes the company: an overpayment, or the part of a
credit note beyond what an invoice still owed. Refunds and applications to
other invoices are rows of their own that name the credit they draw on
(``credit_id``); what is left of a credit is its amount less theirs.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.credit_note import CreditNote

CONSUMING = ("refund", "applied")


def used(db: Session, credit_ids: list[UUID]) -> dict[UUID, int]:
    """How much of each credit has been refunded or applied."""
    if not credit_ids:
        return {}
    rows = db.execute(
        select(CreditNote.credit_id, func.coalesce(func.sum(CreditNote.amount), 0))
        .where(CreditNote.credit_id.in_(credit_ids), CreditNote.note_type.in_(CONSUMING))
        .group_by(CreditNote.credit_id)
    ).all()
    return {cid: int(total or 0) for cid, total in rows}


def remaining(db: Session, credits: list[CreditNote]) -> list[tuple[CreditNote, int]]:
    """Each credit with what is left of it, the ones with nothing left dropped."""
    spent = used(db, [c.id for c in credits])
    out = []
    for c in credits:
        left = int(c.amount or 0) - spent.get(c.id, 0)
        if left > 0:
            out.append((c, left))
    return out


def for_invoice(db: Session, invoice_id: UUID) -> list[tuple[CreditNote, int]]:
    """The credit this invoice gave rise to (an overpayment on it, or a credit
    note on it beyond what it still owed), oldest first, with what is left."""
    rows = db.execute(
        select(CreditNote).where(CreditNote.invoice_id == invoice_id, CreditNote.note_type == "credit")
        .order_by(CreditNote.date, CreditNote.created_at)
    ).scalars().all()
    return remaining(db, list(rows))


def for_party(db: Session, entity_id: UUID | None, kind: str, currency: str) -> list[tuple[CreditNote, int]]:
    """A party's available credit on one side (sales: what we owe the customer;
    purchase: what the supplier owes us) in one currency, oldest first."""
    if entity_id is None:
        return []
    rows = db.execute(
        select(CreditNote).where(
            CreditNote.entity_id == entity_id, CreditNote.note_type == "credit",
            CreditNote.kind == kind, func.upper(CreditNote.currency) == (currency or "").upper(),
        ).order_by(CreditNote.date, CreditNote.created_at)
    ).scalars().all()
    return remaining(db, list(rows))


def total(pairs: list[tuple[CreditNote, int]]) -> int:
    return sum(left for _c, left in pairs)
