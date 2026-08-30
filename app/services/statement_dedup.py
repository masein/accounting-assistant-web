"""Row-level duplicate detection for bank-statement imports.

Upload already rejects a byte-identical file (``BankStatement.content_hash``),
but that only catches re-uploading the *same export*. The realistic accident is
an **overlapping date range**: import Aug 1-15, then later import Aug 1-31. The
file hash differs, so every row from the first fortnight imports a second time
and posts a second journal entry — silently doubling those figures.

Matching is count-based rather than set-based, which is the difference between
"already imported" and "looks like something I've seen". Two identical coffees
on the same day at the same price are two real transactions; if one was already
imported, exactly one of the incoming pair is a duplicate. Comparing counts per
fingerprint gets that right, where a plain "have I seen this?" test would
wrongly discard the second.

Fingerprints are computed on demand from the stored columns rather than kept in
a column, so rows imported before this existed are covered too.
"""
from __future__ import annotations

import hashlib
from collections import Counter
from datetime import date
from typing import Any, Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bank_statement import BankStatementRow
from app.services.statement_categorizer import normalize_narration


def row_fingerprint(
    *,
    tx_date: date,
    debit: int,
    credit: int,
    description: str | None,
    reference: str | None = None,
) -> str:
    """Identify one bank movement.

    A bank reference is the strongest key when the export carries one, so it
    wins outright. Without one we fall back to the normalized narration, which
    strips the card/terminal digits that differ between two exports of the very
    same transaction.
    """
    ref = (reference or "").strip()
    key = ref.lower() if ref else normalize_narration(description)
    base = f"{tx_date.isoformat()}|{int(debit)}|{int(credit)}|{key}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:32]


def _fingerprint_of(row: Any) -> str:
    return row_fingerprint(
        tx_date=row.tx_date,
        debit=getattr(row, "debit", 0) or 0,
        credit=getattr(row, "credit", 0) or 0,
        description=getattr(row, "description", None),
        reference=getattr(row, "reference", None),
    )


def already_imported_counts(
    db: Session,
    *,
    from_date: date,
    to_date: date,
    exclude_statement_id: UUID | None = None,
) -> Counter[str]:
    """Fingerprint → how many times it is already on file in this date range.

    Scoped to the statement's own span so the scan stays small; tenant scoping
    is automatic (BankStatementRow is tenant-mixed). Rows the user explicitly
    skipped still count as seen — they were shown and dismissed once already.
    """
    q = select(BankStatementRow).where(
        BankStatementRow.tx_date >= from_date,
        BankStatementRow.tx_date <= to_date,
    )
    if exclude_statement_id is not None:
        q = q.where(BankStatementRow.statement_id != exclude_statement_id)
    counts: Counter[str] = Counter()
    for row in db.execute(q).scalars().all():
        if row.recon_status == "duplicate":
            continue  # never let a flagged duplicate mask a genuine new row
        counts[_fingerprint_of(row)] += 1
    return counts


def duplicate_row_indices(
    db: Session,
    rows: Iterable[Any],
    *,
    exclude_statement_id: UUID | None = None,
) -> set[int]:
    """Positions in ``rows`` that were already imported previously.

    ``rows`` are freshly parsed rows (any object exposing tx_date/debit/credit/
    description/reference). Order is preserved, so when a fingerprint is on
    file once and arrives twice, the *first* occurrence is the duplicate and
    the second is kept as new.
    """
    rows = list(rows)
    dates = [r.tx_date for r in rows if getattr(r, "tx_date", None)]
    if not dates:
        return set()

    remaining = already_imported_counts(
        db,
        from_date=min(dates),
        to_date=max(dates),
        exclude_statement_id=exclude_statement_id,
    )
    duplicates: set[int] = set()
    for i, row in enumerate(rows):
        if not getattr(row, "tx_date", None):
            continue
        fp = _fingerprint_of(row)
        if remaining.get(fp, 0) > 0:
            remaining[fp] -= 1
            duplicates.add(i)
    return duplicates
