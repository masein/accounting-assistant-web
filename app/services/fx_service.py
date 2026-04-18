"""Foreign exchange service: manages reporting currency and rate lookup/conversion.

Conventions:
  - `rate` on an ExchangeRate row means: 1 unit of `from_currency`
    equals `rate` units of `to_currency` on `effective_date`.
  - For a conversion on some query date `d`, we pick the most recent
    rate with effective_date <= d. If none exists, we fall back to
    the earliest available rate; if still nothing, we return None.
  - If `from == to`, the rate is always 1.0 (identity).
  - Inverse lookups are supported: if no direct rate is stored but
    the reverse pair has one, we invert it.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Iterable

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models.app_setting import AppSetting
from app.models.exchange_rate import ExchangeRate

logger = logging.getLogger(__name__)

REPORTING_CURRENCY_KEY = "reporting_currency"
DEFAULT_REPORTING_CURRENCY = "IRR"


def get_reporting_currency(db: Session) -> str:
    row = db.execute(
        select(AppSetting).where(AppSetting.key == REPORTING_CURRENCY_KEY)
    ).scalar_one_or_none()
    if row and (row.value or "").strip():
        return row.value.strip()
    return DEFAULT_REPORTING_CURRENCY


def set_reporting_currency(db: Session, currency: str) -> str:
    currency = (currency or "").strip() or DEFAULT_REPORTING_CURRENCY
    row = db.execute(
        select(AppSetting).where(AppSetting.key == REPORTING_CURRENCY_KEY)
    ).scalar_one_or_none()
    if row:
        row.value = currency
    else:
        db.add(AppSetting(key=REPORTING_CURRENCY_KEY, value=currency))
    db.flush()
    return currency


def _latest_rate(
    db: Session, from_ccy: str, to_ccy: str, on: date
) -> ExchangeRate | None:
    q = (
        select(ExchangeRate)
        .where(ExchangeRate.from_currency == from_ccy)
        .where(ExchangeRate.to_currency == to_ccy)
        .where(ExchangeRate.effective_date <= on)
        .order_by(ExchangeRate.effective_date.desc())
        .limit(1)
    )
    row = db.execute(q).scalar_one_or_none()
    if row:
        return row
    # Fall back to earliest-ever rate for this pair
    fallback = (
        select(ExchangeRate)
        .where(ExchangeRate.from_currency == from_ccy)
        .where(ExchangeRate.to_currency == to_ccy)
        .order_by(ExchangeRate.effective_date.asc())
        .limit(1)
    )
    return db.execute(fallback).scalar_one_or_none()


def get_rate(
    db: Session, from_ccy: str, to_ccy: str, on: date | None = None
) -> float | None:
    """Return rate such that amount_in_from * rate == amount_in_to.

    Returns None if no rate is found and currencies differ.
    """
    if (from_ccy or "").strip().upper() == (to_ccy or "").strip().upper():
        return 1.0
    if on is None:
        on = date.today()
    fc = from_ccy.strip().upper()
    tc = to_ccy.strip().upper()
    direct = _latest_rate(db, fc, tc, on)
    if direct:
        return float(direct.rate)
    reverse = _latest_rate(db, tc, fc, on)
    if reverse and reverse.rate:
        return 1.0 / float(reverse.rate)
    return None


def convert(
    db: Session,
    amount: float,
    from_ccy: str,
    to_ccy: str,
    on: date | None = None,
) -> float | None:
    rate = get_rate(db, from_ccy, to_ccy, on)
    if rate is None:
        return None
    return float(amount) * rate


def convert_or_none(
    db: Session,
    amount: int | float,
    from_ccy: str,
    to_ccy: str,
    on: date | None = None,
) -> int | None:
    """Convenience integer wrapper. Returns rounded int or None if no rate."""
    result = convert(db, float(amount), from_ccy, to_ccy, on)
    if result is None:
        return None
    return int(round(result))


DEFAULT_RATES: list[tuple[str, str, float, str]] = [
    # (from_currency, to_currency, rate, note)
    ("USD", "IRR", 150_000.0, "Default seed rate — update in Settings → Currency & FX"),
]


def seed_default_rates_if_empty(db: Session) -> int:
    """Seed `DEFAULT_RATES` for any (from, to) pair that has no rows yet.

    Returns the number of rows inserted. Idempotent — skips pairs that already
    have at least one rate, so admin-customised values are preserved across
    restarts.
    """
    inserted = 0
    today = date.today()
    for from_ccy, to_ccy, rate, note in DEFAULT_RATES:
        existing = db.execute(
            select(ExchangeRate)
            .where(ExchangeRate.from_currency == from_ccy)
            .where(ExchangeRate.to_currency == to_ccy)
            .limit(1)
        ).scalar_one_or_none()
        if existing:
            continue
        db.add(ExchangeRate(
            from_currency=from_ccy,
            to_currency=to_ccy,
            rate=rate,
            effective_date=today,
            note=note,
        ))
        inserted += 1
    if inserted:
        db.commit()
    return inserted


def available_currencies(db: Session) -> list[str]:
    """Distinct set of currencies seen in rates + default codes."""
    rows = db.execute(
        select(ExchangeRate.from_currency).distinct()
    ).scalars().all()
    rows2 = db.execute(
        select(ExchangeRate.to_currency).distinct()
    ).scalars().all()
    return sorted(set([*rows, *rows2, DEFAULT_REPORTING_CURRENCY]))
