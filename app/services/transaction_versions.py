"""Each change to an entry kept as a version (chat review 2026-11-10, item 25, scenario CI4).

``transaction_versions`` — the history the audit page shows for an entry, version by version — was written only by
the manual API: the transactions page, its imports and the migration. An entry the assistant recorded, a statement
posted from a card, a date changed, an undo: none had a version, so the history of most entries in personal books
was empty. A confirmed card now leaves one version for every entry it created, changed or deleted, written the
moment it runs (``versions_of_changes``); an executor that already writes its own isn't written twice.
"""
from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from app.models.audit_log import TransactionVersion
from app.models.transaction import Transaction, TransactionLine


def snapshot(txn: Transaction) -> dict[str, Any]:
    """An entry as it stands: its date, reference, narration and lines."""
    return {
        "id": str(txn.id),
        "date": txn.date.isoformat() if txn.date else None,
        "reference": txn.reference,
        "description": txn.description,
        "lines": [
            {"account_code": getattr(ln.account, "code", ""), "debit": ln.debit, "credit": ln.credit,
             "desc": ln.line_description}
            for ln in (txn.lines or [])
        ],
    }


def record(db: Session, txn: Transaction, action: str, *, user_id: str | None = None,
           username: str | None = None) -> None:
    """The entry's next version: «create», «update» or «delete»."""
    count = db.execute(select(func.count(TransactionVersion.id))
                       .where(TransactionVersion.transaction_id == str(txn.id))).scalar() or 0
    db.add(TransactionVersion(transaction_id=str(txn.id), version=count + 1, action=action,
                              snapshot=json.dumps(snapshot(txn), default=str), user_id=user_id, username=username))


@contextmanager
def versions_of_changes(db: Session, *, user_id: str | None = None, username: str | None = None) -> Iterator[None]:
    """Every entry the block creates, changes or deletes gets a version when it ends without an error."""
    created: set[str] = set()
    changed: set[str] = set()
    versioned: set[str] = set()

    def seen(session, _ctx) -> None:
        for obj in session.new:
            if isinstance(obj, Transaction):
                created.add(str(obj.id))
            elif isinstance(obj, TransactionLine):
                changed.add(str(obj.transaction_id))
            elif isinstance(obj, TransactionVersion):
                versioned.add(str(obj.transaction_id))
        for obj in (*session.dirty, *session.deleted):
            if isinstance(obj, Transaction) and session.is_modified(obj, include_collections=False):
                changed.add(str(obj.id))
            elif isinstance(obj, Transaction) and obj in session.deleted:
                changed.add(str(obj.id))
            elif isinstance(obj, TransactionLine):
                changed.add(str(obj.transaction_id))

    event.listen(db, "after_flush", seen)
    try:
        yield
    finally:
        event.remove(db, "after_flush", seen)
    db.flush()
    for tid in sorted(created | changed):
        if tid in versioned or tid == "None":
            continue
        txn = db.execute(select(Transaction).where(Transaction.id == uuid.UUID(tid))
                         .execution_options(include_deleted=True)).scalar_one_or_none()
        if txn is None:                                       # deleted outright: what's left is its id
            n = db.execute(select(func.count(TransactionVersion.id))
                           .where(TransactionVersion.transaction_id == tid)).scalar() or 0
            db.add(TransactionVersion(transaction_id=tid, version=n + 1, action="delete",
                                      snapshot=json.dumps({"id": tid}), user_id=user_id, username=username))
            continue
        action = "create" if tid in created else ("delete" if txn.deleted_at is not None else "update")
        record(db, txn, action, user_id=user_id, username=username)
