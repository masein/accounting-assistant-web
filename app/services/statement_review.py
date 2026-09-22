"""Statement-vs-books review: turn a reconciled bank statement into a list
of concrete contradictions, each with the fix we suggest.

This is the engine behind "upload your statement once in a while and let the
system check it against the books". The reconcile pass says *how many* rows
matched; the review says *what is wrong and what to do*, in a shape both the
Bank Statements page and the AI accountant can act on:

* ``unrecorded``         bank shows money moving, the books don't → post it
                         (suggested account from the categoriser)
* ``needs_confirmation`` same amount as a book entry but the date or the
                         narration differs → almost surely the same
                         transaction, approve the match
* ``amount_mismatch``    a close book entry with a different amount → one of
                         the two is wrong; fix the entry or post the gap
* ``missing_in_bank``    the books say the bank account moved, the statement
                         never shows it → maybe it never happened, maybe it
                         went through another account
* ``duplicate``          row already imported from an earlier statement
* ``balance_gap``        statement closing balance ≠ book balance of the bank
                         account, with whether the unrecorded rows explain it

Findings are ordered by what the user should look at first: mismatches and
missing entries (the books are wrong) before unrecorded rows (the books are
incomplete) before informational duplicates.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.account import Account
from app.models.bank_statement import BankStatement, BankStatementRow
from app.models.entity import Entity
from app.models.transaction import Transaction, TransactionLine
from app.schemas.brain import (
    StatementBalanceCheck,
    StatementFinding,
    StatementReviewResponse,
)

_ORDER = {"balance_gap": 0, "amount_mismatch": 1, "missing_in_bank": 2,
          "needs_confirmation": 3, "unrecorded": 4, "duplicate": 5}


def bank_account_for_statement(db: Session, stmt: BankStatement) -> str | None:
    """The GL account this statement's bank posts to: a bank entity named
    like the statement with its own account wins, else the chart's bank."""
    from app.services.account_resolver import resolve_account_code

    name = (stmt.bank_name or "").strip()
    if name and name.lower() != "unknown":
        ent = db.execute(
            select(Entity).where(Entity.type == "bank", func.lower(Entity.name) == name.lower())
        ).scalars().first()
        if ent is not None and (ent.code or "").strip():
            code = ent.code.strip()
            if db.execute(select(Account.id).where(Account.code == code)).first():
                return code
    try:
        return resolve_account_code(db, "bank")
    except Exception:
        return None


def book_balance_as_of(db: Session, account_code: str, as_of: date) -> int:
    acc = db.execute(select(Account).where(Account.code == account_code)).scalars().first()
    if acc is None:
        return 0
    total = db.execute(
        select(func.coalesce(func.sum(TransactionLine.debit - TransactionLine.credit), 0))
        .join(Transaction, Transaction.id == TransactionLine.transaction_id)
        .where(
            TransactionLine.account_id == acc.id,
            Transaction.date <= as_of,
            Transaction.deleted_at.is_(None),
        )
    ).scalar()
    return int(total or 0)


def _account_name(db: Session, code: str | None) -> str | None:
    if not code:
        return None
    acc = db.execute(select(Account).where(Account.code == code)).scalars().first()
    return acc.name if acc else None


def _txn_amount(txn: Transaction) -> int:
    return sum(int(l.debit or 0) for l in txn.lines)


