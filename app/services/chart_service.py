"""Chart-of-accounts management (roadmap 2026-09 §4.5): add, rename,
deactivate and delete accounts, the tree with balances, and opening balances.

Codes are hierarchical in both charts — a child's code starts with its
parent's (Iran: گروه 11 → کل 1110 → معین 111001 → تفصیلی 11100101; UK: 0 →
0010) — and the level follows the parent: GROUP → GENERAL → SUB → DETAIL.

Guards: an account the automated postings rely on (the locale's posting
defaults: bank, receivables, VAT, payroll…) can't be deactivated or deleted;
deactivating needs a zero balance and no active children; deleting needs no
journal lines ever and no children.

Opening balances are ONE journal (reference ``OPENING-BALANCES``) on the
opening date; saving again replaces it, and the migration importer's opening
journal too. A difference between the sides goes to the opening-adjustments
account, never posted unbalanced.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.account import Account, AccountLevel

OPENING_REFERENCE = "OPENING-BALANCES"
ADJUSTMENT_CODE = "3999"
ADJUSTMENT_NAMES = {"ir": "تعدیلات افتتاحیه", "uk": "Opening balance adjustments", "default": "تعدیلات افتتاحیه"}
_CHILD_LEVEL = {AccountLevel.GROUP: AccountLevel.GENERAL, AccountLevel.GENERAL: AccountLevel.SUB,
                AccountLevel.SUB: AccountLevel.DETAIL}


def _locale(db: Session) -> str:
    from app.services.locale_service import get_reporting_locale
    return (get_reporting_locale(db) or "default").strip().lower()


def protected_codes(db: Session) -> set[str]:
    """The accounts the automated postings actually resolve to, plus the
    opening-adjustments account. Per posting category that is the locale's
    code if the chart has it, else the first fallback chart's code it has —
    the same order ``resolve_account_code`` uses — so a UK-locale company on
    the Iranian chart protects 1110, not a 1200 it doesn't have."""
    from app.services.account_resolver import _FALLBACK_ORDER, POSTING_CODES
    existing = set(db.execute(select(Account.code)).scalars())
    loc = _locale(db)
    table = POSTING_CODES.get(loc, POSTING_CODES["default"])
    out = {ADJUSTMENT_CODE}
    for category, preferred in table.items():
        candidates = [preferred] + [POSTING_CODES[fb][category] for fb in _FALLBACK_ORDER
                                    if category in POSTING_CODES[fb]]
        hit = next((c for c in candidates if c in existing), None)
        out.add(hit or preferred)
    return out


def _by_code(db: Session, code: str) -> Account | None:
    return db.execute(select(Account).where(Account.code == code)).scalars().one_or_none()


def _balances(db: Session) -> dict:
    """account id → (debit, credit) over live journals (undone ones are
    excluded by the soft-delete filter)."""
    from app.models.transaction import TransactionLine
    return {aid: (int(d or 0), int(c or 0)) for aid, d, c in db.execute(
        select(TransactionLine.account_id, func.sum(TransactionLine.debit), func.sum(TransactionLine.credit))
        .group_by(TransactionLine.account_id))}


def _ever_used(db: Session, account_id) -> bool:
    from app.models.transaction import TransactionLine, include_deleted_transactions
    with include_deleted_transactions():                  # an undone journal still references it
        return db.execute(select(TransactionLine.id).where(TransactionLine.account_id == account_id)
                          .limit(1)).first() is not None


def suggest_code(db: Session, parent: Account) -> str:
    """The next free child code: the parent's code plus two digits (GROUP → two
    more digits for a four-digit کل code as the seeded charts use)."""
    width = 2
    existing = {a.code for a in db.execute(select(Account).where(Account.parent_id == parent.id)).scalars()}
    siblings = [c for c in existing if c.startswith(parent.code) and c[len(parent.code):].isdigit()]
    if siblings:
        width = max(len(c) - len(parent.code) for c in siblings)
    start = max((int(c[len(parent.code):]) for c in siblings), default=0) + 1
    for n in range(start, 10 ** width):
        code = f"{parent.code}{n:0{width}d}"
        if _by_code(db, code) is None:
            return code
    raise HTTPException(status_code=409, detail=f"No free code left under {parent.code}.")


