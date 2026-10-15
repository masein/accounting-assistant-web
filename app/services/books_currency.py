"""A person chooses the currency their own books count in (scenario Z3).

A company's base currency stays with the platform admin (the companies
console): its entries, rates and statements depend on it. A person's books are
theirs, and they started in Iranian rials whoever opened them. Changing it
recomputes every entry's base value, as the console does (fx_base.recompute_all):
entries already made stay in their own currency, valued at the rate of their
day where one is known."""
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


def current(db: Session, company_id, lang: str) -> dict:
    c = _company(db, company_id)
    if c is None or (c.kind or "") != "personal":
        raise HTTPException(status_code=422, detail="Only a person's own books choose their currency here.")
    code = (c.base_currency or "IRR").upper()
    return {"currency": code, "name": name(code, lang), "options": options(lang)}


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
    c.base_currency = new
    audit_log(db, action="update", entity_type="company", entity_id=str(c.id), user_id=user.user_id,
              username=user.username, detail=f"the books' currency {old} → {new}")
    db.commit()
    with use_company(str(c.id)):
        recompute_all(db)               # entries already made: valued in the new currency where a rate is known
        db.commit()
    return {"currency": new, "changed": True}