def build_statement_review(db: Session, stmt: BankStatement) -> StatementReviewResponse:
    from app.services.reconciliation import detect_missing_entries
    from app.services.statement_import import reconcile_statement_rows

    recon = reconcile_statement_rows(db, stmt)

    rows = db.execute(
        select(BankStatementRow).where(BankStatementRow.statement_id == stmt.id)
        .order_by(BankStatementRow.row_index)
    ).scalars().all()

    bank_code = bank_account_for_statement(db, stmt)
    findings: list[StatementFinding] = []
    unrecorded_net = 0
    names: dict[str, str | None] = {}

    def name_of(code: str | None) -> str | None:
        if code not in names:
            names[code] = _account_name(db, code)
        return names[code]

    for row in rows:
        amount = row.debit if row.debit > 0 else row.credit
        direction = "out" if row.debit > 0 else "in"
        base = dict(
            row_id=row.id, row_index=row.row_index, tx_date=row.tx_date,
            description=row.description, amount=amount, direction=direction,
        )
        if row.recon_status == "duplicate":
            findings.append(StatementFinding(
                id=f"row:{row.id}", kind="duplicate", severity="info", suggested_fix="none",
                detail="Already imported from an earlier statement; not posted again.", **base,
            ))
            continue
        if row.created_transaction_id is not None or row.recon_status in ("matched", "skipped"):
            continue
        if row.recon_status == "partial" and row.matched_transaction_id is not None:
            txn = db.get(Transaction, row.matched_transaction_id)
            if txn is not None and txn.deleted_at is None:
                book_amount = _txn_amount(txn)
                if book_amount == amount:
                    findings.append(StatementFinding(
                        id=f"row:{row.id}", kind="needs_confirmation", severity="info",
                        matched_transaction_id=txn.id, matched_amount=book_amount,
                        matched_date=txn.date, matched_description=txn.description,
                        suggested_fix="approve_match",
                        detail=(f"Same amount as book entry dated {txn.date.isoformat()} "
                                f"({txn.description or txn.reference or txn.id}); date or narration differs."),
                        **base,
                    ))
                else:
                    findings.append(StatementFinding(
                        id=f"row:{row.id}", kind="amount_mismatch", severity="high",
                        matched_transaction_id=txn.id, matched_amount=book_amount,
                        matched_date=txn.date, matched_description=txn.description,
                        suggested_account_code=row.suggested_account_code,
                        suggested_account_name=name_of(row.suggested_account_code),
                        suggested_fix="review_entry",
                        detail=(f"Bank shows {amount:,}, the closest book entry "
                                f"({txn.date.isoformat()}, {txn.description or txn.reference or txn.id}) "
                                f"shows {book_amount:,}."),
                        **base,
                    ))
                continue
        # unmatched (or partial without a usable candidate) → not in the books
        unrecorded_net += row.credit - row.debit
        findings.append(StatementFinding(
            id=f"row:{row.id}", kind="unrecorded", severity="warning",
            suggested_account_code=row.suggested_account_code,
            suggested_account_name=name_of(row.suggested_account_code),
            category=row.category, suggested_fix="post_row",
            detail=("Money " + ("left" if direction == "out" else "entered")
                    + " the bank account with no matching entry in the books."),
            **base,
        ))

    # Book entries on the bank account the statement never shows.
    if rows:
        known = {r.matched_transaction_id for r in rows if r.matched_transaction_id}
        known |= {r.created_transaction_id for r in rows if r.created_transaction_id}
        missing = detect_missing_entries(
            db, stmt.from_date or rows[0].tx_date, stmt.to_date or rows[-1].tx_date, known
        )
        for txn in missing:
            findings.append(StatementFinding(
                id=f"txn:{txn.id}", kind="missing_in_bank", severity="warning",
                tx_date=txn.date, description=txn.description or txn.reference,
                amount=_txn_amount(txn), transaction_id=txn.id, suggested_fix="review_entry",
                detail=("Recorded in the books on the bank account but absent from this "
                        "statement — confirm it happened (or went through another account)."),
            ))

    # Closing balance check.
    balance: StatementBalanceCheck | None = None
    closing_rows = [r for r in rows if r.balance is not None]
    if closing_rows and bank_code:
        last = max(closing_rows, key=lambda r: (r.tx_date, r.row_index))
        as_of = stmt.to_date or last.tx_date
        book = book_balance_as_of(db, bank_code, as_of)
        gap = int(last.balance) - book
        balance = StatementBalanceCheck(
            statement_closing=int(last.balance), book_balance=book, gap=gap,
            bank_account_code=bank_code, unrecorded_net=unrecorded_net,
            explained=(gap == unrecorded_net),
        )
        if gap != 0:
            findings.append(StatementFinding(
                id="balance", kind="balance_gap", severity="high" if not balance.explained else "info",
                tx_date=as_of, amount=abs(gap), direction="in" if gap > 0 else "out",
                suggested_account_code=bank_code, suggested_account_name=name_of(bank_code),
                suggested_fix="post_row" if balance.explained else "review_entry",
                detail=(f"Statement closes at {last.balance:,}; the books show {book:,} on "
                        f"{bank_code}. " + ("Posting the unrecorded rows closes the gap."
                                           if balance.explained else
                                           "The unrecorded rows do not explain the whole gap.")),
            ))

    findings.sort(key=lambda f: (_ORDER.get(f.kind, 9), f.tx_date or date.min, f.row_index or 0))
    counts = {
        "matched": recon.matched, "partial": recon.partial, "unmatched": recon.unmatched,
        "duplicates": recon.duplicates, "missing_in_bank": recon.missing_in_bank,
        "unrecorded": sum(1 for f in findings if f.kind == "unrecorded"),
        "needs_confirmation": sum(1 for f in findings if f.kind == "needs_confirmation"),
        "amount_mismatch": sum(1 for f in findings if f.kind == "amount_mismatch"),
        "actionable": sum(1 for f in findings if f.kind not in ("duplicate",)),
    }
    return StatementReviewResponse(
        statement_id=stmt.id, bank_name=stmt.bank_name, currency=stmt.currency,
        from_date=stmt.from_date, to_date=stmt.to_date, total_rows=len(rows),
        counts=counts, bank_account_code=bank_code, balance=balance, findings=findings,
        clean=(counts["actionable"] == 0),
    )