def create_account(db: Session, *, name: str, parent_code: str | None, code: str | None = None,
                   detail_type: str | None = None) -> Account:
    name = (name or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="An account needs a name.")
    parent = None
    if parent_code:
        parent = _by_code(db, parent_code.strip())
        if parent is None:
            raise HTTPException(status_code=404, detail=f"Parent account not found: {parent_code}")
        if parent.level == AccountLevel.DETAIL:
            raise HTTPException(status_code=422, detail="A detail (تفصیلی) account can't have children.")
        if not parent.is_active:
            raise HTTPException(status_code=422, detail="The parent account is inactive.")
    code = (code or "").strip() or (suggest_code(db, parent) if parent else None)
    if not code:
        raise HTTPException(status_code=422, detail="A top-level group needs a code.")
    if not code.isdigit() or len(code) > 16:
        raise HTTPException(status_code=422, detail="An account code is digits only, at most 16.")
    if parent and (not code.startswith(parent.code) or code == parent.code):
        raise HTTPException(status_code=422, detail=f"A child of {parent.code} needs a code that starts with it.")
    if _by_code(db, code) is not None:
        raise HTTPException(status_code=409, detail=f"Account {code} already exists.")
    level = _CHILD_LEVEL[parent.level] if parent else AccountLevel.GROUP
    acc = Account(code=code, name=name[:512], level=level, parent_id=parent.id if parent else None,
                  detail_type=(detail_type or None), is_active=True)
    db.add(acc)
    db.flush()
    return acc


def rename(db: Session, acc: Account, *, name: str | None = None, detail_type: str | None = None,
           clear_detail_type: bool = False) -> Account:
    if name is not None:
        if not name.strip():
            raise HTTPException(status_code=422, detail="An account needs a name.")
        acc.name = name.strip()[:512]
    if detail_type is not None or clear_detail_type:
        acc.detail_type = (detail_type or None)
    db.flush()
    return acc


def set_active(db: Session, acc: Account, active: bool) -> Account:
    if active:
        parent = db.get(Account, acc.parent_id) if acc.parent_id else None
        if parent is not None and not parent.is_active:
            raise HTTPException(status_code=422, detail="Reactivate the parent account first.")
        acc.is_active = True
        db.flush()
        return acc
    if acc.code in protected_codes(db):
        raise HTTPException(status_code=409, detail=f"{acc.code} is used by automatic postings and must stay active.")
    d, c = _balances(db).get(acc.id, (0, 0))
    if d != c:
        raise HTTPException(status_code=409, detail=f"{acc.code} still has a balance of {d - c:,} — clear it first.")
    active_children = db.execute(select(Account.code).where(Account.parent_id == acc.id, Account.is_active.is_(True))
                                 ).scalars().all()
    if active_children:
        raise HTTPException(status_code=409, detail="Deactivate its sub-accounts first: " + ", ".join(sorted(active_children)))
    acc.is_active = False
    db.flush()
    return acc


def delete_account(db: Session, acc: Account) -> None:
    if acc.code in protected_codes(db):
        raise HTTPException(status_code=409, detail=f"{acc.code} is used by automatic postings.")
    if db.execute(select(Account.id).where(Account.parent_id == acc.id).limit(1)).first():
        raise HTTPException(status_code=409, detail="It has sub-accounts — delete or move them first.")
    if _ever_used(db, acc.id):
        raise HTTPException(status_code=409, detail="It has entries in the books — deactivate it instead.")
    db.delete(acc)
    db.flush()


def tree(db: Session, *, include_inactive: bool = True) -> list[dict[str, Any]]:
    """Every account with its own and rolled-up balance (debit − credit), nested."""
    accounts = db.execute(select(Account).order_by(Account.code)).scalars().all()
    bal = _balances(db)
    protected = protected_codes(db)
    nodes = {a.id: {"id": str(a.id), "code": a.code, "name": a.name, "level": a.level.value,
                    "parent_id": str(a.parent_id) if a.parent_id else None, "detail_type": a.detail_type,
                    "is_active": a.is_active, "protected": a.code in protected,
                    "balance": bal.get(a.id, (0, 0))[0] - bal.get(a.id, (0, 0))[1], "total": 0, "children": []}
             for a in accounts if include_inactive or a.is_active}
    roots = []
    for a in accounts:
        node = nodes.get(a.id)
        if node is None:
            continue
        parent = nodes.get(a.parent_id) if a.parent_id else None
        (parent["children"] if parent else roots).append(node)

    def roll(n):
        n["total"] = n["balance"] + sum(roll(ch) for ch in n["children"])
        return n["total"]
    for r in roots:
        roll(r)
    return roots


