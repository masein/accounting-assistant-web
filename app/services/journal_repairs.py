"""What a misread journal import left in the books, found, and put right on the user's word (scenarios AH5, AH6).

The chat read منادی حق's 1404 journal with an old parser: the date column as
the voucher number, a ledger code (1100, 1300) as the day. «اعمال شد — 6 سند»
posted six vouchers on 2027-01-30, each with only the lines whose account had
matched — one-sided, a year ahead (2026-10-19). Such a voucher's reference is
its "number", a Jalali date (V1404/01/29), and its own date lies outside that
year: nothing else looks like that.

The journal import also made one of the school's ledgers into two headings,
one per group its sub-ledgers went to: «مخارج مدرسه» 6211 under 62 (its bank
fees) and 6132 under 61 (the rest). Two active headings of one name, neither
posted to, every account under both made or matched by a journal import.

Nothing here acts by itself: ``find`` lists what it sees; ``apply`` puts right
only what the user confirmed of that list — a voucher is put away as an undo
would (``deleted_at``), a heading's accounts move under its twin, which keeps
both codes, and the empty one is put away (inactive). No amount changes. Each
step has its audit row.
"""
from __future__ import annotations

import json
import re
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.money import plain
from app.models.account import Account, AccountLevel
from app.models.audit_log import AuditLog
from app.models.transaction import Transaction, TransactionLine

_MISREAD = re.compile(r"^V(1[34]\d\d)/(\d\d)/(\d\d)$")


def _misread_vouchers(db: Session) -> list[Transaction]:
    found = []
    for txn in db.execute(select(Transaction).where(Transaction.deleted_at.is_(None),
                                                    Transaction.reference.like("V1___/__/__"))).scalars():
        m = _MISREAD.match(txn.reference or "")
        if not m or txn.date is None:
            continue
        year = int(m.group(1))
        if txn.date.year in (year + 621, year + 622):
            continue                              # on a day of the year its number names: not the misread
        found.append(txn)
    return sorted(found, key=lambda t: (t.date, t.reference or ""))


def _split_headings(db: Session) -> list[tuple[Account, list[Account], dict]]:
    """(the heading kept, its twins, accounts under each) for one ledger imported as two headings."""
    from app.services import journal_import as ji
    imported = {str(v) for v in ji.saved_map(db).values() if v}
    if not imported:
        return []
    accounts = db.execute(select(Account)).scalars().all()
    kids: dict = defaultdict(list)
    for acc in accounts:
        if acc.parent_id is not None:
            kids[acc.parent_id].append(acc)
    posted = set(db.execute(select(TransactionLine.account_id).join(Transaction, Transaction.id == TransactionLine.transaction_id)
                            .where(Transaction.deleted_at.is_(None)).distinct()).scalars())
    by_name: dict[str, list[Account]] = defaultdict(list)
    for acc in accounts:
        under = kids.get(acc.id) or []
        if (acc.is_active and acc.level != AccountLevel.GROUP and under and acc.id not in posted
                and all(k.code in imported for k in under)):
            by_name[ji._norm(acc.name)].append(acc)
    out = []
    for same in by_name.values():
        if len(same) < 2:
            continue
        same.sort(key=lambda a: (-len(kids[a.id]), a.code))
        out.append((same[0], same[1:], kids))
    return out


def find(db: Session) -> dict[str, Any]:
    vouchers = []
    for txn in _misread_vouchers(db):
        lines = db.execute(select(TransactionLine).where(TransactionLine.transaction_id == txn.id)).scalars().all()
        vouchers.append({"id": str(txn.id), "reference": txn.reference, "date": txn.date.isoformat(),
                         "debit": plain(sum((ln.debit for ln in lines), 0)),
                         "credit": plain(sum((ln.credit for ln in lines), 0)), "lines": len(lines),
                         "currency": txn.currency})
    headings = [{"name": keep.name, "keep": keep.code,
                 "merge": [{"code": o.code, "accounts": [k.code for k in kids[o.id]]} for o in twins]}
                for keep, twins, kids in _split_headings(db)]
    return {"vouchers": vouchers, "headings": headings}


def apply(db: Session, *, vouchers: list[str], headings: list[str], actor_user_id: str | None,
          actor_username: str | None) -> dict[str, int]:
    """Put right what the user confirmed — and only what ``find`` still sees."""
    from app.services.statement_import import release_statement_rows
    wanted = {str(v) for v in vouchers}
    removed = 0
    now = datetime.now(timezone.utc)
    for txn in _misread_vouchers(db):
        if str(txn.id) not in wanted:
            continue
        txn.deleted_at = now
        release_statement_rows(db, txn.id)
        db.add(AuditLog(action="delete", entity_type="transaction", entity_id=str(txn.id), user_id=actor_user_id,
                        username=actor_username, detail=json.dumps(
                            {"reason": "misread journal import", "reference": txn.reference,
                             "date": txn.date.isoformat()}, ensure_ascii=False)))
        removed += 1
    merge = {str(h) for h in headings}
    merged = 0
    for keep, twins, kids in _split_headings(db):
        codes = [c for c in (keep.source_codes or "").split(",") if c]
        for other in twins:
            if other.code not in merge:
                continue
            moved = [k.code for k in kids[other.id]]
            for child in kids[other.id]:
                child.parent_id = keep.id
            other.is_active = False
            codes += [c for c in (other.source_codes or "").split(",") if c and c not in codes]
            db.add(AuditLog(action="merge", entity_type="account", entity_id=str(other.id), user_id=actor_user_id,
                            username=actor_username, detail=json.dumps(
                                {"reason": "one ledger imported as two headings", "from": other.code,
                                 "into": keep.code, "name": keep.name, "moved": moved}, ensure_ascii=False)))
            merged += 1
        if codes:
            keep.source_codes = ",".join(codes)[:255]
    db.flush()
    if removed or merged:
        from app.api.reports import invalidate_dashboard_cache
        invalidate_dashboard_cache()
    return {"removed": removed, "merged": merged}


def _ids(values) -> list[str]:
    out = []
    for v in values or []:
        try:
            out.append(str(uuid.UUID(str(v))))
        except ValueError:
            continue
    return out
