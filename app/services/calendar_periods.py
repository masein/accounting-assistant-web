"""Months, quarters and years in the company's calendar (roadmap 2026-09 §3.5).

An Iranian company thinks in Jalali months — مهر ۱۴۰۵ runs from 23 September
to 22 October 2026 — so a monthly budget, a trend chart or a year summary cut
on Gregorian months splits every one of its months in two. Everything that
buckets money by period asks here, with the company's display calendar
(``locale_service.get_display_calendar``: Jalali for Iranian companies by
default).

Keys are ``"YYYY-MM"`` in the calendar they belong to; a year below 1700 is a
Jalali year, so a key alone says which calendar it is in (budgets stored
before this change keep their Gregorian keys and still work).

Seasons (فصل) are Jalali quarters: spring is Farvardin–Khordad. A Jalali week
starts on Saturday.
"""
from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache

import jdatetime

GREGORIAN = "gregorian"
JALALI = "jalali"

JALALI_MONTHS_FA = ("فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
                    "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند")
JALALI_MONTHS_EN = ("Farvardin", "Ordibehesht", "Khordad", "Tir", "Mordad", "Shahrivar",
                    "Mehr", "Aban", "Azar", "Dey", "Bahman", "Esfand")
GREGORIAN_MONTHS_EN = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
# as the browser's fa-IR Gregorian dates name them, so a month reads the same in both
GREGORIAN_MONTHS_FA = ("ژانویه", "فوریه", "مارس", "آوریل", "مه", "ژوئن", "ژوئیه", "اوت", "سپتامبر", "اکتبر", "نوامبر", "دسامبر")
SEASONS_FA = ("بهار", "تابستان", "پاییز", "زمستان")
SEASONS_EN = ("Spring", "Summer", "Autumn", "Winter")


def company_calendar(db) -> str:
    from app.services.locale_service import get_display_calendar
    try:
        return JALALI if get_display_calendar(db) == JALALI else GREGORIAN
    except Exception:  # noqa: BLE001 — a report never fails over the calendar
        return GREGORIAN


def calendar_of_key(key: str) -> str:
    return JALALI if int(key[:4]) < 1700 else GREGORIAN


# --- one month -------------------------------------------------------------------------------------------

@lru_cache(maxsize=8192)
def _jalali_ym(d: date) -> tuple[int, int]:
    j = jdatetime.date.fromgregorian(date=d)
    return j.year, j.month


def month_of(d: date, cal: str) -> tuple[int, int]:
    if cal == JALALI:
        return _jalali_ym(d)           # reports convert the same dates over and over
    return d.year, d.month


def month_key(d: date, cal: str) -> str:
    y, m = month_of(d, cal)
    return f"{y:04d}-{m:02d}"


def bounds(year: int, month: int, cal: str) -> tuple[date, date]:
    """(first, last) Gregorian dates of a month of that calendar."""
    if cal == JALALI:
        first = jdatetime.date(year, month, 1).togregorian()
        ny, nm = (year + 1, 1) if month == 12 else (year, month + 1)
        return first, jdatetime.date(ny, nm, 1).togregorian() - timedelta(days=1)
    return date(year, month, 1), date(year, month, monthrange(year, month)[1])


def key_bounds(key: str) -> tuple[date, date]:
    """Bounds of a ``"YYYY-MM"`` key, in the calendar its year says."""
    y, m = int(key[:4]), int(key[5:7])
    return bounds(y, m, calendar_of_key(key))


def month_bounds_of(d: date, cal: str) -> tuple[date, date]:
    return bounds(*month_of(d, cal), cal)


def shift(year: int, month: int, n: int) -> tuple[int, int]:
    idx = year * 12 + (month - 1) + n
    return idx // 12, idx % 12 + 1


def previous_month_key(d: date, cal: str) -> str:
    y, m = shift(*month_of(d, cal), -1)
    return f"{y:04d}-{m:02d}"


@dataclass(frozen=True)
class Period:
    key: str
    start: date
    end: date
    label: str


def months_between(a: date, b: date, cal: str, lang: str = "en") -> list[Period]:
    """Every month of the calendar that touches [a, b], oldest first."""
    out: list[Period] = []
    y, m = month_of(a, cal)
    while True:
        first, last = bounds(y, m, cal)
        if first > b:
            break
        key = f"{y:04d}-{m:02d}"
        out.append(Period(key, first, last, month_label(key, lang)))
        y, m = shift(y, m, 1)
    return out


def last_n_months(today: date, n: int, cal: str, lang: str = "en") -> list[Period]:
    y, m = shift(*month_of(today, cal), -(n - 1))
    first, _ = bounds(y, m, cal)
    return months_between(first, today, cal, lang)


