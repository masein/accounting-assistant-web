"""Effective-dated tax-rate resolution + standard-rate seeding.

``tax_rate_for(db, code, on_date)`` returns the rate in force on ``on_date`` for
a tax code, so an invoice dated before a rate change uses the old rate and one
after uses the new rate (§7.6). Standard rates ship seeded per jurisdiction,
including a historical change so the effective-dating is real, not theoretical.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tax_rate import TaxRate

# Tax treatments (§7.3). Only `standard` charges output tax; zero_rated/exempt
# charge none; reverse_charge shifts the liability to the customer (cross-border
# B2B) and nets to zero, recorded as a notional figure for the return.
TREATMENTS = ("standard", "zero_rated", "exempt", "reverse_charge")

# Standard rates per jurisdiction, each with a real historical change so the
# effective-dated selection is exercised. (Dates mirror real VAT changes.)
_SEED_RATES: list[tuple[str, str, str, float, str, str | None]] = [
    # (code, jurisdiction, description, rate, effective_from, effective_to)
    ("UK_VAT_STANDARD", "UK", "UK VAT standard rate", 17.5, "2008-12-01", "2011-01-03"),
    ("UK_VAT_STANDARD", "UK", "UK VAT standard rate", 20.0, "2011-01-04", None),
    ("UK_VAT_REDUCED", "UK", "UK VAT reduced rate", 5.0, "2008-12-01", None),
    ("UK_VAT_ZERO", "UK", "UK VAT zero rate", 0.0, "2008-12-01", None),
    ("IR_VAT_STANDARD", "IR", "Iran VAT standard rate", 8.0, "2015-03-21", "2019-03-20"),
    ("IR_VAT_STANDARD", "IR", "Iran VAT standard rate", 9.0, "2019-03-21", None),
    ("IR_VAT_ZERO", "IR", "Iran VAT zero rate", 0.0, "2015-03-21", None),
]


def seed_tax_rates(db: Session) -> int:
    """Insert the standard rate rows that don't already exist (idempotent by
    code + effective_from). Returns the number inserted."""
    inserted = 0
    for code, juris, desc, rate, eff_from, eff_to in _SEED_RATES:
        ef = date.fromisoformat(eff_from)
        exists = db.execute(
            select(TaxRate.id).where(TaxRate.code == code, TaxRate.effective_from == ef)
        ).first()
        if exists:
            continue
        db.add(TaxRate(
            code=code, jurisdiction=juris, description=desc, rate=rate,
            effective_from=ef, effective_to=(date.fromisoformat(eff_to) if eff_to else None),
        ))
        inserted += 1
    if inserted:
        db.commit()
    return inserted


def tax_rate_for(db: Session, code: str, on_date: date) -> float | None:
    """The rate (percent) in effect for ``code`` on ``on_date``, or None if no
    row covers that date. Picks the latest-starting window that contains it."""
    if not code:
        return None
    rows = db.execute(
        select(TaxRate).where(
            TaxRate.code == code,
            TaxRate.effective_from <= on_date,
        ).order_by(TaxRate.effective_from.desc())
    ).scalars().all()
    for r in rows:
        if r.effective_to is None or r.effective_to >= on_date:
            return float(r.rate)
    return None


def list_tax_rates(db: Session, code: str | None = None) -> list[TaxRate]:
    q = select(TaxRate).order_by(TaxRate.code, TaxRate.effective_from)
    if code:
        q = q.where(TaxRate.code == code)
    return list(db.execute(q).scalars().all())
