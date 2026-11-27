"""The VAT inside a reduction credit note, as its journal entry posted it.

A credit note's VAT is fixed once, when it posts (app/api/invoices
add_credit_note): the invoice's VAT share of everything credited so far, less
the VAT its earlier notes already reversed, so notes that credit an invoice in
full reverse exactly its VAT (issue #39). The VAT returns (Iran, UK) read that
same posted VAT back from each note's entry, so a return always matches the
ledger and a note's VAT never changes after it posts.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.money import plain
from app.models.account import Account
from app.models.credit_note import CreditNote
from app.models.transaction import TransactionLine


def posted_vats(db: Session, notes: list[CreditNote]) -> dict[Any, int]:
    """{note id: the VAT its journal entry moved} — the debit to output VAT
    of a sales note, the credit to input VAT of a purchase note. Notes posted
    with no entry are left out (the caller decides what they carry)."""
    txn_ids = {cn.transaction_id for cn in notes if cn.transaction_id}
    if not txn_ids:
        return {}
    from app.services.account_resolver import resolve_account_code
    codes = {resolve_account_code(db, "vat_output"), resolve_account_code(db, "vat_input")}
    rows = db.execute(
        select(TransactionLine.transaction_id, func.coalesce(func.sum(TransactionLine.debit), 0),
               func.coalesce(func.sum(TransactionLine.credit), 0))
        .join(Account, Account.id == TransactionLine.account_id)
        .where(TransactionLine.transaction_id.in_(txn_ids), Account.code.in_(codes))
        .group_by(TransactionLine.transaction_id)
    ).all()
    moved = {t: (d, c) for t, d, c in rows}
    out: dict[Any, int] = {}
    for cn in notes:
        if not cn.transaction_id:
            continue
        debit, credit = moved.get(cn.transaction_id, (0, 0))
        out[cn.id] = plain((debit - credit) if cn.kind == "sales" else (credit - debit))
    return out