# --- quarters (seasons), weeks, years ---------------------------------------------------------------------

def quarter_key(d: date, cal: str) -> str:
    y, m = month_of(d, cal)
    return f"{y:04d}-Q{(m - 1) // 3 + 1}"


def quarter_bounds(key: str) -> tuple[date, date]:
    y, q = int(key[:4]), int(key[-1])
    cal = calendar_of_key(key)
    return bounds(y, 3 * q - 2, cal)[0], bounds(y, 3 * q, cal)[1]


def week_start(d: date, cal: str) -> date:
    start_weekday = 5 if cal == JALALI else 0          # Saturday / Monday
    return d - timedelta(days=(d.weekday() - start_weekday) % 7)


def week_key(d: date, cal: str) -> str:
    """Jalali: the week's first day (Saturday) as "1405-07-05". Gregorian: the
    "2026-W39" form reports used before §3.5."""
    s = week_start(d, cal)
    if cal == JALALI:
        j = jdatetime.date.fromgregorian(date=s)
        return f"{j.year:04d}-{j.month:02d}-{j.day:02d}"
    return s.strftime("%Y-W%W")


def year_of(d: date, cal: str) -> int:
    return month_of(d, cal)[0]


def year_bounds(year: int, cal: str | None = None) -> tuple[date, date]:
    cal = cal or (JALALI if year < 1700 else GREGORIAN)
    return bounds(year, 1, cal)[0], bounds(year, 12, cal)[1]


# --- labels ---------------------------------------------------------------------------------------------------

def _fa_digits(text: str) -> str:
    return text.translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))


def month_label(key: str, lang: str = "en") -> str:
    """"مهر ۱۴۰۵" / "Mehr 1405" / "Oct 2026" / "اکتبر ۲۰۲۶"."""
    y, m = int(key[:4]), int(key[5:7])
    if calendar_of_key(key) == JALALI:
        if lang == "fa":
            return f"{JALALI_MONTHS_FA[m - 1]} {_fa_digits(str(y))}"
        return f"{JALALI_MONTHS_EN[m - 1]} {y}"
    if lang == "fa":
        return f"{GREGORIAN_MONTHS_FA[m - 1]} {_fa_digits(str(y))}"
    return f"{GREGORIAN_MONTHS_EN[m - 1]} {y}"


def quarter_label(key: str, lang: str = "en") -> str:
    y, q = int(key[:4]), int(key[-1])
    if calendar_of_key(key) == JALALI:
        return (f"{SEASONS_FA[q - 1]} {_fa_digits(str(y))}" if lang == "fa" else f"{SEASONS_EN[q - 1]} {y}")
    return f"سه‌ماهه {_fa_digits(str(q))} {_fa_digits(str(y))}" if lang == "fa" else f"Q{q} {y}"


# --- a report's buckets -------------------------------------------------------------------------------------

def _gregorian_season(d: date) -> str:
    m = d.month
    season = "Spring" if m in (3, 4, 5) else "Summer" if m in (6, 7, 8) else "Autumn" if m in (9, 10, 11) else "Winter"
    return f"{d.year}-{season}"


def period_key(d: date, granularity: str, cal: str) -> str:
    """The bucket a date falls in: monthly "1405-07", quarterly/seasonal
    "1405-Q3" (a Jalali quarter is a season), weekly the week's first day."""
    if granularity == "weekly":
        return week_key(d, cal)
    if granularity == "quarterly":
        return quarter_key(d, cal)
    if granularity == "seasonal":
        return quarter_key(d, cal) if cal == JALALI else _gregorian_season(d)
    return month_key(d, cal)


def period_ends(a: date, b: date, granularity: str, cal: str) -> list[tuple[str, date]]:
    """(key, last day — never past ``b``) of every bucket touching [a, b]."""
    out: dict[str, date] = {}
    if granularity == "weekly":
        s = week_start(a, cal)
        while s <= b:
            out[week_key(s, cal)] = min(s + timedelta(days=6), b)
            s += timedelta(days=7)
    elif granularity in ("quarterly", "seasonal") and (cal == JALALI or granularity == "quarterly"):
        for p in months_between(a, b, cal):
            key = quarter_key(p.start, cal)
            out[key] = min(quarter_bounds(key)[1], b)
    elif granularity == "seasonal":                   # Gregorian meteorological seasons
        for p in months_between(a, b, GREGORIAN):
            out[_gregorian_season(p.start)] = min(p.end, b)
    else:
        for p in months_between(a, b, cal):
            out[p.key] = min(p.end, b)
    return sorted(out.items(), key=lambda kv: kv[1])
