"""Company expense settings: the per-distance mileage rate/unit and the
approval threshold. Stored as AppSetting key-values so they're configurable
per company; sensible per-locale defaults apply when unset.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.app_setting import AppSetting
from app.services.locale_service import get_reporting_locale

_RATE_KEY = "expense_mileage_rate"
_UNIT_KEY = "expense_mileage_unit"
_THRESHOLD_KEY = "expense_approval_threshold"

# Per-locale defaults. Rates are per unit distance; threshold 0 = disabled
# (everything posts normally) until a company sets one.
_DEFAULTS = {
    "uk": {"rate": 0.45, "unit": "mile"},
    "ir": {"rate": 5000.0, "unit": "km"},
    "default": {"rate": 0.0, "unit": "km"},
}


def _get(db: Session, key: str) -> str | None:
    row = db.execute(select(AppSetting).where(AppSetting.key == key)).scalar_one_or_none()
    return row.value if row else None


def _set(db: Session, key: str, value: str) -> None:
    row = db.execute(select(AppSetting).where(AppSetting.key == key)).scalar_one_or_none()
    if row:
        row.value = value
    else:
        db.add(AppSetting(key=key, value=value))


def get_expense_settings(db: Session) -> dict:
    loc = (get_reporting_locale(db) or "default").strip().lower()
    d = _DEFAULTS.get(loc, _DEFAULTS["default"])
    rate_raw = _get(db, _RATE_KEY)
    unit_raw = _get(db, _UNIT_KEY)
    thr_raw = _get(db, _THRESHOLD_KEY)
    try:
        rate = float(rate_raw) if rate_raw not in (None, "") else d["rate"]
    except ValueError:
        rate = d["rate"]
    try:
        threshold = int(float(thr_raw)) if thr_raw not in (None, "") else 0
    except ValueError:
        threshold = 0
    return {
        "mileage_rate": rate,
        "mileage_unit": (unit_raw or d["unit"]),
        "approval_threshold": threshold,
    }


def set_expense_settings(
    db: Session, *, mileage_rate: float | None = None,
    mileage_unit: str | None = None, approval_threshold: int | None = None,
) -> dict:
    if mileage_rate is not None:
        _set(db, _RATE_KEY, str(float(mileage_rate)))
    if mileage_unit is not None:
        _set(db, _UNIT_KEY, mileage_unit.strip())
    if approval_threshold is not None:
        _set(db, _THRESHOLD_KEY, str(int(approval_threshold)))
    db.commit()
    return get_expense_settings(db)


def get_approval_threshold(db: Session) -> int:
    """The amount above which an expense must be routed for approval (0 = off)."""
    return int(get_expense_settings(db)["approval_threshold"])