# --- opening balances ------------------------------------------------------------------------------------

def _opening_journals(db: Session):
    from app.models.transaction import Transaction
    from app.services.migration_import import OPENING_REFERENCE as MIGRATION_REFERENCE
    return db.execute(select(Transaction).where(Transaction.reference.in_((OPENING_REFERENCE, MIGRATION_REFERENCE)))
                      .order_by(Transaction.created_at.desc())).scalars().all()


def get_opening(db: Session) -> dict[str, Any]:
    from app.models.transaction import TransactionLine
    journals = _opening_journals(db)
    if not journals:
        from app.services.migration_import import default_opening_date
        return {"date": default_opening_date(db).isoformat(), "lines": [], "transaction_id": None, "source": None}
    t = journals[0]
    rows = db.execute(select(Account.code, Account.name, TransactionLine.debit, TransactionLine.credit)
                      .join(Account, Account.id == TransactionLine.account_id)
                      .where(TransactionLine.transaction_id == t.id).order_by(Account.code)).all()
    return {"date": t.date.isoformat(), "transaction_id": str(t.id),
            "source": "migration" if t.reference != OPENING_REFERENCE else "manual",
            "lines": [{"account_code": c, "account_name": n, "debit": int(d or 0), "credit": int(cr or 0)}
                      for c, n, d, cr in rows]}


def set_opening(db: Session, *, on, lines: list[dict[str, Any]], currency: str | None = None) -> dict[str, Any]:
    """Replace the opening journal. Lines per account (debit or credit, not
    both); a difference goes to the opening-adjustments account."""
    from app.schemas.transaction import TransactionCreate, TransactionLineCreate
    from app.services.account_resolver import _ensure_account
    from app.services.fx_service import get_reporting_currency
    from app.services.ledger_posting import create_transaction_from_payload

    merged: dict[str, int] = {}
    for ln in lines:
        code = str(ln.get("account_code") or "").strip()
        acc = _by_code(db, code)
        if acc is None:
            raise HTTPException(status_code=422, detail=f"Account not found: {code}")
        if not acc.is_active:
            raise HTTPException(status_code=422, detail=f"Account {code} is inactive.")
        if acc.level == AccountLevel.GROUP:
            raise HTTPException(status_code=422, detail=f"{code} is a group — enter balances on its accounts.")
        d, c = int(ln.get("debit") or 0), int(ln.get("credit") or 0)
        if d < 0 or c < 0 or (d and c):
            raise HTTPException(status_code=422, detail=f"{code}: enter a debit or a credit, not both.")
        merged[code] = merged.get(code, 0) + d - c
    entries = [(code, net) for code, net in sorted(merged.items()) if net]
    diff = sum(net for _c, net in entries)
    from datetime import date as _date
    from app.services.period_service import assert_period_open
    if on > _date.today():
        raise HTTPException(status_code=422, detail="The opening date can't be in the future.")
    assert_period_open(db, on)
    for t in _opening_journals(db):                       # validated: now replace
        t.deleted_at = datetime.now(timezone.utc)
    db.flush()
    if not entries:
        return {"date": on.isoformat(), "transaction_id": None, "adjustment": 0, "lines": 0}
    if diff:
        loc = _locale(db)
        _ensure_account(db, ADJUSTMENT_CODE, ADJUSTMENT_NAMES.get(loc, ADJUSTMENT_NAMES["default"]), loc)
        entries.append((ADJUSTMENT_CODE, -diff))
    txn = create_transaction_from_payload(db, TransactionCreate(
        date=on, reference=OPENING_REFERENCE, description="Opening balances / تراز افتتاحیه",
        currency=(currency or get_reporting_currency(db) or "IRR"),
        lines=[TransactionLineCreate(account_code=code, debit=max(net, 0), credit=max(-net, 0),
                                     line_description="Opening balance") for code, net in entries]))
    return {"date": on.isoformat(), "transaction_id": str(txn.id), "adjustment": diff, "lines": len(entries)}
