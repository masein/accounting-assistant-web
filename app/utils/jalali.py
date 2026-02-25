"""
Jalali (Solar Hijri / Shamsi) ↔ Gregorian date conversion utilities.

Recognizes formats like:
  1404/11/27   1404-11-27   27/11/1404
  ۱۴۰۴/۱۱/۲۷  (Persian digits)
  27 بهمن 1404  (month name)
  بهمن 1404     (month only → first of month)

All conversions return standard datetime.date (Gregorian).
"""
from __future__ import annotations

import re
from datetime import date

import jdatetime

_PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")

_MONTH_NAMES: dict[str, int] = {
    "فروردین": 1, "farvardin": 1, "فروردين": 1,
    "اردیبهشت": 2, "ordibehesht": 2, "ارديبهشت": 2,
    "خرداد": 3, "khordad": 3,
    "تیر": 4, "tir": 4, "تير": 4,
    "مرداد": 5, "mordad": 5, "amordad": 5,
    "شهریور": 6, "shahrivar": 6, "شهريور": 6,
    "مهر": 7, "mehr": 7,
    "آبان": 8, "aban": 8,
    "آذر": 9, "azar": 9,
    "دی": 10, "dey": 10, "دي": 10,
    "بهمن": 11, "bahman": 11,
    "اسفند": 12, "esfand": 12, "espand": 12,
}

_JALALI_YEAR_RANGE = range(1300, 1500)


def _to_ascii(text: str) -> str:
    return text.translate(_PERSIAN_DIGITS)


def jalali_to_gregorian(year: int, month: int, day: int) -> date:
    """Convert a Jalali date to Gregorian. Raises ValueError on invalid input."""
    jd = jdatetime.date(year, month, day)
    gd = jd.togregorian()
    return date(gd.year, gd.month, gd.day)


def gregorian_to_jalali(d: date) -> tuple[int, int, int]:
    """Convert a Gregorian date to (jalali_year, jalali_month, jalali_day)."""
    jd = jdatetime.date.fromgregorian(date=d)
    return jd.year, jd.month, jd.day


def format_jalali(d: date) -> str:
    """Format a Gregorian date as Jalali string 'YYYY/MM/DD'."""
    y, m, day = gregorian_to_jalali(d)
    return f"{y}/{m:02d}/{day:02d}"


def try_parse_jalali(text: str) -> date | None:
    """
    Try to parse a Jalali date from free text. Returns Gregorian date or None.
    Handles: 1404/11/27, 1404-11-27, ۱۴۰۴/۱۱/۲۷, 27 بهمن 1404, etc.
    """
    if not text:
        return None
    t = _to_ascii(text.strip())

    # 1404/11/27 or 1404-11-27
    m = re.search(r"\b(1[34]\d{2})[/\-](0?[1-9]|1[0-2])[/\-](0?[1-9]|[12]\d|3[01])\b", t)
    if m:
        try:
            return jalali_to_gregorian(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass

    # 27/11/1404 (day first if year > 31)
    m = re.search(r"\b(0?[1-9]|[12]\d|3[01])[/\-](0?[1-9]|1[0-2])[/\-](1[34]\d{2})\b", t)
    if m:
        try:
            return jalali_to_gregorian(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass

    # "27 بهمن 1404" or "بهمن 27 1404" or "بهمن 1404" (day optional)
    for name, month_num in _MONTH_NAMES.items():
        pattern = re.compile(
            r"(?:(\d{1,2})\s+" + re.escape(name) + r"\s+(\d{4}))|"
            r"(?:" + re.escape(name) + r"\s+(\d{1,2})\s+(\d{4}))|"
            r"(?:" + re.escape(name) + r"\s+(\d{4}))",
            re.IGNORECASE,
        )
        match = pattern.search(_to_ascii(text))
        if match:
            groups = match.groups()
            if groups[0] and groups[1]:
                day_val, year_val = int(groups[0]), int(groups[1])
            elif groups[2] and groups[3]:
                day_val, year_val = int(groups[2]), int(groups[3])
            elif groups[4]:
                day_val, year_val = 1, int(groups[4])
            else:
                continue
            if year_val in _JALALI_YEAR_RANGE:
                try:
                    return jalali_to_gregorian(year_val, month_num, day_val)
                except ValueError:
                    pass

    return None


def find_and_replace_jalali_dates(text: str) -> tuple[str, list[tuple[str, date]]]:
    """
    Find all Jalali dates in text, replace them with YYYY-MM-DD Gregorian.
    Returns (new_text, [(original_match, gregorian_date), ...]).
    """
    if not text:
        return text, []

    replacements: list[tuple[str, date]] = []
    result = text

    ascii_text = _to_ascii(text)

    # Pattern: YYYY/MM/DD or YYYY-MM-DD (Jalali year range)
    for m in re.finditer(r"\b(1[34]\d{2})[/\-](0?[1-9]|1[0-2])[/\-](0?[1-9]|[12]\d|3[01])\b", ascii_text):
        try:
            gd = jalali_to_gregorian(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            original = text[m.start():m.end()]
            replacements.append((original, gd))
            result = result.replace(original, gd.isoformat(), 1)
        except ValueError:
            pass

    # Pattern: DD/MM/YYYY (day-first Jalali)
    for m in re.finditer(r"\b(0?[1-9]|[12]\d|3[01])[/\-](0?[1-9]|1[0-2])[/\-](1[34]\d{2})\b", ascii_text):
        y, mo, d = int(m.group(3)), int(m.group(2)), int(m.group(1))
        try:
            gd = jalali_to_gregorian(y, mo, d)
            original = text[m.start():m.end()]
            if original not in [r[0] for r in replacements]:
                replacements.append((original, gd))
                result = result.replace(original, gd.isoformat(), 1)
        except ValueError:
            pass

    # Month name patterns
    for name, month_num in _MONTH_NAMES.items():
        pattern = re.compile(
            r"(\d{1,2})\s+" + re.escape(name) + r"\s+(\d{4})",
            re.IGNORECASE,
        )
        for match in pattern.finditer(_to_ascii(text)):
            d_val, y_val = int(match.group(1)), int(match.group(2))
            if y_val in _JALALI_YEAR_RANGE:
                try:
                    gd = jalali_to_gregorian(y_val, month_num, d_val)
                    original = text[match.start():match.end()]
                    if original not in [r[0] for r in replacements]:
                        replacements.append((original, gd))
                        result = result.replace(original, gd.isoformat(), 1)
                except ValueError:
                    pass

    return result, replacements
