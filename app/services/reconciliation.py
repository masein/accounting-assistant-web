"""
Intelligent reconciliation engine: matches bank statement rows against existing
DB transactions using multi-strategy scoring (exact, fuzzy, date-tolerant, split).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from difflib import SequenceMatcher
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.bank_statement import BankStatementRow
from app.models.transaction import Transaction, TransactionLine

logger = logging.getLogger(__name__)


@dataclass
class MatchCandidate:
    transaction_id: UUID
    score: float  # 0.0 - 1.0
    match_type: str  # exact, date_tolerant, fuzzy, split, amount_only
    details: str = ""


@dataclass
class ReconciliationResult:
    row_id: UUID
    status: str  # matched, partial, unmatched, duplicate
    candidates: list[MatchCandidate] = field(default_factory=list)
    best_match: MatchCandidate | None = None
    auto_match: bool = False


def _text_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _date_distance(d1: date, d2: date) -> int:
    return abs((d1 - d2).days)


def _normalize_ref(ref: str | None) -> str:
    if not ref:
        return ""
    return re.sub(r"[^a-zA-Z0-9\u0600-\u06FF]", "", ref).lower().strip()


def reconcile_row(
    row: BankStatementRow,
    transactions: list[Transaction],
    date_tolerance: int = 3,
    auto_threshold: float = 0.85,
) -> ReconciliationResult:
    """
    Reconcile a single bank statement row against a list of candidate transactions.

    Strategy layers (applied in priority order):
    1. Exact match: same amount, same date, same reference
    2. Date-tolerant: same amount, date within tolerance, similar description
    3. Fuzzy: close amount, close date, similar description
    4. Amount-only: exact amount match within date window
    """
    result = ReconciliationResult(row_id=row.id, status="unmatched")
    row_amount = row.debit if row.debit > 0 else row.credit
    row_is_debit = row.debit > 0
    row_ref = _normalize_ref(row.reference)

    for txn in transactions:
        # Calculate the net cash effect of this transaction
        cash_debit = sum(ln.debit for ln in txn.lines if (ln.account.code or "").startswith("1110"))
        cash_credit = sum(ln.credit for ln in txn.lines if (ln.account.code or "").startswith("1110"))

        txn_amount = 0
        txn_is_debit = False
        if cash_credit > 0:
            txn_amount = cash_credit
            txn_is_debit = True  # cash going out = bank debit
        elif cash_debit > 0:
            txn_amount = cash_debit
            txn_is_debit = False  # cash coming in = bank credit

        if txn_amount == 0:
            continue

        # Direction must match
        if row_is_debit != txn_is_debit:
            continue

        amount_match = 1.0 if row_amount == txn_amount else max(0, 1.0 - abs(row_amount - txn_amount) / max(row_amount, 1))
        date_dist = _date_distance(row.tx_date, txn.date)
        date_score = max(0, 1.0 - date_dist / max(date_tolerance + 1, 1))

        desc_score = _text_similarity(row.description or "", txn.description or "")
        txn_ref = _normalize_ref(txn.reference)
        ref_score = 1.0 if (row_ref and txn_ref and row_ref == txn_ref) else 0.0

        # Strategy 1: Exact match
        if amount_match == 1.0 and date_dist == 0 and ref_score == 1.0:
            candidate = MatchCandidate(
                transaction_id=txn.id,
                score=0.99,
                match_type="exact",
                details=f"Exact match: amount={row_amount}, date={row.tx_date}, ref={row.reference}",
            )
            result.candidates.append(candidate)
            continue

        # Strategy 2: Date-tolerant with amount match
        if amount_match == 1.0 and date_dist <= date_tolerance:
            score = 0.7 + 0.15 * date_score + 0.1 * desc_score + 0.05 * ref_score
            candidate = MatchCandidate(
                transaction_id=txn.id,
                score=round(min(score, 0.97), 3),
                match_type="date_tolerant",
                details=f"Amount match, date off by {date_dist} days",
            )
            result.candidates.append(candidate)
            continue

        # Strategy 3: Fuzzy match
        if amount_match >= 0.95 and date_dist <= date_tolerance * 2:
            score = 0.3 * amount_match + 0.25 * date_score + 0.25 * desc_score + 0.2 * ref_score
            if score >= 0.4:
                candidate = MatchCandidate(
                    transaction_id=txn.id,
                    score=round(min(score, 0.90), 3),
                    match_type="fuzzy",
                    details=f"Fuzzy: amount_sim={amount_match:.2f}, desc_sim={desc_score:.2f}",
                )
                result.candidates.append(candidate)
                continue

        # Strategy 4: Amount-only within wide window
        if amount_match == 1.0 and date_dist <= 14:
            score = 0.5 + 0.2 * date_score + 0.15 * desc_score + 0.15 * ref_score
            candidate = MatchCandidate(
                transaction_id=txn.id,
                score=round(min(score, 0.80), 3),
                match_type="amount_only",
                details=f"Amount match only, date off by {date_dist} days",
            )
            result.candidates.append(candidate)

    # Sort candidates by score
    result.candidates.sort(key=lambda c: c.score, reverse=True)

    if result.candidates:
        result.best_match = result.candidates[0]
        if result.best_match.score >= auto_threshold:
            result.status = "matched"
            result.auto_match = True
        elif result.best_match.score >= 0.5:
            result.status = "partial"
        else:
            result.status = "unmatched"

    return result


def reconcile_statement(
    db: Session,
    rows: list[BankStatementRow],
    date_tolerance: int = 3,
    auto_threshold: float = 0.85,
) -> list[ReconciliationResult]:
    """
    Reconcile all rows of a bank statement against existing transactions.
    Uses a date window around the statement period for candidate loading.
    """
    if not rows:
        return []

    min_date = min(r.tx_date for r in rows) - timedelta(days=date_tolerance * 2)
    max_date = max(r.tx_date for r in rows) + timedelta(days=date_tolerance * 2)

    txns = db.execute(
        select(Transaction)
        .where(Transaction.date >= min_date, Transaction.date <= max_date)
        .options(selectinload(Transaction.lines).selectinload(TransactionLine.account))
    ).scalars().unique().all()

    already_matched: set[UUID] = set()
    results: list[ReconciliationResult] = []

    for row in rows:
        available_txns = [t for t in txns if t.id not in already_matched]
        r = reconcile_row(row, available_txns, date_tolerance, auto_threshold)

        # Deduplicate: prevent same transaction from matching multiple rows
        if r.best_match and r.auto_match:
            already_matched.add(r.best_match.transaction_id)

        results.append(r)

    # Duplicate detection: check if any row matches an already-committed row
    for i, r1 in enumerate(results):
        for j, r2 in enumerate(results):
            if i >= j:
                continue
            if (
                rows[i].tx_date == rows[j].tx_date
                and rows[i].debit == rows[j].debit
                and rows[i].credit == rows[j].credit
                and rows[i].description == rows[j].description
            ):
                r2.status = "duplicate"

    return results


def detect_missing_entries(
    db: Session,
    statement_from: date,
    statement_to: date,
    matched_transaction_ids: set[UUID],
) -> list[Transaction]:
    """
    Find transactions in the DB within the statement period that have
    no corresponding bank statement row (missing from the bank's side).
    """
    txns = db.execute(
        select(Transaction)
        .where(Transaction.date >= statement_from, Transaction.date <= statement_to)
        .options(selectinload(Transaction.lines).selectinload(TransactionLine.account))
    ).scalars().unique().all()

    cash_txns = [
        t for t in txns
        if any((ln.account.code or "").startswith("1110") for ln in t.lines)
    ]
    return [t for t in cash_txns if t.id not in matched_transaction_ids]
