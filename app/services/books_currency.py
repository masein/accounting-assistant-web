"""A person chooses the currency their own books count in (scenario Z3).

A company's base currency stays with the platform admin (the companies
console): its entries, rates and statements depend on it. A person's books are
theirs, and they started in Iranian rials whoever opened them. They change it
only before the books hold entries in another currency: those would count as
nothing until a rate for them is added, and three months of rials read 0.000
OMR (the platform admin's console still rebases books that have entries)."""
from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.currencies import NAMES, name, options


def _company(db: Session, company_id):
    from app.db.tenant import tenant_bypass
    from app.models.company import Company
    with tenant_bypass():
        return db.get(Company, uuid.UUID(str(company_id))) if company_id else None


def _entries_in_other_currencies(db: Session, company_id, new: str | None = None) -> int:
    """Live entries not in ``new``; with no ``new``, every live entry."""
    from sqlalchemy import func, select

    from app.db.tenant import use_company
    from app.models.transaction import Transaction
    q = select(func.count()).select_from(Transaction).where(Transaction.deleted_at.is_(None))
    if new is not None:
        q = q.where(func.upper(func.coalesce(Transaction.currency, "")) != new)
    with use_company(str(company_id)):
        return int(db.execute(q).scalar() or 0)


def current(db: Session, company_id, lang: str) -> dict:
    c = _company(db, company_id)
    if c is None or (c.kind or "") != "personal":
        raise HTTPException(status_code=422, detail="Only a person's own books choose their currency here.")
    code = (c.base_currency or "IRR").upper()
    # whether it can still change: not once entries are made in it
    return {"currency": code, "name": name(code, lang), "options": options(lang),
            "locked": bool(_entries_in_other_currencies(db, c.id))}


def change(db: Session, company_id, code: str, *, user) -> dict:
    """Count these personal books in ``code``. Flushes and commits."""
    from app.core.audit import audit_log
    from app.db.tenant import use_company
    from app.services.fx_base import recompute_all
    c = _company(db, company_id)
    if c is None or (c.kind or "") != "personal":
        raise HTTPException(status_code=422, detail="Only a person's own books choose their currency here.")
    new = (code or "").strip().upper()
    if new not in NAMES:
        raise HTTPException(status_code=422, detail=f"Choose one of the listed currencies: {', '.join(NAMES)}.")
    old = (c.base_currency or "IRR").upper()
    if new == old:
        return {"currency": new, "changed": False}
    # entries in another currency would count as nothing until a rate is added: a person's
    # books choose their currency before the first entry (the screenshots of 2026-10-15
    # showed three months of rials at 0.000 OMR)
    if _entries_in_other_currencies(db, c.id, new):
        raise HTTPException(status_code=422, detail=(
            f"These books already have entries in {name(old, 'en')}, so they keep counting in it. "
            "A person's books choose their currency before the first entry."))
    c.base_currency = new
    audit_log(db, action="update", entity_type="company", entity_id=str(c.id), user_id=user.user_id,
              username=user.username, detail=f"the books' currency {old} → {new}")
    db.commit()
    with use_company(str(c.id)):
        recompute_all(db)               # entries already made: valued in the new currency where a rate is known
        db.commit()
    return {"currency": new, "changed": True}
