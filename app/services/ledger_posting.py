"""Posting a journal entry: the one place a transaction is built and validated.

This lived inside ``app/api/transactions.py`` as a private helper, which meant
seven service modules imported it *from the API layer* — a dependency pointing
the wrong way, and the reason a cross-module import of a validator that never
existed went unnoticed until it 500'd at runtime.

Everything that writes a transaction goes through ``create_transaction_from_payload``:
the AI accountant's execute step, recurring rules, petty cash, equity events,
migration import, bank-statement rows and commitments. It is the single place
enforcing the invariants — balanced legs, non-zero, not future-dated, not in a
closed period — so those guarantees cannot be bypassed by a new caller.
"""
from __future__ import annotations

import re
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.account import Account
from app.models.entity import Entity, TransactionEntity
from app.models.transaction import Transaction, TransactionAttachment, TransactionLine
from app.schemas.transaction import TransactionCreate


def validate_balanced_lines(lines) -> None:
    """Reject an unbalanced or empty set of legs.

    Callers that build lines by hand (journal editing) need this without going
    through the whole create path. Note it was previously imported by
    manager_reports from a module that never defined it, so editing a journal
    entry's lines raised ImportError instead of validating anything.
    """
    total_debit = sum(l.debit for l in lines)
    total_credit = sum(l.credit for l in lines)
    if total_debit != total_credit:
        raise HTTPException(
            status_code=400,
            detail=f"Debits ({total_debit}) must equal credits ({total_credit})",
        )
    if total_debit == 0 and total_credit == 0:
        raise HTTPException(status_code=400, detail="Transaction must have non-zero amounts")


def load_attachments(db: Session, attachment_ids: list[UUID]) -> list[TransactionAttachment]:
    if not attachment_ids:
        return []
    found = db.execute(
        select(TransactionAttachment).where(TransactionAttachment.id.in_(attachment_ids))
    ).scalars().all()
    by_id = {a.id: a for a in found}
    missing = [str(i) for i in attachment_ids if i not in by_id]
    if missing:
        raise HTTPException(status_code=400, detail=f"Attachment not found: {', '.join(missing)}")
    return [by_id[i] for i in attachment_ids if i in by_id]


def get_account_by_code(db: Session, code: str) -> Account:
    code = code.strip()
    acc = db.execute(select(Account).where(Account.code == code)).scalars().one_or_none()
    if not acc:
        raise HTTPException(status_code=400, detail=f"Account not found: {code}")
    return acc


def get_or_create_entity(db: Session, role: str, name: str) -> Entity:
    """Find entity by type and name (case-insensitive), or create it."""
    name = re.sub(r"\s+", " ", (name or "").strip())
    # Guardrail: reject malformed phrase-like names from chat extraction.
    lower_name = name.lower()
    if (
        len(name) < 2
        or len(name) > 80
        or len(name.split()) > 5
        or re.search(r"\b(via|bank|account|about|project|payment|transaction)\b", lower_name)
        or lower_name in {"us", "our", "me", "we", "you", "your"}
    ):
        raise HTTPException(status_code=400, detail=f"Invalid entity name: {name}")
    if not name:
        raise HTTPException(status_code=400, detail="Entity name is empty")
    role = role.strip().lower()
    entity_type = role if role in ("client", "bank", "employee", "supplier") else "employee"
    existing = (
        db.execute(
            select(Entity).where(
                Entity.type == entity_type,
                Entity.name.ilike(name),
            )
        )
        .scalars().first()
    )
    if existing:
        return existing
    entity = Entity(type=entity_type, name=name)
    db.add(entity)
    db.flush()
    return entity


def upsert_role_link(db: Session, t: Transaction, role: str, entity: Entity) -> None:
    role_key = (role or "").strip().lower()
    existing = next((ln for ln in (t.entity_links or []) if (ln.role or "").strip().lower() == role_key), None)
    if existing:
        existing.entity_id = entity.id
    else:
        db.add(TransactionEntity(transaction_id=t.id, entity_id=entity.id, role=role_key))


def create_transaction_from_payload(db: Session, payload: TransactionCreate) -> Transaction:
    lines_data = payload.lines
    total_debit = sum(l.debit for l in lines_data)
    total_credit = sum(l.credit for l in lines_data)
    if total_debit != total_credit:
        raise HTTPException(
            status_code=400,
            detail=f"Debits ({total_debit}) must equal credits ({total_credit})",
        )
    if total_debit == 0 and total_credit == 0:
        raise HTTPException(
            status_code=400,
            detail="Transaction must have non-zero amounts",
        )
    # Warn on future-dated transactions (more than 1 day ahead)
    from datetime import date as _date_type, timedelta
    if payload.date > _date_type.today() + timedelta(days=1):
        raise HTTPException(
            status_code=400,
            detail=f"Transaction date {payload.date} is in the future. Use today's date or a past date.",
        )
    # Block posting / back-dating into a closed (locked) period.
    from app.services.period_service import assert_period_open
    assert_period_open(db, payload.date)
    transaction = Transaction(
        date=payload.date,
        reference=payload.reference,
        description=payload.description,
        currency=(getattr(payload, "currency", None) or "IRR"),
    )
    db.add(transaction)
    db.flush()
    for line in lines_data:
        acc = get_account_by_code(db, line.account_code)
        db.add(
            TransactionLine(
                transaction_id=transaction.id,
                account_id=acc.id,
                debit=line.debit,
                credit=line.credit,
                line_description=line.line_description,
            )
        )
    for link in getattr(payload, "entity_links", []) or []:
        role = link.role.strip().lower()
        if link.entity_id:
            entity = db.get(Entity, link.entity_id)
            if not entity:
                raise HTTPException(status_code=400, detail=f"Entity not found: {link.entity_id}")
        else:
            entity = get_or_create_entity(db, role, link.name or "")
        db.add(
            TransactionEntity(
                transaction_id=transaction.id,
                entity_id=entity.id,
                role=role,
            )
        )
    attachments = load_attachments(db, getattr(payload, "attachment_ids", []) or [])
    for a in attachments:
        if a.transaction_id and a.transaction_id != transaction.id:
            raise HTTPException(status_code=400, detail=f"Attachment already linked: {a.id}")
        a.transaction_id = transaction.id
    return transaction
