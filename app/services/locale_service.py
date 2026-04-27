"""Reporting locale setting: controls which statement template is used.

- "default" — the generic IFRS-style layout (existing behavior).
- "ir" — the Iranian standard template (صورت سود و زیان, صورت وضعیت مالی, ...).
- "uk" — UK FRS 102 Section 1A (Companies Act 2006 formats).

Stored in AppSetting under the `reporting_locale` key.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.app_setting import AppSetting

REPORTING_LOCALE_KEY = "reporting_locale"
DEFAULT_LOCALE = "default"
SUPPORTED_LOCALES = frozenset({"default", "ir", "uk"})

DISPLAY_CALENDAR_KEY = "display_calendar"
SUPPORTED_CALENDARS = frozenset({"gregorian", "jalali"})


def _default_calendar_for_locale(locale: str) -> str:
    return "jalali" if (locale or "").strip().lower() == "ir" else "gregorian"


def get_display_calendar(db: Session) -> str:
    """Read the display-calendar setting. Defaults to Jalali when the
    reporting locale is 'ir', Gregorian otherwise."""
    row = db.execute(
        select(AppSetting).where(AppSetting.key == DISPLAY_CALENDAR_KEY)
    ).scalar_one_or_none()
    if row and (row.value or "").strip() in SUPPORTED_CALENDARS:
        return row.value.strip()
    return _default_calendar_for_locale(get_reporting_locale(db))


def set_display_calendar(db: Session, calendar: str) -> str:
    value = (calendar or "").strip().lower()
    if value not in SUPPORTED_CALENDARS:
        raise ValueError(f"Unsupported calendar '{calendar}'. Supported: {sorted(SUPPORTED_CALENDARS)}")
    row = db.execute(
        select(AppSetting).where(AppSetting.key == DISPLAY_CALENDAR_KEY)
    ).scalar_one_or_none()
    if row:
        row.value = value
    else:
        db.add(AppSetting(key=DISPLAY_CALENDAR_KEY, value=value))
    db.flush()
    return value


def get_reporting_locale(db: Session) -> str:
    row = db.execute(
        select(AppSetting).where(AppSetting.key == REPORTING_LOCALE_KEY)
    ).scalar_one_or_none()
    if row and (row.value or "").strip() in SUPPORTED_LOCALES:
        return row.value.strip()
    return DEFAULT_LOCALE


def set_reporting_locale(db: Session, locale: str) -> str:
    value = (locale or "").strip().lower() or DEFAULT_LOCALE
    if value not in SUPPORTED_LOCALES:
        raise ValueError(f"Unsupported locale '{locale}'. Supported: {sorted(SUPPORTED_LOCALES)}")
    row = db.execute(
        select(AppSetting).where(AppSetting.key == REPORTING_LOCALE_KEY)
    ).scalar_one_or_none()
    if row:
        row.value = value
    else:
        db.add(AppSetting(key=REPORTING_LOCALE_KEY, value=value))
    db.flush()
    return value
